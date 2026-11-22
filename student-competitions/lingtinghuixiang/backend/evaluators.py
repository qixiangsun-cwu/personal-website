# -*- coding: utf-8 -*-
"""事后独立评估器 · 11/12 零侵入原型

设计原则（对应「事后判定 + 三个指标分开遍历」）：
  1. **只读**：不写库、不改通话状态、不参与 WebSocket 实时链路；
     因此不可能影响实时打断、TTS 播报或状态机推进。
  2. **三个评估器各自遍历自己的样本集合**，不共享分母：
       - 闭环率   → 已终结且该由 AI 负责的业务任务（一任务一判）
       - 合规率   → 所有产生过 AI 播报的通话 / 逐轮（不限终态）
       - 平均轮次 → 有效通话里的商家真实发言轮（打断轮不计）
  3. **每个排除项留原因和证据**，并输出样本流向对账表，防止靠剔样本刷分。
  4. 纯标准库实现，不依赖 fastapi / zhipuai / 项目 db 模块，可独立运行。

本模块只做「事后评估」，不替代实时链路。接入 /api/metrics 属于第二阶段，
见同目录交付的《11.12 事后独立评估方案与原型说明.md》。
"""
from __future__ import annotations

import json
import re
import sqlite3
import statistics
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional

# ---- 与 backend/agent.py 保持一致的质检常量（此处复制，避免 import 业务模块）----
REQUIRED_ELEMENTS = ("自报身份", "身份确认", "驳回原因", "整改动作", "结束语")
FORBIDDEN_PHRASES = (
    "保证通过", "绝对能过", "一定能过", "包过", "肯定过", "百分百通过",
    "封店", "关店", "罚款", "处罚", "身份证号", "银行卡号", "验证码", "密码",
)
ELEMENT_PATTERNS = {
    "自报身份": r"资质审核专员|京东.{0,6}专员",
    "身份确认": r"请问您是|您是.{0,20}负责人|是不是.{0,12}负责人|负责人吗|负责人本人",
    "驳回原因": r"因|由于|原因|不符合|缺少|不完整|过期|失效|驳回",
    "整改动作": r"请您|需您|需要您|重新|补齐|提供|修改|上传|开具|提交|登录",
    "结束语": r"感谢|再见|祝",
}

# 闭环率：只有「已终结且 AI 应负责」的终态进分母
CLOSURE_TERMINAL_STATUS = frozenset({"completed"})
# 未终结 / 非 AI 责任 / 无质检对象 —— 从闭环率排除，但**不一定**从合规率排除
EXCLUDED_STATUS = frozenset({
    "ringing", "in_progress", "pending_followup", "superseded",
    "failed", "rejected", "missed", "void", "void_data_issue",
    "void_not_applicable", "void_offline",
})
EXCLUDED_LABEL = {
    "ringing": "未接/响铃中", "in_progress": "进行中", "pending_followup": "待跟进（挂断/改期）",
    "superseded": "被二次呼叫承接", "failed": "链路中断/异常", "rejected": "拒接",
    "missed": "未接通", "void": "不适用/转线下", "void_data_issue": "数据前置缺陷",
    "void_not_applicable": "非适用对象", "void_offline": "业务转线下",
}

# 商家主动改期 / 主动拨回：当前状态机未识别，评估器只在事后标出证据
RESCHEDULE_PATTERNS = (
    "明天再说", "明天再", "改天", "等会", "等一下", "现在不空", "现在没空", "没空",
    "我在忙", "在开会", "下午再打", "晚点再打", "回头再打", "再联系", "下次",
    "我打给你", "我拨回", "主动拨", "预约", "过两天", "今天不方便", "不方便",
)
# 泛化兜底话术：数据前置缺陷的可观测信号（形式合规、实质没说具体驳回原因）
GENERIC_REASON = ("资质材料不符合要求", "材料不符合要求", "具体以京麦后台驳回意见为准")
GENERIC_ACTION = ("电话沟通", "按驳回意见补齐材料后重新提交")


def _load_json(raw: Any, default: Any) -> Any:
    try:
        v = json.loads(raw or "")
    except Exception:
        return default
    return v if isinstance(v, type(default)) else default


def _mean(values: List[float]) -> float:
    return round(statistics.mean(values), 2) if values else 0.0


class MetricEvaluator:
    """只读打开一份 zhiyu.db，独立遍历三个指标的样本集合。"""

    def __init__(self, db_path: str, strict_materials: bool = False):
        self.db_path = str(db_path)
        # 资料核实门槛：开启后，result_json 里没有核实证据的一律不算闭环
        self.strict_materials = bool(strict_materials)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.calls: List[dict] = [dict(r) for r in self.conn.execute("SELECT * FROM calls")]
        self.turns: List[dict] = [dict(r) for r in self.conn.execute("SELECT * FROM call_turns ORDER BY id")]
        try:
            self.tickets = {r["call_id"]: dict(r)
                            for r in self.conn.execute("SELECT * FROM tickets")}
        except sqlite3.Error:
            self.tickets = {}
        self.by_call: Dict[str, List[dict]] = defaultdict(list)
        for t in self.turns:
            self.by_call[t.get("call_id") or ""].append(t)
        self.status_of = {c.get("call_id"): (c.get("status") or "") for c in self.calls}

    # ------------------------------------------------------------------ 工具
    @staticmethod
    def _reschedule_evidence(merchant_turns: List[dict]) -> Optional[str]:
        for t in merchant_turns:
            text = t.get("text") or ""
            if any(k in text for k in RESCHEDULE_PATTERNS):
                return text
        return None

    @staticmethod
    def _materials_evidence(call: dict) -> bool:
        """真实资料核实证据：当前库尚无该字段，此函数用于暴露缺口。"""
        rj = _load_json(call.get("result_json"), {})
        checks = rj.get("closure_checks") or {}
        return bool(rj.get("materials_verified") or checks.get("materials_verified")
                    or rj.get("verified_items") or call.get("materials_verified"))

    # ------------------------------------------------------------ ① 闭环评估器
    def evaluate_closure(self) -> dict:
        """遍历【业务任务】：分母 = resolved + failed_system，一任务只判一次。"""
        excluded: Counter = Counter()
        tasks: List[dict] = []
        reschedule_flagged: List[dict] = []
        materials_missing: List[str] = []

        for c in self.calls:
            cid = c.get("call_id")
            st = (c.get("status") or "").strip()
            m_turns = [t for t in self.by_call.get(cid, []) if t.get("role") == "merchant"]
            ev = self._reschedule_evidence(m_turns)
            if ev:
                reschedule_flagged.append({"call_id": cid, "status": st, "evidence": ev,
                                           "note": "商家已表达改期/主动拨回，任务未终结"})
            if st in EXCLUDED_STATUS or st not in CLOSURE_TERMINAL_STATUS:
                excluded[st or "(空状态)"] += 1
                continue
            if not m_turns:
                excluded["无商家发言"] += 1
                continue
            if cid not in self.tickets:
                excluded["无结构化回写"] += 1
                continue
            rj = _load_json(c.get("result_json"), {})
            has_material = self._materials_evidence(c)
            if not has_material:
                materials_missing.append(cid)
            closed = bool(c.get("closed_loop")) and not rj.get("parked")
            if self.strict_materials and not has_material:
                closed = False
            tasks.append({
                "call_id": cid, "status": st, "closed_loop": closed,
                "materials_evidence": has_material,
                "reschedule_evidence": ev,
                "stop_stage": c.get("stop_stage") or "",
            })

        resolved = sum(1 for t in tasks if t["closed_loop"])
        return {
            "denominator": len(tasks),
            "resolved": resolved,
            "failed_system": len(tasks) - resolved,
            "closed_loop_rate": round(resolved / len(tasks) * 100, 1) if tasks else 0.0,
            "excluded": {EXCLUDED_LABEL.get(k, k): v for k, v in sorted(excluded.items())},
            "reschedule_flagged": reschedule_flagged,
            "materials_evidence_missing": materials_missing,
            "materials_note": ("当前库无资料核实字段，闭环判定仍是「话术播报闭环」；"
                               "启用 strict_materials 后以上 %d 通全部不计闭环。" % len(materials_missing)),
            "strict_materials": self.strict_materials,
            "tasks": tasks,
        }

    # ------------------------------------------------------------ ② 合规评估器
    def evaluate_compliance(self) -> dict:
        """遍历【所有产生过 AI 播报的通话/逐轮】，与通话终态无关。

        两层分开：红线违规（合法性，一票否决）、必说要素覆盖（完整性）。
        """
        agent_by_call: Dict[str, List[dict]] = {}
        for cid, ts in self.by_call.items():
            ats = [t for t in ts if t.get("role") == "agent"]
            if ats:
                agent_by_call[cid] = ats

        denominator = len(agent_by_call)
        violations: List[dict] = []
        coverage_list: List[float] = []
        partial: List[dict] = []
        data_suspects: List[dict] = []
        per_call: List[dict] = []

        for cid, ats in sorted(agent_by_call.items()):
            joined = "".join(t.get("text") or "" for t in ats)
            bad = [p for p in FORBIDDEN_PHRASES if p in joined]
            if bad:
                violations.append({"call_id": cid, "status": self.status_of.get(cid, ""),
                                   "phrases": bad})
            covered = set()
            gated = False
            for t in ats:
                comp = _load_json(t.get("compliance"), {})
                if not isinstance(comp, dict):
                    continue
                if "element_stage" in comp:
                    gated = True          # 有阶段门控数据：以落库的门控结果为准
                elems = comp.get("elements", comp)
                for k, v in elems.items():
                    if k in REQUIRED_ELEMENTS and v:
                        covered.add(k)
            if not gated:
                # 旧数据（11/12 之前落库）没有阶段信息，退回纯文本复核
                for t in ats:
                    for k, pat in ELEMENT_PATTERNS.items():
                        if re.search(pat, t.get("text") or ""):
                            covered.add(k)
            cov = len(covered) / len(REQUIRED_ELEMENTS)
            coverage_list.append(cov)
            st = self.status_of.get(cid, "")
            generic = [g for g in (GENERIC_REASON + GENERIC_ACTION) if g in joined]
            if generic:
                data_suspects.append({"call_id": cid, "status": st, "generic_phrases": generic,
                                      "note": "疑似数据前置缺陷：说不出本场景具体驳回原因/整改动作"})
            if st in EXCLUDED_STATUS and cov < 1.0:
                partial.append({"call_id": cid, "status": st, "coverage": round(cov, 2),
                                "covered": sorted(covered)})
            per_call.append({"call_id": cid, "status": st, "agent_turns": len(ats),
                             "coverage": round(cov, 2), "red_line_ok": not bad,
                             "covered": sorted(covered)})

        passed = sum(1 for p in per_call if p["red_line_ok"] and p["coverage"] >= 1.0)
        # 旧口径对照：只在已终结样本内取工单总分
        legacy_rates = [t.get("compliance_rate") for cid, t in self.tickets.items()
                        if self.status_of.get(cid) == "completed"
                        and t.get("compliance_rate") is not None]
        legacy = round(sum(legacy_rates) / len(legacy_rates) * 100, 1) if legacy_rates else 0.0

        return {
            "denominator": denominator,
            "red_line_violations": violations,
            "red_line_pass_rate": round((denominator - len(violations)) / denominator * 100, 1)
                                  if denominator else 100.0,
            "element_coverage_rate": round(_mean(coverage_list) * 100, 1),
            "official_compliance_rate": round(passed / denominator * 100, 1) if denominator else 0.0,
            "legacy_compliance_rate": legacy,
            "partial_coverage_calls": partial,
            "data_missing_suspects": data_suspects,
            "per_call": per_call,
            "note": "分母=所有产生过 AI 播报的通话（含中途挂断/系统失败/链路中断）；"
                    "只有 0 轮播报的僵尸通话不进分母。",
        }

    # ------------------------------------------------------------ ③ 轮次评估器
    def evaluate_turns(self) -> dict:
        """遍历【有效通话】的商家真实发言轮；打断轮不计入。"""
        rows: List[dict] = []
        interrupted_excluded = 0
        for cid, ts in self.by_call.items():
            st = self.status_of.get(cid, "")
            if st in EXCLUDED_STATUS or st not in CLOSURE_TERMINAL_STATUS:
                continue
            m = [t for t in ts if t.get("role") == "merchant"]
            if not m:
                continue
            real = [t for t in m if not t.get("interrupted")]
            interrupted_excluded += len(m) - len(real)
            rows.append({"call_id": cid, "status": st,
                         "real_turns": len(real), "legacy_turns": len(m)})
        return {
            "denominator": len(rows),
            "avg_turns": _mean([r["real_turns"] for r in rows]),
            "legacy_avg_turns": _mean([r["legacy_turns"] for r in rows]),
            "interrupted_turns_excluded": interrupted_excluded,
            "low_turn_calls": sum(1 for r in rows if r["real_turns"] < 3),
            "rows": rows,
            "note": "打断轮只记录事件、不计入对话轮次；"
                    "「流程走完但不足 3 轮」仍留在分母，不剔除。",
        }

    # -------------------------------------------------------------- 样本对账
    def reconciliation(self, closure: dict, compliance: dict, turns: dict) -> dict:
        status_counter = Counter((c.get("status") or "(空)") for c in self.calls)
        return {
            "total_calls": len(self.calls),
            "closure_denominator": closure["denominator"],
            "compliance_denominator": compliance["denominator"],
            "turns_denominator": turns["denominator"],
            "by_status": {EXCLUDED_LABEL.get(k, k): v for k, v in sorted(status_counter.items())},
            "note": "三个分母本就不同，不是数据丢失：闭环看任务终态、合规看 AI 播报、轮次看商家发言。"
                    "排除项逐类计数并留证据，可与上表对账。",
        }

    # ------------------------------------------------------------------ 汇总
    def report(self) -> dict:
        closure = self.evaluate_closure()
        compliance = self.evaluate_compliance()
        turns = self.evaluate_turns()
        return {
            "db_path": self.db_path,
            "strict_materials": self.strict_materials,
            "closure": closure,
            "compliance": compliance,
            "turns": turns,
            "reconciliation": self.reconciliation(closure, compliance, turns),
            "targets": {"closed_loop_rate": 80.0, "compliance_rate": 95.0,
                        "turns_min": 3},
        }


def format_text(rep: dict) -> str:
    """把报告渲染成可当场给评委看的中文文本。"""
    c, k, t, r = rep["closure"], rep["compliance"], rep["turns"], rep["reconciliation"]
    tg = rep["targets"]
    L: List[str] = []
    add = L.append
    add("=" * 68)
    add("事后独立评估报告（只读 · 三个指标分开遍历）")
    add("=" * 68)
    add(f"数据库：{rep['db_path']}   资料核实门槛：{'启用' if rep['strict_materials'] else '未启用'}")
    add("")
    add("【① 任务闭环率】分母 = 已终结且该由 AI 负责的业务任务（一任务一判）")
    add(f"  闭环率 {c['closed_loop_rate']}%  (达标线 ≥{tg['closed_loop_rate']}%)   "
        f"分子 {c['resolved']} / 分母 {c['denominator']}   系统失败 {c['failed_system']}")
    for name, n in c["excluded"].items():
        add(f"    - 排除 {name}: {n} 通")
    add(f"  资料核实证据缺失：{len(c['materials_evidence_missing'])} 通")
    add(f"  {c['materials_note']}")
    if c["reschedule_flagged"]:
        add(f"  ⚠ 检出商家改期/主动拨回 {len(c['reschedule_flagged'])} 通（当前状态机未识别）：")
        for x in c["reschedule_flagged"][:5]:
            add(f"      {x['call_id']} [{x['status']}] 原话：{x['evidence'][:40]}")
    add("")
    add("【② 必说话术合规率】分母 = 所有产生过 AI 播报的通话（不限终态）")
    add(f"  合规率 {k['official_compliance_rate']}%  (达标线 ≥{tg['compliance_rate']}%)   "
        f"分母 {k['denominator']} 通")
    add(f"  红线通过率 {k['red_line_pass_rate']}%   必说要素覆盖率 {k['element_coverage_rate']}%")
    add(f"  旧口径（只在已终结样本内取工单总分）：{k['legacy_compliance_rate']}%  ← 对比用")
    if k["red_line_violations"]:
        add(f"  ⚠ 红线违规 {len(k['red_line_violations'])} 通（旧口径会漏掉）：")
        for x in k["red_line_violations"][:5]:
            add(f"      {x['call_id']} [{x['status']}] {','.join(x['phrases'])}")
    if k["partial_coverage_calls"]:
        add(f"  中途未终结、要素只覆盖一部分：{len(k['partial_coverage_calls'])} 通（单列，不按 0 分粗暴计入）")
    if k["data_missing_suspects"]:
        add(f"  ⚠ 疑似数据前置缺陷（泛化话术）：{len(k['data_missing_suspects'])} 通")
        for x in k["data_missing_suspects"][:5]:
            add(f"      {x['call_id']} [{x['status']}] {','.join(x['generic_phrases'])[:50]}")
    add("")
    add("【③ 平均对话轮次】分母 = 有效通话的商家真实发言轮（打断轮不计）")
    add(f"  平均 {t['avg_turns']} 轮  (下限 {tg['turns_min']} 轮)   "
        f"分母 {t['denominator']} 通   旧口径 {t['legacy_avg_turns']} 轮")
    add(f"  已剔除打断轮 {t['interrupted_turns_excluded']} 条；不足 3 轮 {t['low_turn_calls']} 通")
    add("")
    add("【样本流向对账】")
    add(f"  总通话 {r['total_calls']} → 闭环分母 {r['closure_denominator']} / "
        f"合规分母 {r['compliance_denominator']} / 轮次分母 {r['turns_denominator']}")
    for name, n in r["by_status"].items():
        add(f"    {name}: {n}")
    add(f"  {r['note']}")
    add("=" * 68)
    return "\n".join(L)
