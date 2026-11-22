# -*- coding: utf-8 -*-
"""数字员工 Agent：四段式外呼状态机（升级自手动版 hello_ai.py）。

升级点：
1. RAG 检索：每轮商家提问 → db.search_knowledge → 注入系统提示词（话术/QA/剧本）；
2. 场景槽位来自数据库：商家 + 场景（关单方式/话术模板），不写死；
3. 情绪判定（规则 + 关键词）与合规校验（必答要素清单）逐轮输出，供看板/工单展示；
4. 流式生成 + 按句回调（on_sentence），供 TTS 边合成边播放，压低首响延迟；
5. 打断处理：asr_metadata.interrupted → E1 状态先回应再恢复主流程。
6. 工具调用（Function Calling，11/12）：商家问到自身工单/材料/政策时，模型自己决定
   调用只读工具查证，查到的真实数据回注进提示词再生成 —— 见 _tool_round()。

四段式：①自报身份 → ②身份确认（不可跳过） → ③驳回原因+整改动作（胜负手） → ④礼貌结束。
"""

import os
import re
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from dotenv import load_dotenv
from zhipuai import ZhipuAI

import db
import decision        # 结构化决策（classify/tool/generate 三次调用合一，DECISION_MODE 门控）
import invariants      # 状态不变式：规则层降级为「否决器」
import prompts          # 提示词层（RSI 提示词通道）：所有长文本提示词的唯一真相源
import unknown_terms    # 未知词台账：「猜+确认+自动沉淀」，代替人工加同音词
import redline
import risk
import tools_registry

try:
    import reply_policy as reply_policy   # 功能补丁包（11/15）：判定函数集，缺失时降级
except Exception:                          # pragma: no cover - 仅在残缺环境触发
    reply_policy = None

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

AGENT_NAME = "京东商家资质审核专员"


def _slot_completeness(slots_map) -> float:
    """槽位完整率（赛题评分维度 2 的取数口径）：已填槽位 / 应有槽位。

    放在模块级而不是方法里，是为了让 summary() 在 slots 模块异常时也能安全返回 0.0，
    永远不让"统计口径"把一通正常电话搞挂。
    """
    try:
        import slots as _sm
        return _sm.completeness(slots_map)
    except Exception:
        return 0.0
HOTLINE = "4006180092"

MODEL = os.getenv("ZHIPU_MODEL", "glm-4-flash")

# 状态
S0, S1, S2, S3, S4, S5 = "S0-INTRODUCE", "S1-VERIFY_PERSON", "S2-REJECTION", "S3-QA", "S4-CONFIRM", "S5-CLOSE"
E1, E2, E3, E4 = "E1-INTERRUPT", "E2-UNCLEAR", "E3-EMOTIONAL", "E4-OUT_OF_SCOPE"
E5 = "E5-VIOLATION"          # 红线拦截（11/12 新增：违规诉求 / 越权 / 辱骂 / 闲聊）
END = "END"

STAGE_NAMES = {"S0": "自报身份", "S1": "商家身份确认", "S2": "情况说明", "S3": "商家答疑", "S4": "整改意愿确认", "S5": "礼貌结束"}
# 必答要素（合规质检清单，赛题评分项）
REQUIRED_ELEMENTS = ["自报身份", "身份确认", "驳回原因", "整改动作", "结束语"]

# 11/12 轮次闸门（用户硬要求：经得起多轮反问/提问，不主动挂断、不乱判闭环）：
# * 下限 MIN_TURNS_TO_CLOSE=3 保留（赛题「不少于 3 轮」）；
# * 上限 MAX_TURNS_HARD=30 仅作纯防御（防自动化评测死循环），正常反问/答疑远到不了；
#   命中上限也只是「优雅收线 + 标记非任务结束」，绝不计入成功闭环。
# * MAX_STALL_ROUNDS=999 等于关闭「原地打转强制推进」：商家反复反问时，状态机应持续
#   在 S2/S3 答疑段解答，而不是强制跳到结束（这是「一直反问就强制收线」的根因，已修）。
MIN_TURNS_TO_CLOSE = 3
MAX_TURNS_HARD = 30
MAX_STALL_ROUNDS = 999        # 关闭强制推进：商家多轮反问也不主动跳结束
ADVANCE_STATE = {S1: S2, S2: S4, S3: S4, S4: END}

# 输出禁语（合规红线）：命中即弃用该句，改播与**当前阶段**匹配的安全句
FORBIDDEN_PHRASES = (
    "保证通过", "绝对能过", "一定能过", "包过", "肯定过", "百分百通过",
    "封店", "关店", "罚款", "处罚", "身份证号", "银行卡号", "验证码", "密码",
)
# 必说要素的结构化识别（只认 agent 真实播报文本，且必须到对应阶段才计覆盖）
# 11/12 收紧：旧版「驳回原因」含单字 `因`、「结束语」含 `感谢`，
# 于是「感谢您的确认」被误判成说了结束语 —— 这是「刚确认身份就 5/5 全亮」的直接原因。
ELEMENT_PATTERNS = {
    "自报身份": r"资质审核专员|京东.{0,6}专员",
    "身份确认": (r"请问您是.{0,20}负责人|是.{0,12}负责人吗|负责人本人|"
                 r"是不是.{0,12}负责人|哪位负责人"),
    "驳回原因": (r"驳回原因|驳回意见|审核未通过|未通过审核|"
                 r"不符合.{0,10}(要求|标准|规定|规范)|缺少.{0,8}(材料|资料|资质|证件)|"
                 r"材料.{0,8}(缺失|不全|不完整|过期|失效)"),
    "整改动作": (r"重新提交|重新上传|补齐|补交|补充材料|"
                 r"按.{0,10}(意见|要求).{0,6}(修改|补|提交)|登录京麦|开具|"
                 r"提供.{0,12}(材料|资料|证明|证件|注册证|授权书|报告)|"
                 r"材料.{0,12}提交|提交.{0,4}(即可|就行)|"
                 r"上传.{0,8}(材料|证件|照片)"),
    "结束语": r"感谢您的接听|祝您.{0,12}(顺利|愉快|生意兴隆)|再见",
}

# 要素归属阶段：说了还不够，得流程走到这一步才算覆盖（阶段 1~4）
ELEMENT_STAGE = {"自报身份": 1, "身份确认": 1, "驳回原因": 2, "整改动作": 2, "结束语": 4}

# 流程阶段（双轨进度条左轨）：prompt_state → 阶段序号（1-4 与 FLOW_STEPS / EL_STAGE 严格对应）
# 1=身份确认(S1) / 2=情况说明(S2：驳回原因+整改动作) / 3=商家答疑(S3) / 4=礼貌结束(S4/S5/END)
# 11/12 修正：原 S1→2 把「身份确认」错映射成「情况说明」，是运营端流程指错环节的根因；
# 原 S2→3、S3→3 之间漏掉了阶段 2，导致 S2 阶段就被算成「情况说明已完成」。现已对齐。
STAGE_OF_STATE = {
    "PRE": 1, S0: 1, S1: 1, S2: 2, S3: 3, S4: 4, S5: 4, END: 4,
    E3: 3, E4: 3, E5: 2,
}
STAGE_NAMES_4 = {1: "身份确认", 2: "情况说明", 3: "商家答疑", 4: "礼貌结束"}

# ============ 统一五环节整改进度模型（12/12 重构 · 全端唯一真相源）============
# 客户接听端 / 实时转写右侧进度看板 / 工单看板 / 历史记录 全部统一为以下 5 环节：
#   1.自我介绍  2.商家身份确认  3.情况说明(驳回原因+整改方法)
#   4.商家答疑(主动问有无疑问，无疑问绿灯 / 有疑问解答后确认)  5.整改意愿确认
# 任何面板都只消费本模型（由 agent.flow_progress() 计算），不再各算各的。
STAGES_5 = [
    {"id": 1, "key": "intro",    "name": "自我介绍"},
    {"id": 2, "key": "identity", "name": "商家身份确认"},
    {"id": 3, "key": "explain",  "name": "情况说明"},
    {"id": 4, "key": "qa",       "name": "商家答疑"},
    {"id": 5, "key": "willing",  "name": "整改意愿确认"},
]
# 内部状态 → 当前所处环节（用于「当前环节」高亮；与 STAGES_5 严格对齐）
STATE_STAGE5 = {
    "PRE": 1, S0: 1, S1: 2, S2: 3, S3: 4, S4: 5, S5: 5, END: 5,
    E1: 2, E2: 3, E3: 5, E4: 5, E5: 3,
}
# 环节归属的「内部要素阶段」用于元素门控（情况说明=驳回原因+整改动作，属内部阶段 2）
STAGE5_OF_ELEMENT = {1: 1, 2: 1, 3: 2, 4: 3, 5: 4}

# 输出侧兜底话术：按阶段给，避免「打包票 + 请问是负责人吗」这种跨阶段复读
SAFE_SENTENCE = {
    1: "这部分我先按系统里的审核结果和您说明。",
    2: "能不能通过要看材料是否齐全合规，这个我不能给您打包票。",
    3: "能不能通过要看材料是否齐全合规，这个我不能给您打包票。",
    4: "这个我确实不能承诺，一切以审核结果为准。",
}

# 禁语误判护栏：客服说「请您不要提供身份证号」「我不能保证通过」是**合规提醒**，
# 不是违规。命中词前 8 字内出现否定/拒绝语境时不计违规（11/12）。
_NEG_GUARD = ("不要", "不能", "不会", "不得", "无需", "不用", "请勿", "切勿", "勿",
              "别", "拒绝", "不索要", "不向")


def _phrase_negated(text: str, phrase: str) -> bool:
    """命中词所在的**小句**内是否出现否定/拒绝语境。

    按小句判断而不是固定窗口：「请您不要提供身份证号、银行卡号或验证码」
    里"不要"只管到第一个词，固定 8 字窗口会漏掉后面两个，造成误判。
    """
    i = text.find(phrase)
    if i < 0:
        return False
    start = 0
    for ch in "，。；！？,;!?":
        j = text.rfind(ch, 0, i)
        if j + 1 > start:
            start = j + 1
    win = text[start:i]
    return any(g in win for g in _NEG_GUARD)


# ---------- 改约时间识别（13/13 修「商家说后天 AI 听不懂」） ----------
# 商家在【整改意愿确认】段说「最近没空」时，AI 必须先问出时间；商家答「后天 / 下周一 /
# 11月20日」这类**可落地日期**才算改约完成。旧版把「没空」直接当完成 → 问完时间就挂断，
# 商家补充的「后天」落到终态被误判成 HESITANT，反复问"您看是哪一部分还有疑虑"。
_TIME_DAYS = (
    "大后天", "后天", "明天", "明日", "今天", "今日", "今晚", "明早", "明晚", "明后天",
    "下下周", "下周", "这周", "本周", "下个礼拜", "下个月", "月底", "月初", "周末", "下周末",
)
_TIME_PERIODS = ("上午", "中午", "下午", "晚上", "早上", "工作日")
_TIME_RE = re.compile(
    # 具体日期：11月20日 / 11月20号 / 20号 / 20日（"号"必须带数字，避免命中"型号"等）
    r"(?:\d{4}年)?\d{1,2}月\d{1,2}[日号]"
    r"|(?<!\d)\d{1,2}[日号]"
    # 11/15 真机修复⑦：中文数字日期（十八号吧 / 二十号 / 三十一号）。
    # 旧版只认阿拉伯数字，「十八号吧」漏判 → 落到 WILL_CLARIFY/HESITANT，
    # AI 反而复读「打算自己去提交，还是有什么不方便」。
    r"|(?:[一二三四五六七八九十]{1,3}|廿|卅)[日号]"
    # 周几：下周一 / 本周五 / 礼拜三（"周一"不带修饰也算）
    r"|(?:下下|下|这|本)?(?:周|週|礼拜)[一二三四五六日天]"
    r"|" + "|".join(_TIME_DAYS) +
    r"|" + "|".join(_TIME_PERIODS))


def _parse_time_expr(text: str):
    """从商家话语里抽出「改约时间」→ (短语, 是否含日期级信息)。

    has_date=False 表示只说了时段（"下午吧""晚上"），还缺哪一天，必须继续追问，
    否则 AI 会拿着一个"下午"就去收尾，商家一脸懵。
    """
    if not text:
        return None, False
    m = _TIME_RE.search(text)
    if not m:
        return None, False
    phrase = m.group(0)
    has_date = not (phrase in _TIME_PERIODS)
    # 「后天上午」这类日期+时段连写 → 一起复述，听起来才像真听懂了
    tail = text[m.end():m.end() + 2]
    for p in _TIME_PERIODS:
        if tail.startswith(p):
            phrase += p
            break
    return phrase, has_date


# ---------- 「明确说没疑问」强证据（13/13 修「说了没疑虑还被追问哪一部分有疑虑」）----------
# 旧版是紧邻词匹配（"没疑问" / "没疑虑"），商家的自然说法中间会夹"其他/其它的"，
# 比如「没有其他疑问」「我没有其他的疑虑了」——匹配不上，于是被判成 hesitant，
# AI 反过来追问"您看是哪一部分还有疑虑？"，商家越答越像复读。
# 用否定前缀 + 疑问类名词的结构匹配，并把"没有什么问题"这类否定短语从疑问检测里剔掉。
_NO_DOUBT_RE = re.compile(
    r"(?:没有|没|无|不再有?)(?:其他|其它|别的|别|任何|啥|什么)?(?:的)?"
    r"(?:疑问|问题|疑虑|疑惑|异议|要问的)")


def _no_doubt(text: str) -> bool:
    return bool(_NO_DOUBT_RE.search(text or ""))


# ---------- 结束语之后的「告别语」（13/13 修 → 11/15 加固「拜拜拜拜」）----------
# AI 已播完结束语、商家回一句「再见再见」时，服务端原来会无条件把通话
# 「恢复为可继续答疑」（state=S3）+ 让模型重新作答 —— 于是模型把两个字的告别
# 判成含糊，反问"您看是哪一部分还有疑虑？"。告别就是告别，直接收线。
#
# 11/15 真机：商家回「拜拜拜拜」，AI 仍问「您看是哪一部分还有疑虑？」。
# 查明服务端短路只在 `agent.finished=True` 时生效；结束语后商家只要再开口一次
# （恢复答疑 / 非闭环收线 / 状态被重置），finished 就变回 False，告别语直接掉进
# S3 含糊分支。因此把告别提升为**与阶段无关的 Agent 意图**（见 preview_reply 的
# BYE 分支），服务端短路只作为「零回复」优化，不再承担唯一识别职责。
#
# 词库按「去掉告别词后是否只剩语气词」判定，天然支持任意叠词
# （拜拜拜拜 / 再见再见 / byebye）与 ASR 同音异字（白白 / 摆摆 / 掰掰）。
_FAREWELL_UNIT_RE = re.compile(
    r"再见|再見|再會|拜拜|拜拜啦|拜拜喽|拜拜咯|白白|白白了|摆摆|掰掰|百百|"
    r"bye|byebye|回见|回头见|后会有期|"
    r"挂了电话|挂了哈|我挂了|先挂|挂了|不聊了|不说了|不打扰了|您忙|保重|先走了",
    re.I)
# 弱告别：只在「结束语已播完」的收尾窗口里算接受收尾；业务中途说这些
# 往往是「没疑问了」（NO_MORE），绝不能在这里误收线。
_FAREWELL_SOFT_RE = re.compile(r"就这样吧|那就这样|先这样|没别的事了|没有别的事了", re.I)
# 语气词 / 标点（问号刻意排除，见 _FAREWELL_BUSY_RE）
_FAREWELL_NOISE_RE = re.compile(
    r"[\s，。！!、,.~…～;；:：'\"“”‘’嗯啊哦噢喔诶额哎唉哈嘿嘞呀呐咯吧嘛的了呢好行对呗喽啦"
    r"那就也都先再又]+")
# 含这些词说明还有新内容 / 疑问 / 转折，一律不算「纯告别」
_FAREWELL_BUSY_RE = re.compile(
    r"[?？]|疑问|问题|怎么|为啥|为什么|哪一部分|哪个|哪里|但是|不过|"
    r"稍等|等一下|等下|还有|再问|想问|请问|问一句|"
    r"材料|驳回|提交|后台|费用|资质|审核|整改|密码|验证码|账号|客服|人工|投诉|"
    r"不是|没听清|听不清|等等|麻烦|帮我|补充|另外|其实|可是|"
    r"别挂|别走|先别|不方便|没空|改天|明天|下周|周[一二三四五六日末]|"
    r"登录|证明|核实|电话号码|换个电话|留个电话|来电|工单|申诉", re.I)


def _farewell_core(text: str, soft: bool = False) -> bool:
    raw = (text or "").strip()
    if not raw or len(raw) > 24:
        return False
    low = raw.lower()
    if _FAREWELL_BUSY_RE.search(low):
        return False
    if not _FAREWELL_UNIT_RE.search(low) and not (soft and _FAREWELL_SOFT_RE.search(low)):
        return False
    rest = _FAREWELL_UNIT_RE.sub("", low)
    if soft:
        rest = _FAREWELL_SOFT_RE.sub("", rest)
    return _FAREWELL_NOISE_RE.sub("", rest) == ""


def is_farewell(text: str) -> bool:
    """商家明确道别（再见 / 拜拜 / 挂了，含叠词与 ASR 同音），不含任何新问题。

    与通话阶段无关：一旦命中，Agent 直接安静收线，不再追问、不补业务要素。
    """
    return _farewell_core(text, soft=False)


def is_farewell_after_closing(text: str) -> bool:
    """结束语已播完的收尾窗口：纯告别 + 「那就这样吧」等接受收尾的短句。"""
    return _farewell_core(text, soft=True)


# 输出占位符清洗（问题4）：模型有时会把知识库里的「（商家公司名/店铺名称）」或
# 自己臆造的「【具体缺失的授权关系】」当作待填模板直接读出来。这些占位符一旦播报，
# 既暴露系统内部模板、又让商家听不懂。这里在「播出/落库/推送」前把残留占位符抹掉，
# 缺具体信息时统一替换为「以京麦商家后台的驳回意见为准」这种合规口径。
_PLACEHOLDER_PATTERNS = [
    r"【[^】]*?(具体|缺失|详情|待填|待补充|未提供|未知|占位|某某|某类|某品牌|XXX|公司名|店铺名称|授权关系|授权链)[^】]*?】",
    r"（[^）]*?(具体|缺失|详情|待填|待补充|未提供|未知|占位|某某|某类|XXX|公司名|店铺名称|授权关系|授权链)[^）]*?）",
    r"（商家公司名[^）]*?）",
    r"（店铺名称[^）]*?）",
    r"【[^】]{0,12}】",   # 兜底：任何残留的单层中文方括号（场景标签式的合法用法不在模型输出里）
]
_FALLBACK_PHRASE = "以京麦商家后台的驳回意见为准"


def sanitize_reply(text: str) -> str:
    """清洗数字员工即将播报的文本，移除未填充的占位符。

    返回清洗后的文本（可能为空白，调用方应跳过空白片段）。
    """
    if not text:
        return text
    out = text
    for pat in _PLACEHOLDER_PATTERNS:
        out = re.sub(pat, _FALLBACK_PHRASE, out)
    # 连续两个兜底并成一个，避免「以京麦…为准以京麦…为准」
    out = re.sub(re.escape(_FALLBACK_PHRASE) + re.escape(_FALLBACK_PHRASE), _FALLBACK_PHRASE, out)
    # 去掉因替换产生的多余标点 / 空格
    out = re.sub(r"\s*，\s*，\s*", "，", out)
    return out.strip()


# 固定安全话术：开场 / 异常 / 模型故障时都不走生成，保证必说话术 100% 覆盖
FIXED_OPENING = ("您好，我是京东商家资质审核专员，本次来电是关于贵店资质审核结果，"
                 "请问您是{company}的负责人吗？")
# 11/19：商家主动来电（「给京东打电话」/回拨）开场——热线服务口径，与外呼完全不同：
# 不说"本次来电是关于贵店资质审核结果"（是商家打来的！）、不反问"您是不是负责人"
# （商家打热线当然是咨询自己的事），身份确认环节在来电场景整体取消。
FIXED_INBOUND_OPENING = ("您好，这里是京东商家服务热线，请问有什么可以帮您？"
                         "如果是关于资质审核的问题，您可以直接说。")
# 11/19：来电模式下商家未开口的空文本轮（重复 start / 静默触发）固定重问候，
# 绝不让大模型自由发挥——线上实测它在这一步主动问「您是负责人吗」。
FIXED_INBOUND_REGREET = ("您好，请讲。您想咨询资质审核的哪方面问题？可以直接说。")
FIXED_CLOSING = "好的，整改要求我已说明清楚。如需咨询可拨打京东商家服务热线{hotline}。感谢您的接听，祝您工作顺利，再见。"
# 11/19：来电场景的收线话术（热线咨询完成，不存在"整改要求已说明"）
FIXED_INBOUND_CLOSING = ("好的，感谢您的来电。后续如还有问题，欢迎再次拨打京东商家服务热线"
                         "{hotline}，或在京麦后台查看驳回意见。祝您工作顺利，再见。")
FIXED_REASON = "您这次的驳回原因是{scenario}。"
FIXED_ACTION = "需要您登录京麦后台，按驳回意见把材料补齐后重新提交。"
FIXED_SERVICE_ERROR = (
    "我是京东商家资质审核专员。您这次的驳回原因是{scenario}；"
    "需要您登录京麦后台，按驳回意见把材料补齐后重新提交。"
    "如需咨询可拨打京东商家服务热线{hotline}。感谢您的接听，再见。")
FIXED_EMOTIONAL = (
    "我是京东商家资质审核专员。理解您的心情，您这次的驳回原因是{scenario}，"
    "需要您登录京麦后台按驳回意见补齐材料后重新提交；相关情况也可拨打商家服务热线{hotline}反馈。"
    "感谢您的接听，再见。")
FIXED_OUT_OF_SCOPE = (
    "我是京东商家资质审核专员。抱歉，这个问题超出资质审核范围；"
    "关于贵店资质驳回，需要您按京麦后台驳回意见补齐材料后重新提交，"
    "也可拨打商家服务热线{hotline}咨询。感谢您的接听，再见。")
FIXED_UNCLEAR = "不好意思，刚才没听清，您能再说一遍吗？"
# 12/12 统一五环节：商家答疑段主动询问、整改意愿确认段主动确认
FIXED_QA = "刚才跟您说的驳回原因和整改方法，您这边还有没有疑问？"
# 11/19：来电场景的答疑兜底（offline / 模型故障时不播外呼口径的"驳回原因已说明"）
FIXED_QA_INBOUND = ("关于您咨询的问题，具体可以登录京麦后台查看驳回意见，"
                    "或拨打京东商家服务热线{hotline}核实。您还有其他想咨询的吗？")
# 13/13 整改意愿确认：用户指定的追问口径（只在商家肯定答复后才点亮该环节）
FIXED_WILLING = "那您后续方便尽快去京麦后台重新提交吗？"
# 13/13 修「改约就挂断」：商家说没空时**只是进入改约流程**，必须先把时间问出来。
# 这一轮不点灯（环节⑤仍未完成），说完等商家给时间，绝不附带结束语。
FIXED_RESCHEDULE_ASK = ("理解您最近比较忙。那您看哪天方便？我把提交时限给您往后顺延，"
                        "需要补齐的材料您先收着，随时登录京麦后台提交就行。"
                        "您给个大概时间，我这边给您备注上。")
# 第二次追问换个说法，避免逐字复读（商家最容易在这里觉得"机器人"）
FIXED_RESCHEDULE_ASK2 = ("没关系，那您大概哪天能腾出时间？说个日期就行，"
                         "我这边给您备注，届时不会重复打扰您。")
# 商家只给了时段没给日期（"下午吧"）→ 继续问哪天，不推进
FIXED_RESCHEDULE_WHICH_DAY = "好的，那您是指哪一天？您说个具体日期，我这边给您备注上。"
# 商家给出可落地日期 → 复述确认 + 收尾，结束语里带上约定时间
FIXED_RESCHEDULE_OK = ("好的，那就按您说的{time}给您备注上，届时您登录京麦后台，"
                       "按驳回意见把材料补齐后重新提交即可。"
                       "如需咨询可拨打京东商家服务热线{hotline}。感谢您的接听，祝您工作顺利，再见。")
# 问了两次仍不给时间（"就是没空，别问了"）→ 不僵持、不强留，礼貌收线并记成非任务结束
FIXED_RESCHEDULE_LATER = ("好的，那我就不占用您时间了。您方便的时候随时登录京麦后台，"
                          "按驳回意见把材料补齐后重新提交即可，"
                          "有问题也可以拨打京东商家服务热线{hotline}。感谢您的接听，再见。")
# 身份确认后的「转场轮」：**只做确认承接，绝不把驳回原因/整改动作塞进这一轮**。
# 单步推进铁律：【身份确认】与【情况说明】不允许在同一回合点亮，
# 所以这一轮只说「我要说明了」，下一轮才真正念驳回原因 + 整改动作。
FIXED_CONFIRM = ("好的，感谢您的确认。下面我为您说明本次资质审核的驳回原因和整改要求。")

# 【11/21 身份重核·点亮修复】反查之后商家「口头认下继续」的应答合集。
# 旧正则只认单个应答词（好的/对/是…），实测「好的好的」匹配不上 → 退出重核窗口、
# 身份灯一直灭着挂在那里。口语里连答应答（好的好的/好好好/嗯嗯/行行行/可以可以）
# 全是「认下+继续」的意思 → 允许应答词重复 1~8 次、中间可夹语气词/标点。
# 注意单元词里没有「不」，「不行/不是/不好」不会被误配。
_RECONFIRM_ACK_RE = re.compile(
    r"^(?:(?:好的?|是的?|对|嗯|行|可以|没错|成|中|好嘞|好滴|好呀|好呢|好哈|好哦)"
    r"[的吧呀啊呢哈哦嘛哟咧，,。！!？?、\s]{0,4}){1,8}$")
# 情况说明轮：一轮内连贯播完「驳回原因 + 整改动作」，播完立刻点亮【情况说明】，
# 同时把「您还有没有疑问」问出来（问的这一刻绝不给【商家答疑】打勾）。
FIXED_EXPLAIN_QA = ("您这次的驳回原因是{scenario}。"
                    "需要您登录京麦后台，按驳回意见把材料补齐后重新提交。"
                    "以上是本次的驳回原因和整改方法，您这边还有没有疑问？")
# 商家在情况说明还没播报时就抢先说「没问题了」：本轮先把情况说明完整补交付，
# 【商家答疑】下一轮才允许点亮（12/13 保留的时序兜底）
FIXED_REDELIVER = (
    "那我先把这次的驳回原因和整改要求跟您说清楚：您这次的驳回原因是{scenario}，"
    "需要您登录京麦后台，按驳回意见把材料补齐后重新提交。这样您这边还有没有疑问？")
# 商家含糊其辞（hesitant）时，主动追问是哪一部分有疑虑（不推进、不打勾）
FIXED_HESITANT = "您看是哪一部分还有疑虑？我可以再给您具体说明一下。"
# 【11/21 离线防复读】连续含糊时旧版交给 LLM 换说法，但离线/无模型兜底
# 没有换说法能力 → 两次逐字复读（1115d⑥ 既有失败）。给离线路径准备备用话术轮换。
FIXED_HESITANT2 = "您具体是哪一块不太清楚？我针对性再给您讲一遍。"
# 11/19：来电场景的含糊应答（热线咨询语境，没有"哪一部分"可指）
FIXED_INBOUND_HESITANT = "好的。那您还有其他想咨询的问题吗？可以直接说。"
# 同一个「含糊」在【整改意愿确认】段必须换个问法：这时已经问过"有没有疑问"，
# 再问"哪一部分还有疑虑"就是答非所问（实测商家说"最近不方便"，AI 仍在问疑虑）。
FIXED_WILL_HESITANT = ("那您这边是打算自己去京麦后台提交，还是有什么不方便的地方？"
                       "您跟我说一下，我帮您看怎么处理更合适。")
# 【11/21 离线防复读】同上：整改意愿段连续含糊的备用换法
FIXED_WILL_HESITANT2 = ("您是暂时不方便操作，还是对要提交什么材料有疑问？"
                        "您说一下情况，我帮您想办法。")
# 连续 3 次含糊 / 未明确确认 → 强制转人工或结束，绝不无限循环卡死
FIXED_LOOP_END = (
    "好的，那这边先不打扰您了。您后续可以登录京麦商家后台查看驳回意见并整改，"
    "如需人工协助可拨打京东商家服务热线{hotline}。感谢您的接听，再见。")
# 身份确认段商家没给出明确答复时的二次确认（绝不因为「喂 / 嗯」就点亮身份确认）
FIXED_VERIFY_AGAIN = "不好意思，跟您确认一下，请问您是{company}的负责人吗？"
# 11/19 人性化补丁：同一句确认连问就换说法，绝不逐字复读（商家最容易因此觉得"人机"）
FIXED_VERIFY_AGAIN2 = ("是这样，为防止审核信息泄露，我需要跟店铺负责人本人核对。"
                       "请问您是{company}的负责人本人吗？")
FIXED_VERIFY_AGAIN3 = ("我这边的登记信息是打给{company}负责人的，需要本人确认后才能继续。"
                       "您这边是负责人本人吗？")
# S1 商家闲聊/反问/没听清（「等等你刚说你是京东的？」「你从哪感知出来的热情」）：
# 不搞固定寒暄（感谢热情这种话说第二次就成了笑话），一律澄清式自报家门，
# 把"我是谁、为什么打来"再讲一遍，然后回到身份确认（11/19）
FIXED_VERIFY_WARM = ("不好意思，刚才可能没说清楚：我是京东商家资质审核专员，"
                     "本次来电是关于贵店资质审核结果的官方回访，"
                     "您也可以拨打商家服务热线{hotline}核实。"
                     "请问您是{company}的负责人吗？")
# ① 功能补丁（11/15）：不信任第 2 次 → 最后警告（第 3 次直接转人工收线）
TRUST_LAST_TEXT = ("再跟您说明一次：我是京东商家资质审核专员，本次的信息您都可以在京麦"
                   "后台核对，也可以拨打商家服务热线核实，我们不会向您索要任何账号、"
                   "密码或验证码。如果您还是不放心，我们这次就先到这里，由人工专员"
                   "再与您联系。那您现在愿意相信我吗？")

_SYS_TEMPLATE_FROZEN_V1 = """你是京东商家资质审核专员（数字员工），正在电话外呼告知商家资质驳回原因与整改方式。
语气专业、礼貌、平和，像真实电话客服一样**短句**交流，每次回复一到两句、不超过50个汉字，适合语音播放。

【通话信息】
商家公司：{company}
店铺名称：{shop}
联系人：{contact}
驳回场景（二级分类）：{scenario}
关单方式：{close_method}
标准参考话术（可在此基础上口语化改写，必须覆盖其中信息）：{script}

【合规红线（必须遵守，违者质检扣分）】
1. 开场必须自报身份"我是京东商家资质审核专员"；
2. 必须先确认对方身份（"请问您是{company}的负责人吗"），确认前不得透露驳回详情；
3. 不得承诺"保证通过""绝对能过""一定能过""肯定过""百分百通过"；被问"能不能过"时，
   只回答"具体以审核完整材料的结果为准"；不得恐吓、威胁封店罚款；
4. 不得索要身份证号、银行卡号、密码、验证码；
5. 回复中不得出现任何括号占位符（如【】或（）里待填的内容，例如"【具体缺失的授权关系】"
   "（商家公司名）"）。如果不知道商家具体缺哪一项材料或哪一项授权关系，只能如实说
   "具体缺失项请以京麦商家后台的驳回意见为准"，绝不可编造具体名称或直接把占位符读出来；
5. 商家提出走后门、改后台、P图造假、加微信、给红包时，一律拒绝并划清边界，不得配合；
6. 如商家要求人工或情绪激烈，礼貌引导并结束通话；
7. 如有疑问可让商家致电京东商家服务热线 {hotline}；
8. 商家谈及与资质审核无关的话题（闲聊、生活、护肤美容、健康养生、娱乐八卦、天气、
   好物分享、教你一招等）时，**一律不接话、不评价、不感兴趣、绝不追问细节或原理**，
   只用一句话把话题拉回资质审核（例如"这个我这边不方便聊，咱们回到本次审核驳回这件事上"）；
   商家反过来向你分享经验/偏方/技巧，同样不展开、不致谢、不说"有帮助"。

【对话智能与情绪（务必做到）】
1. **问什么答什么（仅限资质审核相关）**：商家问业务上的哪一点就答哪一点，一次只答一个问题，不要自顾自往下背书、也不要答非所问；与资质审核无关的话题按上面红线第 8 条处理，绝不回答其内容。
2. **情绪识别与安抚**：识别到商家发火、激动、不耐烦时，先共情安抚（如"理解您的心情，这事确实让您费心了"），再清晰说明处理口径，不硬顶、不重复同一句。
3. **不懂如实说**：遇到自己不清楚、知识库没有、或不知道商家具体缺哪一项材料的问题，如实告知"这个我暂时不确定/具体以京麦商家后台驳回意见为准"，绝不编造、绝不复读占位符。
4. **避免死循环**：上一轮说的话这一轮不要原样重复；商家没听懂就换一种说法解释，而不是把同一句话再说一遍。
5. **不得脱离当前流程环节**：本环节只做本环节的事，不要提前说结束语、不要倒回身份确认。

【业务知识库（RAG 检索结果，回答追问只依据这里，不够就说"具体以京麦后台驳回意见为准"）】
{rag_context}

【五环节整改进度（全端统一）】
1.自我介绍 → 2.商家身份确认 → 3.情况说明(驳回原因+整改方法) → 4.商家答疑(主动问有无疑问) → 5.整改意愿确认。
当前环节：{stage_hint}
当前推进状态：{state_hint}
已完成要素：{done_elements}
本轮必须补齐的要素：{missing_elements}

【流程硬约束】
1. **单步推进**：一轮只推进一个环节，严禁跨环节抢跑——身份确认那一轮只做承接，
   不要在同一句里把驳回原因、整改动作一起倒出来；
2. 情况说明轮必须**一轮说完**：「驳回原因 + 整改动作」连贯说完，禁止只说「下面为您说明…」
   这类预告语就停下等回复；
3. **禁止复读机**：商家已经说「没疑问了 / 明白了 / 懂了」时，**严禁再问「您还有没有疑问」**，
   直接进入下一步（整改意愿确认）；
4. **不要仅凭「好的 / 明白 / 嗯」就跳到结束或下一步**：商家的话里只要带「但是 / 不过 / 具体哪里 /
   为什么 / 怎么」等转折或追问，一律先正面解答，解答完再问是否有别的问题，绝不抢跑结束；
5. 每轮必须让对话真实往前推进，禁止原地重复同一句话；
6. 只依据上面的标准参考话术与知识库回答，不得编造未提供的政策、时限或承诺。

请直接输出客服要说的话，不要输出任何标注、括号说明或状态名。"""

# 【RSI 提示词通道 · 11/21】生效提示词来自 prompts.py —— engineer.py 的白名单直接指向它，
# 于是「自动优化提示词」改的是提示词本身，而不是去改这个文件里的 Python 逻辑。
# 上面那份 _SYS_TEMPLATE_FROZEN_V1 是迁移前的**逐字快照**，只在 _test_prompts_rsi.py 里
# 用来做逐字节比对（保证迁移零行为变化），不参与运行时。
_SYS_TEMPLATE = prompts.SYS_TEMPLATE


def classify_emotion(text: str) -> str:
    """规则版情绪判定（快、稳定；LLM judge 可后置补充）。"""
    t = text or ""
    angry_kw = ("骗子", "混蛋", "垃圾", "投诉你", "滚", "去死", "诈骗", "恶心", "神经病")
    anxious_kw = ("怎么办", "急", "着急", "麻烦", "再不", "等不了", "完蛋", "耽误")
    satisfied_kw = ("明白了", "清楚了", "知道了", "好的谢谢", "谢谢", "理解了")
    if any(k in t for k in angry_kw):
        return "angry"
    if any(k in t for k in anxious_kw):
        return "anxious"
    if any(k in t for k in satisfied_kw):
        return "satisfied"
    return "neutral"


def check_compliance(history: List[dict], gate: bool = True,
                    identity_confirmed: Optional[bool] = None) -> dict:
    """必答要素质检：单线程、按业务顺序点亮，且必须满足「流程走到那一步 + 前置要素已完成」。

    11/12 单线程门控（修「顺序错乱 / 抢跑 / 进度虚高」）：
    * 要素按 REQUIRED_ELEMENTS 固定顺序（自报身份→身份确认→驳回原因→整改动作→结束语）。
      一个要素只有在「命中话术 AND 播出阶段 ≥ 该要素归属阶段」且「排在它前面、尚未完成的
      要素都已点亮」时才算覆盖。这保证：
        - 不能在「驳回原因」还没说清时就点亮「整改动作」（问题1：整改先于驳回）；
        - 阶段数字取「要素实际被播出的阶段」的最大值（delivered_stage），不再是全局 max，
          避免 AI 提前提了一句就把后面所有要素解锁（问题3：进度虚高）。
    * 「身份确认」必须商家口头确认后才算完成（identity_confirmed=True），单纯 AI 提问
      「请问您是负责人吗」不算（问题2）。identity_confirmed 为 None 时退化为旧口径（兼容老数据）。
    * 只认 agent 真实播报文本（role=='assistant'）；商家回合只用于读取身份确认信号。
    * 返回结构只增不改：elements / rate / missing / passed / total 语义不变；
      新增 elements_seen / element_stage / stage / stage_name / delivered_stage /
      early_mentioned / identity_confirmed。
    """
    ORDER = REQUIRED_ELEMENTS
    seen = {k: False for k in ORDER}          # 是否命中话术（不论阶段 / 顺序）
    matched_stage = {}                        # k -> 满足「阶段门控」的最小播出阶段
    confirmed = bool(identity_confirmed) if identity_confirmed is not None else None
    violations: List[dict] = []
    for h in history:
        text = h.get("content") or h.get("text") or ""
        if h.get("role") != "assistant":
            if confirmed is None and h.get("identity_confirmed"):
                confirmed = True
            continue
        st = h.get("stage")
        for key, pattern in ELEMENT_PATTERNS.items():
            if re.search(pattern, text):
                seen[key] = True
                if isinstance(st, int) and st > 0:
                    need = ELEMENT_STAGE.get(key, 1)
                    if st >= need and (key not in matched_stage or st < matched_stage[key]):
                        matched_stage[key] = st
        for bad in FORBIDDEN_PHRASES:
            if bad in text and not _phrase_negated(text, bad):
                violations.append({"role": "agent", "phrase": bad})
    # 单线程：按顺序，前置未完成则该要素即使命中也只能标「已提及未到环节」
    checks = {}
    ok_so_far = True
    for k in ORDER:
        eligible = (k in matched_stage) or (not gate and seen[k])
        if k == "身份确认":
            done = eligible and ok_so_far and (confirmed if confirmed is not None else True)
        else:
            done = eligible and ok_so_far
        checks[k] = done
        if not done:
            ok_so_far = False
    passed = sum(1 for v in checks.values() if v)
    # 11/13 合并修复：gate=False 时命中要素可能不在 matched_stage（无 stage 信息），用 get 兜底防 KeyError
    delivered_stage = max([matched_stage.get(k, 0) for k in ORDER if checks[k]] or [0])
    stage = delivered_stage or (ELEMENT_STAGE.get(ORDER[0], 1) if seen.get(ORDER[0]) else 0)
    return {"elements": checks, "rate": passed / len(ORDER),
            "missing": [k for k, v in checks.items() if not v],
            "violations": violations, "passed": passed, "total": len(ORDER),
            "elements_seen": seen, "element_stage": dict(ELEMENT_STAGE),
            "stage": stage, "stage_name": STAGE_NAMES_4.get(min(stage, 4), ""),
            "delivered_stage": delivered_stage,
            "early_mentioned": [k for k in ORDER if seen[k] and not checks[k]],
            "identity_confirmed": confirmed}



class CallAgent:
    """每通电话一个实例。线程模型：由通话 WebSocket 协程独占驱动。"""

    def __init__(self, call_id: str, merchant: dict, scenario: Optional[dict] = None,
                 version_note: str = "", offline: bool = False):
        self.call_id = call_id
        self.merchant = merchant
        self.scenario = scenario or {}
        self.version_note = version_note  # RSI 补丁注入的当前版本话术调整
        # 【11.21-18 根治·AI 回复重复】commit 互斥锁：插话时旧一轮 chat_stream
        # 还没退出，新一轮 chat_stream / 投机命中的 commit_reply 已并发进来，
        # 两个线程交错写 history / done_elements / turn_no 会把状态撕裂成
        # 「两轮回复各写一半」→ 后续轮上下文错乱、回复内容重复。
        # 锁内是纯内存操作（毫秒级），LLM/TTS 网络调用都在锁外，不拖首响。
        self._commit_lock = threading.RLock()
        # 11/19：来电/外呼分流——商家主动来电走「热线咨询」流程：热线问候开场、
        # 跳过商家身份确认、直接进答疑、说没了/再见就收线，不主动播报驳回结果。
        self.direction = (merchant or {}).get("direction") or "outbound"
        self.inbound = self.direction == "inbound"
        # offline=True：无 key / 无网络也能跑完整状态机（离线回放回归与闭环自测用）。
        # 此时所有回复走固定安全话术，确定性可复现；真实链路行为完全不变。
        self.offline = offline
        self.client = None
        if not offline:
            key = os.getenv("ZHIPU_API_KEY", "")
            if not key:
                raise RuntimeError("未配置 ZHIPU_API_KEY")
            self.client = ZhipuAI(api_key=key)
        self.state = "PRE"          # PRE=未开场；进入 S0 后按四段式走
        self.turn_no = 0
        self.merchant_turns = 0     # 商家真实发言轮数（赛题「≥3 轮」的计数口径）
        self.history: List[dict] = []
        self.done_elements: set[str] = set()   # 与 check_compliance 同源，不再按状态打勾
        self.identity_confirmed = False         # 商家是否口头确认身份（点亮「身份确认」的唯一依据）
        # 11/19：来电场景没有「确认对方身份」环节（商家打热线是咨询自己的事），
        # 自报身份由热线开场白覆盖、身份确认置为不适用，质检与提示词不再追问。
        if self.inbound:
            self.identity_confirmed = True
            self.done_elements |= {"自报身份", "身份确认"}
        # 五环节完成标志（12/12 统一进度模型）：qa=商家答疑确认无疑问；will=承诺整改；resched=没时间改约
        self.qa_confirmed = False
        self._rejection_delivered = False   # 情况说明（具体驳回原因+整改方式）是否已真实播报
        self.will_confirmed = False
        self.will_rescheduled = False
        # 13/13 改约：没空只是流程开始，必须问出并确认时间才算环节⑤完成
        self._await_reschedule_time = False   # 刚问完「哪天方便」，等商家给日期
        self.reschedule_at: Optional[str] = None   # 商家给出的改约时间（原话短语，落库进复盘）
        self._reschedule_asked = 0                # 改约追问次数（防"一直不给时间"死循环）
        # 12/13 语义意图判定 + 防死循环
        self.qa_asked = False               # 是否已主动问过「有无疑问」（问的时候绝不打勾）
        self.hesitant_count = 0             # 商家连续含糊/未明确确认的次数（≥3 强制结束/转人工）
        self.semantic_intent = "other"      # 本轮 LLM 语义标签（new_question / confirmed_understood / hesitant / other）
        self._intent_cache: Dict[str, str] = {}   # 同一句的语义分类缓存，避免投机预生成重复往返
        # 【11/22】「语义救回」缓存：规则想判闲聊时问过模型的句子，避免重复调用。
        # 命中缓存的句子，同一通电话里只问一次模型。
        self._rescue_cache: Dict[str, bool] = {}
        # 被救回的误判（规则想判闲聊、模型认定业务）：落库给 RSI 归因用
        self._rescue_log: list = []
        self.stage_ts = {}                       # 环节 id -> 完成时刻（相对通话开始的秒数）
        self._turn_done: Dict[int, int] = {}     # 环节 id -> 首次点亮的轮次（保证「先说明、后答疑」）
        # 【11/21 环节回跳】曾确认过的环节集合：某环节因商家反问暂时灭灯重核时，
        # 已确认的后续环节保持亮灯（用户口径：不影响后面已经确认的继续亮）。
        self._stage_locked: set = set()
        # 【11/21 关单方式分支决策】按《场景汇总》的「关单总原则」四条判定本通电话
        # 到底该不该外呼、要不要创事件预约二次外呼（赛题《数据说明》七-3 明确要求的
        # 「Agent 分支决策」）。此前 close_method 只是个展示字段，从未参与决策。
        # 失败一律退化为「按场景默认方式」，绝不让判定影响外呼主流程。
        try:
            import close_decision
            self.close_decision = close_decision.from_db(
                self.merchant.get("merchant_id") or self.merchant.get("id"),
                self.scenario, self.call_id)
        except Exception:
            close_decision = None
            self.close_decision = None
        # 【11/21 槽位账本】场景三槽（菜单/一级分类/二级分类）开单即预填；
        # 语义四槽（追问类型/材料/动作/预期）由结构化决策逐轮补齐。
        # 赛题评分维度 2 的「槽位完整率」就是取这个账本的完整度。
        try:
            import slots as _slots_mod
            self.slots = _slots_mod.from_scenario(self.scenario)
        except Exception:
            self.slots = {}
        self.call_start_ts = None                # 通话开始基准时刻（首轮 commit 时锁定）
        self.finished = False
        self._post_end = False      # 已播完结束语、商家又问了一句 → 回来只答疑，绝不复读④⑤
        self.stop_stage = ""
        self._s1_denials = 0        # S1身份确认阶段商家连续否认次数（≥2才礼貌结束）
        self._s1_weak = 0           # S1 商家既没肯定也没否认（喂/嗯/啊）→ 二次确认，绝不直接打勾
        self._s1_stall = 0          # S1 总僵局计数：不管否认/含糊/闲聊/反问，只要没确认就累计，
                                    # ≥5 直接登记转人工收线——不再给"踢皮球踢一整天"的机会（11/19）
        self._stall = 0             # 同一状态连续打转轮数（≥2 强制推进）
        self.non_task_end = False   # 情绪/超范围/未联系人等非业务结束，不计任务闭环
        self.last_intent = ""
        self._close_reason = ""
        self._llm_errors = 0
        # ---- 工具调用（11/12）----
        # tool_log：已落账的工具调用（只在 commit_reply 里累加，preview 期间不写）
        self.tool_log: List[dict] = []
        # _tool_cache：同一句 partial 会触发多次预测，按文本前缀复用决策，避免重复往返
        self._tool_cache: Dict[str, dict] = {}
        # 熔断计数：连续超时 >= tools_registry.MAX_TIMEOUTS 就不再走工具，保首响
        self._tool_timeouts = 0
        # ---- 红线拦截（11/12，13/13 改为三分支柔性拦截）----
        # 商家侧违规诉求留痕：每条都带「触发位置 + 时间 + 分支处置」，供进度条插卡与工单复盘
        self.violation_log: List[dict] = []
        self._vio: Optional[dict] = None
        self._vio_count: Dict[str, int] = {}   # 同类红线第几次触发（≥SEVERE_LIMIT 判屡教不改）
        self._await_ack = False                # 刚教育完，等商家表态（认错 / 换一种违规）
        self._vio_ack = False                  # 本轮商家是否认错（commit 时落进事件）
        self.severe_violation = False          # 是否打过【严重违规】标签（分支 C，转人工结束）
        self.vio_seq = 0                       # 本通第几次红线告警（卡片编号）
        # 11/15 真机修复②：闲聊分级事件（每次都实时推后台进度条 + 收尾进复盘）
        self.offtopic_log: List[dict] = []
        self._offtopic_count = 0
        # ---- 商家风险画像（11/12）----
        # B 级违规会在 risk 表里沉淀成长期标记；本通电话开始前先读一次，
        # 命中过的商家从第一句话起就按更严的边界沟通（见 _system_prompt 的风险段）。
        self.risk: Dict[str, Any] = {}
        try:
            mid = merchant.get("merchant_id") or merchant.get("id")
            if mid:
                self.risk = risk.profile(mid) or {}
        except Exception:
            self.risk = {}
        # 长会话记忆：读取该商家最近的历史通话摘要，注入系统提示词，
        # 让数字员工在回拨 / 二次外呼时能"记得"上次沟通到哪一步、差什么材料。
        try:
            mid2 = merchant.get("merchant_id") or merchant.get("id")
            if mid2:
                self.prior_calls = db.prior_calls(mid2, limit=3,
                                                 exclude_call_id=self.call_id) or []
            else:
                self.prior_calls = []
        except Exception:
            self.prior_calls = []

    # ---------- 提示词 ----------
    def _system_prompt(self, state: str, user_text: str = "") -> str:
        scen = self.scenario
        # 11/19：来电模式**整套专属提示词**——不再用「外呼模板+末尾补丁」的拼法。
        # 实测（21:37 来电）：模板主体全是外呼机制（身份确认/五环节/请问您是负责人），
        # 末尾的来电覆盖段压不住，模型照样问「您是负责人吗？有一项信息需要确认」、
        # 还把问题反抛回商家「您是否知道具体是哪方面的问题」。热线咨询是另一套职业
        # 逻辑，必须从提示词第一行就开始，而不是打补丁。
        if getattr(self, "inbound", False):
            rag = self._rag_context(user_text)
            # 【RSI 提示词通道 · 11/21】来电提示词整体搬到 prompts.SYS_TEMPLATE_INBOUND，
            # engineer.py 补丁 prompts.py 即可优化它，不用再动本文件的逻辑代码。
            return prompts.inbound_prompt(
                self.merchant.get("company_name", "未提供"),
                self.merchant.get("shop_name", "未提供"),
                HOTLINE, rag) + self._risk_hint() + (
                prompts.VERSION_NOTE_PREFIX + self.version_note if self.version_note else "")
        # 【RSI 提示词通道 · 11/21】各状态的推进要求以 prompts.STATE_HINTS 为准（engineer 可补丁）；
        # 下面这份字面量只作兜底，prompts 里缺 key 时才用（保证升级不丢行为）。
        _STATE_HINTS_FALLBACK = {
            "PRE": "通话已接通，现在执行第一段：自报身份，并用一句话说明致电事由，然后询问对方是否是公司负责人。",
            S0: "等待商家确认身份。",
            S1: "商家已确认身份。现在进入第三段「情况说明」：说明具体驳回原因和整改动作（最重要的一段，要具体到'怎么改'）。",
            S2: "情况说明段（一轮说完）：必须在**同一轮回复内连贯**说出「具体驳回原因 + 整改动作」，"
                "禁止只说「下面为您说明…」这类预告语就停下等回复；商家追问时，先针对他问的那一点"
                "结合知识库正面作答，一次只答一个问题，不重复已说过的话。",
            S3: "商家答疑段：商家提出的**任何追问、质疑、转折**（如「具体是哪里不符合？」「为什么？」"
                "「好的但是…」「懂了不过…」）都算强烈的追问意图——**必须先正面解答他的疑问**，"
                "解答完之后再问一句「还有别的问题吗」；**严禁无视追问、直接反问「您还有没有疑问」**。"
                "商家含糊其辞（如「嗯…」「哦」「这样啊」）时，主动追问「您看是哪一部分还有疑虑？」。"
                "只有商家明确表示无疑问时才进入下一环节。",
            S4: "整改意愿确认段：请商家承诺按意见整改；商家说没时间就重新约定时间；商家若对整改本身"
                "有疑问、表示不懂或含糊其辞，先耐心正面解释（依据知识库），不要机械重复确认句，"
                "解释完再确认；只有明确承诺后才收尾。",
            S5: "通话即将结束。",
            E1: "商家打断了你。先简短回应商家刚才说的内容，然后回到你被打断前的进度。",
            E2: "没听清商家的话，礼貌请对方再说一遍。",
            E3: "商家情绪激动。先共情安抚（'理解您的心情，这事确实耽误您时间了'），再清晰说明处理口径，保持克制，必要时引导走官方渠道。",
            E4: "问题超出资质审核范围，说明无法处理，建议联系商家服务热线，礼貌结束。",
            E5: "商家刚才提出了触碰红线的要求，你已按合规口径明确拒绝并划清边界，现在把话题拉回资质审核本身，继续当前流程。",
            END: "通话已结束，只返回简短结束语。",
        }
        state_hint = (prompts.state_hint_of(state)
                      or _STATE_HINTS_FALLBACK.get(state, ""))
        s5 = STATE_STAGE5.get(state, 0) or STATE_STAGE5.get(self.state, 1) or 1
        # 11/15 自测修复：身份未确认段（PRE/S1）不注入 RAG——「我就是商家」「那你说
        # 我姓什么」这类短句会检索出无关业务知识（京麦/商标等），把模型带偏。
        # 身份段有固定话术兜底，不需要知识检索。
        if state in ("PRE", S1):
            rag = ""
        else:
            rag = self._rag_context(user_text)
        missing = [k for k in REQUIRED_ELEMENTS if k not in self.done_elements]
        return _SYS_TEMPLATE.format(
            company=self.merchant.get("company_name", "未提供"),
            shop=self.merchant.get("shop_name", "未提供"),
            contact=self.merchant.get("contact_name", "未提供"),
            scenario=scen.get("level2_category", "未提供"),
            close_method=self._close_method_text(),
            script=(scen.get("script_text", "") or "未提供")[:300],
            hotline=HOTLINE,
            rag_context=rag or "（本轮无命中知识）",
            state_hint=state_hint,
            stage_hint=f"第{s5}环节 · {STAGES_5[s5 - 1]['name']}",
            done_elements="、".join(sorted(self.done_elements)) or "无",
            missing_elements="、".join(missing) or "无（本轮按流程推进即可）",
        ) + self._risk_hint() + self._history_hint() + (prompts.VERSION_NOTE_PREFIX + self.version_note if self.version_note else "") + self._inbound_hint()

    # ---------- 关单方式分支决策（11/21 · 赛题「任务规划」评分点） ----------
    def _close_method_text(self) -> str:
        """提示词里的「关单方式」：带判定依据，让模型知道本通电话的定位。

        以前这里直接塞场景表里的 close_method 字段（纯展示值）；现在塞的是
        按「关单总原则」四条算出来的结论 + 依据，模型据此知道自己是在做
        「必须外呼告知」还是「站内驳回后的补充沟通」。
        """
        d = getattr(self, "close_decision", None)
        base = (self.scenario or {}).get("close_method") or "电话"
        if not d:
            return base
        try:
            import close_decision as _cd
            return _cd.summary_line(d) or base
        except Exception:
            return base

    def note_callback_request(self, kind: str = "") -> None:
        """商家中途要求人工/回电，或出现投诉曝光倾向 → 按关单总原则 3/4 条升级判定。

        只在内存里改判定结果（不影响本轮路由），用于工单留痕与答辩解释：
        「为什么这通电话最后仍然要回电」。
        """
        d = getattr(self, "close_decision", None)
        if not d:
            return
        try:
            import close_decision as _cd
            risk = kind in ("COMPLAINT", "ESCALATE")
            self.close_decision = _cd.with_semantic_signals(
                d, merchant_requested_callback=(kind == "HUMAN"), escalate_risk=risk)
        except Exception:
            pass

    def _inbound_hint(self) -> str:
        """来电场景的口径修正（11/19）：热线咨询 ≠ 外呼，覆盖模板里的外呼措辞。"""
        if not getattr(self, "inbound", False):
            return ""
        return prompts.INBOUND_HINT

    def _history_hint(self) -> str:
        """长会话记忆 → 系统提示词里的「历史沟通」段（11/12 新增）。

        回拨 / 二次外呼时，让数字员工知道这家商家上次沟通到哪一步、差什么材料，
        开场确认身份后自然衔接（如"上次我们沟通了您的商标问题，您已补齐，本次是复核结果"），
        而不是每次都从零开始。绝不向商家逐字复述内部闭环判定。
        """
        pcs = self.prior_calls or []
        if not pcs:
            return ""
        items = []
        for p in pcs[:3]:
            ended = (p.get("ended_at") or "")[:16] or "此前"
            if p.get("closed_loop"):
                summary = "上次已闭环：" + "；".join(p.get("resolved") or [])[:120]
            else:
                summary = ("上次未闭环（停在" + (p.get("stop_stage") or "未知环节") +
                           "）：" + "；".join(p.get("pending") or [])[:120])
            items.append(f"- [{ended}] {summary}")
        return (
            "\n【历史沟通记录（长会话记忆 · 内部参考，不得向商家逐字复述，可自然衔接）】\n"
            + "\n".join(items)
            + "\n若本次为回拨 / 二次外呼，确认身份后可自然提及上次进展（如已补齐的材料、"
              "仍差的材料），再说明本次来电目的；不要每次都假装第一次联系。")

    def _risk_hint(self) -> str:
        """商家风险画像 → 系统提示词里的「边界收紧」段（11/12）。

        这不是话术，是**记忆**：上一次这家商家想让我帮他造假 / 改后台，被拒绝并
        终止了通话；这一次再打过去，我从第一句起就知道要更严 —— 不许任何松动、
        不许给变通办法、再提就直接引企标话术转专员。

        刻意不写「你要主动质问他上次」，因为那会让开场变得像追责；只约束边界。
        """
        rk = self.risk or {}
        if not rk or not (rk.get("b_count") or 0):
            return ""
        tags = "、".join(rk.get("tags") or []) or "违规诉求"
        return (
            f"\n【商家风险画像 · 内部参考，不得向商家复述】"
            f"该商家在历史通话中曾提出「{tags}」类违规要求，累计 {rk['b_count']} 次，"
            f"当前标记等级：{rk.get('level_name', '关注')}。"
            f"本轮仍按正常流程沟通，但边界更严："
            f"① 不对审核结果做任何形式的承诺或暗示；"
            f"② 不协助、不指导任何伪造、变通、绕过流程的做法；"
            f"③ 不接受任何利益表示；"
            f"④ 商家一旦再次提出此类要求，立即引用企标口径拒绝，不再解释、不再协商，"
            f"并告知转交高级专员处理。")

    def _rag_context(self, user_text: str = "") -> str:
        """用**本轮**商家文本检索知识库（11/12 修掉「永远慢一轮」）。

        旧实现读 self.history 最后一条 user，而 history 要到 commit_reply 才追加，
        于是第 2 轮检索的是「喂，你好」、第 3 轮检索的是「是我」——永远用上一句。
        现在由调用方显式传入 user_text；为空（开场）时退回场景词。
        """
        q = (user_text or "").strip()
        if not q:
            scen = self.scenario or {}
            q = scen.get("level2_category", "") or "资质审核 材料 驳回"
        hits = db.search_knowledge(q, top_k=3)
        parts = []
        for h in hits:
            if h.get("source") == "merchant":
                continue  # 商家档案不进话术上下文（db 层已过滤，这里双保险）
            parts.append(f"- [{h['source']}|{h['title'][:20]}] {h['content'][:220]}")
        return "\n".join(parts)

    # ---------- 语义前置判定（11/22 架构修复：模型先判，规则只在硬证据上否决）----------
    _SEMANTIC_VERDICTS = ("business_question", "violation_request", "chitchat", "other")

    def _semantic_verdict(self, text: str) -> str:
        """让模型把这句话归到四类之一（一次 8 token 的小调用，带缓存）。

        只在**规则即将做出"负向判定"**时调用：
          · 规则想判闲聊 → 问一句"这是不是业务问题"（救回）；
          · 红线命中    → 问一句"这是在问概念还是在要求违规"（防误判）。
        正常业务轮不调用，零额外开销。

        返回 business_question / violation_request / chitchat / other；
        空串表示模型沉默/异常/离线/开关关闭 —— 调用方必须走**保守分支**。
        """
        t = (text or "").strip()
        if not t or self.offline or self.client is None:
            return ""
        # 开关：SEMANTIC_GATE 是正式名；SEMANTIC_RESCUE 作为旧名保留兼容
        # （两个都在、任一显式关闭即关闭，避免升级后旧配置失效）。
        for _env in ("SEMANTIC_GATE", "SEMANTIC_RESCUE"):
            if (os.getenv(_env, "on").strip().lower()
                    in ("off", "0", "false", "no")):
                return ""
        if t in self._rescue_cache:
            return self._rescue_cache[t]
        prompt = (
            "你是电话外呼系统的语义判定器。给下面这句商家原话归类，只输出一个英文词。\n"
            "可选值（四选一，不要解释、不要标点）：\n"
            "business_question —— 在**问**京东资质审核相关的事（驳回、材料、京麦后台、政策、"
            "资质造假是什么意思、要补什么…）。注意：语音识别常把业务词听错，"
            "例如「京麦」被识别成「续脉/去脉/清迈/静脉」，只要语义上是在问业务，就算这一类。\n"
            "violation_request —— 在**要求我方**做违规的事：帮着造假/修图/刻章/改后台/"
            "走后门/送礼/要他人隐私信息。\n"
            "chitchat —— 与资质审核无关的闲聊（天气、吃喝、八卦、私人话题）。\n"
            "other —— 以上都不是。\n"
            f"商家原话：{t}"
        )
        try:
            r = self.client.chat.completions.create(
                model=MODEL, messages=[{"role": "user", "content": prompt}],
                temperature=0, max_tokens=10, stream=False)
            raw = (r.choices[0].message.content or "").strip().lower() if r.choices else ""
        except Exception:
            return ""
        verdict = ""
        for v in self._SEMANTIC_VERDICTS:
            if v in raw:
                verdict = v
                break
        self._rescue_cache[t] = verdict
        return verdict

    def _redline_is_question(self, text: str, vio: Optional[dict] = None,
                             semantic_intent: str = "",
                             sem_from_model: bool = False) -> bool:
        """红线命中，但这句其实是在**问概念**、不是**要求违规**吗？（三层判定）

        ## 翻车现场（14:08）

            商家「什么叫资质造假？」→ AI「帮您修图、刻章或者做一份假的证明材料，
            我绝对不能配合…」——把提问当成要求，还教育了商家一顿，
            并记了一条 FAKE_MATERIAL 违规（顺手把合规率也拉低了）。
            而真正的「帮我P一份授权书」反而没拦住。

        旧逻辑只看关键词（「造假」出现即命中），完全不区分**问**与**要求**。

        ## 三层（由便宜到贵，任一层说不清就保守拦截）

          ① 硬否决：出现明确请求动词（帮我做个 / P一下 / 刻个章…）→ 永不撤红；
          ② 句式层：整句是「什么叫X / 什么是X / X是什么意思」→ 是问句（离线也生效）；
          ③ 模型层：句式不典型时问一次模型；只有模型明确 business_question 才撤红；
             模型沉默/报错/离线 → **保持拦截**（合规宁严勿松）。
        """
        t = (text or "").strip()
        if not t:
            return False
        # ① 硬否决：明确的请求动词一律不救
        try:
            if reply_policy and reply_policy._FAKE_REQUEST_VERB_RE.search(t):
                return False
        except Exception:
            pass
        # ② 句式层：求解释句式直接判定为问句
        try:
            if reply_policy and reply_policy.is_lookup_question(t):
                return True
        except Exception:
            pass
        # ③ 语义层：优先用**本轮本来就会调的那次判定结果**（不额外花钱）；
        #    但只有在标签确实**来自模型**时才作数 —— 离线兜底标签（_fallback_intent）
        #    是关键词猜测，拿它去推翻关键词红线会互相打架：实测把「凭什么说我造假」
        #    从 FAKE_DOUBT 误救成普通提问，「你是说我造假吗」的致歉分支也被绕过。
        if sem_from_model and semantic_intent:
            return semantic_intent == "new_question"
        return self._semantic_verdict(t) == "business_question"

    # ---------- 语义救回：规则想判闲聊时，先让模型看一眼（11/22 架构修复）----------
    def _semantic_rescue(self, text: str) -> bool:
        """规则层即将把这句话判成「闲聊 → 拒答」之前，先问一次模型：这真是闲聊吗？

        ## 为什么必须这么改（而不是再加几个词）

        真机连续翻车：商家问「续脉是什么」→ 判闲聊拒答；补上「续/徐/许」之后，
        下一通又冒出「去脉」→ 还是拒答。**按字枚举永远追不上 ASR 听错的方式**，
        因为"听错"是一个开放集合，而词表是封闭集合。

        正确的分工是：**语义判断交模型，规则只做否决**。
        模型看到「去脉是什么」会反推出"您是说京麦吗"，词表永远不会。

        ## 为什么不是每轮都调

        只在「规则即将拒答」这一小概率分支触发，一次 8~10 token 的小调用，
        正常业务轮零额外开销（这点很重要：改造前每轮已经 3 次串行往返了）。

        ## 返回

        True  = 模型判为业务提问 → **不许判闲聊**，放行给答疑段正面回答；
        False = 沉默/异常/离线/开关关闭 → 保持原有规则判定（绝不因救回失败而卡流程）。
        """
        return self._semantic_verdict(text) == "business_question"

    # ---------- 大模型语义意图判定（12/13 · 彻底替代关键词/正则） ----------
    def _classify_user_intent(self, user_text: str) -> str:
        """让大模型判断商家这句话的真实意图，返回结构化标签。

        这是「进度条打勾 / 流程推进」的**唯一语义依据**：
          new_question        —— 提出了新问题、追问、质疑、转折
                                 （例："具体是哪里不符合？""为什么？""好的但是…""懂了不过…"）
          confirmed_understood —— 明确表示已理解且没有新问题（例："明白了没有疑问""清楚了我会整改"）
          hesitant            —— 含糊其辞、犹豫、未明确表态（例："嗯…""哦""这样啊""再说吧"）
          other               —— 其它（开场寒暄等）

        只有 confirmed_understood 才允许点亮「商家答疑 / 整改意愿确认」；
        new_question / hesitant 一律不打勾、不推进 —— 这正是修「进度条抢跑 / 关键词误判」的核心。
        """
        t = (user_text or "").strip()
        if not t:
            return "other"
        # 同一句复用：投机预生成 + 正式链路会各调一次，避免重复往返
        if t in self._intent_cache:
            return self._intent_cache[t]
        if self.offline or self.client is None:
            # 离线/无模型也走同一道「强证据门槛」，保证回放结果与线上口径一致
            label = self._evidence_gate(t, self._fallback_intent(t))
            self._intent_cache[t] = label
            return label
        prompt = (
            "你是电话通话的**业务语义判定器**。判断商家这句话在「京东商家资质审核外呼/热线」"
            "语境下属于哪一类。只输出一个标签，不要任何解释或标点：\n"
            "\n"
            "new_question —— 在**问**事情（追问、质疑、求解释、问操作）"
            "（例：具体哪里不符合？为什么？京麦是啥？在哪里上传？什么叫资质造假？）；\n"
            "commitment —— 在**承诺/表态会照做**（哪怕说得很口语、带时间状语也行）"
            "（例：我睡一觉起来就改、明早就去提交、回头我补上、行我现在去弄、"
            "我这就弄、晚点弄、我自己去改）；\n"
            "proceed —— 让对方**直接说正事**、不打断、默认接受当前流程"
            "（例：有事、你有啥事、你说就行了、你直接说、行你说、那你讲、"
            "我听着、别来回确认了往下说）。"
            "注意：proceed 不是拒绝，也不一定是明确承认身份，"
            "但它意味着「别再来回确认了，往下说」；\n"
            "hangup —— 商家要**挂电话/拒绝继续这次通话**"
            "（例：没事就挂了、我先挂了、忙着呢、不接了、别打了）；\n"
            "confirmed_understood —— 明确表示已理解且没有新问题"
            "（例：明白了没有疑问、没有其他疑问、没什么问题了）；\n"
            "hesitant —— 真的含糊、犹豫、不置可否（例：嗯…、哦、这样啊、再说吧、我看看）；\n"
            "chitchat —— 与资质审核无关的闲聊（天气、吃喝、八卦、私人话题）；\n"
            "violation_request —— 要求我方做违规的事（帮忙造假/修图/刻章/改后台/走后门/要隐私）；\n"
            "other —— 以上都不是（例如「最近没空」「我在开会」这类与流程无关的陈述）。\n"
            "\n"
            "关键区分（务必照做）：\n"
            "1. **口语但明确要去做事，一律判 commitment，不要判 hesitant**。"
            "商家说「我睡一觉起来就改」「晚点弄」都是承诺，不是犹豫。\n"
            "2. 语音识别常把业务词听错（京麦→续脉/去脉/听麦/清迈/静脉），"
            "只要**语义上**在问资质审核相关的事，一律判 new_question，不要判 chitchat。\n"
            "3. 「什么叫X / 什么是X / X是什么意思」是在问概念 → new_question，不是 violation_request。\n"
            "4. 「没有其他疑问」这类否定句是 confirmed_understood，不是提问、不是犹豫。\n"
            f"商家原话：{t}"
        )
        raw = ""
        try:
            r = self.client.chat.completions.create(
                model=MODEL, messages=[{"role": "user", "content": prompt}],
                temperature=0, max_tokens=12, stream=False)
            raw = (r.choices[0].message.content or "").strip().lower()
        except Exception:
            raw = ""
        # 解析顺序有讲究：commitment / violation_request / chitchat 是本次新增的
        # 「业务级」标签，先认它们，避免被 new_question 之类的子串抢走。
        label = ""
        for k in ("violation_request", "hangup", "proceed", "commitment",
                  "confirmed_understood", "new_question", "chitchat", "hesitant"):
            if k in raw:
                label = k
                break
        if not label:
            # 模型没给出可用标签 → 保守兜底，绝不轻易判「确认」
            label = self._fallback_intent(t)
        # 语义标签之上再加一道证据门槛：泛应答（行/好/嗯）绝不判「已确认无疑问」，
        # 但**不覆盖** commitment / violation_request / chitchat / new_question
        # （11/22：以前只认 4 个标签，商家早就答应整改也会被判回 hesitant，
        #  导致 AI 反问「您打算自己提交还是有哪里不方便」——答非所问）。
        label = self._evidence_gate(t, label)
        self._intent_cache[t] = label
        return label

    def _evidence_gate(self, t: str, label: str) -> str:
        """在模型语义标签之上加「强证据门槛」，杜绝泛应答被判成已确认。

        赛题硬要求：只有商家**明确表示**「疑问已解决 / 确实没有疑问」才允许点亮答疑。
        实测「行」「好的」「嗯」这类纯承接语会被模型乐观判成 confirmed_understood，
        导致 AI 刚问完「还有没有疑问」的下一轮就抢跑打勾（表现就是「情况说明和商家答疑
        同时亮绿灯」）。这里用强证据词兜住三种情况：
          * 判 confirmed 但没有任何强证据 → 降为 hesitant（员工主动追问「哪一部分还有疑虑」）；
          * 判 hesitant / other 但明确说了「没有疑问 / 清楚了 / 会整改」→ 纠正为 confirmed；
          * 判 new_question 的默认不覆盖，除非是「没有其他疑问」这类否定句式（明显不是提问）。
        """
        s = (t or "").strip()
        if not s:
            return label
        neg = any(k in s for k in ("不明白", "没明白", "不清楚", "不知道", "不懂", "没懂"))
        # 关键：先把「没有其他疑问 / 没有什么问题」这类否定短语整体摘掉，再判疑问词。
        # 否则"没有什么问题"里的"什么"会被当成提问，强证据反倒被否掉。
        nodoubt = _no_doubt(s)
        probe = _NO_DOUBT_RE.sub("", s) if nodoubt else s
        ask = any(k in probe for k in ("?", "？", "为什么", "怎么", "哪里", "什么", "如何", "咋",
                                       "但是", "不过", "可是"))
        # 「没有其他疑问」这类否定句不是提问：模型偶尔误判成 new_question，
        # 只要摘掉否定短语后既无疑问词也没问号，就纠正为已确认（否则 AI 会一直"解答"）。
        if label == "new_question":
            if nodoubt and not neg and not ask:
                return "confirmed_understood"
            return label
        strong = (not neg) and (nodoubt or any(k in s for k in (
            "没有疑问", "没有问题", "没疑问", "没问题", "没有了", "不用了", "没疑虑", "没疑惑",
            "没其他问题", "没有其他问题", "没啥问题", "都清楚", "清楚了", "明白了",
            "知道了", "懂了", "都懂了", "会整改", "会按", "按意见", "会补", "照做")))
        if label == "confirmed_understood":
            return "confirmed_understood" if strong else "hesitant"
        if label in ("hesitant", "other") and strong and not ask:
            return "confirmed_understood"
        return label

    def _quick_local_intent(self, t: str) -> Optional[str]:
        """【11/21 十轮·提速】本地高置信度意图快判：只在结果确定时返回标签，
        不确定返回 None 交给大模型。目的：砍掉每轮串行的语义意图 LLM 往返（实测 16s）。

        安全性（绝不牺牲队友话术准确性）：
          · S1 阶段队友已改「开口即认」，语义标签不影响 S1 推进——本地判定与
            offline 回放口径完全一致（_evidence_gate(_fallback_intent)），零分歧；
          · S2-S4 只对「铁定确认句」快判：这类句子含强证据词、无否定/疑问/转折，
            大模型的任何输出经 _evidence_gate 后都收敛到 confirmed_understood，
            所以本地判定与走模型结果一致；含承诺词(commitment)的交给模型，避免分歧。
          · 其余一切句子返回 None → 原样走模型，话术逻辑一字未动。
        """
        s = (t or "").strip()
        if not s:
            return "other"
        # S1：只对「铁定肯定确认」快判（是的/是我/本人/就是我…），跳过 LLM。
        # 绝不快判 proceed 催促句（有事/你说就行了）、hangup 挂断句、踢皮球句
        # ——这些靠大模型语义识别，队友的 _route 会用它们推进/收线，快判会吞掉
        # 这个功能（_test_semantic_labels 实测过）。这类句子返回 None 交模型。
        if self.state == S1:
            affirm = any(k in s for k in (
                "是我", "我是", "是的", "嗯对", "对对", "对的", "没错", "本人", "就是我",
                "对，", "对。", "对！"))
            plain_yes = bool(re.fullmatch(r"[是对][的吧呀啊呢。！!？?，,\s]*", s))
            # 否定 / 催促 / 挂断 / 反问 一律交模型
            risky = any(k in s for k in (
                "不是", "不", "别", "挂", "先挂", "忙", "没空", "不方便", "谁",
                "什么", "怎么", "为什么", "吗", "?", "？", "再说", "等", "回头"))
            if (affirm or plain_yes) and not risky:
                return "confirmed_understood"
            return None
        # S2-S4：铁定确认句才快判。
        strong_words = ("没有疑问", "没疑问", "没有问题", "没问题", "没有了", "不用了",
                        "没疑虑", "没疑惑", "都清楚", "清楚了", "明白了", "知道了",
                        "懂了", "都懂了")
        has_strong = _no_doubt(s) or any(k in s for k in strong_words)
        if not has_strong:
            return None
        neg = any(k in s for k in ("不明白", "没明白", "不清楚", "不知道", "不懂", "没懂"))
        ask = any(k in s for k in ("?", "？", "为什么", "怎么", "哪里", "什么",
                                   "如何", "咋", "但是", "不过", "可是"))
        commit = any(k in s for k in ("会整改", "会按", "按意见", "会补", "照做",
                                      "自己去", "明早", "明天就", "这就去"))
        if neg or ask or commit:
            return None   # 有分歧可能 → 交给模型
        return "confirmed_understood"

    def _fallback_intent(self, t: str) -> str:
        """离线 / 模型不可用时的兜底分类（不是主判定路径，保守不轻易判确认）。"""
        s = (t or "").strip()
        if not s:
            return "other"
        # 先摘掉「没有其他疑问 / 没有什么问题」这类否定短语，再判转折与疑问词，
        # 否则"没有什么问题"会因含"什么"被误判成提问（实测 AI 一直"解答"停不下来）。
        nodoubt = _no_doubt(s)
        probe = _NO_DOUBT_RE.sub("", s) if nodoubt else s
        turn = any(k in probe for k in ("但是", "不过", "可是", "就是", "然而"))
        if turn:
            return "new_question"
        if any(k in probe for k in ("？", "?", "为什么", "怎么", "哪里", "什么", "如何", "咋")):
            return "new_question"
        pos = nodoubt or any(k in s for k in (
            "没有疑问", "没有问题", "没疑问", "没问题", "没疑虑", "没疑惑",
            "明白了", "清楚了", "知道了", "懂了",
            "会整改", "会按", "按意见改", "会补"))
        if pos:
            return "confirmed_understood"
        # 「最近不方便 / 没空」不是含糊，是明确的事实陈述（改约由路由层的 no_time 处理）
        if any(k in s for k in ("没空", "没时间", "不方便", "很忙", "太忙", "抽不出", "改天")):
            return "other"
        if len(s) <= 6 or any(k in s for k in ("嗯", "哦", "这样啊", "再看看", "再说")):
            return "hesitant"
        return "other"

    def _explain_delivered(self) -> bool:
        """情况说明是否在本轮之前**真实播报过**（驳回原因 + 整改动作都说过）。

        只看本通电话里数字员工自己说过的内容，不看状态机标志位：
        标志位可能因流程推进被置真，但商家实际没听到——那种情况下不允许点「商家答疑」。
        """
        seen_reason = seen_action = False
        for h in self.history:
            if h.get("role") != "assistant":
                continue
            txt = h.get("content") or ""
            if not seen_reason and re.search(ELEMENT_PATTERNS["驳回原因"], txt):
                seen_reason = True
            if not seen_action and re.search(ELEMENT_PATTERNS["整改动作"], txt):
                seen_action = True
            if seen_reason and seen_action:
                return True
        return False

    # ---------- 红线：位置定位 / 认错识别 / 纠正话术（13/13 三分支） ----------
    def _flow_done_ids(self) -> set:
        """当前已完成的五环节 id 集合（与 flow_progress 同源）。"""
        try:
            return {s["id"] for s in self.flow_progress() if s["done"]}
        except Exception:
            return set()

    def _enrich_vio(self, vio: dict) -> dict:
        """给红线命中结果补「触发位置 + 时间 + 第几次 + 是否严重」，供进度条插卡与复盘。

        done_before = 触发时已完成几个环节 —— 警告卡就插在这个节点**之后**、
        下一个节点**之前**，实现「在哪个环节之前/之后触发就弹在哪里」。
        count = 同类红线第几次；第 SEVERE_LIMIT 次判「屡教不改」（分支 C）。
        """
        key = vio.get("key") or ""
        count = self._vio_count.get(key, 0) + 1
        done = self._flow_done_ids()
        v = dict(vio)
        v.update({
            "seq": int(self.vio_seq) + 1,
            "count": count,
            "done_before": len(done),
            "stage5": max(1, min(5, len(done) + 1)),
            "ts": round(max(0.0, time.time() - (self.call_start_ts or time.time())), 1),
            "awaiting": True,
            "branch": "",
            # 闲聊 / 拒接不算严重违规；同类第 N 次才升级
            "severe": (key not in ("OFF_TOPIC", "REFUSE_CALL")) and count >= redline.SEVERE_LIMIT,
        })
        return v

    # 商家「认错」的显式表达（分支 A 的唯一依据，逐条可审计）
    ACK_WORDS = ("我错了", "对不起", "抱歉", "不好意思", "我不该", "不应该", "是我考虑",
                 "不搞了", "不做了", "不提了", "不这样", "按规矩", "照规矩", "听你的",
                 # 11/19：「我改」收紧为自称动作——裸「我改」会匹配进「给我改过了」
                 # （让 AI 篡改的违规句被误当认错，回了「好的，您能理解就好」）
                 "我去改", "我来改", "我会改", "我马上改", "我这就改", "我认",
                 "明白", "知道了", "懂了", "好的我",
                 # 11/19：澄清/解释类——商家被纠正后最常见的回应，同样算认错拉回
                 "没说给你", "不是说给你", "不是说你", "不是给你", "说给审核", "给审核",
                 "说错了", "口误", "我意思是", "我不是这个意思", "没有这个意思",
                 "开玩笑", "玩笑", "逗你", "问问", "随便问问", "就是问问", "误会")

    def _is_ack(self, t: str) -> bool:
        """教育之后商家是否表态认错（分支 A）。又提违规时一律不算认错。"""
        s = (t or "").strip()
        if not s:
            return False
        if redline.classify(s).get("hit"):
            return False
        return any(k in s for k in self.ACK_WORDS)

    def _correction_text(self, intent: str, vio: Optional[dict]) -> str:
        """红线纠正话术：暂停业务 → 指出问题 → 给正确做法 → 拉回主线 / 严重违规收线。"""
        v = vio or {}
        key = v.get("key") or ""
        if intent == "MERCHANT_REFUSE":
            return redline.REFUSE_TEXT
        c = redline.correction(key)
        if intent == "VIOLATION_SEVERE" or v.get("severe"):
            say = c.get("say") or ""
            return (say + redline.SEVERE_TEXT) if say else redline.SEVERE_TEXT
        if key == "OFF_TOPIC":
            return "咱们还是回到资质审核这件事上，我把需要您补齐的材料再和您确认一遍。"
        parts = []
        if int(v.get("count") or 1) >= 2:
            # 分支 B / 同类再次出现：先点明「刚才那个已经说过」，再教育
            parts.append(redline.ANOTHER_OPEN.format(name=v.get("name") or "这个要求"))
        if c.get("say"):
            parts.append(c["say"])
        if c.get("guide"):
            parts.append(c["guide"])
        parts.append(redline.PULL_BACK)
        return "".join(parts)

    def _identity_settled(self) -> bool:
        """身份环节是否**已落实**（区别于"当前灯亮"）。

        【11/22 真机修复·身份灯台账】灯灭只表示「待重核」的显示状态（11/21 环节回跳设计），
        不等于「从未确认过」。翻车现场（13:27 通）：商家反查「你知道我是谁吗」→ 灯灭，
        之后「说吧说吧 / 没事了 / 可以」都不是「是我/本人/没错」，灯一直没重新点亮，
        于是收尾闸门把已答完整改意愿的商家从 S4 拉回 S1 重问「您是负责人吗」，
        闭环率也跟着被记成未闭环。
        正解：当前灯亮 **或** 曾确认过（_stage_locked 有 2）即算落实 —— 除非商家明确否认
        （否认会走 NOT_CONTACT 分支直接挂机，不会落到这里）。
        """
        if self.identity_confirmed:
            return True
        return 2 in (getattr(self, "_stage_locked", set()) or set())

    def _prospective_done(self, aux: Optional[dict]) -> dict:
        """收尾闸门用：把「本轮即将写入的标志」也算进来，推算五环节完成情况。

        preview 阶段实例属性还没更新（变更压在 aux 里），若只读实例属性，
        会把自己这一轮刚完成的环节误判成未完成，导致收尾永远被退回。
        """
        def g(name):
            if aux is not None and name in aux:
                return aux[name]
            return getattr(self, name, False)

        n1 = self.state != "PRE"
        # 【11/22 真机修复·身份灯台账】环节②的完成判定不能只看当前灯值。
        # 翻车现场（13:27 通 / 资质造假）：商家反查「你知道我是谁吗」→ 按设计熄灭身份灯
        # 待重核 → 之后「说吧说吧 / 没事了 / 可以」都不是「是我/本人/没错」，
        # 灯一直没重新点亮 → 商家已答完整改意愿「可以」时，收尾闸门发现环节②缺失，
        # 把流程从 S4 硬拉回 S1 重问「您是负责人吗」（商家刚说完"可以"又被问身份）。
        # 正解：只要「曾经确认过（_stage_locked 里有 2）」且没被明确否认，就算完成；
        # 灯灭只表示"待重核"的显示状态，不该否掉已完成的事实。
        # 灯灭只表示"待重核"的显示状态，不该否掉已完成的事实（见 _identity_settled）
        n2 = bool(n1 and (g("identity_confirmed") or self._identity_settled()))
        n3 = bool(n2 and (self._explain_delivered() or g("_rejection_delivered")))
        n4 = bool(n3 and g("qa_asked") and g("qa_confirmed"))
        n5 = bool(n4 and (g("will_confirmed") or g("will_rescheduled")))
        return {1: n1, 2: n2, 3: n3, 4: n4, 5: n5}

    def _reconfirm_advance(self) -> tuple:
        """【11/21 环节回跳·用户口径】身份重新认下后，**绝不从头重跑已讲过的环节**：
        跳到「最近的、后面的未完成环节」继续确认；全部完成 → 直接收尾。

        实测翻车现场（10:41 通话）：商家最后答完整改意愿「好的」→ 收尾闸门发现身份
        灯灭 → 回退到 S1 重问「您是负责人吗」→ 商家答「是我」→ 机器从 S2 把
        「驳回原因和整改要求」整段重讲一遍——商家被迫全流程再答一次。
        判定用**原始证据 flag**（不受身份灭灯的 check_compliance 级联影响）：
          情况说明 = _explain_delivered()/_rejection_delivered；
          商家答疑 = qa_asked & qa_confirmed；整改意愿 = will_confirmed|will_rescheduled。
        返回 (ns, intent)；情况说明还没讲过 → (S2, CONFIRMED_ONLY)，
        与首次确认的单步推进完全一致，正常首流程行为零变化。
        """
        st3 = bool(self._explain_delivered() or getattr(self, "_rejection_delivered", False))
        st4 = bool(getattr(self, "qa_asked", False) and getattr(self, "qa_confirmed", False))
        st5 = bool(getattr(self, "will_confirmed", False) or getattr(self, "will_rescheduled", False))
        if not st3:
            return S2, "CONFIRMED_ONLY"       # 情况说明没讲过 → 正常续讲（首次确认路径）
        if not st4:
            return S3, "RECONFIRM_JUMP_QA"    # 讲过了 → 跳答疑：接着问有无疑问
        if not st5:
            return S4, "RECONFIRM_JUMP_WILL"  # 答疑也过了 → 跳整改意愿确认
        return END, "RECONFIRM_JUMP_END"      # 全部完成 → 礼貌收尾，绝不复读

    # ---------- 路由（保留手动版四段式 + 异常分支） ----------
    def followup_after_end(self) -> bool:
        """AI 已播完结束语、商家又开口（且不是告别语）→ 恢复答疑，但**不回退已打勾环节**。

        旧版服务端直接把 `agent.state` 改成 S3 就完了，没有标记：商家问完一句再说
        「没疑问了」，又会从 S3→S4 走一遍「那您后续方便尽快去京麦后台重新提交吗？」，
        刚收完尾的通话被硬拉回去复读。这里打 `_post_end` 标记，`_route` 在答疑
        确认后直接收尾，不再重问④⑤。

        返回 False 表示这通电话本身就没走完（拒接 / 情绪 / 超范围 / 未接通），
        不该把 AI 从结束态拉回来。
        """
        done = {s["id"] for s in self.flow_progress() if s.get("done")}
        if len(done) < 5:
            return False
        self.finished = False
        self._post_end = True
        self.hesitant_count = 0
        self.state = S3          # 回答商家这句补充提问；环节完成标志一律保留
        return True

    def _route(self, text: str, interrupted: bool, commit: bool = True,
               aux: Optional[dict] = None,
               semantic_intent: str = "other") -> tuple[str, str, str]:
        """返回 (prompt_state, next_state, intent)。

        11/11 修正（闭环率上不去的根因）：
        * S1 不再只认肯定词：报公司名 / 自证 / 反问 / 随口应答都算接通，
          避免商家一反问就 S1→S1 原地打转（历史 22 通死在这里）；
        * S4 商家一确认就 S4→END：结束语本轮播完即闭环，不再等商家"再开一口"；
        * 同状态连续 ≥2 轮强制推进；以「商家发言轮数」设 3 轮下限 / 6 轮上限；
        * 情绪 / 超范围 / 非负责人改走含必说要素的安全话术，并标记 non_task_end，
          不再"没说完也算任务闭环"。

        罗 11/12（投机预生成）：commit=False 供投机预生成调用——绝不修改任何
        会话状态，计算出的状态变更写入 aux 字典，由 commit_reply() 统一写回。
        """
        def _set(name: str, value) -> None:
            if commit:
                setattr(self, name, value)
            elif aux is not None:
                aux[name] = value

        if aux is None:
            aux = {}      # 预览调用未传 aux 时也给个临时容器，避免「本轮标志」丢失导致收尾被误拦

        # ⑥ ASR 同音异字归一（没乐→没了 / 方变→方便 …）：所有下游判定统一受益。
        t = reply_policy.asr_norm(text or "") if reply_policy else (text or "")
        # 【11/22】把「RSI 审核通过的误听词映射」应用到本轮文本 —— 词表是长出来的，
        # 不是人一个个敲进代码的（对应 unknown_terms.term_map，审核通过即生效）。
        try:
            t = unknown_terms.apply_norm(t)
        except Exception:
            pass
        resched_at: Optional[str] = None   # 本轮识别出的改约时间（"后天""下周一"")
        tm_expr, tm_is_date = _parse_time_expr(t)
        # 身份确认只认**明确肯定**（「是我 / 是的 / 对 / 本人」）；
        # 「喂 / 嗯 / 啊」这类既非肯定也非否认的应答不算确认，绝不因此点亮【身份确认】。
        affirm_strong = any(k in t for k in (
            "是我", "我是", "是的", "嗯对", "对对", "对的", "没错", "本人", "就是我",
            "对，", "对。", "对！", "你说", "请讲", "在的"))
        # 11/19：催促 AI 继续（「你倒是说呀」实测被判闲聊 → 复读身份确认）。
        # AI 刚问完「您是负责人吗」，商家说「你说吧/你倒是说呀」= 让流程往下走，
        # 视同口头认下推进（含否定词时由 is_go_ahead 自行否决）。
        if not affirm_strong and reply_policy and reply_policy.is_go_ahead(t):
            affirm_strong = True
        # 11/21：AI 问「请问您是负责人吗」，商家答单字「是」= 明确认下（实测被当
        # 含糊复读身份确认）。只要整句就是「是 + 语气词/标点」就算肯定；「不是」
        # 由下方 negative 否决，不会误入。
        if not affirm_strong and re.fullmatch(r"是[的吧呀啊呢。！!？?，,\s]*", t or ""):
            affirm_strong = True
        # 「不是」用 lookbehind 排除「是不是」疑问结构：「你是不是京东的」不是否认（11/19）
        negative = (bool(re.search(r"(?<!是)不是", t))
                    or any(k in t for k in ("找错", "不认识", "打错了")))
        # 11/19：商家在质疑身份（「你真的是京东吗？不是来诈骗的」）时，"诈骗"会命中
        # 情绪暴怒词，曾直接走投诉挂机——但这是身份疑问不是投诉，必须先自证。
        ik_early = reply_policy.identity_kind(t) if reply_policy else ""
        identity_doubt = (ik_early == "AGENT_QUERY"
                          or (reply_policy and reply_policy.is_agent_identity_doubt(t)))
        angry = classify_emotion(t) == "angry" and not identity_doubt
        # 11/19：「你是真人吗」≠ 转人工（实测被当成人工请求直接挂机）——
        # 裸词「真人」移出列表（它几乎总是身份质疑）；只保留明确的转接请求；
        # 且身份质疑整句豁免，绝不走挂机分支。
        human = (not identity_doubt
                 and any(k in t for k in ("转人工", "人工客服", "找人工", "转接人工",
                                          "人工服务", "找真人", "真人客服", "要人工")))

        # 11/15 真机修复⑤：商家明确道别（「拜拜拜拜」「白白」「再见」…）。
        # 旧版只有 server 层在 agent.finished=True 时做零回复短路；结束语后只要
        # 会话被恢复成答疑态（finished=False），告别就会掉进 S3 含糊分支，
        # 反问「您看是哪一部分还有疑虑？」。这里提升为与阶段无关的意图。
        # 原文 + 归一化文本双判，避免 asr_norm 把同音字改掉后漏判。
        bye_hit = (self.state not in ("PRE", END) and bool(t.strip())
                   and (is_farewell(text or "") or is_farewell(t)))
        # 11/15 真机修复⑥：商家明确说「哪一天都没空」= 无可约时间 → 启动转人工。
        # 与 BYE 一样提升为**阶段无关强意图**，并抢在红线/闲聊分类之前，
        # 否则短句会被闲聊词库或 LLM 的 hesitant 标签带偏，又绕回追问日期。
        # 教训同前几轮：判定只挂在 S4 一条链路上，遇到状态回退就会漏判。
        no_day_free = (self.state not in ("PRE", END) and bool(t.strip())
                       and not bye_hit
                       and (reply_policy.no_day_free_hit(t) if reply_policy else False))
        # 同一句里若还给了可落地日期（「我哪天都没空，十八号吧」），以改约确认为准。
        if no_day_free and tm_is_date:
            no_day_free = False

        # 【红线拦截 E5】商家侧的违规诉求在这里判，不靠模型自觉。
        # 13/13 三分支柔性拦截：命中后 AI **立刻暂停业务**做纠正，处置走向由
        # 「商家接下来怎么反应」决定，不再按类目一刀切终止 ——
        # 第一次提造假就挂电话是最伤体验的过度处置，也拿不到业务结果。
        # 11/19 来电守卫：热线咨询模式下商家还没开口时的空文本轮（重复 start /
        # 静默触发）固定回重问候语，绝不放给大模型自由发挥——实测线上模型
        # 在这一步自作主张问「您是负责人吗」，把热线咨询带回外呼剧本。
        inbound_regreet = (self.inbound and self.state not in ("PRE", END)
                           and not t.strip())
        # 本轮语义标签是否**来自模型**（而不是离线关键词兜底）。
        # 只有模型给的标签才有资格否决规则判定；离线兜底标签只做原有用途，
        # 否则等于用关键词的猜测去推翻关键词的判定，越改越松（实测踩过）。
        _sem_from_model = bool((not self.offline) and (self.client is not None)
                               and semantic_intent and semantic_intent != "other")
        vio = {"hit": False}
        if not inbound_regreet and self.state not in ("PRE", END) and t.strip() \
                and not bye_hit and not no_day_free:
            vio = redline.classify(t)
            # 【11/22 架构修复】红线是关键词命中的，但「问概念」≠「要求违规」：
            # 「什么叫资质造假？」在问定义，旧逻辑却按 FAKE_MATERIAL 教育了商家一顿，
            # 还记了一条违规（把合规率也拉低了）。这里让语义判定先看一眼：
            #   ① 有明确请求动词（帮我做/P一下/刻个章）→ 永不撤红；
            #   ② 求解释句式 → 撤红，交给答疑段正面解释；
            #   ③ 其余问模型；模型沉默/报错/离线 → 保持拦截（合规宁严勿松）。
            if vio.get("hit") and self._redline_is_question(t, vio,
                                                             semantic_intent,
                                                             _sem_from_model):
                _set("_rescue", {"stage": "redline", "key": vio.get("key") or "",
                                 "text": t[:40], "verdict": "business_question"})
                vio = {"hit": False, "rescued_from": vio.get("key") or ""}
            # 反向补漏报（双钥匙，11/22）：「帮我P一份授权书」这类不含"造假"字样的真请求，
            # 关键词表根本没命中。规则先看**明确请求动词**（便宜），命中才花一次模型确认；
            # 两把钥匙都成立才补一条红线，走与既有红线同一套话术/计数/留痕。
            if not vio.get("hit"):
                _req_verb = False
                try:
                    _req_verb = bool(reply_policy
                                     and reply_policy._FAKE_REQUEST_VERB_RE.search(t))
                except Exception:
                    _req_verb = False
                if _req_verb:
                    _verdict = (semantic_intent
                                if semantic_intent == "violation_request"
                                else self._semantic_verdict(t))
                    if _verdict == "violation_request":
                        vio = redline.as_hit("FAKE_MATERIAL")
                        _set("_rescue", {"stage": "redline_add", "key": "FAKE_MATERIAL",
                                         "text": t[:40], "verdict": "violation_request"})
            # 【11/21 环节回跳·随机应变】S2+ 反问登记信息（「负责人是姓啥/你联系的是谁」）
            # 与本次外呼直接相关——任何环节都正面回答，绝不判闲聊拉回；
            # 按「环节可重核」模型：身份灯暂时熄灭待重新确认，已确认的后续环节保持亮灯。
            # （S1 段保留原反查分支，其确认推进逻辑不动。）
            if (not vio.get("hit") and self.state != S1
                    and reply_policy
                    and (reply_policy.is_name_challenge(t) or reply_policy.is_contact_ask(t))):
                _set("_name_challenged", True)
                if self.identity_confirmed and not self.inbound:
                    # 来电（热线咨询）没有身份环节，identity_confirmed=True 是"不适用"
                    # 占位，不参与灭灯重核（实测口径：反问登记信息在来电只是普通答疑）
                    _set("identity_confirmed", False)   # 身份灯暂时灭 → 待重新确认
                ps, ns, intent = self.state, self.state, "NAME_CHALLENGE"
                return self._apply_gates(ps, ns, intent, bool(t.strip()), commit, aux,
                                         time_expr=resched_at)
            # 反查后的重新确认：答「对/是我/没错/本人」或连答应答（好的好的/嗯嗯…）
            # → 身份灯重新点亮，流程停在当前环节接着走（后续环节锁定不灭）。
            # 【11/21 修复①】实测「好的好的」旧正则匹配不上，灯一直灭着挂在那里——
            # 换用 _RECONFIRM_ACK_RE（允许连答重复）；另加 negative 守卫防「不是」漏网。
            if (not vio.get("hit") and self.state != S1
                    and getattr(self, "_name_challenged", False)
                    and reply_policy
                    and not negative
                    and not reply_policy.is_name_challenge(t)
                    and not reply_policy.is_contact_ask(t)
                    and (("是我" in t or "本人" in t or "没错" in t)
                         or bool(_RECONFIRM_ACK_RE.fullmatch(t or "")))):
                _set("_name_challenged", False)
                _set("identity_confirmed", True)
                ps, ns, intent = self.state, self.state, "RECONFIRM_OK"
                return self._apply_gates(ps, ns, intent, bool(t.strip()), commit, aux,
                                         time_expr=resched_at)
            if getattr(self, "_name_challenged", False) and not vio.get("hit"):
                # 反查窗口内给了别的应答 → 退出重核窗口（身份灯保持灭，流程继续，
                # 商家下次再说「是我」仍可随时点亮）。
                _set("_name_challenged", False)
            # 【11/21 修复①补充】窗口退出后，商家随时补一句「是我/本人/没错」
            # 仍要能点亮身份灯（用户口径：灭了也得能亮，绝不能永远挂在那里）。
            # 只认明确的身份自证短句（全句匹配），不吞业务内容；「不是，我是…」
            # 是长句不会被全句匹配误吞；「不是」开头的否认由 negative 把关。
            if (not vio.get("hit") and not negative and not self.identity_confirmed
                    and not self.inbound and self.state not in ("PRE", S1, END)
                    and re.fullmatch(r"(?:是[的我]{0,2}|本人|没错|对，?是我|是我本人|"
                                     r"我是(?:负责人|本人|店主|商家)?)"
                                     r"[的吧呀啊呢哈哦。！!？?，,\s]{0,4}", t or "")):
                _set("identity_confirmed", True)
                ps, ns, intent = self.state, self.state, "RECONFIRM_OK"
                return self._apply_gates(ps, ns, intent, bool(t.strip()), commit, aux,
                                         time_expr=resched_at)
            # 11/15 真机修复②：已进入闲聊预警窗口（≥2 次）后，词库外的短闲聊
            # 也要继续累计，否则第 4 次永远到不了、「超过三次拦截」不触发。
            # 11/19 结构豁免：无疑问收束（「那就没了/我知道了」）和忘了想问什么
            # （「我忘了」）都是对「还有没有疑问」的直接回应——必须交给下方
            # no_more / QA_FORGOT 流程分支推进，绝不能被闲聊层吞掉再计数
            # （实测：那就没→拉回→我知道了→窗口吞→计数满4→强制挂机，流程 3/5 卡死）。
            _no_more_or_forgot = (reply_policy and
                                  (reply_policy.no_more_plain(t) or reply_policy.qa_forgot(t)))
            # 【11/21】S4 意愿确认段的短肯定应答（「嗯，方便的。/方便的/有空了」）
            # 必须放行给 S4 意愿判定，绝不进闲聊层——旧版被泛化兜底吞成 OFF_TOPIC，
            # S4 拉回恰好播「打算自己提交还是有什么不方便」的多此一问（真机复现）。
            _s4_ack = (self.state == S4 and reply_policy
                       and reply_policy.s4_ack_affirm(t))
            # 【11/22 架构修复】判闲聊之前先让模型看一眼：ASR 把业务词听错时
            # （续脉/去脉/清迈 = 京麦），关键词表必然失守。模型说 business 就不许拒答。
            # 只在「即将拒答」这一小概率分支触发，正常业务轮零开销。
            _rescue = False
            if (not vio.get("hit") and not _no_more_or_forgot and not _s4_ack
                    and self.state != S1
                    and int(getattr(self, "_offtopic_count", 0) or 0) >= 2
                    and reply_policy and reply_policy.offtopic_continuation(t)):
                _rescue = self._semantic_rescue(t)
                if _rescue:
                    _set("_rescue", {"stage": "window", "text": t[:40], "verdict": "business"})
            if (not vio.get("hit") and not _no_more_or_forgot and not _s4_ack
                    and self.state != S1
                    and int(getattr(self, "_offtopic_count", 0) or 0) >= 2
                    and reply_policy and reply_policy.offtopic_continuation(t)
                    and not _rescue):
                vio = {"hit": True, "key": "OFF_TOPIC"}
            # 11/19 修复：S1 禁用闲聊窗口——身份没确认前，「你是不是京东的」这类
            # 疑问必须交给 identity_kind 正常应答，不能被预警窗口抢先判成闲聊
            # （窗口累计 ≥2 后连身份质疑都会被吞掉，造成复读循环）。
            # 11/19 泛化兜底：词库永远追不上闲聊的多样性。词库未命中时，
            # 「无业务信号 + 非流程应答」的句子一律按闲聊处理（reply_policy.offtopic_generic，
            # 纯规则零延迟），不再依赖人工逐词补充；边缘句宁可放过，交给提示词红线第8条兜底。
            if (not vio.get("hit") and not _no_more_or_forgot and not _s4_ack
                    and reply_policy and reply_policy.offtopic_generic(t)
                    # 11/19：夸奖不是闲聊（「我夸你聪明」实测回「不方便展开」，傻气）
                    and not (reply_policy.is_compliment(t) and self.state != S1)
                    # 【11/22】模型已经判定这是「业务提问 / 承诺整改」时，规则不许再判闲聊。
                    # 翻车现场：商家说「我睡一觉。」（在讲怎么整改），被 offtopic_generic
                    # 判成闲聊拉回「咱们还是回到资质审核这件事」。
                    # 注意：**只在语义标签确实来自模型时**才豁免 —— 离线/无模型时
                    # semantic_intent 是关键词兜底（_fallback_intent），把它当豁免依据
                    # 会让离线判定整体变松（实测把 3 条闲聊回归测试搞红了）。
                    and not (_sem_from_model
                             and semantic_intent in ("new_question", "commitment"))):
                _rescue2 = self._semantic_rescue(t)
                if _rescue2:
                    # 规则要拒答，但模型认定这是业务问题 → 放行给答疑段正面回答。
                    # 这条留痕会被写进 result_json，供 RSI 归因看到"被救回的误判"。
                    _set("_rescue", {"stage": "generic", "text": t[:40],
                                     "verdict": "business"})
                else:
                    vio = {"hit": True, "key": "OFF_TOPIC"}
            # 11/19：红线纠正之后，商家的短句澄清（「没说给你」「开玩笑的」「问问而已」）
            # 是认错/解释，不是闲聊——必须放行给 _await_ack 分支走 VIO_ACK 拉回主线，
            # 否则会被闲聊层吞掉，AI 反复「回到资质审核」，商家越解释越像在闲聊。
            if (vio.get("hit") and vio.get("key") == "OFF_TOPIC"
                    and getattr(self, "_await_ack", False) and self._is_ack(t)):
                vio = {"hit": False}
            # ① 功能补丁（11/15）：闲聊不算红线——不 enrich、不落 _vio、不写工单违规，
            #    只进下面的闲聊分级分支（1~2 拉回 / 3 警告 / 4 收线）。
            if vio.get("hit") and vio.get("key") == "OFF_TOPIC":
                vio = {"hit": True, "key": "OFF_TOPIC"}
            # ② 功能补丁（11/15）：「你是说我造假吗」是误会澄清，不是造假诉求，
            #    不落红线、不进 E5、不记风险标记 → 交给 FAKE_DOUBT / FAKE_ACCUSE。
            if vio.get("hit") and reply_policy and reply_policy.is_fake_doubt(t):
                vio = {"hit": False, "self_doubt": True}
            # ② 功能补丁（11/15）：「你是说我造假吗」是误会澄清，不是造假诉求，
            #    不落红线、不进 E5、不记风险标记 → 交给 FAKE_DOUBT / FAKE_ACCUSE。
            if vio.get("hit") and reply_policy and reply_policy.is_fake_doubt(t):
                vio = {"hit": False, "self_doubt": True}
        # ① 闲聊不算红线：不 enrich、不落 _vio（否则 commit_reply 会把它记成
        #    「红线拦截」留痕、计入红线 KPI）；只保留 key 供下面的闲聊分级分支路由。
        if vio.get("hit") and vio.get("key") == "OFF_TOPIC":
            vio = {"hit": True, "key": "OFF_TOPIC"}
        elif vio.get("hit"):
            vio = self._enrich_vio(vio)
            _set("_vio", vio)

        if bye_hit:
            # 明确道别 → 安静收线：不追问、不补业务要素、不冒充已闭环。
            # 未走完意愿确认（且不是「结束语后回来答疑」）的，标记非任务结束，
            # 避免一通半截电话被算成成功工单。
            _closed = bool(getattr(self, "_post_end", False)
                           or getattr(self, "will_confirmed", False)
                           or getattr(self, "will_rescheduled", False))
            if not _closed:
                _set("non_task_end", True)
            _set("_close_reason", "MERCHANT_BYE")
            ps, ns, intent = self.state, END, "BYE"
        elif (_sem_from_model and semantic_intent == "hangup"
              and not (reply_policy and reply_policy.no_more_plain(t)
                       and not is_farewell(t))):
            # 【11/22 修复】模型把「没了没了」判成 hangup → 直接收线，跳过 S4，
            # 流程停在 3/5。明确“没有疑问”的短应答优先按 NO_MORE 推进整改意愿；
            # 只有真正的道别（is_farewell）才允许走收线。
            _set("non_task_end", True)
            _set("_close_reason", "MERCHANT_REFUSE")
            ps, ns, intent = self.state, END, "MERCHANT_REFUSE"
        elif no_day_free:
            # 全时无空：改约已不可能，继续问日期只会变成机械复读 → 启动转人工。
            # 口径与既有 HUMAN_REQUEST / DISTRUST_TRANSFER 保持一致：
            # ① 话术明确登记转人工 ② 非任务结束（不算闭环）③ 工单特殊标记留痕。
            _set("non_task_end", True)
            _set("_close_reason", "NO_TIME_TRANSFER")
            _set("transfer_human", True)
            _set("_last_flag", {"kind": "no_day_free", "count": 1, "text": t[:50]})
            ps, ns, intent = self.state, END, "NO_TIME_TRANSFER"
        elif self.state == "PRE":
            # 11/19：来电场景跳过身份确认，直接进答疑（热线问候已由开场白覆盖）；
            # 外呼保持原四段式 PRE→S1。
            ps, ns, intent = "PRE", (S3 if self.inbound else S1), "START"
        elif vio.get("self_doubt"):
            # ② 造假质疑（昨天版第九轮口径）：驳回原因不是造假 → 道歉澄清 + 真实驳回原因；
            #    本单驳回原因本来就是资质造假 → 讲核验依据，第 2 次起给「重新提交/转人工」。
            if self._is_fake_scene():
                _set("_fake_accuse_count", int(getattr(self, "_fake_accuse_count", 0) or 0) + 1)
                intent = "FAKE_ACCUSE"
            else:
                intent = "FAKE_DOUBT"
            ps, ns = self.state, self.state
        elif (reply_policy and int(getattr(self, "_fake_accuse_count", 0) or 0) > 0
              and self._is_fake_scene() and reply_policy.is_fake_pushback(t)):
            # ② 已讲过核验依据，商家仍硬顶「我的材料是真的 / 我没改过」：
            #    不再解释第二遍，直接给「原件重新提交 / 转人工复核」两条出路。
            #    （必须排在 distrust 之前——「凭什么不信」会被「不信」抢成不信任计数。）
            _set("_fake_accuse_count", int(getattr(self, "_fake_accuse_count", 0) or 0) + 1)
            ps, ns, intent = self.state, self.state, "FAKE_ACCUSE"
        elif reply_policy and reply_policy.is_distrust(
                t, in_trust_ctx=bool(getattr(self, "_trust_ctx", False))):
            # ① 不信任三级处置：第 1 次自证 + 询问、第 2 次最后警告、第 3 次转人工收线。
            #    疑问式质疑（怎么证明/官方吗）不进这里，仍走原自证链。
            dc = int(getattr(self, "_distrust_count", 0) or 0) + 1
            _set("_distrust_count", dc)
            _set("_trust_ctx", True)
            _set("_last_flag", {"kind": "distrust", "count": dc, "text": t[:50]})
            if dc >= 3:
                _set("non_task_end", True)
                _set("_close_reason", "DISTRUST_TRANSFER")
                ps, ns, intent = E5, END, "DISTRUST_TRANSFER"
            else:
                intent = "TRUST_REASK" if dc == 1 else "TRUST_WARN"
                ps, ns = self.state, self.state
        elif (reply_policy and self.state not in ("PRE", S1, END)
              and reply_policy.is_agent_identity_doubt(t)):
            # 11/15 真机修复④：商家质疑**数字员工本人**身份真实性。
            # 旧逻辑只在 S1 段用 identity_kind 处理；答疑段这些话会被
            # findfault_hit 判成找茬、被 redline 判成辱骂(ABUSE)，或落成
            # HESITANT「哪一部分还有疑虑」—— 全部答非所问。
            # 统一自证 + 给官方核实渠道；第 3 次仍不放心则转人工收线。
            # S1 保持原有 ID_AGENT_QUERY 链路（自证后回到身份确认）。
            ac = int(getattr(self, "_agent_identity_count", 0) or 0) + 1
            _set("_agent_identity_count", ac)
            _set("_last_flag", {"kind": "agent_identity", "count": ac, "text": t[:50]})
            if ac >= 3:
                _set("non_task_end", True)
                _set("_close_reason", "DISTRUST_TRANSFER")
                ps, ns, intent = E5, END, "DISTRUST_TRANSFER"
            else:
                ps, ns, intent = self.state, self.state, "AGENT_IDENTITY"
        elif reply_policy and self.state == S3 and reply_policy.findfault_hit(t):
            # ① 找茬计数（答疑段）：拒绝整改 / 质疑类表态累计 3 次 → 礼貌收线，
            #    标记非任务结束、不计业务闭环（昨天版「三次拦截」口径）。
            ff = int(getattr(self, "_findfault_count", 0) or 0) + 1
            _set("_findfault_count", ff)
            _set("_last_flag", {"kind": "pushback", "count": ff,
                                "ftype": reply_policy.findfault_hit(t), "text": t[:50]})
            if ff >= 3:
                _set("non_task_end", True)
                _set("_close_reason", "PUSHBACK_CLOSE")
                ps, ns, intent = E5, END, "PUSHBACK_CLOSE"
            else:
                ps, ns, intent = S3, S3, "FINDFAULT_ACK"
        elif reply_policy and reply_policy.is_trust_confirm(t) \
                and not (reply_policy.no_more_plain(t) if reply_policy else False):
            # ① 商家表示愿意相信 → 清「等表态」窗口，回到业务推进。
            # 11/19：「行吧行吧，没有了」这类话先撞上本分支，把「没有了」吞掉
            # 又重问一遍「还有没有疑问」——no_more 优先于信任承接。
            _set("_trust_ctx", False)
            if self.state == S1:
                # 11/19：「行行行吧你说吧我信了」= 口头认下 → 与 AFFIRM 同路推进。
                # 实测旧逻辑只回「好的，感谢您的信任。」停在 S1：要素不亮、
                # 自动续讲不触发，商家得追着说「你倒是说呀」才动。
                _set("_s1_denials", 0)
                _set("_s1_weak", 0)
                _set("_s1_stall", 0)
                _set("_name_challenged", False)
                # 【11/21 环节回跳】身份重新认下 → 跳到最近的未完成环节继续；
                # 全部完成直接收尾，绝不把已讲过的环节重跑一遍（用户硬口径）。
                _ns, _it = self._reconfirm_advance()
                if _ns == S2:
                    ps, ns, intent = S1, S2, _it      # 情况说明没讲过：单步承接（原行为）
                elif _ns == END:
                    ps, ns, intent = S4, END, _it     # 全部完成：礼貌收尾
                else:
                    ps, ns, intent = _ns, _ns, _it    # 跳到最近的未完成环节继续确认
            else:
                ps, ns, intent = self.state, self.state, "TRUST_OK"
        elif reply_policy and reply_policy.is_compliment(t) \
                and self.state not in ("PRE", S1, END):
            # 11/19：商家夸 AI（「没开玩笑啊，我夸你聪明」实测回「这个我这边不方便
            # 展开」，傻气透顶）——优雅致谢接住，不落闲聊计数、不拉回、不收线。
            _set("_last_flag", {"kind": "compliment", "text": t[:50]})
            ps, ns, intent = self.state, self.state, "COMPLIMENT"
        elif vio.get("hit"):
            key = vio["key"]
            if key == "OFF_TOPIC":
                if self.state == S1:
                    # 11/19：身份没确认前没有"业务"可拉回，任何闲聊/寒暄/情绪表达
                    # 都只能接住情绪后回到身份确认（VERIFY_WARM），不再弹生硬的
                    # 「回到资质审核」拉回卡；同样计入 S1 总僵局计数，≥5 转人工收线。
                    stall = int(getattr(self, "_s1_stall", 0) or 0) + 1
                    _set("_s1_stall", stall)
                    _set("_last_flag", {"kind": "s1_offtopic", "count": stall, "text": t[:50]})
                    if stall >= 5:
                        _set("non_task_end", True)
                        _set("_close_reason", "NOT_CONTACT")
                        ps, ns, intent = S1, END, "NOT_CONTACT"
                    else:
                        ps, ns, intent = S1, S1, "VERIFY_WARM"
                else:
                    # ① 闲聊分级（昨天版口径）：1~2 次拉回、第 3 次闲聊警告、第 4 次收线；
                    #    闲聊不算红线违规 → 不记 violation、不计红线 KPI。
                    oc = int(getattr(self, "_offtopic_count", 0) or 0) + 1
                    _set("_offtopic_count", oc)
                    _set("_last_flag", {"kind": "offtopic", "count": oc, "text": t[:50]})
                    if oc >= 4:
                        _set("non_task_end", True)
                        _set("_close_reason", "OFFTOPIC_CLOSE")
                        ps, ns, intent = E5, END, "OFFTOPIC_CLOSE"
                    else:
                        ps, ns, intent = self.state, self.state, (
                            "OFFTOPIC_PULL" if oc <= 2 else "OFFTOPIC_WARN")
            elif key == "REFUSE_CALL":
                # 商家明确拒接：尊重意愿、礼貌收线（不算严重违规，只是非任务结束）
                _set("non_task_end", True)
                _set("_close_reason", "MERCHANT_REFUSE")
                ps, ns, intent = E5, END, "MERCHANT_REFUSE"
            elif vio.get("severe"):
                # 分支 C：同一违规屡教不改 → 打【严重违规】标签、转人工并结束通话
                _set("severe_violation", True)
                _set("non_task_end", True)
                _set("_close_reason", "VIOLATION_SEVERE_" + key)
                ps, ns, intent = E5, END, "VIOLATION_SEVERE"
            else:
                # 分支 A 的起点 / 分支 B：先暂停业务纠正，讲完再把话题拉回主线
                ps, ns, intent = E5, self.state, "VIOLATION"
        elif self._await_ack and self._is_ack(t):
            # 分支 A：商家认错 → 记「✅ 已认识到问题」，AI 主动拉回主线并追问业务疑问
            _set("_vio_ack", True)
            if self.qa_confirmed:
                # 答疑环节早已确认过「没问题」，再问一遍就成了复读机 → 直接回到意愿确认
                ps, ns, intent = S4, S4, "VIO_ACK_WILL"
            elif self._explain_delivered():
                ps, ns, intent = S3, S3, "VIO_ACK"
            else:
                ps, ns, intent = self.state, self.state, "VIO_ACK_BACK"
        elif self._await_reschedule_time and tm_is_date and self.state == S4:
            # 刚问完「您看哪天方便」，商家答「后天」——哪怕这句是打断进来的（interrupted），
            # 也必须当成改约答案接住，不能落到 E1「您说」或终态被判成"听不懂"。
            resched_at = tm_expr
            ps, ns, intent = S4, END, "RESCHEDULE_CONFIRMED"
        elif human or angry:
            # 合规要求：要人工 / 情绪激烈要礼貌引导并结束。
            # 但这类结束不是业务闭环，必须显式标记，防止被算成成功工单。
            # 注：关单判定的升级放在 commit_reply（预览路径绝不能改实例状态）。
            reason = "HUMAN_REQUEST" if human else "EMOTIONAL"
            _set("non_task_end", True)
            _set("_close_reason", reason)
            if self.state == S1:
                # 11/15 真机修复③：身份尚未确认，FIXED_EMOTIONAL 会把
                # 「驳回原因是{scenario}」直接播出去 —— 违反「确认身份前
                # 不得透露驳回详情」。改用不披露业务信息的固定收线话术。
                ps, ns, intent = S1, END, ("ID_HUMAN" if human else "ID_COMPLAINT")
            else:
                ps = E3
                ns, intent = END, reason
        elif interrupted and not t.strip() and self.state not in ("PRE", END):
            # 【11.21-11 抢答修复】打断不再吞掉商家的话。
            # 旧逻辑：只要 was_playing（商家开口时 AI 还在播报）就把这句话整句丢进
            # E1/INTERRUPTED —— 内容不参与语义判定、不点任何槽位灯、状态原地不动。
            # 真机后果：开场白期间插话的「我是」点不亮身份灯，之后一路被判 VERIFY_AGAIN
            # 重问身份、重走流程；抢答的「我会改明天改」触发不了收尾。这正是用户口述的
            # 「第一句打不断 + 抢答后倒回身份确认」同一根因（窄补丁见改约分支的注释）。
            # 打断在服务端已通过 interrupt_tts + clear 让 AI 闭嘴，文本层只需正常处理：
            # 要么在这之前命中 bye/hangup/no_day_free 等阶段无关分支，要么落到下方
            # S1/S2/S3/S4 正常路由，照常点灯、推进、收尾。仅在「纯打断、一个字都没有」
            # 时才保留 E1 先接话（商家只用嗯一声让 AI 停、还没说内容）。
            ps, ns, intent = E1, self.state, "INTERRUPTED"
        elif self.state == S1:
            # 11/12 修正（衔接 bug#2 身份确认不得抢跑）：任何显式否认都优先判为未接通，
            # 不再被「负责人」等肯定关键词反转为确认。
            # ③ 功能补丁（11/15）：改用 reply_policy.identity_kind 细分判定——
            #   · 肯定自证（含「商家/店主」）优先于否认（「不是，我就是商家本人」不误挂）；
            #   · 代接/等待/复读/质疑身份等细分应答走 IDENTITY_REPLIES 固定话术；
            #   · 商家反查「我姓什么」当轮只念姓氏、不推进，下一轮不否认才认下；
            #   · 全程绝不泄露驳回明细（合规红线：确认身份前不得透露）。
            ik = ik_early   # 复用开头已算好的 identity_kind（含 AGENT_QUERY 扩词）
            # 11/21：S1 反查也认「你们要联系谁/负责人是姓啥」（登记信息反问）
            _name_ch = bool(reply_policy and (reply_policy.is_name_challenge(t)
                                              or reply_policy.is_contact_ask(t)))
            # 11/19 总僵局计数：只要本轮没确认身份就 +1（闲聊/反问/含糊/否认全算），
            # 累计 ≥5 直接登记转人工收线。denials/weak 各自的口径保留不变，
            # 但混合踢皮球（问一句、否认一句、寒暄一句轮着来）再也拖不垮流程。
            stall = int(getattr(self, "_s1_stall", 0) or 0) + 1
            _set("_s1_stall", stall)
            if _name_ch:
                # 反查身份：当轮只念工单登记的对接人，不推进、不透露驳回信息；
                # 下一轮只要不否认/不换人/不代接/不投诉/不要求重听，即视为本人认下。
                _set("_name_challenged", True)
                ps, ns, intent = S1, S1, "NAME_CHALLENGE"
            elif (ik == "AFFIRM" or (not ik and affirm_strong and not negative)
                    # 【11/22】模型判为「让 AI 直接说正事」（有事/你有啥事/你说就行了）：
                    # 这不是含糊，是催你往下说。旧逻辑只认关键词，于是连说四遍同一句
                    # 「请问您是负责人吗」，真人早挂了。这里按口头认下处理。
                    or (_sem_from_model and semantic_intent == "proceed")):
                # 【单步推进·第一铁律】这一轮**只点亮【身份确认】**：
                # 只做「收到确认」的承接，绝不把驳回原因/整改动作塞进同一回合，
                # 情况说明留到下一轮（AI 主动续讲）再播、再点亮。
                _set("_s1_denials", 0)
                _set("_s1_weak", 0)
                _set("_s1_stall", 0)
                _set("_name_challenged", False)
                # 【11/21 环节回跳】同上：重核通过 → 跳最近未完成环节/收尾，绝不重跑
                _ns, _it = self._reconfirm_advance()
                if _ns == S2:
                    ps, ns, intent = S1, S2, _it      # 情况说明没讲过：单步承接（原行为）
                elif _ns == END:
                    ps, ns, intent = S4, END, _it     # 全部完成：礼貌收尾
                else:
                    ps, ns, intent = _ns, _ns, _it    # 跳到最近的未完成环节继续确认
            elif ik in ("COMPLAINT", "HUMAN", "ENTITY_CHANGED"):
                _set("non_task_end", True)
                _set("_close_reason", ik)
                ps, ns, intent = S1, END, "ID_" + ik
            elif stall >= 5:
                # 总僵局闸门：连续 5 轮拿不到明确确认 → 不再陪踢皮球，
                # 登记备注 + 转人工收线（人性化兜底，用户口径 11/19）。
                _set("non_task_end", True)
                _set("_close_reason", "NOT_CONTACT")
                ps, ns, intent = S1, END, "NOT_CONTACT"
            elif ik in ("DEPUTY", "FETCH_OWNER", "REPEAT", "AGENT_QUERY", "REFUSE_VERIFY"):
                ps, ns, intent = S1, S1, "ID_" + ik
            elif getattr(self, "_name_challenged", False) and not ik and not negative:
                # 反查后的下一轮：没有否认/换人/代接/投诉/重听 → 认下进情况说明。
                _set("_s1_denials", 0)
                _set("_s1_weak", 0)
                _set("_s1_stall", 0)
                _set("_name_challenged", False)
                # 【11/21 环节回跳】同上：重核通过 → 跳最近未完成环节/收尾，绝不重跑
                _ns, _it = self._reconfirm_advance()
                if _ns == S2:
                    ps, ns, intent = S1, S2, _it      # 情况说明没讲过：单步承接（原行为）
                elif _ns == END:
                    ps, ns, intent = S4, END, _it     # 全部完成：礼貌收尾
                else:
                    ps, ns, intent = _ns, _ns, _it    # 跳到最近的未完成环节继续确认
            elif ik == "DENY" or negative:
                _set("_name_challenged", False)
                denials = self._s1_denials + 1
                _set("_s1_denials", denials)
                _set("_s1_weak", 0)
                if denials >= 2:
                    _set("non_task_end", True)
                    _set("_close_reason", "NOT_CONTACT")
                    ps, ns, intent = S1, END, "NOT_CONTACT"
                else:
                    ps, ns, intent = S1, S1, "NOT_CONTACT"
            else:
                # 既没肯定也没否认（「喂 / 嗯 / 啊」）→ 再确认一次，绝不打勾。
                # 反查之后不再重复「请问您是…负责人吗」（念过姓氏再问就是二次确认）。
                weak = self._s1_weak + 1
                _set("_s1_weak", weak)
                if weak >= 4:
                    _set("non_task_end", True)
                    _set("_close_reason", "NOT_CONTACT")
                    ps, ns, intent = S1, END, "NOT_CONTACT"
                elif getattr(self, "_name_challenged", False):
                    _set("_name_challenged", False)
                    ps, ns, intent = S1, S1, "NAME_CHALLENGE"
                else:
                    ps, ns, intent = S1, S1, "VERIFY_AGAIN"
        elif self.state == S2:
            # 情况说明段：本轮把「驳回原因 + 整改动作」一轮说完并问有无疑问，
            # 播完立刻点亮【情况说明】；【商家答疑】要等商家下一轮明确说没问题。
            if not self._rejection_delivered:
                ps, ns, intent = S2, S3, "REJECTION_DELIVER"
            elif semantic_intent == "commitment":
                # 商家已表态会去做（哪怕是"我睡一觉起来就改"）→ 视为已理解
                ps, ns, intent = S2, S3, "UNDERSTOOD"
            elif semantic_intent == "confirmed_understood":
                ps, ns, intent = S2, S3, "UNDERSTOOD"
            elif semantic_intent == "hesitant":
                ps, ns, intent = S2, S2, "HESITANT"
            else:
                ps, ns, intent = S2, S2, "NEED_CLARIFICATION"
        elif self.state == S3:
            # 商家答疑段：**只有大模型判定「明确理解且无新问题」才点亮并推进**。
            # 追问/转折 → 正面解答（停留）；含糊 → 主动追问（停留）；连续 3 次含糊 → 转人工/结束。
            #
            # 【11/22 未知词台账】「拆楼是啥」这类求解释句式，如果被问的词既不是已知业务词、
            # 知识库也查不到（很可能是 ASR 听错），就记进台账：
            #   第 1 次 → 正常走答疑（提示词红线第 9 条会要求模型反问确认，不许编定义）；
            #   第 2 次起 → 直接如实说"这个词我这边确实没有相关信息"，不再让模型瞎猜。
            # 每条记录都会出现在 RSI「待审核」里，审核通过后自动进 term_map 并对下一通电话生效。
            _unk = None
            try:
                _term = unknown_terms.extract_term(t)
                if _term and not unknown_terms.is_known(_term):
                    _unk = unknown_terms.note(_term, t, self.call_id)
                    _set("_unknown_term", _unk)
            except Exception:
                _unk = None
            if unknown_terms.should_admit(_unk):
                ps, ns, intent = S3, S3, "UNKNOWN_ADMIT"
            elif not self.qa_asked and not self.inbound:
                # 异常兜底：若尚未主动问过「有无疑问」，先问一次（问的时候绝不打勾）
                # 11/19：来电场景没有这个环节——热线开场就是"有什么可以帮您"，
                # 商家的第一句话就是问题本身，直接进答疑，不要再反问。
                ps, ns, intent = S3, S3, "QA_ASK"
            elif reply_policy and (reply_policy.no_more_plain(t)
                                   or (self.direction != "inbound" and reply_policy.will_promise_hit(t))):
                # ⑤⑥ 功能补丁（11/15）：「没了没了 / 没有没有 / 没乐没乐 / 都明白了」
                # 就是明确表示没有疑问——直接推进整改意愿确认，
                # 严禁再问「还有没有疑问」（复读机），也不能默认算答应整改。
                # 11/19：「没啥疑问了，我现在就去提交」= 没疑问 + 整改承诺一句话说完，
                # will_promise 命中同样推进（实测这句卡在答疑段被当闲聊/含糊，永远进不了意愿确认）。
                # 来电场景：商家说没了/谢谢 → 直接礼貌收线（没有意愿确认环节）。
                if self._post_end or self.inbound:
                    ps, ns, intent = S3, END, "CLOSING"
                else:
                    ps, ns, intent = S3, S4, "NO_MORE"
            elif reply_policy and reply_policy.is_go_ahead(t):
                # 【11/22 真机修复·答疑段催促】商家说「说吧 / 你倒是说 / 继续说」= 让 AI 接着讲。
                # 翻车现场（13:29:18）：答疑段里商家说「说吧说吧」，被语义标签判成 hesitant，
                # AI 反问「您看是哪一部分还有疑虑？」——商家刚让你说，你又问他哪不明白。
                # is_go_ahead 早就有（S1 用它口头认下身份），这里补上 S3 的接线：
                # 把「驳回原因 + 整改动作」再讲一遍，停在答疑段（不推进、不打勾）。
                _set("hesitant_count", 0)
                ps, ns, intent = S3, S3, "EXPLAIN_REDELIVER"
            elif semantic_intent == "commitment":
                # 【11/22】商家在答疑段就表态「会去做」（如「我睡一觉起来就改」）：
                # 视为已理解 + 有整改意愿，直接推进意愿确认（不再问「还有没有疑问」）。
                _set("hesitant_count", 0)
                if self._post_end or self.inbound:
                    ps, ns, intent = S3, END, "CLOSING"
                else:
                    ps, ns, intent = S3, S4, "NO_MORE"
            elif semantic_intent == "confirmed_understood":
                if self._post_end or self.inbound:
                    # 【结束语之后回来答疑】④⑤早就在收尾前完成了，再问一遍就是复读机
                    # → 商家说没问题了就直接再收尾，绝不回到「整改意愿确认」。
                    # 来电场景：商家说明白了 → 热线服务完成，礼貌收线。
                    ps, ns, intent = S3, END, "CLOSING"
                else:
                    ps, ns, intent = S3, S4, "QA_CONFIRMED"
            elif reply_policy and reply_policy.qa_forgot(t):
                # 11/19：「我忘了」（忘了刚才想问啥）——不是闲聊！
                # 商家是接「还有没有疑问」时忘了自己的问题，正确行为是
                # 主动把驳回原因和整改方法再讲一遍，绝不回「不方便展开」。
                _set("hesitant_count", 0)
                ps, ns, intent = self.state, self.state, "QA_FORGOT"
            elif reply_policy and reply_policy.qa_has_question(t):
                # 11/19：商家回答「您还有没有疑问」时表示**有**（有有有/有的/我有个问题）。
                # 实测「有有有」被判闲聊拉回——商家根本没法说出自己的问题。
                # 正确行为：邀请继续说（本轮不推进，等商家把问题说完再答）。
                _set("hesitant_count", 0)
                ps, ns, intent = S3, S3, "QA_HAS"
            elif semantic_intent == "hesitant":
                if self.hesitant_count >= 2:          # 本轮为第 3 次 → 终止，绝不无限循环
                    _set("non_task_end", True)
                    _set("_close_reason", "HESITANT_LOOP")
                    ps, ns, intent = S3, END, "HESITANT_LOOP"
                else:
                    ps, ns, intent = S3, S3, "HESITANT"
            else:
                # new_question / other：先正面解答，解答完再问是否还有别的问题（不推进、不打勾）
                ps, ns, intent = S3, S3, "NEED_CLARIFICATION"
        elif self.state == S4:
            # 整改意愿确认段：no_time 是**事实提取**（改约），其余推进/停留由语义标签决定。
            # ⑤ 功能补丁（11/15）：no_time 改用正则判定，容许「不太 / 不大 / 不怎么」
            # 插进「不方便」（旧子串匹配漏判「不太方便」，会被 will_yes 误判或判成含糊）。
            no_time = ((reply_policy.no_time_hit(t) if reply_policy else False)
                       or any(k in t for k in (
                "没时间", "没空", "没的空", "抽不出", "抽不出时间", "腾不出", "没工夫", "没功夫",
                "不方便", "去不了", "忙不过来", "今天不行", "改天", "以后再说", "等有空",
                "暂时不", "暂时没", "下次", "过几天", "很忙", "太忙", "最近忙",
                "延后", "往后推", "缓一缓")))
            # 商家对「方便尽快去京麦后台重新提交吗」的肯定答复：明确承诺才点亮环节5。
            # 只要句子里带疑问/不懂语气（"我不懂为什么要整改"），就不算承诺——
            # 否则光凭出现"整改"两个字就收尾，正是"听不懂还硬推进"的老毛病。
            ask_like = (any(k in t for k in ("吗", "？", "?", "为什么", "怎么", "哪里", "什么",
                                             "如何", "咋", "不懂", "不明白", "不清楚", "哪天",
                                             "几号", "哪天方便"))
                        and not any(k in t for k in ("这就去", "去改", "去提交", "马上改")))
            # 11/15 真机修复④：「我马上去 / 现在就去 / 我自己去」等落实型承诺，
            # 旧词表整类漏判，被 LLM 标签拖进 hesitant → 复读追问 → HESITANT_LOOP 收线。
            promise = (reply_policy.will_promise_hit(t) if reply_policy else False)
            will_yes = (not ask_like) and (promise or any(k in t for k in (
                "这就去", "马上改", "去改", "去提交", "会提交", "重新提交", "去办", "去准备",
                "会的", "没问题", "可以", "好的", "行", "尽快", "尽快改",
                "提交", "整改", "补上", "补齐",
                # 11/21 真机补充：AI 现场换了问法（「您现在方便操作吗」），商家答
                # 「嗯，方便的。」——"方便"就是肯定应答，必须点亮意愿收尾。
                # 绝不多问一句「打算自己提交还是有什么不方便」（多此一问）。
                # 否定形态「不方便/不太方便」在上面 no_time 分支已被拦下，到不了这里。
                "方便", "有空了", "腾出时间了")))
            will_no = any(k in t for k in ("不去", "不提交", "不改", "改不了", "提交不了", "不会",
                                           "没法", "没办法", "不能提交", "做不到"))
            # 【改约时间识别】"最近没空"只是流程开始，绝不当成环节⑤已完成：
            #   ① 给了日期（后天 / 下周一 / 11月20日）→ 改约确认，复述时间后收尾
            #   ② 只给时段（下午吧）→ 继续问哪天
            #   ③ 只说没空不给时间 → 先问"哪天方便"，本轮不点灯、不播结束语
            tm, tm_has_date = tm_expr, tm_is_date
            # 11/15 真机修复④：明确承诺整改 = 环节⑤已完成，直接确认收尾。
            # 必须排在「给了日期 → 改约」之前，否则「我明天就去」会被当成"没空改约"，
            # 又绕回不点亮意愿。此处也与语义标签解耦，离线/线上结果一致。
            if promise and not will_no and not no_time:
                ps, ns, intent = S4, END, "WILL_CONFIRMED"
            elif tm and tm_has_date:
                resched_at = tm
                ps, ns, intent = S4, END, "RESCHEDULE_CONFIRMED"
            elif tm and not tm_has_date:
                ps, ns, intent = S4, S4, "RESCHEDULE_WHICH_DAY"
            elif no_time:
                # 说了没空就是**改约**，一律先问时间：哪怕句子里带了"提交"这类动词
                # （"最近不方便去后台提交"），也不许当成承诺直接收尾。
                if self._reschedule_asked >= 2:
                    # 问过两次还不给时间：礼貌收线，标记非任务结束（不算闭环），不僵持
                    _set("non_task_end", True)
                    _set("_close_reason", "RESCHEDULE_NO_TIME")
                    ps, ns, intent = S4, END, "RESCHEDULE_NO_TIME"
                else:
                    ps, ns, intent = S4, S4, "RESCHEDULE_ASK"
            elif semantic_intent == "commitment":
                # 【11/22】「我睡一觉起来就改」「明早去提交」这类口语承诺 → 直接闭环。
                # 旧逻辑只认 will_yes 词表里的固定说法，口语承诺全掉进 hesitant，
                # AI 反问「您打算自己提交还是有哪里不方便」——商家刚答应，你又问他愿不愿意。
                ps, ns, intent = S4, END, "WILL_CONFIRMED"
            elif semantic_intent == "confirmed_understood" or (will_yes and not will_no):
                ps, ns, intent = S4, END, "WILL_CONFIRMED"
            elif semantic_intent == "hesitant":
                if self.hesitant_count >= 2:
                    _set("non_task_end", True)
                    _set("_close_reason", "HESITANT_LOOP")
                    ps, ns, intent = S4, END, "HESITANT_LOOP"
                else:
                    ps, ns, intent = S4, S4, "HESITANT"
            else:
                ps, ns, intent = S4, S4, "WILL_CLARIFY"
        elif self.state == S5:
            ps, ns, intent = S5, END, "COMPLETED"
        else:
            ps, ns, intent = END, END, "COMPLETED"

        # 11/19 来电守卫：热线咨询 + 商家未开口的空文本轮 → 固定重问候，
        # 覆盖状态机给出的任何 intent（防止 LLM / 状态机把热线带回外呼剧本）。
        if inbound_regreet:
            ps, ns, intent = self.state, self.state, "INBOUND_REGREET"
        return self._apply_gates(ps, ns, intent, bool(t.strip()), commit, aux,
                                 time_expr=resched_at)

    def _apply_gates(self, ps: str, ns: str, intent: str, has_user_text: bool,
                     commit: bool = True, aux: Optional[dict] = None,
                     time_expr: Optional[str] = None):
        """轮次闸门：保证 3–6 轮连续对话，且状态机不原地打转。

        罗 11/12：commit=False 时 _stall 的变更写入 aux 而不落实例，
        并用 aux 里预计算的 non_task_end 参与判定（预览用未来值）。
        """
        def _set(name: str, value) -> None:
            if commit:
                setattr(self, name, value)
            elif aux is not None:
                aux[name] = value

        if aux is None:
            aux = {}

        # 【严格时序闸门 · 只此一处】「商家答疑」绝不允许抢在「情况说明」前面点亮。
        # 判定依据是**本轮之前**的真实播报内容（history 此时还不含本轮回复）：
        # 只要驳回原因/整改动作还没真正交付过，就不许把 qa_confirmed 打上，
        # 改为本轮先把情况说明完整补交付（EXPLAIN_REDELIVER），下一轮再确认答疑。
        # 这样「情况说明」与「商家答疑」永远至少隔一轮，进度条不会两个环节同时变绿。
        if intent == "QA_CONFIRMED":
            if not self._explain_delivered():
                ps, ns, intent = S3, S3, "EXPLAIN_REDELIVER"
        # 【单步推进·身份确认】只认商家明确肯定；本轮**只**点亮「身份确认」，
        # 情况说明留到下一轮 AI 主动续讲时再播、再点亮（严禁两灯同回合）。
        # 【11/21 环节回跳】重核通过后的跳转意图（JUMP_*）同样要点亮身份灯——
        # 否则收尾闸门/_prospective_done 仍判身份未确认，永远回不去。
        if intent in ("CONFIRMED", "CONFIRMED_ONLY",
                      "RECONFIRM_JUMP_QA", "RECONFIRM_JUMP_WILL", "RECONFIRM_JUMP_END"):
            _set("identity_confirmed", True)
            _set("hesitant_count", 0)
        # 情况说明已播报（驳回原因+整改方式真实交付）→ 标记，本轮的「有无疑问」也算已问过；
        # 但**绝不在这一轮点亮【商家答疑】** —— 要等商家明确说没问题（下一轮）。
        if intent == "REJECTION_DELIVER":
            _set("_rejection_delivered", True)
            _set("qa_asked", True)
        elif intent == "EXPLAIN_REDELIVER":
            _set("_rejection_delivered", True)   # 补交付同样算「情况说明已播报」，但**不点答疑灯**
            _set("qa_asked", True)
        elif intent == "QA_ASK":
            _set("qa_asked", True)              # 问「有无疑问」的这一刻绝不打勾
        elif intent == "RECONFIRM_JUMP_QA":
            # 【11/21 环节回跳】重核通过后跳问「有无疑问」：同样只记问过、绝不打勾
            _set("qa_asked", True)
        # 商家答疑确认无疑问（SSL 语义标签 confirmed_understood 才走到这里）：点亮「商家答疑」
        elif intent == "QA_CONFIRMED":
            _set("qa_confirmed", True)
            _set("hesitant_count", 0)
        elif intent == "NO_MORE":
            # ⑤⑥ 功能补丁（11/15）：商家明确说「没了 / 都明白了」→ 环节④（商家答疑）
            # 一并点亮。否则收尾闸门会发现④缺失，把流程拉回 S3 再问一遍
            # 「还有没有疑问」——商家都说没了还问，正是要消灭的复读。
            _set("qa_confirmed", True)
            _set("qa_asked", True)
            _set("hesitant_count", 0)
        elif intent == "NEED_CLARIFICATION" and self.inbound:
            # 11/19：来电场景商家真的问过一个问题并得到解答 → 咨询答疑环节点亮
            _set("qa_asked", True)
        elif intent == "VIO_ACK":
            # 分支 A 收尾：已把话题拉回主线并追问业务疑问 → 算已问过（但不打勾）
            _set("_await_ack", False)
            _set("qa_asked", True)
            _set("hesitant_count", 0)
        elif intent == "VIO_ACK_WILL":
            _set("_await_ack", False)
            _set("hesitant_count", 0)
        elif intent == "VIO_ACK_BACK":
            _set("_await_ack", False)
            _set("hesitant_count", 0)
        elif intent == "VIOLATION":
            # 刚教育完，进入「等商家表态」窗口：认错→分支A；再提违规→分支B；都不理→回主线
            _set("_await_ack", True)
        elif intent in ("VIOLATION_SEVERE", "MERCHANT_REFUSE"):
            _set("_await_ack", False)
        elif intent in ("NEED_CLARIFICATION", "WILL_CLARIFY", "UNDERSTOOD"):
            _set("hesitant_count", 0)           # 商家在积极互动 → 重置含糊计数
        elif intent == "HESITANT":
            _set("hesitant_count", self.hesitant_count + 1)
        # 整改意愿确认：承诺整改 → will_confirmed；没时间改约 → will_rescheduled（同样算完成）
        elif intent == "WILL_CONFIRMED":
            _set("will_confirmed", True)
            _set("hesitant_count", 0)
        elif intent == "WILL_RESCHEDULE":
            _set("will_rescheduled", True)
            _set("hesitant_count", 0)
        # 【改约两段式】没空 ≠ 完成：
        #   问时间（RESCHEDULE_ASK）→ 不点灯、不推进，只开一个「等商家给日期」的窗口；
        #   商家给出日期（RESCHEDULE_CONFIRMED）→ 才算环节⑤完成，并把时间记进复盘。
        elif intent == "RESCHEDULE_ASK":
            _set("_await_reschedule_time", True)
            _set("_reschedule_asked", self._reschedule_asked + 1)
            _set("hesitant_count", 0)
        elif intent == "RESCHEDULE_WHICH_DAY":
            _set("_await_reschedule_time", True)
            _set("_reschedule_asked", self._reschedule_asked + 1)
            _set("hesitant_count", 0)   # 商家给了时段，是在积极互动，不是含糊
        elif intent == "RESCHEDULE_CONFIRMED":
            _set("_await_reschedule_time", False)
            _set("reschedule_at", time_expr)
            _set("will_rescheduled", True)
            _set("hesitant_count", 0)
        elif intent == "RESCHEDULE_NO_TIME":
            _set("_await_reschedule_time", False)

        nt_end = self.non_task_end or (bool(aux.get("non_task_end")) if aux else False)
        merchant_after = self.merchant_turns + (1 if has_user_text else 0)
        # 安全上限（纯防御，防自动化评测死循环）：命中即优雅收线并标记为非任务结束，
        # 绝不计入成功闭环；正常多轮反问/答疑远到不了这里，因此不会「因商家多问几句就挂断」。
        if merchant_after >= MAX_TURNS_HARD and ns != END and not nt_end:
            _set("non_task_end", True)
            _set("_close_reason", "MAX_TURNS")
            ns, intent = END, "MAX_TURNS"
            ps = S4 if ps not in (S4, S5, E3, E4) else ps
            # 上限收线属于「非任务结束」，重算一次，后面的收尾闸门不能再把它拦回来
            nt_end = True
        # 3 轮下限：不足 3 轮商家发言不许收尾（要人工/情绪激烈等非任务结束除外）
        # 11/15：商家已明确道别（BYE）时，所有「留客」闸门一律放行 ——
        # 不因不足 3 轮 / 五环节没补齐就强行把通话拉回业务答疑。
        if ns == END and intent != "BYE" and not nt_end and merchant_after < MIN_TURNS_TO_CLOSE \
                and not self.inbound:
            # 收尾被轮次闸门拦下时，必须把「本轮的完成标志」撤回，
            # 否则进度条会先亮到 5/5、电话却还在继续（虚高）。
            if intent == "WILL_CONFIRMED":
                _set("will_confirmed", False)
                intent = "WILL_ASK"
            elif intent == "WILL_RESCHEDULE":
                _set("will_rescheduled", False)
                intent = "WILL_ASK"
            elif intent == "RESCHEDULE_CONFIRMED":
                # 轮次不够收尾 → 退回「继续问时间」，绝不假装已经改约成功
                _set("will_rescheduled", False)
                _set("reschedule_at", None)
                _set("_await_reschedule_time", True)
                intent = "RESCHEDULE_ASK"
            ns = S2 if self.state in ("PRE", S1, S2) else S4
            ps = ns
            if intent not in ("WILL_ASK", "RESCHEDULE_ASK"):
                intent = "FORCED_ADVANCE"
        # 红线「等表态」窗口只维持一轮：这一轮既没认错也没再提违规（回到业务）就自动关闭
        if self._await_ack and intent not in ("VIOLATION",) and not intent.startswith("VIO_ACK"):
            _set("_await_ack", False)
        # 【收尾闸门 · 用户硬规则】五环节没全部完成，绝不允许播结束语、也不允许给所有节点打勾。
        # 缺哪个环节就回到哪个环节补，而不是「说一句再见 + 补要素」把进度条一次拉满。
        # 11/19：来电是热线咨询，没有外呼五环节，商家说没了/再见即收线。
        if ns == END and intent != "BYE" and not nt_end and not self.inbound:
            done = self._prospective_done(aux)
            missing = [i for i in (1, 2, 3, 4, 5) if not done[i]]
            if missing:
                m = missing[0]
                ns = {1: S1, 2: S1, 3: S2, 4: S3, 5: S4}[m]
                ps = ns
                intent = {1: "VERIFY_AGAIN", 2: "VERIFY_AGAIN", 3: "REJECTION_DELIVER",
                          4: "QA_ASK", 5: "WILL_ASK"}[m]
        # 同状态原地打转 ≥2 轮 → 强制推进到下一阶段
        if ns == self.state and ns not in ("PRE", END):
            stall = self._stall + 1
            if stall >= MAX_STALL_ROUNDS:
                ns = ADVANCE_STATE.get(ns, S4)
                ps = S4 if ns == END else ns
                intent = "FORCED_ADVANCE"
                _set("_stall", 0)
            else:
                _set("_stall", stall)
        else:
            _set("_stall", 0)
        # 红线计数落账（同类第几次 / 卡片编号）：预览写 aux、正式提交写实例
        v = (aux or {}).get("_vio") or (self._vio if commit else None)
        if v and v.get("hit"):
            _set("_vio_count", {**self._vio_count, v["key"]: int(v.get("count") or 1)})
            _set("vio_seq", max(int(self.vio_seq), int(v.get("seq") or 0)))
        # ① 功能补丁（11/15）：特殊标记 / 闲聊计数统一由 commit_reply 落 flag_log
        #    （预览路径 _last_flag 落 aux，正式提交后追加进实例列表并清空）。
        # 「等信任表态」窗口只维持一轮：商家回到业务 / 表示相信就自动关闭
        if getattr(self, "_trust_ctx", False) and intent not in (
                "TRUST_REASK", "TRUST_WARN", "DISTRUST_TRANSFER", "TRUST_OK"):
            _set("_trust_ctx", False)
        return ps, ns, intent

    # ---------- 话术上下文与合规护栏（11/11 新增） ----------
    def _ctx(self, tpl: str) -> str:
        """填充固定话术模板里的公司 / 场景 / 关单方式 / 热线。"""
        scen = self.scenario or {}
        return tpl.format(
            company=self.merchant.get("company_name") or "贵公司",
            scenario=scen.get("level2_category") or "资质材料不符合要求",
            close_method=scen.get("close_method") or "电话沟通",
            hotline=HOTLINE)

    # ---------- 功能补丁包（11/15）新增方法 ----------
    def _is_fake_scene(self) -> bool:
        """本单驳回原因是不是**真的**「资质造假」。

        同一句「你凭什么说我提供的是假的」，两种场景两种答法：
        商标/授权类 → 商家误会，道歉澄清；资质造假类 → 讲核验依据，不否定审核结论。
        """
        scen = self.scenario or {}
        blob = " ".join(str(x or "") for x in (
            scen.get("level2_category"), scen.get("script_text"),
            scen.get("level1_category")))
        return bool(re.search(r"造假|伪造|作假|失真|修改痕迹", blob))

    def _fake_doubt_reply(self) -> str:
        """商家误会「AI 在指控自己造假」→ 道歉澄清 + 真实驳回原因拉回主线。"""
        level2 = (self.scenario or {}).get("level2_category") or "资质材料这块不满足平台要求"
        return self._ctx(reply_policy.FAKE_DOUBT_TEXT.replace("{level2}", level2))

    def _fake_found_text(self, n: Optional[int] = None) -> str:
        """真·资质造假场景的质疑答复：核验依据 + 哪份材料 + 两条出路（第 2 次起）。"""
        scen = self.scenario or {}
        level2 = re.sub(r"[（(].*?[)）]", "", str(scen.get("level2_category") or "资质造假")).strip() or "资质造假"
        level1 = scen.get("level1_category") or "相关材料"
        judge = "提交的材料与官方登记的信息不一致，材料上有修改痕迹"
        if n is None:
            n = getattr(self, "_fake_accuse_count", 0) or 0
        if int(n) >= 2:
            return self._ctx(reply_policy.FAKE_FOUND_AGAIN_TEXT)
        return self._ctx(reply_policy.FAKE_FOUND_TEXT.format(
            judge=judge, level2=level2, level1=level1))

    def _name_challenge_reply(self) -> str:
        """商家反查「你说我姓什么」→ 念工单登记的对接人姓氏；念不出就如实说没登记。

        只念姓氏/称呼，不念全名、绝不带任何驳回信息（确认身份前不得透露）。
        """
        _full, _surname, titled = reply_policy.contact_bits(
            self.merchant.get("contact_name"))
        company = self.merchant.get("company_name") or "贵公司"
        if _surname:
            return (f"您是{company}的对接人{titled}，工单上登记的就是您。"
                    f"那我们接着说这次资质审核的意见，可以吗？")
        if self.state == S1:
            return "工单上没有登记具体对接人姓名，麻烦您让贵店负责人接听，我再说明详情。"
        # 【11/21】S2+ 中途反查而工单没登记姓名：如实说明 + 接着说，
        # 绝不再要求「换负责人接听」（电话本来就是打给对接人的，前后矛盾）
        return (f"工单上没有登记具体对接人的姓名，登记的是{company}的联系方式。"
                f"那我们接着说这次资质审核的意见，可以吗？")

    def _safe_sentence(self, sentence: str, stage: int = 0) -> str:
        """输出侧禁语闸门：逐句生效，命中红线就整句换成**本阶段**的合规安全句。

        11/12 修语义断裂：旧版所有阶段都用同一句"这部分我不能向您打包票，请您按意见
        补齐材料后重新提交"，在身份确认阶段会变成「打包票 + 请问是负责人吗」的复读。
        现在按阶段取值，S1/S2/S3 讲材料、S4 讲审核结果，句子与上下文不再冲突。
        """
        if any(bad in sentence and not _phrase_negated(sentence, bad)
               for bad in FORBIDDEN_PHRASES):
            return SAFE_SENTENCE.get(stage) or SAFE_SENTENCE[2]
        return sentence

    def _fixed_reply(self, ps: str, ns: str, intent: str,
                     vio: Optional[dict] = None, aux: Optional[dict] = None) -> Optional[str]:
        """本轮的固定安全话术；返回 None 表示交给 LLM 生成。"""
        # 【11/22 修复·红线话术错位】商家明确拒接时，无论从哪条路径进来
        # （语义 hangup 走 ps=当前状态 / 红线 REFUSE_CALL 走 ps=E5），
        # 都必须播红线的收线话术并收线；否则会落进 S1 的身份确认反问，
        # 出现"识别对了、话术播错"的越拒越纠缠。
        if intent == "MERCHANT_REFUSE":
            return redline.REFUSE_TEXT
        # ---------- 功能补丁包（11/15）新增意图 ----------
        # 这些分支必须放在 ps==E5 / 收尾分支之前，否则会被 FIXED_CLOSING /
        # _correction_text 截走播错话术。
        if intent == "INBOUND_REGREET":
            return self._ctx(FIXED_INBOUND_REGREET)
        if intent == "COMPLIMENT":
            # 11/19：夸奖 → 致谢接住；S1 段仍走 VERIFY_WARM（身份没确认不能谈业务）
            if self.inbound:
                return "谢谢您的夸奖。资质审核方面的问题，您直接问我就行。"
            return ("谢谢您的认可。那咱们继续——"
                    "您对这次的驳回原因还有什么疑问吗？")
        if intent == "RECONFIRM_OK":
            # 【11/21】身份重新确认完毕：谢过 + 按当前环节接着问，后续环节灯不灭
            tail = {S2: self._ctx(FIXED_REASON),
                    S3: "您对这次驳回的原因和整改方法，还有什么疑问吗？",
                    S4: self._ctx(FIXED_WILLING)}.get(ps, "")
            return "好的，谢谢您的确认，登记的对接人没问题。咱们接着说。" + tail
        if intent == "UNKNOWN_ADMIT":
            # 【11/22】未知词第 2 次问：如实说不了解，绝不编定义（编的业务口径会直接误导商家）。
            # 这条同时兜住了"模型想猜"的冲动 —— 到这一层已经不经过模型了。
            return self._ctx(
                "这个词我这边确实没有相关信息，不能给您乱解释。"
                "您可以登录京麦后台查看驳回意见，或拨打京东商家服务热线{hotline}核实。"
                "您还有其他问题吗？")
        if intent == "QA_HAS":
            # 11/19：商家表示有疑问 → 邀请继续说，绝不能拉回闲聊口径
            return "好的，那您继续说，我听着。"
        if intent == "QA_FORGOT":
            # 11/19：商家忘了想问什么 → 主动把驳回原因和整改方法再讲一遍
            script = (self.scenario or {}).get("script_text") or "按京麦后台的驳回意见补齐材料后重新提交"
            return self._ctx("没关系，可能我刚才说得太快了。我再跟您说一遍——"
                             "驳回原因是{scenario}；" + script + "。"
                             "这回清楚了吗，还有别的想问的吗？")
        if intent == "BYE":
            # 商家已说「拜拜 / 再见」：只回一句极简收线，绝不反问
            # 「您看是哪一部分还有疑虑」，也不重复整段结束语。
            return "好的，感谢您的接听，再见。"
        if intent == "NAME_CHALLENGE":
            return self._name_challenge_reply()
        if intent and intent.startswith("ID_"):
            tpl = reply_policy.IDENTITY_REPLIES.get(intent[3:]) if reply_policy else None
            return self._ctx(tpl) if tpl else self._ctx(FIXED_VERIFY_AGAIN)
        if intent == "FAKE_DOUBT":
            return self._fake_doubt_reply()
        if intent == "FAKE_ACCUSE":
            return self._fake_found_text((aux or {}).get("_fake_accuse_count"))
        if intent == "TRUST_REASK":
            return self._ctx(reply_policy.TRUST_WARN_TEXT)
        if intent == "TRUST_WARN":
            return self._ctx(TRUST_LAST_TEXT)
        if intent == "TRUST_OK":
            if self.inbound:
                # 11/19：来电没有"驳回原因和整改方法"可回问（那是外呼话术）
                return "好的，您说。"
            tail = {S1: self._ctx(FIXED_CONFIRM), S2: self._ctx(FIXED_REASON),
                    S3: FIXED_QA, S4: self._ctx(FIXED_WILLING)}.get(ps, "")
            return ("好的，感谢您的信任。" + tail).strip()
        if intent == "AGENT_IDENTITY":
            # 11/15 真机修复④：自证身份 + 官方核实渠道，绝不反问「哪一部分还有疑虑」。
            # 第 2 次换说法（主动回拨核实），避免连着播同一段自证变成复读机。
            n = int((aux or {}).get("_agent_identity_count")
                    or getattr(self, "_agent_identity_count", 0) or 1)
            if self.inbound:
                # 11/19：来电没有"官方回访"语境（是商家主动打来的），话术换咨询口径
                return self._ctx(
                    "理解您的顾虑。我是京东商家服务热线的资质审核专员，"
                    "您也可以拨打官方热线{hotline}核实本次通话。"
                    "请问您想咨询什么问题？")
            return self._ctx(reply_policy.AGENT_IDENTITY_TEXT2 if n >= 2
                             else reply_policy.AGENT_IDENTITY_TEXT)
        if intent == "FINDFAULT_ACK":
            return ("理解您的心情。您这边是对驳回意见有疑问，还是暂时不想补材料？"
                    "说一下您的顾虑，我帮您看怎么处理更合适。")
        if intent == "NO_MORE":
            # 商家明确说没疑问 → 只问整改意愿，绝不复读驳回内容、不问「还有疑问吗」
            return self._ctx(FIXED_WILLING)
        if intent == "OFFTOPIC_PULL":
            # 11/19：拉回话术按阶段区分——S4 整改意愿确认段已经问过「有无疑问」，
            # 再往回问就是流程倒退（实测商家说改约被误判闲聊后被绕回答疑段）。
            # S4 拉回改成顺着意愿确认的口径继续问；其余阶段保持原话术。
            if ps == S4:
                # 【11/21 离线防复读】上一轮刚说过这句含糊追问，再逐字复读就是复读机
                # （1115d⑥：S4 连续两轮含糊 → 同一句×2）。换备用问法。
                if getattr(self, "last_intent", "") in ("HESITANT", "OFFTOPIC_PULL"):
                    return self._ctx(FIXED_WILL_HESITANT2)
                return self._ctx(FIXED_WILL_HESITANT)
            if self.inbound:
                # 11/19：来电场景没有"驳回原因"可回问，拉回口径换成咨询语境
                # 【11/22 防复读】上一轮已经说过同一句就别逐字再来一遍
                # （真机：商家「哇塞。你好。」→ 这句；「没事没事。」→ 又一模一样的这句）
                if getattr(self, "last_intent", "") == "OFFTOPIC_PULL":
                    return ("咱们还是回到资质审核这件事上——"
                            "您这次是想了解驳回原因，还是材料怎么补？")
                return ("这个我这边不方便展开。咱们回到您咨询的问题上——"
                        "您想了解资质审核的哪方面呢？")
            return ("这个我这边不方便展开。咱们还是回到资质审核这件事——"
                    "您对这次的驳回原因还有什么疑问吗？")
        if intent == "OFFTOPIC_WARN":
            return self._ctx(reply_policy.OFFTOPIC_WARN_TEXT)
        if intent == "OFFTOPIC_CLOSE":
            return self._ctx("那今天就不多打扰您了。您有资质审核相关的问题，"
                             "可以拨打京东商家服务热线{hotline}。感谢您的接听，再见。")
        if intent == "DISTRUST_TRANSFER":
            return self._ctx(reply_policy.DISTRUST_TRANSFER_TEXT)
        if intent == "NO_TIME_TRANSFER":
            # 全时无空 → 不再问「哪天方便」，直接登记转人工 + 收线。
            # 必须排在下面 ns==END 的 FIXED_CLOSING 之前，否则会被结束语截走。
            return self._ctx(reply_policy.NO_DAY_FREE_TRANSFER_TEXT)
        if intent == "PUSHBACK_CLOSE":
            return self._ctx(reply_policy.FINDFAULT_CLOSE_TEXT)
        if ps == "PRE":
            # 11/19：来电用热线问候开场（不播外呼话术、不确认身份）
            return self._ctx(FIXED_INBOUND_OPENING if self.inbound else FIXED_OPENING)
        if ps == E5:
            # 红线命中：固定纠正话术（暂停业务 → 定性 → 引导 → 拉回 / 严重违规收线），
            # 绝不让模型自由发挥（避免松口，也避免它把业务话术硬塞回来）。
            return self._correction_text(intent, vio)
        if ps == E2:
            return FIXED_UNCLEAR
        if intent == "HUMAN_REQUEST":
            # 11/21：五环节任意位置说「转人工」→ 统一人工跟进话术（用户指定），
            # 不重讲驳回原因、不引导热线自助——登记诉求 + 人工客服会联系。
            return ("好的，您需要人工服务的诉求我已经登记，"
                    "后续会有我们的人工客服联系您，请您保持电话畅通。"
                    "感谢您的接听，再见。")
        if ps == E3:
            return self._ctx(FIXED_EMOTIONAL)
        if ps == E4:
            return self._ctx(FIXED_OUT_OF_SCOPE)
        # 【分支 A】商家认错 → 记录「✅ 已认识到问题」，主动拉回主线并追问业务疑问。
        # 必须放在收尾分支之前：VIO_ACK 的 ns 仍可能是 S3/S4。
        if intent == "VIO_ACK":
            return redline.ACK_BACK_ASK
        if intent == "VIO_ACK_WILL":
            return "好的，您能理解就好。" + self._ctx(FIXED_WILLING)
        if intent == "VIO_ACK_BACK":
            return redline.ACK_BACK
        # 【改约两段式】没空 → 先问哪天（不播结束语）；只给时段 → 继续问哪天；
        # 拿到日期 → 复述时间 + 收尾。必须放在下面的「收尾」分支之前，
        # 否则 ns==END 会先被 FIXED_CLOSING 截走，又变成「问完时间就挂断」。
        if intent == "RESCHEDULE_ASK":
            asked = (aux or {}).get("_reschedule_asked") or self._reschedule_asked
            # 注意 asked 是「本轮已+1」之后的值：第 2 次追问才换说法
            return self._ctx(FIXED_RESCHEDULE_ASK2 if asked >= 2 else FIXED_RESCHEDULE_ASK)
        if intent == "RESCHEDULE_WHICH_DAY":
            return self._ctx(FIXED_RESCHEDULE_WHICH_DAY)    # 只给时段 → 追问具体哪天
        if intent == "RESCHEDULE_CONFIRMED":
            when = (aux or {}).get("reschedule_at") or self.reschedule_at or "您说的时间"
            return FIXED_RESCHEDULE_OK.format(time=when, hotline=HOTLINE)
        if intent == "RESCHEDULE_NO_TIME":
            return self._ctx(FIXED_RESCHEDULE_LATER)
        # 整改意愿确认段：商家说「没时间 / 不懂」→ 先据实回应（改约 / 解释），
        # 不要直接结束语或机械重复确认句。此分支须在下方「收尾」分支之前拦截
        # （WILL_RESCHEDULE 的 ns 已是 END，否则会被误判成普通结束语）。
        if ps == S4 and intent in ("WILL_CLARIFY", "WILL_RESCHEDULE"):
            return None
        if ns == END and (ps in (S4, S5) or intent in ("CLOSING", "MAX_TURNS", "COMPLETED")):
            return self._ctx(FIXED_INBOUND_CLOSING if self.inbound else FIXED_CLOSING)
        # 【单步推进·转场轮】只做「收到确认」的承接，情况说明留到下一轮（AI 主动续讲）。
        # 11/15 真机修复③：明确否认必须排在 `if ps == S1` 兜底之前。
        # 旧顺序里 `if ps == S1: FIXED_CONFIRM` 先命中，把 NOT_CONTACT 的正确
        # 话术变成死代码 —— 商家说「我不是」也听到「感谢您的确认，下面说明
        # 驳回原因」，既没认出否认，又像是要提前透露驳回详情（合规红线）。
        if intent == "NOT_CONTACT":
            if ns == END:
                return self._ctx(reply_policy.IDENTITY_REPLIES["IDENTITY_GIVEUP"])
            return ("我是京东商家资质审核专员。可能联系的号码有误，"
                    "请问哪位负责人方便接听？也请留意京麦后台的资质审核通知。")
        if intent == "RECONFIRM_JUMP_QA":
            # 【11/21 环节回跳】身份重核通过、情况说明已讲过 → 谢过 + 跳答疑问有无疑问
            return ("好的，感谢您的确认，登记的对接人没问题。咱们接着说——"
                    + self._ctx(FIXED_QA))
        if intent == "RECONFIRM_JUMP_WILL":
            # 重核通过、答疑也过了 → 谢过 + 直接确认整改意愿
            return ("好的，感谢您的确认，登记的对接人没问题。最后跟您确认一下——"
                    + self._ctx(FIXED_WILLING))
        if intent == "RECONFIRM_JUMP_END":
            # 重核通过、五环节全部完成 → 谢过 + 礼貌收尾，绝不重跑任何环节
            return "好的，感谢您的确认，登记的对接人没问题。" + self._ctx(FIXED_CLOSING)
        if intent == "CONFIRMED_ONLY":
            return self._ctx(FIXED_CONFIRM)
        if intent == "VERIFY_WARM":
            # S1 商家寒暄/情绪表达：先接住情绪，再回到身份确认（11/19 人性化）
            return self._ctx(FIXED_VERIFY_WARM)
        if ps == S1 and intent == "VERIFY_AGAIN":
            # 11/19 人性化：连问就换说法，绝不逐字复读同一句「请问您是负责人吗」
            stall = int(getattr(self, "_s1_stall", 0) or 0)
            if stall >= 4:
                return self._ctx(FIXED_VERIFY_AGAIN3)
            if stall >= 3:
                return self._ctx(FIXED_VERIFY_AGAIN2)
            return self._ctx(FIXED_VERIFY_AGAIN)
        if ps == S1:
            # 11/15 真机修复③：S1 兜底绝不能再冒充「已确认」。
            # 任何未明确确认的情况一律再确认一次，不播 FIXED_CONFIRM。
            return self._ctx(FIXED_VERIFY_AGAIN)
        if ps == S2 and intent == "REJECTION_DELIVER":
            # 情况说明轮：驳回原因 + 整改动作一轮说完，末尾问「有无疑问」；
            # 播完立刻点亮【情况说明】，【商家答疑】等商家明确说没问题才点亮。
            return self._ctx(FIXED_EXPLAIN_QA)
        # 情况说明尚未真正播报、商家却已说「没问题」→ 本轮先完整补交付（答疑灯下一轮才亮）
        if intent == "EXPLAIN_REDELIVER":
            return self._ctx(FIXED_REDELIVER)
        # NOT_CONTACT 已在上方（S1 兜底之前）统一处理，此处不再保留死代码。
        # 含糊其辞（hesitant）：主动追问是哪一部分有疑虑，不推进、不打勾。
        # 但【整改意愿确认】段已经问过"有没有疑问"了，再问疑虑是答非所问 →
        # 换成问"打算自己提交还是有什么不方便"，把话头拉回意愿本身。
        if intent == "HESITANT":
            # 11/15 真机修复④：同一句「打算自己提交还是有什么不方便」连播两遍
            # 是最刺眼的复读机症状。上一轮已经用过这句，本轮交给大模型换一种说法，
            # 绝不逐字重复（真正的承诺表达已在 _route 短路，不会走到这里）。
            # 11/19：不再限 S4——实测 S3 连续两轮含糊也逐字复读
            # （「您看是哪一部分还有疑虑？」×2，22:37 截图）。
            if getattr(self, "last_intent", "") == "HESITANT":
                if self.offline or self.client is None:
                    # 【11/21 离线防复读】离线/无模型没有换说法能力，
                    # 用备用话术轮换，绝不逐字复读（1115d⑥）
                    return self._ctx(FIXED_WILL_HESITANT2 if ps == S4 else FIXED_HESITANT2)
                return None
            if self.inbound:
                # 11/19：来电含糊（「我是」「嗯」）一律交给大模型——商家之前问的
                # 问题还挂在对话里（实测「我是」之后反问「还有其他想咨询的问题吗」
                # 极不合逻辑：他刚问过「资质审核出什么问题」，你该回答它）。
                # 来电专属提示词要求直接作答， hesitation 兜底由 hesitant_count 把关。
                return None
            return self._ctx(FIXED_WILL_HESITANT if ps == S4 else FIXED_HESITANT)
        # 连续 3 次含糊 / 未明确确认 → 转人工或结束，绝不无限循环卡死
        if intent == "HESITANT_LOOP":
            return self._ctx(FIXED_LOOP_END)
        # 刚到答疑、尚未主动问过「有无疑问」→ 问一次（问的时候绝不打勾）
        if intent == "QA_ASK":
            return self._ctx(FIXED_QA_INBOUND if self.inbound else FIXED_QA)
        # 【禁止复读机】商家已经明确说「没疑问了 / 懂了」→ 绝不再问一遍有没有疑问，
        # 直接进入整改意愿确认那一句（环节④本轮点亮，环节⑤等商家肯定答复才点亮）。
        if intent == "QA_CONFIRMED":
            return self._ctx(FIXED_WILLING)
        # 12/12 五环节：商家答疑 / 整改意愿确认 的「主动询问」只在进入该环节的第一句触发；
        # 商家随后提出的具体疑问 / 异议 / 说没时间，一律交给大模型依据知识库作答，绝不重复脚本。
        # —— 这正是之前「听不懂疑问、只会重复上面说的话」的根因：S3/S4 被无条件固定话术架空，
        #    大模型 + RAG 那条能真正听懂并回答的路径永远走不到（fixed 非空即短路）。
        if ns == S3 and intent == "UNDERSTOOD":
            return self._ctx(FIXED_QA_INBOUND if self.inbound else FIXED_QA)  # 主动问一次「有无疑问」（来电不会走到这里）
        if ps == S3 and intent == "NEED_CLARIFICATION":
            return None                            # 商家追问 → 大模型据知识库**正面解答**（解答完再问是否有别的问题）
        if ps == S3:
            return None                            # 确认无疑问 → 大模型简短确认后进入意愿确认
        if ps == S4 and intent in ("WILL_CLARIFY", "HESITANT"):
            return None                            # 不懂 / 追问 → 大模型先解释，不机械重复确认句
        if ps == S4:
            return self._ctx(FIXED_WILLING)        # 进入意愿确认的第一句 / 正常确认承诺
        return None

    def _stage_reply(self, ps: str) -> str:
        """离线 / 无模型时的确定性阶段话术（回放回归与断网兜底）。"""
        if ps == E5:
            return SAFE_SENTENCE[2]
        if ps == S1:
            # 11/15 真机修复③：离线/无模型兜底同样不得冒充「已确认」
            return self._ctx(FIXED_VERIFY_AGAIN)
        if ps == S2:
            return self._ctx(FIXED_EXPLAIN_QA)
        if ps == S3:
            return self._ctx(FIXED_QA_INBOUND if self.inbound else FIXED_QA)
        if ps == S4:
            return self._ctx(FIXED_WILLING)
        if ps in (S5, END):
            return self._ctx(FIXED_INBOUND_CLOSING if self.inbound else FIXED_CLOSING)
        return self._ctx(FIXED_SERVICE_ERROR)

    def _topup_elements(self, reply: str, ps: str, ns: str, intent: str = "") -> tuple[str, list]:
        """模型说完后补齐本轮缺失的必说要素 → (最终播报文本, 追加句列表)。

        11/12 阶段门控（修「抢跑」）：
        * 业务要素（驳回原因/整改动作）**只在「真正在交付/收尾」的轮次**才补；
          S1（身份确认后第一次说明）绝不补 —— 否则「刚说是我」的那一轮就把
          第三段内容塞进去，进度条瞬间走完，与真实通话完全不符。
        * 12/12 关键修正（修「答疑轮重复上面说的话」）：商家在答疑段提出具体疑问、
          说「没时间 / 不懂」、或确认「无疑问」的轮次，不再重贴驳回原因 / 整改动作——
          这些要素在前面的情况说明段已经说过、整通累计已覆盖，重贴正是「只会重复」的根源。
          收尾轮（ns==END）仍兜底补齐全部要素，保证「挂断时要素覆盖率 100%」不丢。
        * 只认真实播报文本；补的句子同样计入 check_compliance。
        """
        if intent in ("BYE", "NO_TIME_TRANSFER"):
            # 告别轮 / 全时无空转人工轮绝不补「驳回原因/整改动作/结束语」：
            # ns==END 会让下面的 final 分支补成「再见…您这次的驳回原因是…」
            # 的倒序拼接，既是复读又是合规泄漏。话术自带结束语，安静收线即可。
            return reply, []
        stage = STAGE_OF_STATE.get(ps) or 0
        if stage == 0:
            stage = STAGE_OF_STATE.get(self.state, 1) or 1
        if ps == E5:
            # 红线轮只播企标拒绝话术：不追加"驳回原因是…/请补齐材料"，
            # 否则会变成"本次沟通先到这里。好的，整改要求我已说明清楚…"的荒诞拼接。
            return reply, []
        if ps == S1 and not self.identity_confirmed:
            # 11/15 真机修复③：身份未确认时，S1 收尾轮（否认/要人工/情绪激烈）
            # 绝不能补「驳回原因/整改动作/结束语」。旧逻辑 ns==END 就无条件
            # fill_biz，会把驳回详情硬拼到「再见」后面，构成合规泄漏。
            return reply, []
        final = (ns == END)
        # 12/13 修复：收尾轮的结束语归属阶段 4，否则 ps 仍是 S3(stage=3) 时
        # 「结束语」要素永远判为未覆盖（ELEMENT_STAGE=4），导致 FIXED_CLOSING 被反复补、
        # 结束语重复拼接多次（连续含糊触发 HESITANT_LOOP 时最明显）。
        if final and stage < 4:
            stage = 4
        # 这些轮次是在「答疑 / 回应异议 / 改约 / 过渡」，不应再机械重贴业务要素
        no_topup = intent in ("NEED_CLARIFICATION", "WILL_CLARIFY", "WILL_RESCHEDULE",
                              "RESCHEDULE_ASK", "RESCHEDULE_WHICH_DAY",
                              "QA_CONFIRMED", "UNDERSTOOD", "ASK_QUESTION",
                              "HESITANT", "HESITANT_LOOP", "QA_ASK", "EXPLAIN_REDELIVER",
                              # 红线纠正 / 认错拉回 / 身份二次确认：都不是业务交付轮，
                              # 绝不能把「驳回原因/整改动作」硬贴进来（会打断纠正语境）
                              "VIO_ACK", "VIO_ACK_BACK", "VIO_ACK_WILL", "VIOLATION", "VIOLATION_SEVERE",
                              "MERCHANT_REFUSE", "VERIFY_AGAIN", "VERIFY_WARM", "CONFIRMED_ONLY",
                              # 【11/21 环节回跳】重核通过后的跳转轮不是业务交付轮，
                              # 绝不重贴驳回原因/整改动作（跳转话术里只有承接+下一问）
                              "RECONFIRM_JUMP_QA", "RECONFIRM_JUMP_WILL", "RECONFIRM_JUMP_END",
                              # 11/15：否认身份 / 核实数字员工身份，都不是业务交付轮
                              "NOT_CONTACT", "AGENT_IDENTITY",
                              # 11/15：明确道别轮 / 全时无空转人工轮不补任何业务要素
                              "BYE", "NO_TIME_TRANSFER")
        # 11/19：来电场景不主动补贴驳回原因/整改动作（商家问什么答什么，
        # 话术里硬塞"您这次的驳回原因是…"是外呼行为）；结束语补齐保留。
        fill_biz = (final or (ps in (S2, S3) and not no_topup)) and not self.inbound
        merged, tops = reply, []
        for _ in range(2):
            comp = check_compliance(
                list(self.history) + [{"role": "assistant", "content": merged, "stage": stage}])
            add = []
            if fill_biz and not comp["elements"]["驳回原因"]:
                add.append(self._ctx(FIXED_REASON))
            if fill_biz and not comp["elements"]["整改动作"]:
                add.append(self._ctx(FIXED_ACTION))
            if ns == END and not comp["elements"]["结束语"] and "再见" not in merged:
                # 「再见」守卫（11/19）：来电场景停在 S3，结束语要素的阶段门控永远不亮，
                # 补齐循环会反复追加结束语——文本里已有「再见」就绝不再补。
                add.append(self._ctx(FIXED_INBOUND_CLOSING if self.inbound else FIXED_CLOSING))
            if not add:
                break
            merged += "".join(add)
            tops.extend(add)
        return merged, tops

    def _emit_all(self, text: str, on_sentence) -> None:
        for s in self._to_sentences(text):
            if on_sentence:
                on_sentence(self._safe_sentence(s))

    # ---------- 工具调用（Function Calling，11/12 新增） ----------
    def _tool_ctx(self, prompt_state: str = "", intent: str = "") -> dict:
        """工具执行上下文：把「当前通话绑定的商家」交给工具，模型只需表达查什么。"""
        scen = self.scenario or {}
        return {
            "call_id": self.call_id,
            "merchant_id": self.merchant.get("merchant_id") or self.merchant.get("id"),
            "company": self.merchant.get("company_name", ""),
            "shop": self.merchant.get("shop_name", ""),
            "scenario": scen.get("level2_category", ""),
            "prompt_state": prompt_state,
            "intent": intent,
        }

    @staticmethod
    def _tool_key(prompt_state: str, text: str) -> str:
        """决策缓存键：状态 + 规范化文本前 12 字。

        ASR partial 是逐步增长的同义串（"我上次不是已经" → "我上次不是已经交过材料了"），
        前 12 字通常已能确定「要查什么」，因此同一前缀只决策一次，
        把一轮里 N 次投机预测的 LLM 往返压到 1 次。
        """
        norm = re.sub(r"[^一-鿿0-9a-zA-Z]", "", text or "")
        return f"{prompt_state}|{norm[:12]}"

    def _tool_cache_get(self, prompt_state: str, user_text: str) -> Optional[dict]:
        """取缓存的决策结果。除精确命中外，还支持「前缀复用」：

        投机预生成往往用半句话（8 字）先决策过一次，等 final 到达时句子更长
        （12 字以上），键就对不上。这里额外允许「缓存键是当前文本前缀」的情况，
        于是 final 阶段（真正的首响关键路径）基本都能直接复用，不再多付一次 LLM 往返。
        """
        key = self._tool_key(prompt_state, user_text)
        hit = self._tool_cache.get(key)
        if hit is not None:
            return hit
        norm = re.sub(r"[^一-鿿0-9a-zA-Z]", "", user_text or "")
        for k, v in self._tool_cache.items():
            state, _, ktext = k.partition("|")
            if state == prompt_state and ktext and norm.startswith(ktext):
                return v
        return None

    def _tool_round(self, user_text: str, prompt_state: str, intent: str) -> tuple[str, list]:
        """【无副作用，除决策缓存与熔断计数】本轮要不要查、查到了什么。

        返回 (evidence, records)：
          evidence 拼进系统提示词，records 交给 commit_reply 落账 + 前端展示。
        任何异常 / 超时 / 开关关闭都返回 ("", [])，即退回纯状态机原链路。
        """
        if self.offline or self.client is None or not tools_registry.enabled():
            return "", []
        if self._tool_timeouts >= tools_registry.MAX_TIMEOUTS:
            return "", []
        if not tools_registry.should_use(user_text, prompt_state, intent):
            return "", []
        cached = self._tool_cache_get(prompt_state, user_text)
        if cached is None:
            res = tools_registry.run(self.client, MODEL, user_text,
                                     self._tool_ctx(prompt_state, intent))
            if res.get("timeout"):
                self._tool_timeouts += 1
            else:
                self._tool_timeouts = 0
            cached = {"evidence": res.get("evidence") or "",
                      "records": res.get("records") or []}
            if len(self._tool_cache) > 40:
                self._tool_cache.clear()
            self._tool_cache[self._tool_key(prompt_state, user_text)] = cached
        return cached["evidence"], cached["records"]

    # ---------- 主入口 ----------
    def _build_messages(self, user_text: str, prompt_state: str, intent: str,
                        evidence: str = "", extra: str = "") -> list:
        """组装本轮 LLM 消息（罗 11/12：从 chat_stream 中抽出，供投机预生成复用）。

        evidence：工具查证结果的文本块（11/12），插在 system 与 history 之间，
        让模型在生成播报时能直接引用真实数据。
        extra：本轮临时补充约束（如"商家已表示理解，本轮不要提前说再见"）。
        """
        system_prompt = self._system_prompt(prompt_state, user_text)
        if extra:
            system_prompt += "\n" + extra
        if intent == "NOT_CONTACT":
            system_prompt += ("\n【重要】商家表示自己不是负责人或不认识该公司。请礼貌表示可能联系有误，"
                              "询问哪位负责人方便接听此电话，并建议商家留意京东商家后台的资质审核通知。"
                              "保持专业耐心，不要直接结束通话。")
        msgs = [{"role": "system", "content": system_prompt}]
        if evidence:
            msgs.append({"role": "system", "content": evidence})
        # history 里每条额外带 stage（阶段门控用），但调用大模型时必须只传 role/content，
        # 否则 SDK 会把未知字段一起序列化，可能被服务端拒绝。
        msgs.extend({"role": h.get("role"), "content": h.get("content", "")}
                    for h in self.history[-12:])
        msgs.append({"role": "user", "content": user_text or "（开场）"})
        return msgs

    def _generate(self, messages: list, fixed_reply: str = "",
                  on_fragment: Optional[Callable[[str], None]] = None,
                  stage: int = 0) -> tuple[str, list, int]:
        """纯文本生成：返回 (完整回复, 片段列表, 命中禁语的句数)。不修改任何会话状态。

        流式输出时尽早把**第一个可播片段**交给回调（首字响应关键路径）：
        优先在累计 >=12 字之后的逗号/顿号处切，无标点时累计 >=26 字对齐
        20~26 字之间的标点强切，避免把「审核/专员」这类词从中间切开。
        所有片段拼接后与完整回复逐字一致。
        冉 11/11 的输出侧禁语闸门 _safe_sentence 逐片段生效（11/12 起按 stage 取句）。
        失败时不向回调推送任何内容，由上层用安全话术兜底。
        """
        fragments: list[str] = []
        safe_hits = 0

        def emit(frag: str) -> None:
            nonlocal safe_hits
            if not frag:
                return
            safe = self._safe_sentence(frag, stage)
            if not safe:
                return
            if safe != frag:
                safe_hits += 1
            fragments.append(safe)
            if on_fragment:
                on_fragment(safe)

        if fixed_reply:
            for sent in self._to_sentences(fixed_reply):
                emit(sent)
            return "".join(fragments), fragments, safe_hits

        reply_full = ""
        try:
            stream = self.client.chat.completions.create(
                model=MODEL, messages=messages, temperature=0.3, stream=True,
                max_tokens=120)
            buf = ""
            first_flushed = False
            for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta.content or ""
                buf += delta
                if not first_flushed and len(buf) >= 12:
                    cut = None
                    for m in re.finditer(r"[，,、]", buf):
                        if m.end() >= 12:
                            cut = m
                            break
                    if cut:
                        pos = cut.end()
                    elif len(buf) >= 26:
                        nxt = re.search(r"[，,、。！？；!?;]", buf[20:26])
                        pos = 20 + nxt.end() if nxt else 20
                    else:
                        pos = 0
                    if pos:
                        before = len(fragments)
                        emit(buf[:pos])
                        if len(fragments) > before:
                            reply_full += fragments[-1]
                        buf = buf[pos:]
                        first_flushed = True
                if re.search(r"[。！？；!?;]", delta):
                    sents = self._to_sentences(buf)
                    for sent in sents[:-1]:
                        before = len(fragments)
                        emit(sent)
                        if len(fragments) > before:
                            reply_full += fragments[-1]
                    buf = sents[-1] if sents else ""
            if buf.strip():
                before = len(fragments)
                emit(buf)
                if len(fragments) > before:
                    reply_full += fragments[-1]
        except Exception:
            # 失败不推流；上层（preview_reply）会用覆盖必说要素的安全话术兜底
            return "抱歉，系统繁忙，请稍后再试。", [], 0
        return reply_full, fragments, safe_hits

    def preview_reply(self, user_text: str, interrupted: bool = False,
                      asr_confidence: float = 1.0,
                      on_fragment: Optional[Callable[[str], None]] = None,
                      auto: bool = False) -> dict:
        """【无副作用】生成一轮回复的预览，供投机预生成在后台提前调用。（罗 11/12）

        绝不修改 history / state / turn_no / done_elements / _s1_denials / _stall；
        需要写回的状态变更统一放进返回值的 aux 字典，由 commit_reply() 落账。
        冉 11/11 的合规语义全部保留：固定 / 离线话术、要素补齐 _topup_elements、
        禁语闸门 _safe_sentence、异常回退覆盖必说要素的安全话术。

        auto=True（13/13）：转场轮之后由服务端主动续讲（商家没开口），
        因此**不写入任何商家发言**，只落一条 AI 回复。
        """
        aux: dict = {}
        if self.finished:
            end = "感谢您的接听，再见。"
            if on_fragment:
                on_fragment(end)
            return {"ai_reply": end, "fragments": [end], "user_text": user_text,
                    "prompt_state": END, "next_state": END, "intent": "COMPLETED",
                    "emotion": "neutral", "asr_confidence": asr_confidence,
                    "interrupted": interrupted, "state": END, "turn": self.turn_no,
                    "finished": True, "action": "HANGUP", "error": False, "aux": aux}
        has_text = bool((user_text or "").strip())
        # 11/19：大模型必须看到**同音纠正后**的文本。此前纠正只发生在规则层（_route
        # 内部归一），LLM 拿到的仍是 ASR 原文——「经脉是啥」规则层放行（已纠正为京麦），
        # LLM 却按原文把京麦问题当中医闲聊拒绝。规则层与生成层必须吃同一份数据。
        if reply_policy and user_text:
            user_text = reply_policy.asr_norm(user_text)
        # 12/13 语义意图判定（禁用关键词/正则）：只有 S2/S3/S4 的商家发言轮需要，
        # 由大模型输出 new_question / confirmed_understood / hesitant，作为推进与打勾的唯一依据。
        #
        # 【11/21 结构化决策 · DECISION_MODE=struct】把「意图 + 槽位 + 播报」合并成**一次**调用
        # （改造前这里是 classify，后面还要再走 tool-decide + generate 两次往返，串行 2~3s）。
        # 默认 legacy：行为与改造前逐字节一致，保证 983 条离线回归零回归。
        semantic_intent = "other"
        struct_pv = None
        if has_text and self.state in (S1, S2, S3, S4):
            # 【11/21 十轮·提速】本地高置信度快判：S1 全部 + S2-S4 铁定确认句
            # 直接本地定标签，跳过串行的语义意图 LLM 往返（实测这一跳 16s）。
            # 不确定的句子（模糊/提问/承诺/转折）返回 None → 下面原样走模型，
            # 队友的话术判定逻辑与准确性完全保留。
            _quick = self._quick_local_intent(user_text)
            if _quick is not None:
                semantic_intent = _quick
            elif decision.is_struct() and not self.offline and self.client is not None:
                struct_pv = decision.decide(self.client, MODEL, self, user_text, self.state)
                if struct_pv.get("ok"):
                    semantic_intent = struct_pv["intent"]
                    decision.merge_slots(self, struct_pv)
                    aux["_struct"] = decision.describe(struct_pv)
                else:
                    # 模型这次没给出可用结构化结果 → 退回原分类器，绝不因此丢掉这一轮
                    semantic_intent = self._classify_user_intent(user_text)
            else:
                semantic_intent = self._classify_user_intent(user_text)
        if asr_confidence < 0.6 and not interrupted:
            prompt_state, next_state, intent = self._apply_gates(
                E2, self.state, "ASR_UNCLEAR", has_text, commit=False, aux=aux)
        else:
            prompt_state, next_state, intent = self._route(
                user_text, interrupted, commit=False, aux=aux,
                semantic_intent=semantic_intent)
        # 【11/21 否决器】模型只能提议「停 / 进入下一环节」，且必须过不变式校验；
        # 违反（如身份未确认就想进披露段）一律退回规则算出的状态，并留痕。
        # 注意：结束通话不进否决器 —— 收线一律由规则层的闭环闸门决定
        # （_test_invariants.py 已证明现有规则引擎的 24 次真实迁移全部满足不变式）。
        if struct_pv and struct_pv.get("ok") and next_state != END:
            try:
                _ctx = invariants.snapshot(self)
                _ctx["state"] = self.state
                _cand = decision.propose_state(struct_pv, self.state, next_state)
                # 迁后预计事实：本轮回复本身会确立「身份确认」，不能拿迁前快照当准
                _after = {
                    "identity_confirmed": bool(
                        self.identity_confirmed or aux.get("identity_confirmed")
                        or intent in ("CONFIRMED_ONLY", "RECONFIRM_OK")),
                    "non_task_end": bool(getattr(self, "non_task_end", False)
                                         or aux.get("non_task_end")),
                    "close_reason": aux.get("_close_reason") or "",
                }
                _final, _bad = decision.apply_veto(struct_pv, _cand, next_state,
                                                   _ctx, after=_after)
                aux["_veto"] = {"proposed": _cand, "adopted": _final, "violations": _bad}
                if _bad:
                    aux["_veto_note"] = invariants.describe(_bad)
                next_state = _final
            except Exception:
                pass
        if prompt_state in (E1, E2):
            aux["_resume_state"] = self.state

        fragments: list[str] = []
        collected: list[str] = []   # 经禁语闸门后的真实播报片段

        def emit_out(frag: str) -> None:
            frag = sanitize_reply(frag)
            if not frag:
                return
            fragments.append(frag)
            collected.append(frag)
            if on_fragment:
                on_fragment(frag)

        fixed = self._fixed_reply(prompt_state, next_state, intent, aux.get("_vio"), aux)
        error = False
        stage = STAGE_OF_STATE.get(prompt_state) or 0
        if stage == 0:
            stage = STAGE_OF_STATE.get(self.state, 1) or 1
        # 【工具调用】固定话术 / 离线分支不需要查数据；只有真要走大模型时才决策。
        # 决策 + 查库全部在投机预生成的后台线程里完成，拿到结果后随预览一起缓存；
        # 命中时正常播报，未命中则整轮丢弃 —— 绝不把工具往返压在首响关键路径上。
        evidence, tool_records = ("", [])
        # struct 模式：证据已经随那一次决策调用一起给模型了，不再单独走工具往返
        # （这也是「删掉关键词门控 should_use」的原因：不必再预判值不值得查）。
        _struct_ok = bool(struct_pv and struct_pv.get("ok"))
        if fixed is None and not (self.offline or self.client is None) and not _struct_ok:
            evidence, tool_records = self._tool_round(user_text, prompt_state, intent)
            if tool_records:
                aux["_tool_calls"] = tool_records
        if fixed is not None:
            for sent in self._to_sentences(fixed):
                emit_out(sent)
        elif _struct_ok:
            # 【结构化决策】播报文本是同一次调用的产出。**必须过 _generate 的 fixed_reply 通道**，
            # 因为它逐句套 _safe_sentence 禁语闸门；直接 emit 会绕过输出侧合规检查
            # （实测：模型返回「保证通过」会被原样播出去，这是本次改造自己踩到的坑）。
            _gen, _frags, _hits = self._generate([], struct_pv.get("say") or "", emit_out, stage)
            if _hits:
                aux["_struct_blocked"] = int(_hits)
            if not collected:
                # 整句都被禁语闸门拦掉了 → 用覆盖全部必说要素的安全话术兜底
                merged, _ = self._topup_elements(self._ctx(FIXED_SERVICE_ERROR),
                                                 prompt_state, next_state, intent)
                for sent in self._to_sentences(merged):
                    emit_out(sent)
        elif self.offline or self.client is None:
            # 离线回放 / 模型不可用：确定性话术，不联网、不抛错、对话继续
            merged, _ = self._topup_elements(self._stage_reply(prompt_state),
                                             prompt_state, next_state, intent)
            for sent in self._to_sentences(merged):
                emit_out(sent)
        else:
            try:
                # 商家已表示理解（S2/S3 → S4）：本轮只做一句确认，禁止提前说结束语，
                # 否则会出现"祝您生意兴隆，再见！需要您登录京麦后台…"这种倒序拼接。
                extra = ""
                if next_state == S4 and prompt_state in (S2, S3):
                    extra = ("【本轮特别注意】商家本轮已表示理解。请用一到两句话确认整改要求"
                             "（补齐什么材料、去哪里提交），**不要出现“再见/祝您/感谢您的接听”"
                             "等结束语**，结束语留到下一轮再说。")
                messages = self._build_messages(user_text, prompt_state, intent, evidence, extra)
                # 11/12 修复：原来把外层 on_fragment 直接传给 _generate，导致
                # emit_out 从未被调用 —— 播报能出声，但 collected / fragments 永远是空，
                # 于是 preview 返回的 ai_reply 只剩「补要素」的固定句、落库文本丢失、
                # 投机预生成拿不到首片段（SPEC-FAIL）。改为传 emit_out：
                # 它内部会再回调外层 on_fragment，流式行为不变，只是文本终于被收下来。
                gen_reply, _, safe_hits = self._generate(messages, "", emit_out, stage)
                if gen_reply.startswith("抱歉，系统繁忙"):
                    raise RuntimeError("llm_error")
                # 【输出侧重写】命中禁语时，如果片段还没真正播出（投机预生成的窗口：
                # on_fragment 为空、片段只是缓存在 pv 里），就再生成一次并要求模型规避。
                # 直连流式（on_fragment 非空）时声音已经出去了，撤不回来，只能靠兜底句。
                if safe_hits and on_fragment is None:
                    guard = {"role": "system", "content": (
                        "上一版回答出现了承诺性或越界表述，已被系统拦截。请重写这一段："
                        "不得出现“保证/一定/肯定/百分百/包过/必须能过”等承诺，"
                        "不得索要身份证号、银行卡号、验证码，不得替商家承诺审核结果。"
                        "保持与当前阶段一致，一到两句、不超过50个汉字。")}
                    retry = messages[:-1] + [guard] + messages[-1:]
                    gen2, frags2, hits2 = self._generate(retry, "", None, stage)
                    if gen2 and not gen2.startswith("抱歉") and hits2 < safe_hits:
                        fragments.clear()
                        collected.clear()
                        for f in frags2:
                            fragments.append(f)
                            collected.append(f)
                        gen_reply = gen2
                        aux["_regen"] = True
            except Exception:
                error = True
                aux["_llm_errors"] = self._llm_errors + 1
                # 11/11：模型异常不再只回「系统繁忙」，改播覆盖全部必说要素的安全话术
                merged, _ = self._topup_elements(self._ctx(FIXED_SERVICE_ERROR),
                                                 prompt_state, next_state, intent)
                for sent in self._to_sentences(merged):
                    emit_out(sent)
                intent = "SERVICE_ERROR"
        reply_full = "".join(collected)
        # 记录与状态推进的依据（11/11：要素只从真实播报文本来，不再按状态打勾）
        merged, tops = self._topup_elements(reply_full, prompt_state, next_state, intent)
        if tops:
            for s in tops:
                emit_out(s)
            reply_full = merged
        # 11/15 真机修复③：输出侧最后一道合规闸门。身份未确认时，任何含
        # **具体驳回原因**的播报文本整句换成身份核实话术，防止模型自由发挥、
        # 异常兜底或要素补齐把驳回详情提前说出来（确认前不得透露）。
        if (reply_policy and not self.identity_confirmed and prompt_state == S1
                and (self.scenario or {}).get("level2_category")
                and str(self.scenario["level2_category"]) in reply_full):
            reply_full = self._ctx(reply_policy.IDENTITY_REPLIES["REFUSE_VERIFY"])
        reply_full = sanitize_reply(reply_full)
        return {"ai_reply": reply_full, "fragments": fragments, "user_text": user_text,
                "prompt_state": prompt_state, "next_state": next_state,
                "intent": intent, "semantic_intent": semantic_intent,
                "emotion": classify_emotion(user_text),
                "asr_confidence": asr_confidence, "interrupted": interrupted,
                "state": next_state, "turn": self.turn_no, "auto": auto,
                "finished": next_state == END,
                "action": "HANGUP" if next_state == END else "SPEAK",
                "error": error, "aux": aux,
                "tool_calls": tool_records}

    def commit_reply(self, pv: dict) -> dict:
        """【11.21-18】commit 互斥外壳：并发提交（旧轮 chat_stream 尚未退出 +
        新轮 chat_stream / 投机命中提交）在这里串行化，状态绝不交错撕裂。
        锁内是纯内存操作；RLock 兼容 chat_stream 内部再进入。"""
        with self._commit_lock:
            return self._commit_reply_impl(pv)

    def _commit_reply_impl(self, pv: dict) -> dict:
        """把一次 preview 正式提交进会话状态（只在命中 / 正式链路里调用一次）。

        冉 11/11 的落账语义完整保留：history 记真实播报、要素由 check_compliance
        从真实播报文本重新计算、merchant_turns 只数商家真实发言。
        罗 11/12：preview 期间延迟计算的状态变更（aux）在这里统一写回。
        """
        if self.finished and not pv.get("finished"):
            # 会话已在上一轮结束：不再重复落账
            return dict(pv, state=self.state, action="HANGUP", finished=True)
        aux = pv.get("aux") or {}
        awaiting_before = bool(self._await_ack)   # aux 落账前的「等商家表态」窗口状态
        for name, value in aux.items():
            setattr(self, name, value)
        # ① 功能补丁（11/15）：特殊标记（不信任/找茬）与闲聊计数落 flag_log；
        #    通话收尾由 server 汇总写进工单轨迹（不计红线 KPI）。
        _lf = aux.get("_last_flag")
        if _lf:
            self.flag_log = list(getattr(self, "flag_log", []) or []) + [dict(_lf)]
            self._last_flag = None
        # 【11/22】语义救回留痕：规则误判清单进 result_json，
        # 让 RSI 的失败归因能看见"哪些句子规则要拒答、模型救了回来"。
        _rs = aux.get("_rescue")
        if _rs:
            self._rescue_log = list(getattr(self, "_rescue_log", []) or []) + [dict(_rs)]
            self._rescue = None
        # 11/15 真机修复②：闲聊分级事件 —— 每一轮都生成结构化事件，
        # server 实时推给后台进度条（琥珀色「闲聊警告」卡 + 事件流），
        # 并沉淀进复盘 offtopic.items；不进红线 KPI、不做违规定性。
        off_out = None
        if _lf and _lf.get("kind") == "offtopic":
            _oc = int(_lf.get("count") or 1)
            _oi = pv.get("intent")
            _lvl = ("END" if _oi == "OFFTOPIC_CLOSE"
                    else "WARN" if _oi == "OFFTOPIC_WARN" else "PULL")
            off_out = {
                "seq": _oc,
                "count": _oc,
                "text": (_lf.get("text") or pv.get("user_text") or "")[:60],
                "ts": round(max(0.0, time.time() - (self.call_start_ts or time.time())), 1),
                "done_before": len(self._flow_done_ids()),
                "level": _lvl,
                "outcome": ("已礼貌收线（第 4 次业务外闲聊）" if _lvl == "END"
                            else "已红线提醒（第 3 次）" if _lvl == "WARN"
                            else "未展开业务外作答，已拉回资质审核主线"),
                "reply": (pv.get("ai_reply") or "")[:80],
                "alert": True,
            }
            self.offtopic_log = list(getattr(self, "offtopic_log", []) or []) + [off_out]
        # 【工具调用落账】只有正式提交的这一轮才留痕，投机预测里的查询不落库
        tool_calls = list(pv.get("tool_calls") or [])
        if tool_calls:
            self.tool_log.extend(tool_calls)
            try:
                tools_registry.record_events(self.call_id,
                                             self.merchant.get("merchant_id") or self.merchant.get("id"),
                                             tool_calls)
            except Exception:
                pass
        user_text = pv["user_text"]
        reply_full = pv["ai_reply"]
        prompt_state = pv["prompt_state"]
        next_state = pv["next_state"]
        intent = pv["intent"]
        auto = bool(pv.get("auto"))
        if prompt_state in (E1, E2):
            self._resume_state = self.state
        has_text = bool((user_text or "").strip())
        # 该句是在第几段播出的 —— 要素阶段门控的唯一依据（E1/E2 沿用被打断前的阶段）
        st = STAGE_OF_STATE.get(prompt_state) or 0
        if st == 0:
            st = STAGE_OF_STATE.get(self.state, 1) or 1
        if not auto:
            # 自动续讲轮没有商家发言，绝不伪造一条「（开场）」式的用户消息进历史
            self.history.append({"role": "user", "content": user_text or "（开场）",
                                  "identity_confirmed": self.identity_confirmed})
        self.history.append({"role": "assistant", "content": reply_full, "stage": st})
        # 【红线留痕】只记本轮的 aux，不用实例属性，避免上一轮的违规被重复记账
        vio = aux.get("_vio")
        vio_out = None
        vio_ack = bool(aux.get("_vio_ack") or self._vio_ack)
        if vio_ack:
            # 分支 A：商家认错 → 给最近一条「等待表态」的事件盖上「✅ 已认识到问题」
            for v in reversed(self.violation_log):
                if v.get("awaiting"):
                    v["awaiting"] = False
                    v["branch"] = "A"
                    v["ack"] = True
                    v["ack_text"] = (user_text or "")[:40]
                    v["outcome"] = "✅ 已认识到问题 · AI 已拉回主线并追问业务疑问"
                    break
        elif awaiting_before and not (vio and vio.get("hit")):
            # 上一轮刚教育完，商家既没认错也没再提违规（回到业务）→ 记为「已教育」
            for v in reversed(self.violation_log):
                if v.get("awaiting"):
                    v["awaiting"] = False
                    v["outcome"] = "已教育 · 商家未明确认错，已回到业务主线"
                    break
        if vio and vio.get("hit"):
            # 正在等待表态的上一条：本轮又提了新的 → 记「未认错」
            for v in reversed(self.violation_log):
                if v.get("awaiting"):
                    v["awaiting"] = False
                    v["branch"] = "B" if v.get("key") != vio.get("key") else "B2"
                    v["outcome"] = ("商家换了一种违规 → 已弹新告警继续教育"
                                    if v.get("key") != vio.get("key")
                                    else "同一违规再次出现 → 已再次教育")
                    break
            # 带上商家原话：落库与前端「风险标记」都靠它做证据（_vio 是纯分类结果，没有原文）
            vio_out = dict(vio, text=(user_text or "")[:60])
            vio_out["branch"] = "C" if vio.get("severe") else "B" if vio.get("count", 1) >= 2 else ""
            correct = vio.get("correct") or {}
            # 播报的纠正话术以「三分支口径」为准（旧的 B 级三段式里含"转交高级专员"，
            # 与「分支 B 绝不转人工」冲突，这里统一覆盖）
            if correct.get("say"):
                vio_out["say"] = correct["say"]
            if correct.get("guide"):
                vio_out["guide"] = correct["guide"]
            entry = {
                "seq": vio.get("seq") or (len(self.violation_log) + 1),
                "turn": self.turn_no + 1, "key": vio["key"], "name": vio["name"],
                "level": vio["level"], "phrase": vio["phrase"], "how": vio["how"],
                "excel": vio["excel"], "action": vio["action"],
                "text": vio_out["text"], "reply": vio["reply"],
                # 纠正话术留痕：怎么定性的、给了什么正确做法，全部可复盘
                "say": correct.get("say") or vio.get("say", ""),
                "guide": correct.get("guide") or vio.get("guide", ""),
                "close": vio.get("close", ""), "tag": vio.get("tag", ""),
                "mark": bool(vio.get("mark")),
                # ---- 13/13 三分支：触发位置 + 时间 + 第几次 + 处置结果 ----
                "count": int(vio.get("count") or 1),
                "stage5": int(vio.get("stage5") or 1),
                "done_before": int(vio.get("done_before") or 0),
                "ts": vio.get("ts"),
                "alert": bool(vio.get("alert", True)),
                "severe": bool(vio.get("severe")),
                "branch": vio_out["branch"],
                "ack": False, "ack_text": "",
                "awaiting": (not vio.get("severe")) and vio["key"] != "REFUSE_CALL",
                "outcome": "",
            }
            if vio.get("severe"):
                entry["outcome"] = "🚨 严重违规 · 屡教不改，已转人工并结束通话"
            elif vio["key"] == "REFUSE_CALL":
                entry["outcome"] = "商家明确拒接，已礼貌收线（尊重意愿）"
            self.violation_log.append(entry)
            self.vio_seq = max(int(self.vio_seq), int(entry["seq"]))
        self._vio_ack = False
        self.turn_no += 1
        if has_text:
            self.merchant_turns += 1
        self.state = next_state
        self.finished = (next_state == END)
        self.last_intent = intent
        self.semantic_intent = pv.get("semantic_intent", "other")
        # 【11/21 关单决策】只有提交路径才升级判定：商家本轮要求人工/回电，或走投诉收线，
        # 按「关单总原则」第 3/4 条把结论升级为「必须回电」并留痕（不改本轮路由）。
        if intent in ("HUMAN_REQUEST", "ID_HUMAN"):
            self.note_callback_request("HUMAN")
        elif intent in ("EMOTIONAL", "ID_COMPLAINT"):
            self.note_callback_request("COMPLAINT")
        comp = check_compliance(self.history, identity_confirmed=self.identity_confirmed)
        self.done_elements = {k for k, v in comp["elements"].items() if v}
        self._record_flow_timestamps()
        flow5 = self.flow_progress()
        action = "HANGUP" if self.finished else "SPEAK"
        return {"ai_reply": reply_full, "state": next_state, "action": action,
                "finished": self.finished, "emotion": pv.get("emotion", "neutral"),
                "intent": intent, "semantic_intent": self.semantic_intent,
                "turn": self.turn_no,
                "merchant_turns": self.merchant_turns,
                "asr_confidence": pv.get("asr_confidence", 1.0),
                "interrupted": pv.get("interrupted", False),
                "compliance": comp,
                "violation": vio_out,
                "offtopic": off_out,
                "tool_calls": tool_calls,
                "flow5": flow5}

    def chat_stream(self, user_text: str, interrupted: bool = False,
                    asr_confidence: float = 1.0,
                    on_sentence: Optional[Callable[[str], None]] = None,
                    auto: bool = False) -> dict:
        """兼容原接口（罗 11/12）：流式生成（逐片段回调）→ 提交状态 → 返回结果。

        auto=True（13/13）：转场轮后的自动续讲，不写商家发言。
        """
        if self.finished:
            return {"ai_reply": "感谢您的接听，再见。", "state": END, "action": "HANGUP",
                    "finished": True, "emotion": "neutral", "turn": self.turn_no}
        pv = self.preview_reply(user_text, interrupted, asr_confidence,
                                on_fragment=on_sentence, auto=auto)
        return self.commit_reply(pv)

    @staticmethod
    def _to_sentences(text: str) -> list[str]:
        parts = re.split(r"(?<=[。！？；!?;])", text)
        return [p for p in (p.strip() for p in parts) if p]

    # ---------- 统一五环节进度（12/12 · 全端唯一真相源） ----------
    def _mark_stage(self, sid: int) -> None:
        """记录某环节完成时刻（相对通话开始秒数）+ 完成轮次；只记第一次。"""
        if sid in self.stage_ts:
            return
        if self.call_start_ts is None:
            self.call_start_ts = time.time()
        self.stage_ts[sid] = round(max(0.0, time.time() - self.call_start_ts), 1)
        self._turn_done.setdefault(sid, self.turn_no)

    def _record_flow_timestamps(self) -> None:
        """每轮 commit 后，依据真实完成标志回填各环节完成时刻。"""
        comp = check_compliance(self.history, identity_confirmed=self.identity_confirmed)
        if self.state != "PRE":
            self._mark_stage(1)                                  # 自我介绍：开场已播报
        if self.identity_confirmed:
            self._mark_stage(2)                                  # 商家身份确认
        if comp["elements"].get("驳回原因") and comp["elements"].get("整改动作"):
            self._mark_stage(3)                                  # 情况说明（驳回原因 + 整改方法）
        if self.qa_confirmed and self.qa_asked:
            # 铁律：情况说明必须是**更早一轮**点亮的，否则本轮只排队、不点灯
            t3 = self._turn_done.get(3)
            if t3 is None or t3 < self.turn_no:
                self._mark_stage(4)                              # 商家答疑（无疑问）
        if self.will_confirmed or self.will_rescheduled:
            self._mark_stage(5)                                  # 整改意愿确认（承诺/改约）

    def flow_progress(self) -> List[dict]:
        """返回统一五环节进度：每个环节 {id,name,key,done,current,ts}。

        完成判定（只认真实证据，与 check_compliance 同源）：
          1 自我介绍   = 状态已离开 PRE（开场已播报）
          2 商家身份确认 = 商家口头确认 (identity_confirmed)
          3 情况说明   = 驳回原因 AND 整改动作 均真实播出到对应阶段
          4 商家答疑   = qa_confirmed（主动问过且商家确认无疑问）
          5 整改意愿确认 = will_confirmed(承诺) OR will_rescheduled(没时间改约)
        current = 第一个未完成的环节（用于「当前环节」高亮）。
        ts = 该环节完成时的相对秒数（挂断后仍保留，供历史记录显示「第 N 秒完成」）。
        """
        comp = check_compliance(self.history,
                                identity_confirmed=self._identity_settled())
        done = {
            1: self.state != "PRE",
            2: self.identity_confirmed,
            3: bool(comp["elements"].get("驳回原因") and comp["elements"].get("整改动作")),
            # 商家答疑：必须「真的问过有无疑问」+「商家明确确认无疑问」两个条件同时成立。
            # 只问不打勾（qa_asked 单条件绝不允许点亮），客服自问自答式抢跑在这里被彻底堵死。
            4: bool(self.qa_confirmed and self.qa_asked),
            5: self.will_confirmed or self.will_rescheduled,
        }
        # 铁律：情况说明(3) 与 商家答疑(4) 绝不同一轮变绿（防御异常数据 / 历史回放）。
        if done[3] and done[4]:
            t3, t4 = self._turn_done.get(3), self._turn_done.get(4)
            if t3 is not None and t4 is not None and t3 >= t4:
                done[4] = False
        # 顺序约束：前一环节没完成，后面一律不算完成（杜绝跳步 / 抢跑 / 时间戳倒挂）。
        # 【11/21 环节回跳】例外：曾确认过的环节（_stage_locked）不连带熄灭——
        # 身份重核暂时灭灯时，后面已确认的环节继续亮（用户口径）。
        _locked = getattr(self, "_stage_locked", None) or set()
        for _sid in (2, 3, 4, 5):
            if done.get(_sid):
                _locked.add(_sid)
                if hasattr(self, "_stage_locked"):
                    self._stage_locked.add(_sid)
        # 【11/21 环节回跳】身份重核灭灯时 check_compliance 会把下游要素级联熄灭——
        # 曾确认过的环节（3/4/5）直接按锁定记录保持亮灯；环节2 除外（它是当前
        # 重核对象，必须如实显示灭灯，引导商家重新确认）。
        for _sid in (3, 4, 5):
            if _sid in _locked:
                done[_sid] = True
        seq_ok = True
        for _s in STAGES_5:
            if not seq_ok and _s["id"] not in _locked:
                done[_s["id"]] = False
            seq_ok = done[_s["id"]] or _s["id"] in _locked
        # 【11/22】通话已结束后不存在"待重核"：曾确认过就把②补亮，
        # 否则看板会出现「②未完成 + 已闭环」这种自相矛盾的状态。
        if self.finished and self._identity_settled():
            done[2] = True
        cur_stage = None
        for s in STAGES_5:
            if not done[s["id"]]:
                cur_stage = s["id"]
                break
        if cur_stage is None:
            cur_stage = 5
        # 11/19：来电（热线咨询）不走外呼五段式——进度条换 3 段式模型，
        # 否则商家页永远显示「3/5、商家答疑不亮」的怪状态（实测截图反馈）。
        if self.inbound:
            finished = self.finished or self.state == END
            return [
                {"id": 1, "name": "热线问候", "key": "greet",
                 "done": self.state != "PRE",
                 "current": self.state == "PRE", "ts": self.stage_ts.get(1)},
                {"id": 2, "name": "商家答疑", "key": "qa",
                 "done": bool(self.qa_confirmed or self.qa_asked),
                 "current": self.state == S3 and not (self.qa_confirmed or self.qa_asked),
                 "ts": self.stage_ts.get(4)},
                {"id": 3, "name": "结束收线", "key": "close",
                 "done": bool(finished),
                 "current": not finished and self.state == END,
                 "ts": self.stage_ts.get(5) if finished else None},
            ]
        out = []
        for s in STAGES_5:
            sid = s["id"]
            out.append({
                "id": sid, "name": s["name"], "key": s["key"],
                "done": bool(done[sid]),
                "current": (not done[sid]) and (sid == cur_stage),
                "ts": self.stage_ts.get(sid),
            })
        return out

    def summary(self) -> dict:
        """通话结束后的结构化回写内容（11/11：闭环只认真实证据）。

        闭环四项硬条件：流程走完 + 5 项必说话术全覆盖且无禁语 + ≥3 轮商家发言
        + 属任务型结束（情绪/超范围/未联系人不算）。旧版 `finished and rate>=0.8`
        允许缺一项就算闭环，是「没说完也算成功」的根因。
        结构化回写由 server.finalize_call 在同一事务里完成；回写失败则异常抛出，
        closed_loop 根本不会落库，因此「已回写」这一条由落库结果本身保证。
        """
        comp = check_compliance(self.history,
                                identity_confirmed=self._identity_settled())
        scen = self.scenario or {}
        elements_ok = comp["rate"] >= 1.0 and not comp["violations"]
        turns_ok = self.merchant_turns >= MIN_TURNS_TO_CLOSE
        task_end = not self.non_task_end
        closed = bool(self.finished and elements_ok and turns_ok and task_end)

        if closed:
            resolved = [f"已向商家说明驳回原因：{scen.get('level2_category', '')}",
                        "已告知整改动作与重新提交流程",
                        "已确认商家理解整改要求",
                        "已完成结束话术与结构化回写"]
            pending = []
            stop_stage = "已完成"
        else:
            done = [k for k, v in comp["elements"].items() if v]
            resolved = [f"已覆盖必说要素：{'、'.join(done)}"] if done else []
            if self.violation_log:
                # 13/13 按三分支复盘：认错的、换了违规的、屡教不改转人工的分别写清楚
                acked = [v for v in self.violation_log if v.get("branch") == "A"]
                others = [v for v in self.violation_log if v.get("branch") != "A"]
                if acked:
                    resolved.insert(0, "已纠正商家「" + "、".join(sorted({v["name"] for v in acked})) +
                                    "」类违规，商家已认识到问题（✅ 已认识到问题），"
                                    "已把话题拉回资质审核主线继续沟通")
                if others:
                    resolved.insert(0, "已拦截并教育商家违规诉求：" +
                                    "、".join(sorted({v["name"] for v in others})) +
                                    "（在进度条对应环节弹【红线违规警告】、暂停业务纠正后拉回主线）")
                if self.severe_violation:
                    resolved.insert(0, "商家同一违规屡教不改，已打【严重违规】标签、"
                                       "转人工专员跟进并终止本次通话")
            pending = [f"未闭环原因：{self._pending_reason(comp, elements_ok, turns_ok, task_end)}",
                       f"停在环节：{STAGE_NAMES.get(str(self.state)[:2], self.state)}",
                       "商家需重新提交材料后等待再次审核"]
            stop_stage = (f"非任务结束（{self._close_reason}）" if self.non_task_end
                          else STAGE_NAMES.get(str(self.state)[:2], self.state))
        b_hits = [v for v in self.violation_log if v["level"] == "B"]
        # 改约复盘：商家没空但约定了具体时间 → 写清楚约到哪天，工单里一眼能看到
        if self.reschedule_at:
            resolved.insert(0, f"商家近期没空，已约定【{self.reschedule_at}】再跟进提交，"
                               f"请按该时间安排提醒")
        elif self._close_reason == "NO_TIME_TRANSFER":
            # 11/15 真机修复⑥：全时无空转人工，复盘里必须写清「已转人工」，
            # 否则运营端只看到一通未闭环电话，不知道要人工接手。
            resolved.insert(0, "商家表示哪一天都没空（无可约时间），已登记转人工专员，"
                               "由人工同事另约时间跟进整改，本次通话未强行追问日期")
        elif self._close_reason == "RESCHEDULE_NO_TIME":
            resolved.insert(0, "商家表示近期没空，未约定具体改约时间（已请其自行择期提交）")
        return {"resolved": resolved, "pending": pending,
                "compliance": comp, "emotion": self._dominant_emotion(),
                "closed_loop": closed,
                # 【11/21】关单方式判定留痕：这通电话「该不该打、依据哪条规则、要不要预约二次外呼」。
                # 写进 result_json 后，工单卡、复盘、答辩都能逐条解释（赛题「可溯源性」）。
                "close_decision": dict(self.close_decision or {}),
                # 【11/21】槽位账本 + 完整率：赛题评分维度 2「槽位完整率」的取数点。
                "slots": dict(self.slots or {}),
                "slot_completeness": _slot_completeness(self.slots),
                # 【11/22】被"语义救回"的句子：规则要判闲聊、模型认定是业务问题。
                # 落库后 RSI 失败归因能看到"规则误判清单"——这才是 RSI 能自我进化的入口。
                "semantic_rescue": list(getattr(self, "_rescue_log", []) or []),
                "closure_checks": {"finished": self.finished, "elements_complete": elements_ok,
                                   "no_violation": not comp["violations"], "turns_ok": turns_ok,
                                   "merchant_turns": self.merchant_turns,
                                   "task_end": task_end,
                                   "writeback": "由 server.finalize_call 同事务保证"},
                "stop_stage": stop_stage, "close_reason": self._close_reason,
                # 11/15 真机修复⑥：全时无空已启动转人工，供复盘/运营端识别
                "transfer_human": bool(getattr(self, "transfer_human", False)),
                "state": self.state,
                "llm_errors": self._llm_errors,
                "violations": list(self.violation_log),
                "violation_count": len(self.violation_log),
                # ① 功能补丁（11/15）：特殊标记（不信任/找茬）与闲聊分级，供工单留痕
                "flags": list(getattr(self, "flag_log", []) or []),
                # 11/15 真机修复②：闲聊复盘数据（前端 offInfo(rj) / 复盘时间线消费）
                "offtopic": {
                    "count": len(self.offtopic_log),
                    "items": list(self.offtopic_log),
                    "warned": any(x.get("level") == "WARN" for x in self.offtopic_log),
                    "ended": any(x.get("level") == "END" for x in self.offtopic_log),
                    "final_outcome": (
                        "已礼貌收线（第 4 次业务外闲聊）"
                        if any(x.get("level") == "END" for x in self.offtopic_log)
                        else "已红线提醒（第 3 次）"
                        if any(x.get("level") == "WARN" for x in self.offtopic_log)
                        else ""),
                },
                "violation_blocked": len(b_hits),
                "violation_severe": bool(self.severe_violation),
                "reschedule_at": self.reschedule_at,
                "will_rescheduled": bool(self.will_rescheduled),
                "risk_tagged": bool(b_hits) or bool(self.severe_violation),
                "merchant_risk": self.risk or {},
                "tool_calls": self._tool_summary(),
                "flow5": self.flow_progress()}

    def _tool_summary(self) -> dict:
        """通话级工具调用汇总（11/12）：次数 / 明细 / 是否命中，写进 result_json 供复盘。"""
        items = [{"tool": t.get("tool"), "name": t.get("name"),
                  "title": t.get("title"), "summary": t.get("summary"),
                  "ok": bool(t.get("ok")), "ms": t.get("ms"),
                  "args": t.get("args") or {}} for t in self.tool_log]
        return {"count": len(items),
                "ok": sum(1 for x in items if x["ok"]),
                "tools": sorted({x["tool"] for x in items if x.get("tool")}),
                "items": items}

    def _pending_reason(self, comp: dict, elements_ok: bool, turns_ok: bool, task_end: bool) -> str:
        reasons = []
        if not self.finished:
            reasons.append("流程未走完")
        if not elements_ok:
            reasons.append("必说话术缺失：" + "、".join(comp["missing"]))
            if comp["violations"]:
                reasons.append("命中输出禁语")
        if not turns_ok:
            reasons.append(f"对话轮次不足（{self.merchant_turns}/{MIN_TURNS_TO_CLOSE}）")
        if not task_end:
            reasons.append(f"非任务型结束（{self._close_reason}）")
        return "；".join(reasons) or "未知"

    def _dominant_emotion(self) -> str:
        emos = [classify_emotion(h["content"]) for h in self.history if h["role"] == "user"]
        if "angry" in emos:
            return "angry"
        if "anxious" in emos:
            return "anxious"
        if emos and all(e == "satisfied" for e in emos[-1:]):
            return "satisfied"
        return "neutral" if not emos else emos[-1]
