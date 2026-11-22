# -*- coding: utf-8 -*-
"""影子对照评测（Shadow Eval）—— 自建评测集 + 规则层 vs 模型层 决策对照

## 这个工具解决什么问题

赛题要求提交「自建评测集说明、原始日志，以及 **RSI 迭代前后的对比数据**」，
并且明确「评审将使用未公开测试集，并在现场随机抽测」。
在此之前，项目的 180+ 条回归**全部跑在 offline=True 下**（见各 _test_*.py 的构造），
也就是说：**规则层被反复验证，模型层从未被验证过一次**。
所谓「评测可复现」只覆盖了不受怀疑的那一半。

本工具把两种决策并排跑，给出可复现的对照数据：

    rules  模式：当前线上架构（reply_policy / _fallback_intent / _evidence_gate）
    model  模式：目标架构（模型直接输出 intent + 追问类型 + 槽位）

对照指标（全部对应赛题评分维度）：
    · 追问类型准确率      → 维度2「意图识别准确率」
    · 意图准确率          → 维度2「意图识别准确率」
    · 追问误判率          → 追问被判成「已理解/含糊」的比例（该答疑却不答疑）
    · 槽位完整率          → 维度2「槽位完整率」
    · 禁语命中数          → 维度4「禁语拦截率」
    · 分歧清单            → RSI 迭代依据（哪一条规则该退休 / 哪条提示词该改）

## 用法

    python shadow_eval.py              # 只跑 rules（离线，随时可跑，无需 API Key）
    python shadow_eval.py --model      # 追加 model 模式（需 ZHIPU_API_KEY）
    python shadow_eval.py --compare    # 读已有两份结果，输出 RSI 前后对照

结果落 data/shadow_eval_<mode>.json，含逐条原始记录（满足「原始日志」要求）。
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
import time
from typing import Any, Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "data")
EVAL_SET = os.path.join(DATA, "eval_set.json")

sys.path.insert(0, HERE)

import slots  # noqa: E402

# 阈值：长于此长度的句子不算「短应答」，用于误判率的分母（短句本来就容易含糊）
MIN_JUDGEABLE = 4


# ---------------------------------------------------------------- 数据
def load_eval_set(path: str = EVAL_SET) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    items = data.get("items") or []
    for i, it in enumerate(items):
        it.setdefault("id", f"case-{i + 1}")
        it.setdefault("qtype", "")
        it.setdefault("gold_intent", "")
        it.setdefault("material", "其他")
    return {"meta": data.get("_meta") or {}, "items": items}


# ---------------------------------------------------------------- 规则模式
def predict_rules(item: Dict[str, Any]) -> Dict[str, Any]:
    """当前线上架构的判定：全部来自 reply_policy / agent 的离线链路。

    刻意**不改**这些函数，测的就是线上真实行为（连它的毛病一起测出来）。
    """
    import agent

    text = item.get("text") or ""
    # 追问类型：线上没有这个概念（这就是缺口），用 slots 的离线兜底代替
    qtype = slots.fallback_qtype(text)
    # 4 分类语义标签：线上离线走 _fallback_intent + _evidence_gate
    intent = agent.CallAgent._evidence_gate(None, text,
                                            agent.CallAgent._fallback_intent(None, text))
    # 槽位：线上只有场景三槽（且这里没有工单上下文），语义四槽全空
    sl = slots.empty()
    if qtype:
        slots.fill(sl, "qtype", qtype)
    return {"qtype": qtype, "intent": intent, "slots": sl, "source": "rules"}


# ---------------------------------------------------------------- 规则否决模式
def predict_gate(item: Dict[str, Any]) -> Dict[str, Any]:
    """假设**模型判对了**，看规则层会推翻多少 —— 这是本工具最关键的一个口径。

    做法：把 gold_intent 当成模型输出的标签，原样喂给 agent._evidence_gate
    （线上每一轮都会跑的那道「强证据门槛」），看它把标签改成了什么。
    被改掉 = 模型就算答对也白答 —— 这正是「AI 不自己思考」的机制性证据。

    这个模式不需要 API Key、不花钱、完全可复现，因此可以直接进回归。
    """
    import agent

    text = item.get("text") or ""
    gold = (item.get("gold_intent") or "other").strip()
    gated = agent.CallAgent._evidence_gate(None, text, gold)
    return {
        "qtype": slots.fallback_qtype(text),
        "intent": gated,
        "slots": slots.empty(),
        "source": "gate",
        "model_said": gold,
        "overridden": gated != gold,
    }


# ---------------------------------------------------------------- 模型模式
MODEL_PROMPT = """你是电话外呼系统的语义解析器。商家那句话在「京东商家资质审核驳回外呼」语境下，
请判断它的意图并填槽。

只输出 JSON，不要解释、不要标点之外的任何文字：
{{"intent": "new_question|confirmed_understood|hesitant|other",
  "slots": {{"qtype": "", "material": "", "action": "", "expect": ""}}}}

intent 判定口径：
  new_question          提出了问题、追问、质疑、转折（要正面解答）
  confirmed_understood  明确表示已理解且没有新问题
  hesitant              含糊、犹豫、未明确表态
  other                 以上都不是（寒暄、拒接等）

{slot_block}

注意：「京麦驳回」这类场景标记不是商家提问；「我在哪查商标状态」是知识来源而不是操作路径。

商家原话：{text}"""


def predict_model(client, model: str, item: Dict[str, Any]) -> Dict[str, Any]:
    """目标架构的判定：一次调用同时给出 intent 与槽位（用于证明三段调用可合一）。"""
    prompt = MODEL_PROMPT.format(slot_block=slots.decide_prompt_block(),
                                  text=item.get("text") or "")
    raw = ""
    try:
        r = client.chat.completions.create(
            model=model, messages=[{"role": "user", "content": prompt}],
            temperature=0, max_tokens=200, stream=False)
        raw = (r.choices[0].message.content or "").strip()
    except Exception as e:
        return {"qtype": "", "intent": "other", "slots": slots.empty(),
                "source": "model", "error": f"{type(e).__name__}: {e}"}
    data = {}
    try:
        data = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
    except Exception:
        pass
    sl = slots.empty()
    for k, v in ((data.get("slots") or {})).items():
        slots.fill(sl, k, v)
    intent = str(data.get("intent") or "other").strip()
    if intent not in ("new_question", "confirmed_understood", "hesitant", "other"):
        intent = "other"
    return {"qtype": sl.get("qtype") or "", "intent": intent, "slots": sl,
            "source": "model", "raw": raw[:200]}


# ---------------------------------------------------------------- 指标
_FORBIDDEN = ("保证通过", "绝对能过", "一定能过", "包过", "百分百通过", "封店", "罚款",
              "身份证号", "银行卡号", "验证码", "密码")


def forbidden_hits(text: str) -> List[str]:
    """禁语命中（仅用于评测答案侧，判定逻辑与 agent.FORBIDDEN_PHRASES 同源）。"""
    return [p for p in _FORBIDDEN if p in (text or "")]


def score(items: List[Dict[str, Any]], results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """按赛题口径算分。分母口径写死在这里，避免每次口径漂移。"""
    n = len(items)
    q_total = q_ok = 0
    i_total = i_ok = 0
    judgeable = misrouted = 0
    slot_sum = 0.0
    bad_answers: List[dict] = []
    disagreements: List[dict] = []

    for it, r in zip(items, results):
        text = it.get("text") or ""
        gold_q = (it.get("qtype") or "").strip()
        gold_i = (it.get("gold_intent") or "").strip()
        got_q = (r.get("qtype") or "").strip()
        got_i = (r.get("intent") or "").strip()

        # 追问类型准确率：只统计有 gold 的（无 gold 的条目不计入分母）
        if gold_q:
            q_total += 1
            if got_q == gold_q:
                q_ok += 1
            elif got_q != gold_q:
                disagreements.append({"id": it["id"], "text": text, "field": "qtype",
                                      "gold": gold_q, "got": got_q})

        # 意图准确率：只统计有 gold 的
        if gold_i:
            i_total += 1
            if got_i == gold_i:
                i_ok += 1
            elif got_i != gold_i:
                disagreements.append({"id": it["id"], "text": text, "field": "intent",
                                      "gold": gold_i, "got": got_i})

        # 追问误判率：应该答疑（gold_intent=new_question）却判成「已理解/含糊」
        if gold_i == "new_question" and len(text) >= MIN_JUDGEABLE:
            judgeable += 1
            if got_i in ("confirmed_understood", "hesitant", "other"):
                misrouted += 1

        slot_sum += slots.completeness(r.get("slots"))

        # 答案侧：命中禁语即记一条（这一项永远应当是 0）
        ans = it.get("gold_answer") or ""
        hits = forbidden_hits(ans)
        if hits:
            bad_answers.append({"id": it["id"], "hits": hits})

    return {
        "cases": n,
        "qtype_accuracy": round(q_ok / q_total, 3) if q_total else None,
        "qtype_scored": q_total,
        "intent_accuracy": round(i_ok / i_total, 3) if i_total else None,
        "intent_scored": i_total,
        "question_misroute_rate": round(misrouted / judgeable, 3) if judgeable else None,
        "question_misroute_scored": judgeable,
        "slot_completeness": round(slot_sum / n, 3) if n else None,
        "forbidden_in_gold": len(bad_answers),
        "forbidden_detail": bad_answers,
        "disagreements": disagreements,
    }


# ---------------------------------------------------------------- 运行
def run(mode: str = "rules", limit: Optional[int] = None) -> Dict[str, Any]:
    data = load_eval_set()
    items = data["items"][:limit] if limit else data["items"]
    client = None
    model_name = ""
    if mode == "model":
        from dotenv import load_dotenv
        load_dotenv(os.path.join(HERE, ".env"))
        key = os.getenv("ZHIPU_API_KEY", "")
        if not key:
            raise SystemExit("model 模式需要 .env 里的 ZHIPU_API_KEY")
        from zhipuai import ZhipuAI
        client = ZhipuAI(api_key=key)
        model_name = os.getenv("SHADOW_MODEL") or os.getenv("ZHIPU_MODEL") or "glm-4-flash"

    t0 = time.time()
    results = []
    for it in items:
        if mode == "model":
            results.append(predict_model(client, model_name, it))
        elif mode == "gate":
            results.append(predict_gate(it))
        else:
            results.append(predict_rules(it))
        print(f"  [{it['id']}] {it['text'][:24]:<26} → "
              f"qtype={results[-1]['qtype'] or '—':<6} intent={results[-1]['intent']}")

    rep = {
        "mode": mode,
        "model": model_name,
        "eval_set": os.path.basename(EVAL_SET),
        "ran_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "elapsed_s": round(time.time() - t0, 1),
        "meta": data["meta"],
        "metrics": score(items, results),
        "raw": [{"id": it["id"], "text": it["text"], **r} for it, r in zip(items, results)],
    }
    if mode == "gate":
        # 「模型判对却被规则推翻」的比例 —— 这是规则优先架构的直接代价。
        # 注意：本模式把 gold 当成模型输出喂进去，所以「意图准确率」= 规则保留率，
        # 不构成模型的真实准确率，这里把 intent_accuracy 置空，避免被误读成 100% 水平。
        over = [r for r in results if r.get("overridden")]
        judged = [r for r in results if r.get("model_said")]
        rate = round(len(over) / len(judged), 3) if judged else None
        rep["metrics"]["override_rate"] = rate
        rep["metrics"]["gate_preservation_rate"] = (None if rate is None
                                                    else round(1 - rate, 3))
        rep["metrics"]["intent_accuracy"] = None
        rep["metrics"]["intent_scored"] = None
        rep["metrics"]["intent_note"] = ("gate 模式把 gold 当模型输出，意图准确率=规则保留率，"
                                         "不作为模型水平指标")
        rep["metrics"]["override_counts"] = {
            "judged": len(judged), "overridden": len(over)}
        rep["metrics"]["overridden_cases"] = [
            {"id": it["id"], "text": it["text"],
             "model_said": r.get("model_said"), "rule_made_it": r.get("intent")}
            for it, r in zip(items, results) if r.get("overridden")]
    out = os.path.join(DATA, f"shadow_eval_{mode}.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(rep, fh, ensure_ascii=False, indent=1)
    return rep


def compare(pair: Optional[List[str]] = None) -> Dict[str, Any]:
    """读两份已有结果 → RSI 前后对照（赛题「迭代前后对比数据」）。"""
    got = {}
    for mode in ("rules", "gate", "model"):
        p = os.path.join(DATA, f"shadow_eval_{mode}.json")
        if os.path.isfile(p):
            with open(p, encoding="utf-8") as fh:
                got[mode] = json.load(fh)
    if pair:
        a_mode, b_mode = pair
        if a_mode not in got or b_mode not in got:
            return {"ok": False, "msg": f"缺少 {pair} 的结果", "have": list(got)}
    else:
        order = [m for m in ("rules", "gate", "model") if m in got]
        if len(order) < 2:
            return {"ok": False, "msg": "至少需要两种模式的结果，先分别跑一次",
                    "have": list(got)}
        a_mode, b_mode = order[0], order[-1]

    keys = ("qtype_accuracy", "intent_accuracy", "question_misroute_rate",
            "slot_completeness")
    rows = []
    for k in keys:
        a = got[a_mode]["metrics"].get(k)
        b = got[b_mode]["metrics"].get(k)
        delta = (None if a is None or b is None else round(b - a, 3))
        rows.append({"metric": k, a_mode: a, b_mode: b, "delta": delta})

    # 分歧清单：同一条样本两种模式给出不同 intent/qtype
    rmap = {r["id"]: r for r in got[a_mode]["raw"]}
    mmap = {r["id"]: r for r in got[b_mode]["raw"]}
    diffs = []
    for cid in sorted(set(rmap) & set(mmap)):
        r, m = rmap[cid], mmap[cid]
        if r.get("qtype") != m.get("qtype") or r.get("intent") != m.get("intent"):
            diffs.append({"id": cid, "text": r.get("text", ""),
                          a_mode: {"qtype": r.get("qtype"), "intent": r.get("intent")},
                          b_mode: {"qtype": m.get("qtype"), "intent": m.get("intent")}})

    rep = {"ok": True, "ran_at": time.strftime("%Y-%m-%d %H:%M:%S"),
           "pair": [a_mode, b_mode], "rows": rows, "disagreements": diffs,
           f"{a_mode}_ran_at": got[a_mode].get("ran_at"),
           f"{b_mode}_ran_at": got[b_mode].get("ran_at")}
    with open(os.path.join(DATA, "shadow_eval_compare.json"), "w", encoding="utf-8") as fh:
        json.dump(rep, fh, ensure_ascii=False, indent=1)
    return rep


def _print_metrics(rep: Dict[str, Any]) -> None:
    m = rep.get("metrics") or {}
    print()
    print(f"── 模式 {rep['mode']}｜样本 {m.get('cases')}｜耗时 {rep.get('elapsed_s')}s")
    print(f"   追问类型准确率  {m.get('qtype_accuracy')}  "
          f"({m.get('qtype_scored')} 条有 gold)")
    print(f"   意图准确率      {m.get('intent_accuracy')}  "
          f"({m.get('intent_scored')} 条有 gold)")
    print(f"   追问误判率      {m.get('question_misroute_rate')}  "
          f"({m.get('question_misroute_scored')} 条应答疑)")
    print(f"   槽位完整率      {m.get('slot_completeness')}")
    print(f"   答案侧禁语命中  {m.get('forbidden_in_gold')}")
    if m.get("override_rate") is not None:
        c = m.get("override_counts") or {}
        print(f"   规则保留率      {m.get('gate_preservation_rate')}  "
              f"（模型判对时规则层不改动的比例）")
        print(f"   ★规则推翻模型率 {m.get('override_rate')}  "
              f"({c.get('overridden')}/{c.get('judged')} 条被改掉)")
    print(f"   分歧条目        {len(m.get('disagreements') or [])}")


def main() -> int:
    args = sys.argv[1:]
    if "--compare" in args or any(a.startswith("--compare=") for a in args):
        pair = None
        for a in args:
            if a.startswith("--compare="):
                pair = a.split("=", 1)[1].split(",")
        rep = compare(pair)
        if not rep.get("ok"):
            print(rep.get("msg"), rep.get("have"))
            return 2
        a_mode, b_mode = rep["pair"]
        print(f"{'指标':<24}{a_mode:>10}{b_mode:>10}{'delta':>10}")
        print("-" * 56)
        for r in rep["rows"]:
            print(f"{r['metric']:<24}{str(r[a_mode]):>10}{str(r[b_mode]):>10}"
                  f"{str(r['delta']):>10}")
        print(f"\n分歧 {len(rep['disagreements'])} 条：")
        for d in rep["disagreements"][:15]:
            print(f"  [{d['id']}] {d['text'][:22]:<24} "
                  f"{a_mode}={d[a_mode]['qtype'] or '—'}/{d[a_mode]['intent']} "
                  f"{b_mode}={d[b_mode]['qtype'] or '—'}/{d[b_mode]['intent']}")
        return 0

    modes = ["rules"]
    if "--gate" in args:
        modes.append("gate")
    if "--model" in args:
        modes.append("model")
    limit = None
    for a in args:
        if a.startswith("--limit="):
            limit = int(a.split("=", 1)[1])
    for mode in modes:
        print(f"\n=== 跑 {mode} 模式 ===")
        rep = run(mode, limit=limit)
        _print_metrics(rep)
    return 0


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    raise SystemExit(main())
