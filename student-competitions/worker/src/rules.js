// Port of reply_policy.py + redline.py + agent.py pure helpers (call-agent rules layer)
import {
  ASR_HOMOPHONE_MAP,
  REPLY,
  B_SCRIPT,
  CORRECT,
  ALERT,
  SEVERE_LIMIT,
  PULL_BACK,
  ANOTHER_OPEN,
  ACK_BACK_ASK,
  ACK_BACK,
  SEVERE_TEXT,
  REFUSE_TEXT,
  CATEGORIES,
  STRICT,
} from "./literals.gen.js";

export const HOTLINE = "4006180092";
export const MIN_TURNS_TO_CLOSE = 3;
export const MAX_TURNS_HARD = 30;
export const MAX_STALL_ROUNDS = 999;

export const S0 = "S0-INTRODUCE";
export const S1 = "S1-VERIFY_PERSON";
export const S2 = "S2-REJECTION";
export const S3 = "S3-QA";
export const S4 = "S4-CONFIRM";
export const S5 = "S5-CLOSE";
export const E1 = "E1-INTERRUPT";
export const E2 = "E2-UNCLEAR";
export const E3 = "E3-EMOTIONAL";
export const E4 = "E4-OUT_OF_SCOPE";
export const E5 = "E5-VIOLATION";
export const END = "END";

export const REQUIRED_ELEMENTS = ["自报身份", "身份确认", "驳回原因", "整改动作", "结束语"];

export const ELEMENT_STAGE = { 自报身份: 1, 身份确认: 1, 驳回原因: 2, 整改动作: 2, 结束语: 4 };
export const STAGE_NAMES_4 = { 1: "身份确认", 2: "情况说明", 3: "商家答疑", 4: "礼貌结束" };
export const STAGES_5 = [
  { id: 1, name: "自我介绍", key: "intro" },
  { id: 2, name: "商家身份确认", key: "identity" },
  { id: 3, name: "情况说明", key: "explain" },
  { id: 4, name: "商家答疑", key: "qa" },
  { id: 5, name: "整改意愿确认", key: "willing" },
];
export const STATE_STAGE5 = {
  PRE: 1,
  [S0]: 1,
  [S1]: 2,
  [S2]: 3,
  [S3]: 4,
  [S4]: 5,
  [S5]: 5,
  [END]: 5,
  [E1]: 2,
  [E2]: 3,
  [E3]: 5,
  [E4]: 5,
  [E5]: 3,
};
export const STAGE_OF_STATE = {
  PRE: 1,
  [S0]: 1,
  [S1]: 1,
  [S2]: 2,
  [S3]: 3,
  [S4]: 4,
  [S5]: 4,
  [END]: 4,
  [E3]: 3,
  [E4]: 3,
  [E5]: 2,
};
export const SAFE_SENTENCE = {
  1: "这部分我先按系统里的审核结果和您说明。",
  2: "能不能通过要看材料是否齐全合规，这个我不能给您打包票。",
  3: "能不能通过要看材料是否齐全合规，这个我不能给您打包票。",
  4: "这个我确实不能承诺，一切以审核结果为准。",
};
export const ADVANCE_STATE = { [S1]: S2, [S2]: S4, [S3]: S4, [S4]: END };

const ELEMENT_PATTERNS = {
  自报身份: /资质审核专员|京东.{0,6}专员/,
  身份确认: /请问您是.{0,20}负责人|是.{0,12}负责人吗|负责人本人|是不是.{0,12}负责人|哪位负责人/,
  驳回原因:
    /驳回原因|驳回意见|审核未通过|未通过审核|不符合.{0,10}(要求|标准|规定|规范)|缺少.{0,8}(材料|资料|资质|证件)|材料.{0,8}(缺失|不全|不完整|过期|失效)/,
  整改动作:
    /重新提交|重新上传|补齐|补交|补充材料|按.{0,10}(意见|要求).{0,6}(修改|补|提交)|登录京麦|开具|提供.{0,12}(材料|资料|证明|证件|注册证|授权书|报告)|材料.{0,12}提交|提交.{0,4}(即可|就行)|上传.{0,8}(材料|证件|照片)/,
  结束语: /感谢您的接听|祝您.{0,12}(顺利|愉快|生意兴隆)|再见/,
};
export { ELEMENT_PATTERNS };

const FORBIDDEN_PHRASES = [
  "保证通过",
  "绝对能过",
  "一定能过",
  "包过",
  "肯定过",
  "百分百通过",
  "封店",
  "关店",
  "罚款",
  "处罚",
  "身份证号",
  "银行卡号",
  "验证码",
  "密码",
];
export { FORBIDDEN_PHRASES };
const NEG_GUARD = ["不要", "不能", "不会", "不得", "无需", "不用", "请勿", "切勿", "勿", "别", "拒绝", "不索要", "不向"];

export function phraseNegated(text, phrase) {
  const s = String(text || "");
  let start = 0;
  while (true) {
    const idx = s.indexOf(phrase, start);
    if (idx < 0) return false;
    const win = s.slice(Math.max(0, idx - 6), idx);
    for (const n of NEG_GUARD) {
      if (win.includes(n)) return true;
    }
    start = idx + 1;
  }
}

// ---------------------------------------------------------------- ASR normalize
export function asrNorm(text) {
  let s = String(text || "").trim();
  if (!s) return "";
  const RE1 = /^[\u4e00-\u9fa5]{0,2}[一以][微味位未为卫][啊呀吧呢哦嘛的呀。？?！!，,\s]*$/;
  if (RE1.test(s)) return "什么意思啊";
  const out = [];
  for (let i = 0; i < s.length; i++) {
    const c = s[i];
    if (c === "麦" || c === "卖" || c === "脉" || c === "迈" || c === "唛") {
      const prev = out.length ? out[out.length - 1] : "";
      const next = s.slice(i + 1, i + 3);
      if (/[京静经净井惊精茎荆晶鲸青清轻晴情请星行幸兴型形续徐许须需旭叙蓄序绪去]/.test(prev)) {
        out.push("麦");
        continue;
      }
      if (/^(是|啥|什么|干|怎|有|在|哪个|哪些)/.test(next)) {
        out.push("麦");
        continue;
      }
    }
    out.push(c);
  }
  s = out.join("");
  for (const [k, v] of Object.entries(ASR_HOMOPHONE_MAP)) {
    if (s.includes(k)) s = s.split(k).join(v);
  }
  return s;
}

// ---------------------------------------------------------------- farewell
const FAREWELL_UNIT_RE =
  /再见|再見|再會|拜拜|拜拜啦|拜拜喽|拜拜咯|白白|白白了|摆摆|掰掰|百百|bye|byebye|回见|回头见|后会有期|挂了电话|挂了哈|我挂了|先挂|挂了|不聊了|不说了|不打扰了|您忙|保重|先走了/i;
const FAREWELL_SOFT_RE = /就这样吧|那就这样|先这样|没别的事了|没有别的事了/;
const FAREWELL_NOISE_RE = /[\s，。！!、,.~…～;；:："'""嗯啊哦噢喔诶额哎唉哈嘿嘞呀呐咯吧嘛的了呢好行对呗喽啦那就也都先再又]+/g;
const FAREWELL_BUSY_RE =
  /\?|？|疑问|问题|怎么|为啥|为什么|哪一部分|哪个|哪里|但是|不过|稍等|等一下|等下|还有|再问|想问|请问|问一句|材料|驳回|提交|后台|费用|资质|审核|整改|密码|验证码|账号|客服|人工|投诉|不是|没听清|听不清|等等|麻烦|帮我|补充|另外|其实|可是|别挂|别走|先别|不方便|没空|改天|明天|下周|周[一二三四五六日末]|登录|证明|核实|电话|换个电话|留个电话|来电|工单|申诉/i;

function farewellCore(text, soft) {
  const s = String(text || "").trim();
  if (!s || s.length > 24) return false;
  if (FAREWELL_BUSY_RE.test(s)) return false;
  const re = soft ? new RegExp(FAREWELL_UNIT_RE.source + "|" + FAREWELL_SOFT_RE.source, "i") : FAREWELL_UNIT_RE;
  if (!re.test(s)) return false;
  const rest = s.replace(new RegExp(re.source, "gi"), "").replace(FAREWELL_NOISE_RE, "");
  return rest.length === 0;
}
export function isFarewell(text) {
  return farewellCore(text, false);
}
export function isFarewellAfterClosing(text) {
  return farewellCore(text, true);
}

// ---------------------------------------------------------------- time expressions
const TIME_DAYS = [
  "大后天",
  "后天",
  "明天",
  "明日",
  "今天",
  "今日",
  "今晚",
  "明早",
  "明晚",
  "明后天",
  "下下周",
  "下周",
  "这周",
  "本周",
  "下个礼拜",
  "下个月",
  "月底",
  "月初",
  "周末",
  "下周末",
];
const TIME_PERIODS = ["上午", "中午", "下午", "晚上", "早上", "工作日"];
export function parseTimeExpr(text) {
  const s = String(text || "");
  const parts = [
    "(?:\\d{4}年)?\\d{1,2}月\\d{1,2}[日号]",
    "(?<!\\d)\\d{1,2}[日号]",
    "(?:[一二三四五六七八九十]{1,3}|廿|卅)[日号]",
    "(?:下下|下|这|本)?(?:周|週|礼拜)[一二三四五六日天]",
    ...TIME_DAYS,
    ...TIME_PERIODS,
  ];
  const re = new RegExp(parts.join("|"));
  const m = s.match(re);
  if (!m) return ["", false];
  let phrase = m[0];
  const after = s.slice(m.index + phrase.length, m.index + phrase.length + 2);
  for (const p of TIME_PERIODS) {
    if (after.startsWith(p)) {
      phrase += p;
      break;
    }
  }
  return [phrase, !TIME_PERIODS.includes(phrase)];
}

// ---------------------------------------------------------------- no_more / no_time / will_promise
const NO_MORE_TOKENS = [
  "没有其他疑问了",
  "没有其他疑问",
  "没有别的问题了",
  "没有其他问题了",
  "其它问题",
  "其他问题",
  "没有问题",
  "没问题了",
  "没疑问了",
  "没有疑问",
  "没疑虑",
  "那就没",
  "那就这样吧",
  "没什么问题了",
  "没啥疑问了",
  "没其他问题",
  "没事了",
  "不用了",
  "不必了",
  "就这些",
  "问完了",
  "先这样",
  "都清楚了",
  "都明白了",
  "清楚了",
  "明白了",
  "都懂了",
  "知道了",
  "可以了",
  "就这样吧",
  "没有了",
  "没了",
  "没有",
  "没啦",
  "没咯",
  "没喽",
  "行了",
  "好了",
  "懂了",
];
const NO_MORE_NOISE_RE =
  /[\s，。！!、,.~…～;；:："'""谢谢|多谢|感谢|好吧|行吧|对对|是的|对了|那个|这个|然后|就是|哎|唉|嗯|呃|额|噢|哦|啊|嘿|喂|哈|诶|欸|昂|恩|那|好|行|对|吧|呀|嘛|呢|啦|咯|喽|呗|是|没|ok|OK|也|还|了|真|确实|我|您|你]+/g;
const NO_MORE_VETO_RE =
  /[?？]|怎么|怎样|为什么|为啥|哪|谁|多少|能不能|可不可以|是否|是不是|有没有|再讲|再说|还有|另外|等一下|我想问|请问|不过|但是|可是|我想|我要|麻烦|帮我|登录|后台|提交|材料|资质|驳回|审核|申诉|费用|发票|京麦|补齐|补充|疑问|问题/;

export function noMorePlain(text) {
  let s = asrNorm(String(text || "").trim());
  if (!s || /[?？]/.test(s)) return false;
  let hit = false;
  for (const t of NO_MORE_TOKENS) {
    if (s.includes(t)) {
      hit = true;
      s = s.split(t).join("");
    }
  }
  if (!hit) return false;
  if (NO_MORE_VETO_RE.test(s)) return false;
  return NO_MORE_NOISE_RE.test(s) ? s.replace(NO_MORE_NOISE_RE, "").length === 0 : s.length === 0;
}

const NO_TIME_RE =
  /不(?:太|大|怎么|是|够|很)?方便|不(?:太)?好(?:去|提交|办|说)|没(?:有)?(?:时间|空|工夫|功夫)|抽不(?:出|开)|腾不(?:出|开)|去不了|办不了|忙不过来|太忙|很忙|最近忙|改天|以后再说|等有空|暂时不|暂时没|下次|过几天|延后|往后推|缓一缓|很久[^。！!？?]{0,6}有空|(?:要|得|得等|要等)很久|等很久|好久(?:之后|以后)|(?:晚点|晚些|过阵子|过段时间|过一阵|等一阵|等一段时间|缓段时间)[^。！!？?]{0,8}(?:再|才有|说|有空)|短期内(?:没|不|难)[^。！!？?]{0,6}|(?:几个月|半年|一年)(?:之后|以后|后)/;
export function noTimeHit(text) {
  return NO_TIME_RE.test(String(text || ""));
}

const NO_DAY_FREE_SCOPE_RE = /哪天|哪一天|哪一日|每天|天天|每日|整天|成天|任何时候|啥时候|什么时候|一直|从来|根本|完全|始终|永远|全年|周周|月月/;
const NO_DAY_FREE_NO_RE = /没(?:有)?(?:空|时间|工夫|功夫)|不(?:太|大|怎么|是|够|很)?方便|去不了|去不成|腾不出|抽不出|抽不开|忙|不行/;
const NO_DAY_FREE_VETO_RE =
  /[?？]|不是|不一定|未必|不见得|也不至于|哪天都行|哪天都可以|哪天方便|什么时候方便|有空|能去|可以去|都行|随便哪天|\d{1,2}[日号]|(?:下下|下|这|本)?(?:周|週|礼拜)[一二三四五六日天]|后天|明天|今日|今天|下个月|月底|月初/;
export function noDayFreeHit(text) {
  const s = asrNorm(String(text || ""));
  if (!s || s.length > 40 || NO_DAY_FREE_VETO_RE.test(s)) return false;
  return NO_DAY_FREE_SCOPE_RE.test(s) && NO_DAY_FREE_NO_RE.test(s);
}

const WILL_PROMISE_CORE = new Set([
  "我去",
  "我去改",
  "我去办",
  "我去提交",
  "我去补",
  "我去弄",
  "我去处理",
  "我去准备",
  "我马上就去",
  "我马上去",
  "我立马去",
  "我立刻去",
  "我立即去",
  "我现在就去",
  "我现在去",
  "我这就去",
  "我自己去",
  "我亲自去",
  "我自行去",
  "马上去",
  "立马去",
  "立刻去",
  "立即去",
  "现在就去",
  "现在去",
  "这就去",
  "自己去",
  "亲自去",
  "自行去",
  "马上改",
  "马上提交",
  "马上补",
  "马上办",
  "马上处理",
  "我马上改",
  "我马上提交",
  "我马上补",
  "我马上办",
  "我马上处理",
  "这就改",
  "这就提交",
  "自己去改",
  "自己去提交",
  "我自己去改",
  "我自己去提交",
  "我马上去改",
  "我马上就去改",
  "我现在就去改",
  "我现在就去提交",
  "等下就去",
  "等会儿就去",
  "待会就去",
  "待会儿就去",
  "一会就去",
  "一会儿就去",
  "回头就去",
  "今天就去",
  "稍后就去",
  "我明天就去",
  "我待会就去",
  "我一会就去",
]);
const WILL_PROMISE_RE =
  /(?:马上|立马|立刻|立即|即刻|现在|这就|这会儿|待会|待会儿|一会|一会儿|等下|等会儿|等一下|回头|稍后|今天|明天|自己|亲自|自行|我来|下周|下个?月|后天|周末|下周末|明天上午|明天下午)[^。！!？?]{0,8}?(?:去|改|提交|办|处理|补|准备|弄|上传|整理)/;
const WILL_PROMISE_VETO_RE =
  /[?？]|为什么|为啥|怎么|咋|哪里|哪天|什么时候|能不能|可不可以|是不是|有没有|不懂|不明白|不清楚|不知道|凭什么|再说吧|要不要|还是|或者|不去|不提交|不改|改不了|提交不了|去不了|办不了|做不到|不会|没法|没办法|不能|没(?:有)?(?:时间|空|工夫|功夫)|不方便|太忙|很忙|最近忙|改天|以后再说|下次|过几天|延后|往后推|缓一缓|等有空|暂时不|暂时没/;
const WILL_PROMISE_SIDE_RE = /去(?:问|找|看看|查|打听|想想|考虑|研究|聊|说|拿|接|买|吃饭)/;
const WILL_PROMISE_NOISE_RE =
  /[\s，。！!、,.~…～;；:："'""嗯|呃|额|哎|唉|哦|噢|啊|哈|诶|欸|好吧|好的|行|对|吧|呀|嘛|呢|啦|呗|喽|咯]+/g;

export function willPromiseHit(text) {
  const s = asrNorm(String(text || ""));
  if (!s || s.length > 40) return false;
  if (WILL_PROMISE_VETO_RE.test(s) || WILL_PROMISE_SIDE_RE.test(s)) return false;
  const core = s.replace(WILL_PROMISE_NOISE_RE, "");
  if (!core) return false;
  if (WILL_PROMISE_CORE.has(core)) return true;
  return WILL_PROMISE_RE.test(s);
}

const S4_AFFIRM_RE = /方便|有空|可以|好的?|行|没问题|会的?|尽快|提交|整改|补齐|马上|这就|待会|回头|尽量|尽力|愿意|配合|去办|处理/;
const S4_AFFIRM_NEG_RE =
  /不方便|没空|没时间|不去|不提交|不改|改不了|不会|没法|没办法|不能|再说吧|改天|下次|？|\?|吗|为什么|怎么|哪里|哪天|几号|什么时候|什么|凭啥|凭什么/;
export function s4AckAffirm(text) {
  const s = asrNorm(String(text || ""));
  if (!s || s.length > 16) return false;
  if (S4_AFFIRM_NEG_RE.test(s)) return false;
  return S4_AFFIRM_RE.test(s);
}

// ---------------------------------------------------------------- fake doubt / pushback
const SELF_FAKE_DOUBT_RE =
  /你是不是|你是机器人|机器人吧|你是ai|是不是ai|你是假的|假人|真人吗|你是真人|是不是骗子|骗子吧|诈骗|冒充|录音吧|你是不是在骗|骗人的吧|假的吧/i;
const FAKE_REQUEST_VERB_RE =
  /帮我|给我|替我|让你|叫我|能不能帮|可不可以帮|做个|做一份|办个|买个|搞个|弄个|p一下|p个|ps|修图|抠图|刻章|刻个/i;
export { FAKE_REQUEST_VERB_RE };
export function isFakeDoubt(text) {
  const s = String(text || "").trim().toLowerCase();
  if (!s || !SELF_FAKE_DOUBT_RE.test(s)) return false;
  return !FAKE_REQUEST_VERB_RE.test(s);
}

const FAKE_PUSHBACK_RE =
  /(材料|证件|资料|发票|合同|流水|证明|注册证|授权书).{0,8}(是真的|都是真的|没改|没动|没造假|没作假|是原件|都是原件|原件)|我(们)?(这边)?(没|没有)(改|动|造假|伪造|作假|P过|ps过)|(这|那|都)(是|就是)?原件|你不信|不信你(去)?查|凭什么(不|说|认为)/i;
export function isFakePushback(text) {
  const s = String(text || "").trim();
  if (!s || s.length > 40) return false;
  return FAKE_PUSHBACK_RE.test(s);
}

// ---------------------------------------------------------------- identity
const NAME_CHALLENGE_RE =
  /你是哪位|您是哪位|你是哪个|您是哪位|你谁啊|你是谁|哪个部门的|你是干什么的|哪个公司的|你是不是京东的|你凭什么|怎么证明|怎么核实|哪来的|什么人|打哪来|什么单位|你叫什么/;
const S1_CORRECT_RE = /不是不是|不不|说错了/;
const S1_DENY_BRIEF_RE = /^[不没别][不对是没别，,。！!？?\s]{0,7}$/;
const S1_SELFPROOF_RE =
  /我是(?:这家|这个|本店|该店|店铺)?(?:的)?(?:负责人|老板|店主|店长|法人|负责人本人)|就是我|本人|我是老板|我是店主|我是法人/;
const CONTACT_TITLE = ["女士", "先生", "小姐", "主管", "经理", "总监", "总", "老师", "师傅", "工程师"];
const IDENTITY_RULES = [
  [
    "COMPLAINT",
    /投诉你|投诉你们|我要投诉|举报你|举报你们|我要举报|找你领导|叫你们领导|上级|曝光你们|发到网上|12315|消协/i,
  ],
  ["HUMAN", /转人工|人工客服|找人工|转接人工|人工服务|找真人|真人客服|要人工|叫个人|找个人跟我说/i],
  [
    "ENTITY_CHANGED",
    /已经不是我了|早就不做了|换老板了|转出去了|店关了|公司注销|已经注销|转让了|换人了|不是我这家|已经关了/i,
  ],
  ["DEPUTY", /他不在|她不在|不在公司|出差了|负责人不在|老板不在|我是员工|我是店员|我是客服|代接|替他接/i],
  ["FETCH_OWNER", /我叫(?:他|她|负责人|老板)|我去叫|我喊(?:他|她)|我找他来|稍等.{0,4}(叫|喊)|我给您找|我让他接/i],
  ["REPEAT", /再说一遍|没听清|听不清|再说一次|重复一下|你刚才说什么|什么没说清|声音太小|听不到|信号不好/i],
  [
    "AGENT_QUERY",
    /你是机器人|你是ai|是不是机器人|你是不是ai|真人吗|你是真人|假人|机器人吧|你是假的|录音吧|智能客服|数字人|机器客服|ai客服/i,
  ],
  [
    "REFUSE_VERIFY",
    /不告诉你|凭什么告诉你|不确认|没必要确认|不用确认|不想说|不方便说|不想回答|为什么要告诉你|我为什么要确认/i,
  ],
  ["DENY", /(?<!是)不是|找错|不认识|打错|没这人|没有这个人|号码不对/i],
];
export function isNameChallenge(text) {
  const s = String(text || "").trim();
  if (!s || s.length > 30) return false;
  return NAME_CHALLENGE_RE.test(s);
}
const CONTACT_ASK_RE = /负责人(?:姓什么|叫什么|是哪位|怎么称呼|电话)|你们老板(?:姓什么|是谁)|老板姓什么|负责人是谁/;
export function isContactAsk(text) {
  const s = String(text || "").trim();
  if (!s || s.length > 40) return false;
  return CONTACT_ASK_RE.test(s);
}
export function identityKind(text) {
  const s = String(text || "").trim();
  if (!s) return "";
  if (S1_CORRECT_RE.test(s)) {
    if (S1_SELFPROOF_RE.test(s)) return "AFFIRM";
    if (S1_DENY_BRIEF_RE.test(s)) return "DENY";
    return "";
  }
  for (const [kind, re] of IDENTITY_RULES) {
    if (re.test(s)) return kind;
  }
  if (S1_SELFPROOF_RE.test(s)) return "AFFIRM";
  if (affirmStrongHit(s)) return "AFFIRM";
  return "";
}
export { affirmStrongHit };
export function contactBits(contact) {
  const c = String(contact || "").trim();
  if (!c || ["未提供", "无", "-", "—", "未知"].includes(c)) return ["", "", ""];
  const name = c.slice(0, 6);
  const surname = name[0] || "";
  if (name.length > 1 && CONTACT_TITLE.includes(name.slice(1))) return [name, surname, name];
  for (const t of CONTACT_TITLE) {
    if (name.endsWith(t)) return [name, name[0], name];
  }
  return [name, surname, surname];
}

// ---------------------------------------------------------------- trust / distrust
const TRUST_DOUBT_RE =
  /你是骗子|诈骗|骗人的吧|骗子公司|怎么证明你是京东|凭什么相信|不信任|不可信|信不过|你是假的|不敢相信|怀疑你|冒充的吧|假的吧|真的假的|骗人的|我不信|不敢信|怀疑|核实一下|怎么核实/i;
const TRUST_OK_RE = /相信你|信你|信任你|可以相信|那我信|好的信你|信了|放心吧相信|相信|我信|信得过|可以信/i;
const DISTRUST_STRONG_RE =
  /你是骗子|骗子|诈骗|冒充|假京东|钓鱼|骗钱|骗人的|不可信|信不过|不敢相信|我不信|不敢信|明显是假|百分百是假|肯定是假|录音了|我要报警|报警/i;
const DISTRUST_BRIEF_RE = /真的假的|假冒|信你才怪|滚|别骗我|骗我|套路/i;
const DISTRUST_BUSINESS_RE =
  /资质|审核|驳回|材料|补|提交|后台|京麦|商标|授权|类目|发票|审核|申诉|费用|多少钱|多久|几天|怎么弄|怎么办|哪里|怎么操作/i;
export function isTrustDoubt(text) {
  const s = String(text || "").trim();
  if (!s) return false;
  return TRUST_DOUBT_RE.test(s);
}
export function isDistrust(text, inTrustCtx = false) {
  const s = String(text || "").trim();
  if (!s || s.length > 24) return false;
  if (DISTRUST_STRONG_RE.test(s)) return true;
  if (inTrustCtx && DISTRUST_BRIEF_RE.test(s)) return true;
  return false;
}
export function isTrustConfirm(text) {
  const s = String(text || "").trim();
  if (!s) return false;
  if (isTrustDoubt(s) || isDistrust(s, true)) return false;
  if (DISTRUST_BUSINESS_RE.test(s)) return false;
  return TRUST_OK_RE.test(s);
}

const REFUSE_STILL_RE =
  /不补|不改|不弄|不交|不提交|不愿意|不想|就不|没门|算了不|不准备|懒得|没必要|爱咋咋地|别再问|烦不烦|用不着|管不着|管不了我|拿我怎样/i;
const ASK_CONSEQUENCE_RE = /会怎样|有什么影响|后果|会不会|有影响吗|会怎么样/i;
export function isRefusalStill(text) {
  const s = String(text || "").trim();
  if (!s) return false;
  if (ASK_CONSEQUENCE_RE.test(s)) return false;
  return REFUSE_STILL_RE.test(s);
}
export function findfaultHit(text) {
  const s = String(text || "").trim();
  if (!s) return "";
  if (isDistrust(s) || TRUST_DOUBT_RE.test(s)) return "scam";
  if (isRefusalStill(s)) return "refuse";
  return "";
}

// ---------------------------------------------------------------- agent identity doubt
const AGENT_IDENTITY_CORE_RE =
  /你是机器人|你是ai|是不是机器人|你是不是ai|你是真人吗|真人吗|你是真人|假人|机器人吧|你是假的|不是真人|智能客服|数字人|机器客服|ai客服|语音助手|自动回复/i;
const AGENT_IDENTITY_VETO_RE =
  /造假|伪造|作假|假材料|假证|授权书|商标|类目|发票|费用|怎么登录|怎么提交|怎么补|哪里提交|多久|什么时候能|材料|资质/i;
const AGENT_IDENTITY_KEEP_RE = /身份|诈骗|骗子|冒充|工号|机器人|官方|哪个部门|怎么证明/i;
export function isAgentIdentityDoubt(text) {
  const s = String(text || "").trim();
  if (!s || s.length > 45) return false;
  if (!AGENT_IDENTITY_CORE_RE.test(s)) return false;
  if (AGENT_IDENTITY_VETO_RE.test(s) && !AGENT_IDENTITY_KEEP_RE.test(s)) return false;
  return true;
}

// ---------------------------------------------------------------- qa / offtopic family
const QA_HAS_RE = /有(?:个|一个)?(?:问题|疑问)|问一下|问个问题|我想问|请教|还有个问题|再问|还有问题|问您/i;
const COMPLIMENT_RE = /你说的(?:很|挺)?好|讲得(?:很|挺)?清楚|服务(?:很|挺)?好|态度(?:很|挺)?好|专业|辛苦|费心|谢谢你讲解|讲得明白/i;
const QA_FORGOT_RE = /忘了|没记住|记不住|记不清|不记得|再说一下|再说一遍|重复|没记下/i;
const PURE_FILLER_RE = /^(?:嗯+|哦+|噢+|啊+|哈+|呵+|嘿+|诶+|欸+|好+|行+|对+|是+|可+|这+|那+|我+|你+)[。，！!？?、\s]*$/;
const SOCIAL_TOKENS = ["你好", "您好", "在吗", "喂", "哈喽", "hello", "嗨", "早上好", "下午好", "晚上好", "谢谢", "多谢", "感谢"];
const SOCIAL_STRIP = /你好|您好|在吗|喂|哈喽|hello|嗨|早上好|下午好|晚上好|谢谢|多谢|感谢/gi;
const BUSINESS_SIGNAL_RE =
  /资质|审核|驳回|材料|补|提交|后台|京麦|商标|授权|类目|发票|申诉|费用|收费|多少钱|复审|文审|品牌|入驻|店铺|证件|证明|执照|章|链路|上传|下载|修改|整改|过审|通过|不通过|结果|进度|通知|工单|热线|专员|工号|实名|结算|账号|法人|登录|注册|官网|平台|规则|要求|标准|流程|多久|几天|复印|扫描|原件|过期|失效|年检|公示|信息|资料|单号|订单|扣分|处罚|封店|怎么[^。！!？?]{0,4}(改|办|弄|处理|操作|补|开|填|修|解决|弄好|过|通过)|(哪里|在哪|什么地方)[^。！!？?]{0,4}(改|办|弄|提交|上传|下载|看|查|修)|疑问|疑虑|顾虑|问题|什么意思|啥意思|含义|涵义|看了|看到|查看|没有什么?问题|没什么问题|我想问|请问|问一下|请教|咨询|了解一下|打听|帮我看看|帮我查/;
const OFFTOPIC_BUSY_RE =
  /天气|股票|基金|电影|旅游|足球|篮球|王者|游戏|吃饭|吃的|减肥|护肤|身材|结婚|对象|工资|你多大|年龄|星座|音乐|唱歌|笑话|段子|喝酒|咖啡|奶茶|宠物|猫|狗|放假|周末去哪|电视剧|综艺|网红|新闻|国家|政治/i;
const FLOW_VETO_RE = /等|稍|马上|这就|现在|回头|先|然后|接着|再说|后面/i;
const GO_AHEAD_RE = /你说|请讲|说吧|讲吧|继续|然后呢|接着|往下说|你说吧|可以说了|那你讲|说吧./;
const ACK_TOKEN_RE = /^(?:嗯+|哦+|噢+|啊+|好+|行+|对+|是+|可以|没问题|明白|知道了|懂了)+[。，！!？?、\s]*$/;
const LOOKUP_ASK_RE = /什么叫|啥叫|何为|何谓|什么是|啥是|是啥子|咋理解|怎么理解/;
export function qaHasQuestion(text) {
  let s = String(text || "").trim().replace(/[。！？!?~\s]+$/g, "");
  if (!s || s.length > 30) return false;
  if (/没(有)?(问题|疑问|别的|其他|什么)/.test(s)) return false;
  return /[？?哪什么为啥为什么怎么啥吗]/.test(s) && QA_HAS_RE.test(s);
}
export function isCompliment(text) {
  const s = String(text || "").trim();
  if (!s || s.length > 40) return false;
  if (/耍聪明|自作聪明|小聪明|别夸|少来|讥讽|嘲讽/.test(s)) return false;
  return COMPLIMENT_RE.test(s);
}
export function qaForgot(text) {
  const s = String(text || "").trim().replace(/[。！？!?~\s]+$/g, "");
  if (!s || s.length > 14) return false;
  if (/别忘|不会忘|没忘|记着|记得|带上|提交/.test(s)) return false;
  return QA_FORGOT_RE.test(s);
}
export function isPureAck(text) {
  const s = String(text || "").trim().replace(/[。，！!？?、~\s]+$/g, "");
  if (!s || s.length > 12) return false;
  return ACK_TOKEN_RE.test(s);
}
export function isLookupQuestion(text) {
  return LOOKUP_ASK_RE.test(String(text || ""));
}
export function isSocialGreeting(text) {
  const s = String(text || "").trim();
  if (!s || s.length > 12) return false;
  if (BUSINESS_SIGNAL_RE.test(s)) return false;
  return SOCIAL_TOKENS.some((t) => s.includes(t));
}
export function offtopicContinuation(text) {
  const s = String(text || "").trim();
  if (s.length < 2 || s.length > 24) return false;
  if (PURE_FILLER_RE.test(s)) return false;
  if (OFFTOPIC_BUSY_RE.test(s)) return false;
  if (BUSINESS_SIGNAL_RE.test(s)) return false;
  return true;
}
export function isGoAhead(text) {
  const s = String(text || "").trim();
  if (!s || s.length > 20) return false;
  if (/不|别|暂停|等等|等下|慢|停/.test(s)) return false;
  return /你说|请讲|说吧|继续|然后呢|接着|往下说/.test(s) || isGoAheadLoose(s);
}
function isGoAheadLoose(s) {
  return /^[嗯哦噢啊好行对是的，。\s]*$/.test(s) && s.length > 0;
}
const PLAIN_YES_RE = /^(?:是|对|是的|对的|对我|是我|就是|嗯对|对对|没错)[的吧呀啊呢。！!？?，,\s]*$/;
function affirmStrongHit(t) {
  const strong = ["是我", "我是", "是的", "嗯对", "对对", "对的", "没错", "本人", "就是我", "对，", "对。", "对！", "你说", "请讲", "在的"];
  if (strong.some((w) => t.includes(w))) return true;
  if (PLAIN_YES_RE.test(t)) return true;
  if (isGoAhead(t)) return true;
  return false;
}
export function offtopicGeneric(text) {
  const s = asrNorm(String(text || "").trim());
  if (!s || s.length < 2) return false;
  if (isSocialGreeting(s)) return false;
  if (isPureAck(s)) return false;
  if (BUSINESS_SIGNAL_RE.test(s)) return false;
  if (LOOKUP_ASK_RE.test(s)) return false;
  if (identityKind(s)) return false;
  if (willPromiseHit(s)) return false;
  if (noMorePlain(s)) return false;
  if (noTimeHit(s)) return false;
  if (isFakeDoubt(s)) return false;
  if (isAgentIdentityDoubt(s)) return false;
  if (isTrustDoubt(s)) return false;
  if (isDistrust(s)) return false;
  if (findfaultHit(s)) return false;
  if (isNameChallenge(s)) return false;
  if (FLOW_VETO_RE.test(s)) return false;
  return true;
}

// ---------------------------------------------------------------- emotion
export function classifyEmotion(text) {
  const s = String(text || "");
  if (/骗子|混蛋|垃圾|投诉你|滚|去死|诈骗|恶心|神经病/.test(s)) return "angry";
  if (/怎么办|急|着急|麻烦|再不|等不了|完蛋|耽误/.test(s)) return "anxious";
  if (/明白了|清楚了|知道了|好的谢谢|谢谢|理解了/.test(s)) return "satisfied";
  return "neutral";
}

// ---------------------------------------------------------------- sanitize / safe sentence
const PLACEHOLDER_PATTERNS = [
  /【[^】]*?(?:具体|缺失|详情|待填|待补充|未提供|未知|占位|某某|某类|某品牌|XXX|公司名|店铺名称|授权关系|授权链)[^】]*?】/g,
  /（[^）]*?(?:具体|缺失|详情|待填|待补充|未提供|未知|占位|某某|某类|XXX|公司名|店铺名称|授权关系|授权链)[^）]*?）/g,
  /（商家公司名[^）]*?）/g,
  /（店铺名称[^）]*?）/g,
  /【[^】]{0,12}】/g,
];
const FALLBACK_PHRASE = "以京麦商家后台的驳回意见为准";
export const FALLBACK_PHRASE_TXT = FALLBACK_PHRASE;
export function toSentences(text) {
  return String(text || "")
    .split(/(?<=[。！？；!?;])/)
    .map((p) => p.trim())
    .filter((p) => p.length > 0);
}

export function sanitizeReply(text) {
  let out = String(text || "");
  for (const p of PLACEHOLDER_PATTERNS) out = out.replace(p, FALLBACK_PHRASE);
  out = out.split(FALLBACK_PHRASE + FALLBACK_PHRASE).join(FALLBACK_PHRASE);
  out = out.replace(/\s*，\s*，\s*/g, "，");
  return out.trim();
}

export function safeSentence(frag, stage) {
  const s = String(frag || "").trim();
  if (!s) return "";
  for (const bad of FORBIDDEN_PHRASES) {
    if (s.includes(bad) && !phraseNegated(s, bad)) {
      return SAFE_SENTENCE[stage] || SAFE_SENTENCE[4];
    }
  }
  return s;
}

// ---------------------------------------------------------------- compliance
export function checkCompliance(history, identityConfirmed = null, gate = true) {
  const ORDER = REQUIRED_ELEMENTS;
  const seen = {};
  for (const k of ORDER) seen[k] = false;
  const matchedStage = {};
  let confirmed = identityConfirmed === null || identityConfirmed === undefined ? null : !!identityConfirmed;
  const violations = [];
  for (const h of history || []) {
    const text = String(h.content || "");
    if (h.role !== "assistant") {
      if (confirmed === null && h.identity_confirmed) confirmed = true;
      continue;
    }
    const st = h.stage;
    for (const [key, re] of Object.entries(ELEMENT_PATTERNS)) {
      if (re.test(text)) {
        seen[key] = true;
        if (typeof st === "number" && st > 0) {
          const need = ELEMENT_STAGE[key] || 1;
          if (st >= need && (matchedStage[key] === undefined || st < matchedStage[key])) matchedStage[key] = st;
        }
      }
    }
    for (const bad of FORBIDDEN_PHRASES) {
      if (text.includes(bad) && !phraseNegated(text, bad)) violations.push({ role: "agent", phrase: bad });
    }
  }
  const checks = {};
  let okSoFar = true;
  for (const k of ORDER) {
    let eligible = k in matchedStage || (!gate && seen[k]);
    if (k === "身份确认") eligible = eligible && (confirmed === null ? true : confirmed);
    const done = eligible && okSoFar;
    checks[k] = done;
    if (!done) okSoFar = false;
  }
  const passed = Object.values(checks).filter(Boolean).length;
  const deliveredStage = Math.max(0, ...ORDER.filter((k) => checks[k]).map((k) => matchedStage[k] || 0));
  const stage = deliveredStage || (seen[ORDER[0]] ? ELEMENT_STAGE[ORDER[0]] || 1 : 0);
  const missing = ORDER.filter((k) => !checks[k]);
  const earlyMentioned = ORDER.filter((k) => seen[k] && !checks[k]);
  return {
    elements: checks,
    rate: passed / ORDER.length,
    missing,
    violations,
    passed,
    total: ORDER.length,
    elements_seen: seen,
    element_stage: { ...ELEMENT_STAGE },
    stage,
    stage_name: STAGE_NAMES_4[Math.min(stage, 4)] || "",
    delivered_stage: deliveredStage,
    early_mentioned: earlyMentioned,
    identity_confirmed: confirmed,
  };
}

// ---------------------------------------------------------------- redline
const NEG_TAIL_RE = /(不|没|没有|别|无需|不用|不是|非)[^，。！？；]{0,5}$/;
const QUESTION_FLUFF_RE = /能不能|可不可以|会不会|是否|是不是|要不要|有没有|行不行|对不对|好不好/g;
function negated(text, start) {
  const win = text.slice(Math.max(0, start - 6), start).replace(QUESTION_FLUFF_RE, "");
  return NEG_TAIL_RE.test(win);
}

function replyFor(key) {
  const b = B_SCRIPT[key];
  if (b) return [b.say, b.guide, b.close].filter(Boolean).join("");
  return REPLY[key] || REPLY.OVER_PROMISE;
}
function correctionFor(key) {
  const c = CORRECT[key];
  if (c && (c.say || c.guide)) return { say: c.say || "", guide: c.guide || "" };
  const b = B_SCRIPT[key] || {};
  return { say: b.say || "", guide: b.guide || "" };
}
function isAlert(key) {
  return key in ALERT ? ALERT[key] : true;
}

function makeHit(cat, phrase, how) {
  const level = STRICT || cat.level === "B" ? "B" : "A";
  const sc = level === "B" ? B_SCRIPT[cat.key] || null : null;
  return {
    hit: true,
    key: cat.key,
    name: cat.name,
    level,
    base_level: cat.level,
    excel: cat.excel,
    desc: cat.desc,
    phrase,
    how,
    action: level === "B" ? "terminate" : "warn",
    reply: replyFor(cat.key),
    say: sc ? sc.say || "" : "",
    guide: sc ? sc.guide || "" : "",
    close: sc ? sc.close || "" : "",
    tag: sc ? sc.tag || "" : "",
    mark: level === "B",
    non_task_end: level === "B",
    alert: isAlert(cat.key),
    correct: correctionFor(cat.key),
    pull_back: PULL_BACK,
  };
}

export function classifyRedline(text) {
  const t = String(text || "").trim().toLowerCase();
  if (!t) return { hit: false };
  for (const cat of CATEGORIES) {
    const vetoes = cat.veto || [];
    const anties = cat.antiveto || [];
    const vetoed = vetoes.some((p) => new RegExp(p, "i").test(t));
    const blocked = anties.some((p) => new RegExp(p, "i").test(t));
    if (vetoed && !blocked) continue;
    for (const w of cat.words || []) {
      const idx = t.indexOf(String(w).toLowerCase());
      if (idx >= 0 && !negated(t, idx)) return makeHit(cat, w, "keyword");
    }
    for (const p of cat.patterns || []) {
      const m = t.match(new RegExp(p, "i"));
      if (m && !negated(t, m.index)) return makeHit(cat, m[0], "pattern");
    }
  }
  return { hit: false };
}

export function redlineAsHit(key) {
  const cat = CATEGORIES.find((c) => c.key === key);
  if (cat) return makeHit(cat, "（语义判定命中·建议人工复核）", "semantic");
  return {
    hit: true,
    key,
    name: key,
    level: "B",
    phrase: "（语义判定命中）",
    how: "semantic",
    action: "terminate",
    reply: replyFor(key),
  };
}

export function redlineCategories() {
  return CATEGORIES.map((c) => ({
    key: c.key,
    name: c.name,
    level: c.level,
    excel: c.excel,
    desc: c.desc,
    words: c.words || [],
    patterns: c.patterns || [],
    veto: c.veto || [],
    antiveto: c.antiveto || [],
  }));
}

export {
  REPLY,
  B_SCRIPT,
  CORRECT,
  ALERT,
  SEVERE_LIMIT,
  PULL_BACK,
  ANOTHER_OPEN,
  ACK_BACK_ASK,
  ACK_BACK,
  SEVERE_TEXT,
  REFUSE_TEXT,
};
