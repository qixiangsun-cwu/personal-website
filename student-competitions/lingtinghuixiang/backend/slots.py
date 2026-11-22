# -*- coding: utf-8 -*-
"""槽位体系 —— 三级分类树 + 五类追问（11/21 新增）

## 为什么要有这个模块

赛题评分维度 2 明确要打分「**槽位完整率**」，而项目此前只有 5 个「必说要素」
（自报身份/身份确认/驳回原因/整改动作/结束语），那是**AI 说了什么**的质检项，
不是**商家这句话被理解成了什么**的结构化槽位。两者不是一回事。

赛题《数据说明》第七节第 4 条也直接把槽位设计给了出来：

> 做槽位体系：一级菜单、一级分类、二级分类本身就是三层意图/槽位设计，
> 可直接映射到对话状态机的结构。

第 25 行给了第五个维度 —— 商家追问的五种类型，并说明它们在数据里全部出现：

> 商家追问的五种类型在数据中全部出现，可作为意图体系的设计依据：
> 操作路径类 / 门槛标准类 / 质疑合理性类 / 知识来源类 / 结果预期类。

所以本模块定义 7 个槽位：
    menu        一级菜单（品牌 / 入驻 / 行业资质 / 公司资质 / 商品资质）
    category1   一级分类（商标注册证 / 授权书-文审 / …）
    category2   二级分类（68 条具体驳回原因）
    qtype       追问类型（五类之一；商家陈述而非追问时为空）
    material    涉及的材料名（商标注册证 / 授权书 / 发票 / 流水 / 营业执照…）
    action      需要的整改动作（补齐 / 重新开具 / 删除类目 / 修改店名…）
    expect      商家对结果的预期（补上能不能过）

前三个是「这通电话属于哪个场景」（通话开始时即可从工单预填），
后四个是「商家这句话在说什么」（逐轮由模型填）。

## 设计原则

* **槽位值只来自枚举或原文片段**，不允许模型自由生成新分类 —— 否则工单里会出现
  同一件事的十种叫法，槽位完整率反而失去意义。
* 填槽由模型做（语义活），本模块只负责：枚举定义、预填、合并、校验、算完整率。
  `fallback_qtype()` 是**离线回归用**的确定性兜底，明确标注「不是主判定路径」。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------- 枚举
MENUS = ("品牌", "入驻", "行业资质", "公司资质", "商品资质")

# 赛题《数据说明》给出的五类追问（顺序即优先级：越靠前越具体）
QUESTION_TYPES = {
    "操作路径": "问在哪里改、怎么操作、入口在哪",
    "门槛标准": "问具体标准、数量金额、时效要求",
    "质疑合理性": "认为自己的材料没问题、质疑驳回结论或规则不合理",
    "知识来源": "问去哪查、怎么分辨、依据是什么",
    "结果预期": "问补齐之后能不能通过、什么时候有结果",
}

# 整改动作枚举（对齐组委会话术里的动词，避免同义异名）
ACTIONS = ("补齐", "重新开具", "重新提交", "删除类目", "修改店铺名称", "上传",
           "验真", "停用后重申请", "换文字商标", "加盖章", "无需整改")

MATERIALS = ("商标注册证", "授权书", "发票", "流水", "营业执照", "行业资质",
             "身份证复印件", "店铺名称", "类目", "公章", "品牌", "其他")

SLOT_KEYS = ("menu", "category1", "category2", "qtype", "material", "action", "expect")
# 「场景三槽」在通话开单时就能从工单预填；「语义四槽」必须逐轮理解
SCENARIO_SLOTS = ("menu", "category1", "category2")
SEMANTIC_SLOTS = ("qtype", "material", "action", "expect")

SLOT_LABELS = {
    "menu": "一级菜单", "category1": "一级分类", "category2": "二级分类",
    "qtype": "追问类型", "material": "涉及材料", "action": "整改动作",
    "expect": "结果预期",
}


def empty() -> Dict[str, Optional[str]]:
    """七个槽位全部置空（结构固定，缺值用 None 而不是缺 key，方便算完整率）。"""
    return {k: None for k in SLOT_KEYS}


def fill(slots: Dict[str, Any], key: str, value: Any) -> Dict[str, Any]:
    """写入一个槽位；空值/纯空白不覆盖已有值（逐轮累积时保护先用信息）。"""
    if key not in SLOT_KEYS:
        return slots
    if value is None:
        return slots
    v = str(value).strip()
    if not v or v in ("未提供", "无", "-", "—", "未知", "null", "None"):
        return slots
    slots[key] = v[:60]
    return slots


def merge(base: Dict[str, Any], extra: Dict[str, Any]) -> Dict[str, Any]:
    """合并两份槽位：已有值优先，只补空缺（后一轮不能推翻前一轮已确认的结论）。"""
    out = empty()
    out.update({k: v for k, v in (base or {}).items() if k in SLOT_KEYS})
    for k in SLOT_KEYS:
        if not out.get(k) and (extra or {}).get(k):
            out[k] = extra[k]
    return out


def from_scenario(scenario: Optional[dict]) -> Dict[str, Any]:
    """用本通电话绑定的场景预填「场景三槽」。"""
    s = scenario or {}
    sl = empty()
    fill(sl, "menu", s.get("level1_menu"))
    fill(sl, "category1", s.get("level1_category"))
    fill(sl, "category2", s.get("level2_category"))
    return sl


def completeness(slots: Optional[dict], required: Tuple[str, ...] = SLOT_KEYS) -> float:
    """槽位完整率 = 已填槽位数 / 应有槽位数（0~1，两位小数）。

    赛题口径的「槽位完整率」就是取这个值在整通电话/整个评测集上的平均。
    """
    req = [k for k in required if k in SLOT_KEYS]
    if not req:
        return 0.0
    got = sum(1 for k in req if (slots or {}).get(k))
    return round(got / len(req), 2)


def missing(slots: Optional[dict], required: Tuple[str, ...] = SLOT_KEYS) -> List[str]:
    return [k for k in required if k in SLOT_KEYS and not (slots or {}).get(k)]


def validate(slots: Optional[dict]) -> List[str]:
    """校验槽位取值是否合法（枚举外的新值一律报错，保证工单口径统一）。"""
    errs = []
    s = slots or {}
    if s.get("menu") and s["menu"] not in MENUS:
        errs.append(f"menu 不在枚举内: {s['menu']}")
    if s.get("qtype") and s["qtype"] not in QUESTION_TYPES:
        errs.append(f"qtype 不在五类追问内: {s['qtype']}")
    if s.get("action") and s["action"] not in ACTIONS:
        errs.append(f"action 不在枚举内: {s['action']}")
    if s.get("material") and s["material"] not in MATERIALS:
        errs.append(f"material 不在枚举内: {s['material']}")
    return errs


def build_tree(scenarios: List[dict]) -> Dict[str, Dict[str, List[str]]]:
    """从 scenarios 表构建三级分类树（一级菜单 → 一级分类 → [二级分类]）。

    用「列语义双用」的清洗口径：空值不建节点（组委会数据里分类只在组内首行出现，
    import_data 已向下填充；这里再兜一次，避免空字符串污染枚举）。
    """
    tree: Dict[str, Dict[str, List[str]]] = {}
    for s in scenarios or []:
        m = (s.get("level1_menu") or "").strip()
        c1 = (s.get("level1_category") or "").strip()
        c2 = (s.get("level2_category") or "").strip()
        if not m:
            continue
        tree.setdefault(m, {})
        if c1:
            tree[m].setdefault(c1, [])
            if c2 and c2 not in tree[m][c1]:
                tree[m][c1].append(c2)
    return tree


def tree_stats(tree: Dict[str, Dict[str, List[str]]]) -> Dict[str, Any]:
    """分类树规模统计（答辩时一句话说明槽位体系的覆盖度）。"""
    menus = len(tree)
    cats1 = sum(len(v) for v in tree.values())
    cats2 = sum(len(x) for v in tree.values() for x in v.values())
    return {"menus": menus, "categories1": cats1, "categories2": cats2,
            "menu_names": list(tree.keys())}


# ---------------------------------------------------------------- 离线兜底（非主判定）
# 明确声明：**这不是主判定路径**。线上由模型输出 qtype（见 DECIDE_PROMPT 的 slots 字段），
# 这里只是给离线回归一个确定性、可复现的兜底，避免离线时 qtype 全空导致完整率无意义。
#
# 判定顺序刻意让「来源类」先于「操作路径类」：
# 「我在哪可以查询到这个商标状态」里的「在哪」会撞上操作路径，但它问的是**去哪查**，
# 属于知识来源。所以知识来源先判，且操作路径不再匹配裸「在哪」。
_FALLBACK = (
    ("知识来源", ("在哪查", "哪里查", "怎么查", "查询到", "可以查询", "怎么分辨",
                  "怎么知道", "查商标", "哪个网站", "依据", "哪条规则")),
    ("操作路径", ("怎么改", "怎么更", "更改", "哪里修改", "在哪修改", "在哪改",
                  "怎么弄", "怎么操作", "如何操作", "上传的口", "入口", "后台哪儿")),
    ("门槛标准", ("多长时间", "几个月", "多久", "多少", "要求", "标准", "门槛",
                  "有效期", "金额", "数量")),
    ("质疑合理性", ("明明", "怎么会", "凭什么", "不合理", "我这就是真的", "已经",
                    "不行吗", "为什么要", "不是挺")),
    ("结果预期", ("能过", "能通过", "可以通过", "什么时候", "结果", "能下来")),
)
# 判「是不是在追问」的疑问线索（没有这些词就按陈述处理，避免把「京麦驳回」当提问）
_Q_WORDS = ("?", "？", "吗", "呢", "怎么", "哪", "什么", "多少", "几", "为什么", "如何")


def fallback_qtype(text: str) -> str:
    """离线兜底：按线索词猜测追问类型（唯一用途是让离线回归有确定性输入）。"""
    s = (text or "").strip()
    if not s or len(s) > 60:
        return ""
    if not any(w in s for w in _Q_WORDS):
        return ""
    for name, words in _FALLBACK:
        if any(w in s for w in words):
            return name
    return ""


def decide_prompt_block() -> str:
    """给模型看的槽位填写要求（拼进结构化决策提示词）。

    刻意把枚举值全列出来：模型只能在枚举里选，不自由生成分类名 ——
    这样工单里不会出现同一件事的十种叫法，槽位完整率才有可比性。
    """
    q = "\n".join(f"    · {k}：{v}" for k, v in QUESTION_TYPES.items())
    return (
        "【槽位填写要求】只能从下列枚举里取值，不得自由造词；无法判断就留空字符串。\n"
        f"  · menu 一级菜单：{' / '.join(MENUS)}\n"
        f"  · material 涉及材料：{' / '.join(MATERIALS)}\n"
        f"  · action 整改动作：{' / '.join(ACTIONS)}\n"
        f"  · qtype 追问类型（商家在追问时才填，纯陈述留空）：\n{q}"
    )


def summary_line(slots: Optional[dict]) -> str:
    """一行展示，给工单/看板用。"""
    s = slots or {}
    parts = [f"{SLOT_LABELS[k]}={s[k]}" for k in SLOT_KEYS if s.get(k)]
    done = completeness(s)
    return f"槽位 {done:.0%}｜" + ("、".join(parts) if parts else "（未填）")
