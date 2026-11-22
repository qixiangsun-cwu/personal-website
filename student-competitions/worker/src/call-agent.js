// Port of backend/agent.py :: class CallAgent (JS)
import * as RP from "./rules.js";
import * as prompts from "./prompts.js";
import { searchKnowledge, priorCalls, riskProfile } from "./server-helpers.js";
import * as llm from "./llm.js";

const {
  HOTLINE,
  S0, S1, S2, S3, S4, S5, E1, E2, E3, E4, E5, END,
  MIN_TURNS_TO_CLOSE,
  MAX_TURNS_HARD,
  MAX_STALL_ROUNDS,
  REQUIRED_ELEMENTS,
  ELEMENT_PATTERNS,
  ELEMENT_STAGE,
  STAGE_NAMES_4,
  STAGES_5,
  STATE_STAGE5,
  STAGE_OF_STATE,
  SAFE_SENTENCE,
  ADVANCE_STATE,
} = RP;

const _NOW = () => Date.now() / 1000;

// ---------------------------------------------------------------- fixed scripts
export const FIXED_INBOUND_OPENING =
  "您好，这里是京东商家服务热线，请问有什么可以帮您？如果是关于资质审核的问题，您可以直接说。";
export const FIXED_OPENING =
  "您好，我是京东商家资质审核专员，本次来电是关于贵店资质审核结果，请问您是{company}的负责人吗？";
export const FIXED_INBOUND_REGREET = "您好，请讲。您想咨询资质审核的哪方面问题？可以直接说。";
export const FIXED_CLOSING =
  "好的，整改要求我已说明清楚。如需咨询可拨打京东商家服务热线{hotline}。感谢您的接听，祝您工作顺利，再见。";
export const FIXED_INBOUND_CLOSING =
  "好的，感谢您的来电。后续如还有问题，欢迎再次拨打京东商家服务热线{hotline}，或在京麦后台查看驳回意见。祝您工作顺利，再见。";
export const FIXED_REASON = "您这次的驳回原因是{scenario}。";
export const FIXED_ACTION = "需要您登录京麦后台，按驳回意见把材料补齐后重新提交。";
export const FIXED_SERVICE_ERROR =
  "我是京东商家资质审核专员。您这次的驳回原因是{scenario}；需要您登录京麦后台，按驳回意见把材料补齐后重新提交。如需咨询可拨打京东商家服务热线{hotline}。感谢您的接听，再见。";
export const FIXED_EMOTIONAL =
  "我是京东商家资质审核专员。理解您的心情，您这次的驳回原因是{scenario}，需要您登录京麦后台按驳回意见补齐材料后重新提交；相关情况也可拨打商家服务热线{hotline}反馈。感谢您的接听，再见。";
export const FIXED_OUT_OF_SCOPE =
  "我是京东商家资质审核专员。抱歉，这个问题超出资质审核范围；关于贵店资质驳回，需要您按京麦后台驳回意见补齐材料后重新提交，也可拨打商家服务热线{hotline}咨询。感谢您的接听，再见。";
export const FIXED_UNCLEAR = "不好意思，刚才没听清，您能再说一遍吗？";
export const FIXED_QA = "刚才跟您说的驳回原因和整改方法，您这边还有没有疑问？";
export const FIXED_QA_INBOUND =
  "关于您咨询的问题，具体可以登录京麦后台查看驳回意见，或拨打京东商家服务热线{hotline}核实。您还有其他想咨询的吗？";
export const FIXED_WILLING = "那您后续方便尽快去京麦后台重新提交吗？";
export const FIXED_RESCHEDULE_ASK =
  "理解您最近比较忙。那您看哪天方便？我把提交时限给您往后顺延，需要补齐的材料您先收着，随时登录京麦后台提交就行。您给个大概时间，我这边给您备注上。";
export const FIXED_RESCHEDULE_ASK2 =
  "没关系，那您大概哪天能腾出时间？说个日期就行，我这边给您备注，届时不会重复打扰您。";
export const FIXED_RESCHEDULE_WHICH_DAY = "好的，那您是指哪一天？您说个具体日期，我这边给您备注上。";
export const FIXED_RESCHEDULE_OK =
  "好的，那就按您说的{time}给您备注上，届时您登录京麦后台，按驳回意见把材料补齐后重新提交即可。如需咨询可拨打京东商家服务热线{hotline}。感谢您的接听，祝您工作顺利，再见。";
export const FIXED_RESCHEDULE_LATER =
  "好的，那我就不占用您时间了。您方便的时候随时登录京麦后台，按驳回意见把材料补齐后重新提交即可，有问题也可以拨打京东商家服务热线{hotline}。感谢您的接听，再见。";
export const FIXED_CONFIRM = "好的，感谢您的确认。下面我为您说明本次资质审核的驳回原因和整改要求。";
export const RECONFIRM_ACK_RE = /^(?:(?:好的?|是的?|对|嗯|行|可以|没错|成|中|好嘞|好滴|好呀|好呢|好哈|好哦)[的吧呀啊呢哈哦嘛哟咧，,。！!？?、\s]{0,4}){1,8}$/;
export const FIXED_EXPLAIN_QA =
  "您这次的驳回原因是{scenario}。需要您登录京麦后台，按驳回意见把材料补齐后重新提交。以上是本次的驳回原因和整改方法，您这边还有没有疑问？";
export const FIXED_REDELIVER =
  "那我先把这次的驳回原因和整改要求跟您说清楚：您这次的驳回原因是{scenario}，需要您登录京麦后台，按驳回意见把材料补齐后重新提交。这样您这边还有没有疑问？";
export const FIXED_HESITANT = "您看是哪一部分还有疑虑？我可以再给您具体说明一下。";
export const FIXED_HESITANT2 = "您具体是哪一块不太清楚？我针对性再给您讲一遍。";
export const FIXED_INBOUND_HESITANT = "好的。那您还有其他想咨询的问题吗？可以直接说。";
export const FIXED_WILL_HESITANT =
  "那您这边是打算自己去京麦后台提交，还是有什么不方便的地方？您跟我说一下，我帮您看怎么处理更合适。";
export const FIXED_WILL_HESITANT2 = "您是暂时不方便操作，还是对要提交什么材料有疑问？您说一下情况，我帮您想办法。";
export const FIXED_LOOP_END =
  "好的，那这边先不打扰您了。您后续可以登录京麦商家后台查看驳回意见并整改，如需人工协助可拨打京东商家服务热线{hotline}。感谢您的接听，再见。";
export const FIXED_VERIFY_AGAIN = "不好意思，跟您确认一下，请问您是{company}的负责人吗？";
export const FIXED_VERIFY_AGAIN2 =
  "是这样，为防止审核信息泄露，我需要跟店铺负责人本人核对。请问您是{company}的负责人本人吗？";
export const FIXED_VERIFY_AGAIN3 =
  "我这边的登记信息是打给{company}负责人的，需要本人确认后才能继续。您这边是负责人本人吗？";
export const FIXED_VERIFY_WARM =
  "不好意思，刚才可能没说清楚：我是京东商家资质审核专员，本次来电是关于贵店资质审核结果的官方回访，您也可以拨打商家服务热线{hotline}核实。请问您是{company}的负责人吗？";
export const TRUST_LAST_TEXT =
  "再跟您说明一次：我是京东商家资质审核专员，本次的信息您都可以在京麦后台核对，也可以拨打商家服务热线核实，我们不会向您索要任何账号、密码或验证码。如果您还是不放心，我们这次就先到这里，由人工专员再与您联系。那您现在愿意相信我吗？";

const _STATE_HINTS_FALLBACK = {
  [S1]: "商家已确认身份。现在进入第三段「情况说明」，先说驳回原因，再给整改动作。",
  [S3]: "商家答疑段：回答商家疑问，不要说结束语。",
};

export const ACK_WORDS = [
  "我错了", "对不起", "抱歉", "不好意思", "我不该", "不应该", "是我考虑", "不搞了", "不做了", "不提了",
  "不这样", "按规矩", "照规矩", "听你的", "我去改", "我来改", "我会改", "我马上改", "我这就改", "我认",
  "明白", "知道了", "懂了", "好的我", "没说给你", "不是说给你", "不是说你", "不是给你", "说给审核",
  "给审核", "说错了", "口误", "我意思是", "我不是这个意思", "没有这个意思", "开玩笑", "玩笑", "逗你",
  "问问", "随便问问", "就是问问", "误会",
];

const NO_TOPUP_INTENTS = new Set([
  "NEED_CLARIFICATION", "WILL_CLARIFY", "WILL_RESCHEDULE", "RESCHEDULE_ASK", "RESCHEDULE_WHICH_DAY",
  "QA_CONFIRMED", "UNDERSTOOD", "ASK_QUESTION", "HESITANT", "HESITANT_LOOP", "QA_ASK",
  "EXPLAIN_REDELIVER", "VIO_ACK", "VIO_ACK_BACK", "VIO_ACK_WILL", "VIOLATION", "VIOLATION_SEVERE",
  "MERCHANT_REFUSE", "VERIFY_AGAIN", "VERIFY_WARM", "CONFIRMED_ONLY", "RECONFIRM_JUMP_QA",
  "RECONFIRM_JUMP_WILL", "RECONFIRM_JUMP_END", "NOT_CONTACT", "AGENT_IDENTITY", "BYE", "NO_TIME_TRANSFER",
]);

function toSentences(text) {
  return String(text || "")
    .split(/(?<=[。！？；!?;])/)
    .map((p) => p.trim())
    .filter(Boolean);
}

function fmt(tpl, ctx) {
  return String(tpl || "").replace(/\{(\w+)\}/g, (m, k) => (k in ctx ? String(ctx[k]) : m));
}

export class CallAgent {
  constructor({ callId, merchant, scenario, versionNote = "", env, db, offline = false }) {
    this.call_id = callId;
    this.merchant = merchant || {};
    this.scenario = scenario || {};
    this.version_note = versionNote || "";
    this.env = env;
    this.db = db;
    this.direction = this.merchant.direction || "outbound";
    this.inbound = this.direction === "inbound";
    this.offline = offline;
    this.client = offline ? null : true;

    this.state = "PRE";
    this.turn_no = 0;
    this.merchant_turns = 0;
    this.history = [];
    this.done_elements = new Set();
    this.identity_confirmed = false;
    if (this.inbound) {
      this.identity_confirmed = true;
      this.done_elements.add("自报身份");
      this.done_elements.add("身份确认");
    }
    this.qa_confirmed = false;
    this.qa_asked = false;
    this._rejection_delivered = false;
    this.will_confirmed = false;
    this.will_rescheduled = false;
    this._await_reschedule_time = false;
    this.reschedule_at = "";
    this._reschedule_asked = 0;
    this.hesitant_count = 0;
    this.semantic_intent = "other";
    this._intent_cache = {};
    this._rescue_cache = {};
    this._rescue_log = [];
    this._rescue = null;
    this._last_flag = null;
    this.flag_log = [];
    this.stage_ts = {};
    this._turn_done = {};
    this._stage_locked = new Set();
    this.slots = {};
    this.close_decision = null;
    this.call_start_ts = null;
    this.finished = false;
    this._post_end = false;
    this.stop_stage = "";
    this._s1_denials = 0;
    this._s1_weak = 0;
    this._s1_stall = 0;
    this._stall = 0;
    this.non_task_end = false;
    this.last_intent = "";
    this._close_reason = "";
    this._llm_errors = 0;
    this.tool_log = [];
    this.violation_log = [];
    this._vio = null;
    this._vio_count = {};
    this._await_ack = false;
    this._vio_ack = false;
    this.severe_violation = false;
    this.vio_seq = 0;
    this.offtopic_log = [];
    this._offtopic_count = 0;
    this._distrust_count = 0;
    this._fake_accuse_count = 0;
    this._findfault_count = 0;
    this._agent_identity_count = 0;
    this._trust_ctx = false;
    this._name_challenged = false;
    this.transfer_human = false;
    this.pristine = [];
    this.risk = {};
    this.prior_calls = [];
  }

  async init() {
    const mid = this.merchant.merchant_id || this.merchant.id;
    if (this.db && mid) {
      try {
        this.risk = (await riskProfile(this.db, mid)) || {};
      } catch {
        this.risk = {};
      }
      try {
        this.prior_calls = (await priorCalls(this.db, mid, 3, this.call_id)) || [];
      } catch {
        this.prior_calls = [];
      }
    }
  }

  // ------------------------------------------------------------ prompt pieces
  _ctx(tpl) {
    return fmt(tpl, {
      company: this.merchant.company_name || "贵公司",
      scenario: this.scenario.level2_category || "资质材料不符合要求",
      close_method: this.scenario.close_method || "电话沟通",
      hotline: HOTLINE,
    });
  }

  _close_method_text() {
    return this.scenario.close_method || "电话";
  }

  _inbound_hint() {
    return this.inbound ? prompts.INBOUND_HINT : "";
  }

  async _rag_context(userText) {
    const q = String(userText || "").trim() || String(this.scenario.level2_category || "") || "资质审核 材料 驳回";
    if (!this.db) return "";
    let hits = [];
    try {
      hits = await searchKnowledge(this.db, q, 3);
    } catch {
      return "";
    }
    const lines = [];
    for (const h of hits) {
      if (h.source === "merchant") continue;
      lines.push(`- [${h.source}|${String(h.title || "").slice(0, 20)}] ${String(h.content || "").slice(0, 220)}`);
    }
    return lines.join("\n");
  }

  _risk_hint() {
    const rk = this.risk || {};
    if (!(rk.b_count || 0)) return "";
    const tags = (rk.tags || []).join("、") || "违规诉求";
    return (
      `\n【商家风险画像 · 内部参考，不得向商家复述】该商家在历史通话中曾提出「${tags}」类违规要求，累计 ${rk.b_count} 次，当前标记等级：${rk.level_name || "关注"}。` +
      "本轮仍按正常流程沟通，但边界更严：① 不对审核结果做任何形式的承诺或暗示；② 不协助、不指导任何伪造、变通、绕过流程的做法；③ 不接受任何利益表示；" +
      "④ 商家一旦再次提出此类要求，立即引用企标口径拒绝，不再解释、不再协商，并告知转交高级专员处理。"
    );
  }

  _history_hint() {
    const prior = this.prior_calls || [];
    if (!prior.length) return "";
    const lines = [];
    for (const p of prior.slice(0, 3)) {
      const ended = String(p.ended_at || "").slice(0, 16) || "此前";
      if (p.closed_loop) {
        lines.push(`- [${ended}] 上次已闭环：${(p.resolved || []).join("；").slice(0, 120)}`);
      } else {
        lines.push(`- [${ended}] 上次未闭环（停在${p.stop_stage || "未知环节"}）：${(p.pending || []).join("；").slice(0, 120)}`);
      }
    }
    return (
      "\n【历史沟通记录（长会话记忆 · 内部参考，不得向商家逐字复述，可自然衔接）】\n" +
      lines.join("\n") +
      "\n若本次为回拨 / 二次外呼，确认身份后可自然提及上次进展（如已补齐的材料、仍差的材料），再说明本次来电目的；不要每次都假装第一次联系。"
    );
  }

  async _system_prompt(state, userText = "") {
    const company = this.merchant.company_name || "未提供";
    const shop = this.merchant.shop_name || "未提供";
    if (this.inbound) {
      const rag = await this._rag_context(userText);
      let out = prompts.inboundPrompt(company, shop, HOTLINE, rag) + this._risk_hint();
      if (this.version_note) out += prompts.VERSION_NOTE_PREFIX + this.version_note;
      return out;
    }
    const stateHint = prompts.stateHintOf(state) || _STATE_HINTS_FALLBACK[state] || "";
    const s5 = STATE_STAGE5[state] || STATE_STAGE5[this.state] || 1;
    const stageHint = `第${s5}环节 · ${STAGES_5[s5 - 1].name}`;
    const rag = state === "PRE" || state === S1 ? "" : await this._rag_context(userText);
    const done = [...this.done_elements].sort();
    const missing = REQUIRED_ELEMENTS.filter((k) => !this.done_elements.has(k));
    const base = fmt(RP.SYS_TEMPLATE || "", {
      company,
      shop,
      contact: this.merchant.contact_name || "未提供",
      scenario: this.scenario.level2_category || "未提供",
      close_method: this._close_method_text(),
      script: String(this.scenario.script_text || "未提供").slice(0, 300),
      hotline: HOTLINE,
      rag_context: rag || "（本轮无命中知识）",
      state_hint: stateHint,
      stage_hint: stageHint,
      done_elements: done.join("、") || "无",
      missing_elements: missing.join("、") || "无（本轮按流程推进即可）",
    });
    let out = base + this._risk_hint() + this._history_hint();
    if (this.version_note) out += prompts.VERSION_NOTE_PREFIX + this.version_note;
    return out + this._inbound_hint();
  }

  // ------------------------------------------------------------ helpers
  get _sysTemplate() {
    return RP.SYS_TEMPLATE || "";
  }

  _identity_settled() {
    return this.identity_confirmed || this._stage_locked.has(2);
  }

  _explain_delivered() {
    for (const h of this.history) {
      if (h.role !== "assistant") continue;
      const t = String(h.content || "");
      if (ELEMENT_PATTERNS["驳回原因"].test(t) && ELEMENT_PATTERNS["整改动作"].test(t)) return true;
    }
    return false;
  }

  _is_ack(text) {
    const s = String(text || "").trim();
    if (!s) return false;
    if (RP.classifyRedline(s).hit) return false;
    return ACK_WORDS.some((w) => s.includes(w));
  }

  _flow_done_ids() {
    return new Set(this.flow_progress().filter((s) => s.done).map((s) => s.id));
  }

  _enrich_vio(vio) {
    const key = vio.key || "";
    const count = (this._vio_count[key] || 0) + 1;
    const done = this._flow_done_ids();
    return {
      ...vio,
      seq: this.vio_seq + 1,
      count,
      done_before: done.size,
      stage5: Math.max(1, Math.min(5, done.size + 1)),
      ts: Math.round(Math.max(0, _NOW() - (this.call_start_ts || _NOW())) * 10) / 10,
      awaiting: true,
      branch: "",
      severe: key !== "OFF_TOPIC" && key !== "REFUSE_CALL" && count >= RP.SEVERE_LIMIT,
    };
  }

  _correction_text(intent, vio) {
    if (intent === "MERCHANT_REFUSE") return RP.REFUSE_TEXT;
    const key = vio.key || "";
    const c = RP.CORRECT[key] || {};
    if (intent === "VIOLATION_SEVERE" || vio.severe) {
      return (c.say || "") + RP.SEVERE_TEXT;
    }
    if (key === "OFF_TOPIC") {
      return "咱们还是回到资质审核这件事上，我把需要您补齐的材料再和您确认一遍。";
    }
    const parts = [];
    if ((vio.count || 0) >= 2) parts.push(fmt(RP.ANOTHER_OPEN, { name: vio.name || "这个要求" }));
    parts.push(c.say || "");
    parts.push(c.guide || "");
    parts.push(RP.PULL_BACK);
    return parts.join("");
  }

  _prospective_done(aux) {
    const g = (name) => (aux && name in aux ? aux[name] : this[name] || false);
    const n1 = this.state !== "PRE";
    const n2 = n1 && (g("identity_confirmed") || this._identity_settled());
    const n3 = n2 && (this._explain_delivered() || g("_rejection_delivered"));
    const n4 = n3 && g("qa_asked") && g("qa_confirmed");
    const n5 = n4 && (g("will_confirmed") || g("will_rescheduled"));
    return { n1, n2, n3, n4, n5, first_missing: !n1 ? 1 : !n2 ? 2 : !n3 ? 3 : !n4 ? 4 : !n5 ? 5 : 0 };
  }

  _reconfirm_advance() {
    const st3 = this._explain_delivered() || this._rejection_delivered;
    if (!st3) return [S2, "CONFIRMED_ONLY"];
    const st4 = this.qa_asked && this.qa_confirmed;
    if (!st4) return [S3, "RECONFIRM_JUMP_QA"];
    const st5 = this.will_confirmed || this.will_rescheduled;
    if (!st5) return [S4, "RECONFIRM_JUMP_WILL"];
    return [END, "RECONFIRM_JUMP_END"];
  }

  followup_after_end() {
    const done = new Set(this.flow_progress().filter((s) => s.done).map((s) => s.id));
    if (done.size < 5) return false;
    this.finished = false;
    this._post_end = true;
    this.hesitant_count = 0;
    this.state = S3;
    return true;
  }

  _is_fake_scene() {
    const s = `${this.scenario.level2_category || ""}${this.scenario.script_text || ""}${this.scenario.level1_category || ""}`;
    return /造假|伪造|作假|失真|修改痕迹/.test(s);
  }

  _fake_doubt_reply() {
    return this._ctx(
      "理解您的顾虑，我这边确实是京东官方拨出的。您可以先挂断，登录京麦后台查看这次的驳回意见，也可以拨打京东商家服务热线{hotline}核实。方便的话咱们继续，我把需要补齐的材料跟您说清楚。",
    );
  }

  _fake_found_text(n = 1) {
    const level2 = String(this.scenario.level2_category || "").replace(/[（(][^）)]*[）)]/g, "");
    const level1 = this.scenario.level1_category || "相关材料";
    const judge = "提交的材料与官方登记的信息不一致，材料上有修改痕迹";
    if ((n || 1) >= 2) {
      return this._ctx(
        `我刚才已经说明过，${judge}。您这次的问题属于${level2 || level1}，只要按驳回意见用真实材料重新提交就可以。请您不要再申辩材料没问题了，我们继续说明整改要求。`,
      );
    }
    return this._ctx(
      `理解您的心情。不过审核是系统比对官方登记的${level1}信息，${judge}，这个结论不是我说了算。您这次的驳回原因是${level2 || targetDefault()}，按驳回意见用真实材料重新提交就可以。`,
    );
    function targetDefault() {
      return "该项材料不符合要求";
    }
  }

  _name_challenge_reply() {
    const [name, surname] = RP.contactBits(this.merchant.contact_name);
    const company = this.merchant.company_name || "贵公司";
    if (this.state === S1 || this.state === "PRE") {
      if (!name) {
        return "我这边是京东商家资质审核专员，本次来电是关于贵店资质审核结果的官方回访。工单上没有登记具体对接人姓名，麻烦您让贵店负责人接听，我再说明详情。";
      }
      return `我这边是京东商家资质审核专员，工单上登记的联系人是${surname ? surname + "先生/女士" : name}。请问您是他本人吗？`;
    }
    if (!name) {
      return `我这边是京东商家资质审核专员，工单上没有登记具体对接人的姓名，登记的是${company}的联系方式。那我们接着说这次资质审核的意见，可以吗？`;
    }
    return `我这边是京东商家资质审核专员，工单上登记的联系人是${name}。我们接着说这次资质审核的驳回原因，可以吗？`;
  }

  // ------------------------------------------------------------ routing
  _set(name, value, commit, aux) {
    if (commit) this[name] = value;
    else aux[name] = value;
  }

  _route(text, interrupted, commit = true, aux = null, semanticIntent = "other") {
    if (!aux) aux = {};
    const set = (n, v) => this._set(n, v, commit, aux);
    let t = RP.asrNorm(String(text || ""));
    let reschedAt = "";
    const [tmExpr, tmIsDate] = RP.parseTimeExpr(t);
    const affirmStrong = RP.affirmStrongHit(t);
    const negative = /(?<!是)不是/.test(t) || /找错|不认识|打错了/.test(t);
    const ikEarly = RP.identityKind(t);
    const identityDoubt = ikEarly === "AGENT_QUERY" || RP.isAgentIdentityDoubt(t);
    const angry = RP.classifyEmotion(t) === "angry" && !identityDoubt;
    const human =
      !identityDoubt &&
      /转人工|人工客服|找人工|转接人工|人工服务|找真人|真人客服|要人工/.test(t);
    const byeHit = this.state !== "PRE" && this.state !== END && !!t.trim() && (RP.isFarewell(text || "") || RP.isFarewell(t));
    let noDayFree = this.state !== "PRE" && this.state !== END && !!t.trim() && !byeHit && RP.noDayFreeHit(t);
    if (tmIsDate) noDayFree = false;
    const inboundRegreet = this.inbound && this.state !== "PRE" && this.state !== END && !t.trim();
    const semFromModel = !this.offline && !!semanticIntent && semanticIntent !== "other";

    let vio = { hit: false };
    if (!inboundRegreet && this.state !== "PRE" && this.state !== END && t.trim() && !byeHit && !noDayFree) {
      vio = RP.classifyRedline(t);
      if (vio.hit && this._redline_is_question(t, vio, semanticIntent, semFromModel)) {
        set("_rescue", { stage: "redline", key: vio.key, text: t.slice(0, 40), verdict: "business_question" });
        vio = { hit: false, rescued_from: vio.key };
      }
      if (!vio.hit && RP.FAKE_REQUEST_VERB_RE.test(t)) {
        const verdict = semanticIntent === "violation_request" ? semanticIntent : this._semanticVerdictCached(t);
        if (verdict === "violation_request") {
          vio = RP.redlineAsHit("FAKE_MATERIAL");
          set("_rescue", { stage: "redline_add", key: "FAKE_MATERIAL", text: t.slice(0, 40), verdict: "violation_request" });
        }
      }
      if (!vio.hit && this.state !== S1 && (RP.isNameChallenge(t) || RP.isContactAsk(t))) {
        set("_name_challenged", true);
        if (this.identity_confirmed && !this.inbound) set("identity_confirmed", false);
        return this._apply_gates(this.state, this.state, "NAME_CHALLENGE", !!t.trim(), commit, aux, reschedAt);
      }
      if (
        !vio.hit &&
        this.state !== S1 &&
        this._name_challenged &&
        !negative &&
        !RP.isNameChallenge(t) &&
        !RP.isContactAsk(t) &&
        (t.includes("是我") || t.includes("本人") || t.includes("没错") || RECONFIRM_ACK_RE.test(t))
      ) {
        set("_name_challenged", false);
        set("identity_confirmed", true);
        return this._apply_gates(this.state, this.state, "RECONFIRM_OK", !!t.trim(), commit, aux, reschedAt);
      }
      if (this._name_challenged && !vio.hit) set("_name_challenged", false);
      if (
        !vio.hit &&
        !negative &&
        !this.identity_confirmed &&
        !this.inbound &&
        this.state !== "PRE" &&
        this.state !== S1 &&
        this.state !== END &&
        /^(?:是[的我]{0,2}|本人|没错|对，?是我|是我本人|我是(?:负责人|本人|店主|商家)?)[的吧呀啊呢哈哦。！!？?，,\s]{0,4}$/.test(t)
      ) {
        set("identity_confirmed", true);
        return this._apply_gates(this.state, this.state, "RECONFIRM_OK", !!t.trim(), commit, aux, reschedAt);
      }
      const noMoreOrForgot = RP.noMorePlain(t) || RP.qaForgot(t);
      const s4ack = this.state === S4 && RP.s4AckAffirm(t);
      let rescued = false;
      if (!vio.hit && !noMoreOrForgot && !s4ack && this.state !== S1 && this._offtopic_count >= 2 && RP.offtopicContinuation(t)) {
        rescued = this._semanticRescueCached(t);
        if (rescued) set("_rescue", { stage: "window", text: t.slice(0, 40), verdict: "business" });
      }
      if (!vio.hit && !noMoreOrForgot && !s4ack && this.state !== S1 && this._offtopic_count >= 2 && RP.offtopicContinuation(t) && !rescued) {
        vio = { hit: true, key: "OFF_TOPIC" };
      }
      if (
        !vio.hit &&
        !noMoreOrForgot &&
        !s4ack &&
        RP.offtopicGeneric(t) &&
        !(RP.isCompliment(t) && this.state !== S1) &&
        !(semFromModel && (semanticIntent === "new_question" || semanticIntent === "commitment"))
      ) {
        const rescued2 = this._semanticRescueCached(t);
        if (rescued2) set("_rescue", { stage: "generic", text: t.slice(0, 40), verdict: "business" });
        else vio = { hit: true, key: "OFF_TOPIC" };
      }
      if (vio.hit && vio.key === "OFF_TOPIC" && this._await_ack && this._is_ack(t)) vio = { hit: false };
      if (vio.hit && vio.key === "OFF_TOPIC") vio = { hit: true, key: "OFF_TOPIC" };
      if (vio.hit && RP.isFakeDoubt(t)) vio = { hit: false, self_doubt: true };
    }

    if (vio.hit && vio.key === "OFF_TOPIC") {
      vio = { hit: true, key: "OFF_TOPIC" };
    } else if (vio.hit) {
      vio = this._enrich_vio(vio);
      set("_vio", vio);
    }

    // ---- branch chain
    if (byeHit) {
      const closed = this._post_end || this.will_confirmed || this.will_rescheduled;
      if (!closed) {
        set("non_task_end", true);
        set("_close_reason", "MERCHANT_BYE");
      }
      return this._apply_gates(this.state, END, "BYE", !!t.trim(), commit, aux, reschedAt);
    }
    if (semFromModel && semanticIntent === "hangup" && !(RP.noMorePlain(t) && !RP.isFarewell(t))) {
      set("non_task_end", true);
      set("_close_reason", "MERCHANT_REFUSE");
      return this._apply_gates(this.state, END, "MERCHANT_REFUSE", !!t.trim(), commit, aux, reschedAt);
    }
    if (noDayFree) {
      set("non_task_end", true);
      set("_close_reason", "NO_TIME_TRANSFER");
      set("transfer_human", true);
      set("_last_flag", { kind: "no_day_free", count: 1, text: t.slice(0, 50) });
      return this._apply_gates(this.state, END, "NO_TIME_TRANSFER", !!t.trim(), commit, aux, reschedAt);
    }
    if (this.state === "PRE") {
      return this._apply_gates("PRE", this.inbound ? S3 : S1, "START", !!t.trim(), commit, aux, reschedAt);
    }
    if (vio.self_doubt) {
      if (this._is_fake_scene()) {
        this._fake_accuse_count += 1;
        set("_fake_accuse_count", this._fake_accuse_count);
        return this._apply_gates(this.state, this.state, "FAKE_ACCUSE", !!t.trim(), commit, aux, reschedAt);
      }
      return this._apply_gates(this.state, this.state, "FAKE_DOUBT", !!t.trim(), commit, aux, reschedAt);
    }
    if (this._fake_accuse_count > 0 && this._is_fake_scene() && RP.isFakePushback(t)) {
      this._fake_accuse_count += 1;
      set("_fake_accuse_count", this._fake_accuse_count);
      return this._apply_gates(this.state, this.state, "FAKE_ACCUSE", !!t.trim(), commit, aux, reschedAt);
    }
    if (RP.isDistrust(t, this._trust_ctx)) {
      const dc = this._distrust_count + 1;
      set("_distrust_count", dc);
      set("_trust_ctx", true);
      set("_last_flag", { kind: "distrust", count: dc, text: t.slice(0, 50) });
      if (dc >= 3) {
        set("non_task_end", true);
        set("_close_reason", "DISTRUST_TRANSFER");
        return this._apply_gates(E5, END, "DISTRUST_TRANSFER", !!t.trim(), commit, aux, reschedAt);
      }
      const it = dc === 1 ? "TRUST_REASK" : "TRUST_WARN";
      return this._apply_gates(this.state, this.state, it, !!t.trim(), commit, aux, reschedAt);
    }
    if (this.state !== "PRE" && this.state !== S1 && this.state !== END && RP.isAgentIdentityDoubt(t)) {
      const ac = this._agent_identity_count + 1;
      set("_agent_identity_count", ac);
      set("_last_flag", { kind: "agent_identity", count: ac, text: t.slice(0, 50) });
      if (ac >= 3) {
        set("non_task_end", true);
        set("_close_reason", "DISTRUST_TRANSFER");
        return this._apply_gates(E5, END, "DISTRUST_TRANSFER", !!t.trim(), commit, aux, reschedAt);
      }
      return this._apply_gates(this.state, this.state, "AGENT_IDENTITY", !!t.trim(), commit, aux, reschedAt);
    }
    if (this.state === S3 && RP.findfaultHit(t)) {
      const ff = this._findfault_count + 1;
      set("_findfault_count", ff);
      set("_last_flag", { kind: "pushback", count: ff, ftype: RP.findfaultHit(t), text: t.slice(0, 50) });
      if (ff >= 3) {
        set("non_task_end", true);
        set("_close_reason", "PUSHBACK_CLOSE");
        return this._apply_gates(E5, END, "PUSHBACK_CLOSE", !!t.trim(), commit, aux, reschedAt);
      }
      return this._apply_gates(S3, S3, "FINDFAULT_ACK", !!t.trim(), commit, aux, reschedAt);
    }
    if (RP.isTrustConfirm(t) && !RP.noMorePlain(t)) {
      set("_trust_ctx", false);
      if (this.state === S1) {
        set("_s1_denials", 0);
        set("_s1_weak", 0);
        set("_s1_stall", 0);
        set("_name_challenged", false);
        const [ns, it] = this._reconfirm_advance();
        const ps = ns === S2 ? S1 : ns === END ? S4 : ns;
        return this._apply_gates(ps, ns, it, !!t.trim(), commit, aux, reschedAt);
      }
      return this._apply_gates(this.state, this.state, "TRUST_OK", !!t.trim(), commit, aux, reschedAt);
    }
    if (RP.isCompliment(t) && this.state !== "PRE" && this.state !== S1 && this.state !== END) {
      set("_last_flag", { kind: "compliment", count: 1, text: t.slice(0, 50) });
      return this._apply_gates(this.state, this.state, "COMPLIMENT", !!t.trim(), commit, aux, reschedAt);
    }
    if (vio.hit) {
      if (vio.key === "OFF_TOPIC" && this.state === S1) {
        const c = this._s1_stall + 1;
        set("_s1_stall", c);
        if (c >= 5) {
          set("non_task_end", true);
          set("_close_reason", "NOT_CONTACT");
          return this._apply_gates(S1, END, "NOT_CONTACT", !!t.trim(), commit, aux, reschedAt);
        }
        return this._apply_gates(S1, S1, "VERIFY_WARM", !!t.trim(), commit, aux, reschedAt);
      }
      if (vio.key === "OFF_TOPIC") {
        const oc = this._offtopic_count + 1;
        set("_offtopic_count", oc);
        if (oc >= 4) {
          set("non_task_end", true);
          set("_close_reason", "OFFTOPIC_CLOSE");
          return this._apply_gates(E5, END, "OFFTOPIC_CLOSE", !!t.trim(), commit, aux, reschedAt);
        }
        set("_last_flag", { kind: "offtopic", count: oc, text: t.slice(0, 50) });
        return this._apply_gates(this.state, this.state, oc <= 2 ? "OFFTOPIC_PULL" : "OFFTOPIC_WARN", !!t.trim(), commit, aux, reschedAt);
      }
      if (vio.key === "REFUSE_CALL") {
        set("non_task_end", true);
        set("_close_reason", "MERCHANT_REFUSE");
        return this._apply_gates(E5, END, "MERCHANT_REFUSE", !!t.trim(), commit, aux, reschedAt);
      }
      if (vio.severe) {
        set("severe_violation", true);
        set("non_task_end", true);
        set("_close_reason", "VIOLATION_SEVERE_" + vio.key);
        return this._apply_gates(E5, END, "VIOLATION_SEVERE", !!t.trim(), commit, aux, reschedAt);
      }
      return this._apply_gates(E5, this.state, "VIOLATION", !!t.trim(), commit, aux, reschedAt);
    }
    if (this._await_ack && this._is_ack(t)) {
      set("_vio_ack", true);
      if (this.qa_confirmed) return this._apply_gates(S4, S4, "VIO_ACK_WILL", !!t.trim(), commit, aux, reschedAt);
      if (this._explain_delivered()) return this._apply_gates(S3, S3, "VIO_ACK", !!t.trim(), commit, aux, reschedAt);
      return this._apply_gates(this.state, this.state, "VIO_ACK_BACK", !!t.trim(), commit, aux, reschedAt);
    }
    if (this._await_reschedule_time && tmIsDate && this.state === S4) {
      reschedAt = tmExpr;
      return this._apply_gates(S4, END, "RESCHEDULE_CONFIRMED", !!t.trim(), commit, aux, reschedAt);
    }
    if (human || angry) {
      const reason = human ? "HUMAN_REQUEST" : "EMOTIONAL";
      set("non_task_end", true);
      set("_close_reason", reason);
      if (this.state === S1) {
        return this._apply_gates(S1, END, human ? "ID_HUMAN" : "ID_COMPLAINT", !!t.trim(), commit, aux, reschedAt);
      }
      return this._apply_gates(E3, END, reason, !!t.trim(), commit, aux, reschedAt);
    }
    if (interrupted && !t.trim() && this.state !== "PRE" && this.state !== END) {
      return this._apply_gates(E1, this.state, "INTERRUPTED", false, commit, aux, reschedAt);
    }

    let ps = this.state;
    let ns = this.state;
    let intent = "NEED_CLARIFICATION";

    if (this.state === S1) {
      const stall = this._s1_stall + 1;
      set("_s1_stall", stall);
      const nameCh = RP.isNameChallenge(t) || RP.isContactAsk(t);
      if (nameCh) {
        set("_name_challenged", true);
        ps = S1;
        ns = S1;
        intent = "NAME_CHALLENGE";
      } else if (ikEarly === "AFFIRM" || (!ikEarly && affirmStrong && !negative) || (semFromModel && semanticIntent === "proceed")) {
        set("_s1_denials", 0);
        set("_s1_weak", 0);
        set("_s1_stall", 0);
        set("_name_challenged", false);
        const [rns, rit] = this._reconfirm_advance();
        if (rns === S2) {
          ps = S1;
          ns = S2;
        } else if (rns === END) {
          ps = S4;
          ns = END;
        } else {
          ps = rns;
          ns = rns;
        }
        intent = rit;
      } else if (["COMPLAINT", "HUMAN", "ENTITY_CHANGED"].includes(ikEarly)) {
        set("non_task_end", true);
        set("_close_reason", ikEarly);
        ps = S1;
        ns = END;
        intent = "ID_" + ikEarly;
      } else if (stall >= 5) {
        set("non_task_end", true);
        set("_close_reason", "NOT_CONTACT");
        ps = S1;
        ns = END;
        intent = "NOT_CONTACT";
      } else if (["DEPUTY", "FETCH_OWNER", "REPEAT", "AGENT_QUERY", "REFUSE_VERIFY"].includes(ikEarly)) {
        ps = S1;
        ns = S1;
        intent = "ID_" + ikEarly;
      } else if (this._name_challenged && !ikEarly && !negative) {
        const [rns, rit] = this._reconfirm_advance();
        ps = rns === END ? S4 : rns;
        ns = rns;
        intent = rit;
      } else if (ikEarly === "DENY" || negative) {
        const d = this._s1_denials + 1;
        set("_s1_denials", d);
        set("_s1_weak", 0);
        ps = S1;
        if (d >= 2) {
          set("non_task_end", true);
          set("_close_reason", "NOT_CONTACT");
          ns = END;
          intent = "NOT_CONTACT";
        } else {
          ns = S1;
          intent = "NOT_CONTACT";
        }
      } else {
        const w = this._s1_weak + 1;
        set("_s1_weak", w);
        ps = S1;
        if (w >= 4) {
          set("non_task_end", true);
          set("_close_reason", "NOT_CONTACT");
          ns = END;
          intent = "NOT_CONTACT";
        } else {
          ns = S1;
          intent = "VERIFY_AGAIN";
        }
      }
    } else if (this.state === S2) {
      ps = S2;
      if (!this._rejection_delivered) {
        ns = S3;
        intent = "REJECTION_DELIVER";
      } else if (semanticIntent === "commitment" || semanticIntent === "confirmed_understood") {
        ns = S3;
        intent = "UNDERSTOOD";
      } else if (this.hesitant_count > 0 || /嗯|哦|再看看|再说/.test(t)) {
        ns = S2;
        intent = "HESITANT";
      } else {
        ns = S2;
        intent = "NEED_CLARIFICATION";
      }
    } else if (this.state === S3) {
      ps = S3;
      if (!this.qa_asked && !this.inbound) {
        ns = S3;
        intent = "QA_ASK";
      } else if (RP.noMorePlain(t)) {
        if (this._post_end || this.inbound) {
          ns = END;
          intent = "CLOSING";
        } else {
          ns = S4;
          intent = "NO_MORE";
        }
      } else if (semanticIntent !== "commitment" && RP.willPromiseHit(t)) {
        // 商家直接承诺「我这就/明天就去改」——S3 答疑段也应当推进到意愿确认段，
        // 而不是被当成「没有其他疑问」。
        if (this._post_end || this.inbound) {
          ns = END;
          intent = "CLOSING";
        } else {
          ns = S4;
          intent = "NO_MORE";
        }
      } else if (RP.isGoAhead(t)) {
        set("hesitant_count", 0);
        ns = S3;
        intent = "EXPLAIN_REDELIVER";
      } else if (semanticIntent === "commitment") {
        if (this._post_end || this.inbound) {
          ns = END;
          intent = "CLOSING";
        } else {
          ns = S4;
          intent = "NO_MORE";
        }
      } else if (RP.isPureAck(t) || semanticIntent === "confirmed_understood") {
        if (this._post_end || this.inbound) {
          ns = END;
          intent = "CLOSING";
        } else {
          ns = S4;
          intent = "QA_CONFIRMED";
        }
      } else if (RP.qaForgot(t)) {
        ns = S3;
        intent = "QA_FORGOT";
      } else if (RP.qaHasQuestion(t)) {
        ns = S3;
        intent = "QA_HAS";
      } else if (this.hesitant_count > 0) {
        if (this.hesitant_count >= 2) {
          set("non_task_end", true);
          ns = END;
          intent = "HESITANT_LOOP";
        } else {
          ns = S3;
          intent = "HESITANT";
        }
      } else {
        ns = S3;
        intent = "NEED_CLARIFICATION";
      }
    } else if (this.state === S4) {
      ps = S4;
      const noTime = RP.noTimeHit(t) || /没时间|没空|没的空|抽不出|抽不出时间|腾不出|没工夫|没功夫|不方便|去不了|忙不过来|今天不行|改天|以后再说|等有空|暂时不|暂时没|下次|过几天|很忙|太忙|最近忙|延后|往后推|缓一缓/.test(t);
      const askLike = /吗|？|\?|为什么|怎么|哪里|什么|如何|咋|不懂|不明白|不清楚|哪天|几号/.test(t) && !/这就去|去改|去提交|马上改/.test(t);
      const promise = RP.willPromiseHit(t);
      const willYes =
        !askLike &&
        (promise ||
          /这就去|马上改|去改|去提交|会提交|重新提交|去办|去准备|会的|没问题|可以|好的|行|尽快|尽快改|提交|整改|补上|补齐|方便|有空了|腾出时间了/.test(t));
      const willNo = /不去|不提交|不改|改不了|提交不了|不会|没法|没办法|不能提交|做不到/.test(t);
      if (promise && !willNo && !noTime) {
        ns = END;
        intent = "WILL_CONFIRMED";
      } else if (tmIsDate) {
        reschedAt = tmExpr;
        ns = END;
        intent = "RESCHEDULE_CONFIRMED";
      } else if (tmExpr) {
        ns = S4;
        intent = "RESCHEDULE_WHICH_DAY";
      } else if (noTime) {
        if (this._reschedule_asked >= 2) {
          set("non_task_end", true);
          ns = END;
          intent = "RESCHEDULE_NO_TIME";
        } else {
          ns = S4;
          intent = "RESCHEDULE_ASK";
        }
      } else if (semanticIntent === "commitment") {
        ns = END;
        intent = "WILL_CONFIRMED";
      } else if (RP.isPureAck(t) || (willYes && !willNo)) {
        ns = END;
        intent = "WILL_CONFIRMED";
      } else if (this.hesitant_count > 0) {
        if (this.hesitant_count >= 2) {
          set("non_task_end", true);
          ns = END;
          intent = "HESITANT_LOOP";
        } else {
          ns = S4;
          intent = "HESITANT";
        }
      } else {
        ns = S4;
        intent = "WILL_CLARIFY";
      }
    } else if (this.state === S5) {
      ps = S5;
      ns = END;
      intent = "COMPLETED";
    } else {
      ps = this.state;
      ns = END;
      intent = "COMPLETED";
    }

    if (inboundRegreet) {
      ps = this.state;
      ns = this.state;
      intent = "INBOUND_REGREET";
    }
    return this._apply_gates(ps, ns, intent, !!t.trim(), commit, aux, reschedAt);
  }

  _apply_gates(ps, ns, intent, hasText, commit, aux, timeExpr) {
    const set = (n, v) => this._set(n, v, commit, aux);
    if (intent === "QA_CONFIRMED" && !this._explain_delivered()) {
      ps = S3;
      ns = S3;
      intent = "EXPLAIN_REDELIVER";
    }
    if (["CONFIRMED", "CONFIRMED_ONLY", "RECONFIRM_JUMP_QA", "RECONFIRM_JUMP_WILL", "RECONFIRM_JUMP_END", "RECONFIRM_OK"].includes(intent)) {
      set("identity_confirmed", true);
      set("hesitant_count", 0);
    }
    if (intent === "REJECTION_DELIVER" || intent === "EXPLAIN_REDELIVER") {
      set("_rejection_delivered", true);
      set("qa_asked", true);
    }
    if (intent === "QA_ASK" || intent === "RECONFIRM_JUMP_QA") set("qa_asked", true);
    if (intent === "QA_CONFIRMED") set("qa_confirmed", true);
    if (intent === "NO_MORE") {
      set("qa_confirmed", true);
      set("qa_asked", true);
    }
    if (intent === "NEED_CLARIFICATION" && this.inbound) set("qa_asked", true);
    if (intent === "VIO_ACK") {
      set("_await_ack", false);
      set("qa_asked", true);
    }
    if (intent === "VIO_ACK_WILL" || intent === "VIO_ACK_BACK") set("_await_ack", false);
    if (intent === "VIOLATION") set("_await_ack", true);
    if (intent === "VIOLATION_SEVERE" || intent === "MERCHANT_REFUSE") set("_await_ack", false);
    if (["NEED_CLARIFICATION", "WILL_CLARIFY", "UNDERSTOOD"].includes(intent)) set("hesitant_count", 0);
    if (intent === "HESITANT") {
      set("hesitant_count", this.hesitant_count + 1);
    }
    if (intent === "WILL_CONFIRMED") set("will_confirmed", true);
    if (intent === "WILL_RESCHEDULE") set("will_rescheduled", true);
    if (intent === "RESCHEDULE_ASK" || intent === "RESCHEDULE_WHICH_DAY") {
      set("_await_reschedule_time", true);
      set("_reschedule_asked", this._reschedule_asked + 1);
    }
    if (intent === "RESCHEDULE_CONFIRMED") {
      set("reschedule_at", timeExpr || "");
      set("will_rescheduled", true);
    }
    if (intent === "RESCHEDULE_NO_TIME") set("_await_reschedule_time", false);

    const ntEnd = (commit ? this.non_task_end : !!aux.non_task_end || this.non_task_end) || false;
    const merchantAfter = (commit ? this.merchant_turns : (aux.merchant_turns ?? this.merchant_turns)) + (hasText ? 1 : 0);
    if (merchantAfter >= MAX_TURNS_HARD && ns !== END && !ntEnd) {
      set("non_task_end", true);
      intent = "MAX_TURNS";
      ns = END;
    }
    if (ns === END && intent !== "BYE" && !ntEnd && merchantAfter < MIN_TURNS_TO_CLOSE && !this.inbound) {
      set("will_confirmed", false);
      set("will_rescheduled", false);
      set("reschedule_at", "");
      ns = ["PRE", S1, S2].includes(this.state) ? S2 : S4;
      ps = ns;
      if (intent !== "WILL_ASK" && intent !== "RESCHEDULE_ASK") intent = "FORCED_ADVANCE";
    }
    set("_await_ack", commit ? this._await_ack : (aux._await_ack ?? this._await_ack));
    if (ns === END && intent !== "BYE" && !ntEnd && !this.inbound) {
      const p = this._prospective_done(aux);
      const m = p.first_missing;
      if (m) {
        ns = { 1: S1, 2: S1, 3: S2, 4: S3, 5: S4 }[m];
        intent = { 1: "VERIFY_AGAIN", 2: "VERIFY_AGAIN", 3: "REJECTION_DELIVER", 4: "QA_ASK", 5: "WILL_ASK" }[m];
      }
    }
    if (ns === ps) {
      const st = this._stall + 1;
      set("_stall", st);
      if (st >= MAX_STALL_ROUNDS && ADVANCE_STATE[ps]) {
        ns = ADVANCE_STATE[ps];
        intent = "FORCED_ADVANCE";
      }
    } else {
      set("_stall", 0);
    }

    const vio = commit ? this._vio : aux._vio;
    if (vio && vio.hit) {
      const k = vio.key || "";
      set("_vio_count", { ...this._vio_count, [k]: (this._vio_count[k] || 0) + 1 });
      set("vio_seq", vio.seq || this.vio_seq + 1);
    }
    if (!["TRUST_REASK", "TRUST_WARN", "DISTRUST_TRANSFER", "TRUST_OK"].includes(intent)) set("_trust_ctx", false);
    return { ps, ns, intent };
  }

  // ------------------------------------------------------------ fixed replies
  _fixed_reply(ps, ns, intent, vio, aux) {
    const n = this._agent_identity_count;
    const lastIntent = this.last_intent;
    switch (intent) {
      case "MERCHANT_REFUSE":
        return RP.REFUSE_TEXT;
      case "INBOUND_REGREET":
        return this._ctx(FIXED_INBOUND_REGREET);
      case "COMPLIMENT":
        return this.inbound
          ? "谢谢您的夸奖。资质审核方面的问题，您直接问我就行。"
          : "谢谢您的认可。那咱们继续——您对这次的驳回原因还有什么疑问吗？";
      case "RECONFIRM_OK": {
        const tail =
          ps === S2 ? this._ctx(FIXED_REASON) : ps === S3 ? "您对这次驳回的原因和整改方法，还有什么疑问吗？" : ps === S4 ? this._ctx(FIXED_WILLING) : "";
        return "好的，谢谢您的确认，登记的对接人没问题。咱们接着说。" + tail;
      }
      case "UNKNOWN_ADMIT":
        return this._ctx(
          "这个词我这边确实没有相关信息，不能给您乱解释。您可以登录京麦后台查看驳回意见，或拨打京东商家服务热线{hotline}核实。您还有其他问题吗？",
        );
      case "QA_HAS":
        return "好的，那您继续说，我听着。";
      case "QA_FORGOT": {
        const script = this.scenario.script_text || "按京麦后台的驳回意见补齐材料后重新提交";
        return this._ctx(
          `没关系，可能我刚才说得太快了。我再跟您说一遍——驳回原因是{scenario}；${script}。这回清楚了吗，还有别的想问的吗？`,
        );
      }
      case "BYE":
        return "好的，感谢您的接听，再见。";
      case "NAME_CHALLENGE":
        return this._name_challenge_reply();
      case "FAKE_DOUBT":
        return this._fake_doubt_reply();
      case "FAKE_ACCUSE":
        return this._fake_found_text(aux && aux._fake_accuse_count ? aux._fake_accuse_count : this._fake_accuse_count);
      case "TRUST_REASK":
        return this._ctx(RP.REPLY.RELATION);
      case "TRUST_WARN":
        return this._ctx(TRUST_LAST_TEXT);
      case "TRUST_OK":
        if (this.inbound) return "好的，您说。";
        {
          const tail =
            ps === S1 ? this._ctx(FIXED_CONFIRM) : ps === S2 ? this._ctx(FIXED_REASON) : ps === S3 ? FIXED_QA : ps === S4 ? this._ctx(FIXED_WILLING) : "";
          return "好的，感谢您的信任。" + tail;
        }
      case "AGENT_IDENTITY":
        if (this.inbound) {
          return `理解您的顾虑。我是京东商家服务热线的资质审核专员，您也可以拨打官方热线${HOTLINE}核实本次通话。请问您想咨询什么问题？`;
        }
        return n >= 2 ? AGENT_IDENTITY_TEXT2 : AGENT_IDENTITY_TEXT;
      case "FINDFAULT_ACK":
        return "理解您的心情。您这边是对驳回意见有疑问，还是暂时不想补材料？说一下您的顾虑，我帮您看怎么处理更合适。";
      case "NO_MORE":
        return this._ctx(FIXED_WILLING);
      case "OFFTOPIC_PULL":
        if (ps === S4) {
          const wasH = lastIntent === "HESITANT" || lastIntent === "OFFTOPIC_PULL";
          return this._ctx(wasH ? FIXED_WILL_HESITANT2 : FIXED_WILL_HESITANT);
        }
        if (this.inbound) {
          return lastIntent === "OFFTOPIC_PULL"
            ? "咱们还是回到资质审核这件事上——您这次是想了解驳回原因，还是材料怎么补？"
            : "这个我这边不方便展开。咱们回到您咨询的问题上——您想了解资质审核的哪方面呢？";
        }
        return "这个我这边不方便展开。咱们还是回到资质审核这件事——您对这次的驳回原因还有什么疑问吗？";
      case "OFFTOPIC_WARN":
        return this._ctx("咱们还是回到资质审核这件事上，您的驳回意见我这边马上跟您说明。");
      case "OFFTOPIC_CLOSE":
        return this._ctx("那今天就不多打扰您了。您有资质审核相关的问题，可以拨打京东商家服务热线{hotline}。感谢您的接听，再见。");
      case "DISTRUST_TRANSFER":
        return this._ctx(RP.DISTRUST_TRANSFER_TEXT || "理解您的顾虑，我这边先把情况记录下来，转交人工专员与您核实。感谢您的接听，再见。");
      case "NO_TIME_TRANSFER":
        return this._ctx("理解您最近实在腾不出时间。那我把这个情况登记下来，转给人工专员后续再和您对接。感谢您的接听，再见。");
      case "PUSHBACK_CLOSE":
        return this._ctx("好的，那咱们今天就先到这里。您后续随时可以登录京麦后台查看驳回意见并整改，有问题也可以拨打京东商家服务热线{hotline}。感谢您的接听，再见。");
      default:
        break;
    }
    if (intent.startsWith("ID_")) {
      const key = intent.slice(3);
      return this._ctx(RP.IDENTITY_REPLIES?.[key] || FIXED_VERIFY_AGAIN);
    }
    if (ps === "PRE") return this._ctx(this.inbound ? FIXED_INBOUND_OPENING : FIXED_OPENING);
    if (ps === E5) return this._ctx(this._correction_text(intent, vio || {}));
    if (ps === E2) return FIXED_UNCLEAR;
    if (intent === "HUMAN_REQUEST")
      return "好的，您需要人工服务的诉求我已经登记，后续会有我们的人工客服联系您，请您保持电话畅通。感谢您的接听，再见。";
    if (ps === E3) return this._ctx(FIXED_EMOTIONAL);
    if (ps === E4) return this._ctx(FIXED_OUT_OF_SCOPE);
    if (intent === "VIO_ACK") return RP.ACK_BACK_ASK;
    if (intent === "VIO_ACK_WILL") return "好的，您能理解就好。" + this._ctx(FIXED_WILLING);
    if (intent === "VIO_ACK_BACK") return RP.ACK_BACK;
    if (intent === "RESCHEDULE_ASK") return this._reschedule_asked >= 2 ? FIXED_RESCHEDULE_ASK2 : FIXED_RESCHEDULE_ASK;
    if (intent === "RESCHEDULE_WHICH_DAY") return FIXED_RESCHEDULE_WHICH_DAY;
    if (intent === "RESCHEDULE_CONFIRMED")
      return fmt(FIXED_RESCHEDULE_OK, { time: (aux && aux.reschedule_at) || this.reschedule_at || "您说的时间", hotline: HOTLINE });
    if (intent === "RESCHEDULE_NO_TIME") return this._ctx(FIXED_RESCHEDULE_LATER);
    if (ps === S4 && (intent === "WILL_CLARIFY" || intent === "WILL_RESCHEDULE")) return null;
    if (ns === END && (ps === S4 || ps === S5 || ["CLOSING", "MAX_TURNS", "COMPLETED"].includes(intent)))
      return this._ctx(this.inbound ? FIXED_INBOUND_CLOSING : FIXED_CLOSING);
    if (intent === "NOT_CONTACT") {
      if (ns === END) return this._ctx(RP.IDENTITY_REPLIES?.IDENTITY_GIVEUP || FIXED_VERIFY_WARM);
      return "我是京东商家资质审核专员。可能联系的号码有误，请问哪位负责人方便接听？也请留意京麦后台的资质审核通知。";
    }
    if (intent === "RECONFIRM_JUMP_QA") return "好的，感谢您的确认，登记的对接人没问题。咱们接着说——" + this._ctx(FIXED_QA);
    if (intent === "RECONFIRM_JUMP_WILL") return "好的，感谢您的确认，登记的对接人没问题。最后跟您确认一下——" + this._ctx(FIXED_WILLING);
    if (intent === "RECONFIRM_JUMP_END") return "好的，感谢您的确认，登记的对接人没问题。" + this._ctx(FIXED_CLOSING);
    if (intent === "CONFIRMED_ONLY") return this._ctx(FIXED_CONFIRM);
    if (intent === "VERIFY_WARM") return this._ctx(FIXED_VERIFY_WARM);
    if (ps === S1 && intent === "VERIFY_AGAIN") {
      if (this._s1_stall >= 4) return this._ctx(FIXED_VERIFY_AGAIN3);
      if (this._s1_stall >= 3) return this._ctx(FIXED_VERIFY_AGAIN2);
      return this._ctx(FIXED_VERIFY_AGAIN);
    }
    if (ps === S1) return this._ctx(FIXED_VERIFY_AGAIN);
    if (ps === S2 && intent === "REJECTION_DELIVER") return this._ctx(FIXED_EXPLAIN_QA);
    if (intent === "EXPLAIN_REDELIVER") return this._ctx(FIXED_REDELIVER);
    if (intent === "HESITANT") {
      if (lastIntent === "HESITANT" && (this.offline || !this.client)) return this._ctx(ps === S4 ? FIXED_WILL_HESITANT2 : FIXED_HESITANT2);
      if (this.inbound) return null;
      return this._ctx(ps === S4 ? FIXED_WILL_HESITANT : FIXED_HESITANT);
    }
    if (intent === "HESITANT_LOOP") return this._ctx(FIXED_LOOP_END);
    if (intent === "QA_ASK") return this._ctx(this.inbound ? FIXED_QA_INBOUND : FIXED_QA);
    if (intent === "QA_CONFIRMED") return this._ctx(FIXED_WILLING);
    if (ns === S3 && intent === "UNDERSTOOD") return this._ctx(FIXED_QA);
    if (ps === S3) return null;
    if (ps === S4 && (intent === "WILL_CLARIFY" || intent === "HESITANT")) return null;
    if (ps === S4) return this._ctx(FIXED_WILLING);
    return null;
  }

  _topup_elements(reply, ps, ns, intent) {
    const text = String(reply || "");
    if (["BYE", "NO_TIME_TRANSFER", "RESCHEDULE_CONFIRMED", "RESCHEDULE_NO_TIME", "DISTRUST_TRANSFER", "PUSHBACK_CLOSE", "OFFTOPIC_CLOSE"].includes(intent)) {
      return [text, false];
    }
    if (ps === E5 || (ps === S1 && !this.identity_confirmed)) return [text, false];
    let stage = STAGE_OF_STATE[ps] || 1;
    const final = ns === END;
    if (final && stage < 4) stage = 4;
    const noTopup = NO_TOPUP_INTENTS.has(intent);
    const fillBiz = (final || (["S2", S3].includes(ps) && !noTopup)) && !this.inbound;
    if (final && ELEMENT_PATTERNS["结束语"].test(text)) return [text, false];
    let out = text;
    let added = false;
    for (let round = 0; round < 2; round++) {
      const need = [];
      if (fillBiz && !ELEMENT_PATTERNS["驳回原因"].test(out)) need.push(this._ctx(FIXED_REASON));
      if (fillBiz && !ELEMENT_PATTERNS["整改动作"].test(out)) need.push(this._ctx(FIXED_ACTION));
      if (final && !out.includes("再见") && !ELEMENT_PATTERNS["结束语"].test(out)) {
        need.push(this._ctx(this.inbound ? FIXED_INBOUND_CLOSING : FIXED_CLOSING));
      }
      const rest = need.filter((x) => x && !out.includes(x));
      if (!rest.length) break;
      out = out + rest[0];
      added = true;
    }
    return [out, added];
  }

  // ------------------------------------------------------------ LLM intent
  _semantic_verdict_sync(text) {
    return "";
  }
  _semantic_rescue_sync(_text) {
    return false;
  }

  async _semantic_verdict(text) {
    const t = String(text || "").trim();
    if (!t || this.offline || !this.client) return "";
    if (t in this._rescue_cache) return this._rescue_cache[t];
    const prompt =
      "你是电话外呼系统的语义判定器。给下面这句商家原话归类，只输出一个英文词。\n可选值（四选一，不要解释、不要标点）：\n" +
      " business_question —— 在**问**京东资质审核相关的事（驳回、材料、京麦后台、政策、资质造假是什么意思、要补什么…）。注意：语音识别常把业务词听错，例如「京麦」被识别成「续脉/去脉/清迈/静脉」，只要语义上是在问业务，就算这一类。\n" +
      " violation_request —— 在**要求我方**做违规的事：帮着造假/修图/刻章/改后台/走后门/送礼/要他人隐私信息。\n" +
      " chitchat —— 与资质审核无关的闲聊（天气、吃喝、八卦、私人话题）。\n" +
      " other —— 以上都不是。\n" +
      `商家原话：${t}`;
    let out = "";
    try {
      out = String(await llm.complete(this.env, [{ role: "user", content: prompt }], { temperature: 0, max_tokens: 10 }))
        .trim()
        .toLowerCase();
    } catch {
      out = "";
    }
    for (const v of ["business_question", "violation_request", "chitchat", "other"]) {
      if (out.includes(v)) out = v;
    }
    if (!["business_question", "violation_request", "chitchat", "other"].includes(out)) out = "";
    this._rescue_cache[t] = out;
    return out;
  }

  // 同步预判：只用本地规则与已缓存的语义结论，不为每个红线命中额外调用一次模型。
  _semanticVerdictCached(text) {
    const t = String(text || "").trim();
    if (!t) return "";
    return this._rescue_cache[t] || "";
  }

  _semanticRescueCached(text) {
    return this._semanticVerdictCached(text) === "business_question";
  }

  // 与 Python 版 _redline_is_question 同语义，但同步执行（供 _route 内联判断）。
  _redline_is_question(text, _vio, semanticIntent, semFromModel) {
    const t = String(text || "").trim();
    if (!t) return false;
    if (RP.FAKE_REQUEST_VERB_RE.test(t)) return false;
    if (RP.isLookupQuestion(t)) return true;
    if (semFromModel && semanticIntent) return semanticIntent === "new_question";
    return this._semanticRescueCached(t);
  }

  async _semantic_rescue(text) {
    return (await this._semantic_verdict(text)) === "business_question";
  }

  _evidence_gate(t, label) {
    const s = String(t || "").trim();
    if (!s) return label;
    const neg = /不明白|没明白|不清楚|不知道|不懂|没懂/.test(s);
    const nodoubt = /(?:没有|没|无|不再有?)(?:其他|其它|别的|别|任何|啥|什么)?(?:的)?(?:疑问|问题|疑虑|疑惑|异议|要问的)/.test(s);
    const probe = nodoubt ? s.replace(/(?:没有|没|无|不再有?)(?:其他|其它|别的|别|任何|啥|什么)?(?:的)?(?:疑问|问题|疑虑|疑惑|异议|要问的)/g, "") : s;
    const ask = /[?？]|为什么|怎么|哪里|什么|如何|咋|但是|不过|可是/.test(probe);
    if (label === "new_question") return nodoubt && !neg && !ask ? "confirmed_understood" : label;
    const strongWords =
      /没有疑问|没有问题|没疑问|没问题|没有了|不用了|没疑虑|没疑惑|没其他问题|没有其他问题|没啥问题|都清楚|清楚了|明白了|知道了|懂了|都懂了|会整改|会按|按意见|会补|照做/;
    const strong = !neg && (nodoubt || strongWords.test(s));
    if (label === "confirmed_understood") return strong ? "confirmed_understood" : "hesitant";
    if ((label === "hesitant" || label === "other") && strong && !ask) return "confirmed_understood";
    return label;
  }

  _fallback_intent(t) {
    const s = String(t || "").trim();
    if (!s) return "other";
    const nodoubt = /(?:没有|没|无|不再有?)(?:其他|其它|别的|别|任何|啥|什么)?(?:的)?(?:疑问|问题|疑虑|疑惑|异议|要问的)/.test(s);
    const probe = nodoubt ? s.replace(/(?:没有|没|无|不再有?)(?:其他|其它|别的|别|任何|啥|什么)?(?:的)?(?:疑问|问题|疑虑|疑惑|异议|要问的)/g, "") : s;
    if (/但是|不过|可是|就是|然而/.test(probe)) return "new_question";
    if (/[?？]|为什么|怎么|哪里|什么|如何|咋/.test(probe)) return "new_question";
    if (nodoubt || /没有疑问|没有问题|没疑问|没问题|没疑虑|没疑惑|明白了|清楚了|知道了|懂了|会整改|会按|按意见改|会补/.test(s))
      return "confirmed_understood";
    if (/没空|没时间|不方便|很忙|太忙|抽不出|改天/.test(s)) return "other";
    if (s.length <= 6 || /嗯|哦|这样啊|再看看|再说/.test(s)) return "hesitant";
    return "other";
  }

  _quick_local_intent(t) {
    const s = String(t || "").trim();
    if (!s) return "other";
    if (this.state === S1) {
      const affirm = /是我|我是|是的|嗯对|对对|对的|没错|本人|就是我|对，|对。|对！/.test(s);
      const plainYes = /^[是对][的吧呀啊呢。！!？?，,\s]*$/.test(s);
      const risky = /不是|不|别|挂|先挂|忙|没空|不方便|谁|什么|怎么|为什么|吗|[?？]|再说|等|回头/.test(s);
      if (risky) return null;
      return affirm || plainYes ? "confirmed_understood" : null;
    }
    const strongWords =
      /没有疑问|没疑问|没有问题|没问题|没有了|不用了|没疑虑|没疑惑|都清楚|清楚了|明白了|知道了|懂了|都懂了/;
    const nodoubt = /(?:没有|没|无|不再有?)(?:其他|其它|别的|别|任何|啥|什么)?(?:的)?(?:疑问|问题|疑虑|疑惑|异议|要问的)/.test(s);
    if (!(nodoubt || strongWords.test(s))) return null;
    const neg = /不明白|没明白|不清楚|不知道|不懂|没懂/.test(s);
    const ask = /[?？]|为什么|怎么|哪里|什么|如何|咋|但是|不过|可是/.test(s);
    const commit = /会整改|会按|按意见|会补|照做|自己去|明早|明天就|这就去/.test(s);
    if (neg || ask || commit) return null;
    return "confirmed_understood";
  }

  async _classify_user_intent(userText) {
    const t = String(userText || "").trim();
    if (!t) return "other";
    if (t in this._intent_cache) return this._intent_cache[t];
    if (this.offline || !this.client) {
      const label = this._evidence_gate(t, this._fallback_intent(t));
      this._intent_cache[t] = label;
      return label;
    }
    const prompt =
      "你是电话通话的**业务语义判定器**。判断商家这句话在「京东商家资质审核外呼/热线」语境下属于哪一类。只输出一个标签，不要任何解释或标点：\n" +
      "- new_question：在问资质审核相关的业务问题（驳回原因、要补什么材料、去哪个后台改、政策含义等）。\n" +
      "- commitment：明确表示要去做什么事（口语但明确要做，如「我一下就去弄」「晚点我就改」）。\n" +
      "- proceed：让对方直接说正事（「你说吧」「直接说」），不是拒绝。\n" +
      "- hangup：要挂电话、表示没空再谈（「先挂了」「不聊了」）。\n" +
      "- confirmed_understood：表示明白了、没问题了（「都清楚了」「没有其他疑问」）。\n" +
      "- hesitant：含糊、犹豫（「再看看」「嗯」「这样啊」）。\n" +
      "- chitchat：与业务无关的闲聊（天气、八卦、私人话题）。\n" +
      "- violation_request：要求我方做违规的事（造假、修图、改后台、走后门、送礼）。\n" +
      "- other：以上都不是。\n" +
      "关键区分（务必照做）：\n" +
      "1. **口语但明确要去做事，一律判 commitment，不要判 hesitant**。商家说「我睡一觉起来就改」「晚点弄」都是承诺，不是犹豫。\n" +
      "2. 语音识别常把业务词听错（京麦→续脉/去脉/听麦/清迈/静脉），只要**语义上**在问资质审核相关的事，一律判 new_question，不要判 chitchat。\n" +
      "3. 「什么叫X / 什么是X / X是什么意思」是在问概念 → new_question，不是 violation_request。\n" +
      "4. 「没有其他疑问」这类否定句是 confirmed_understood，不是提问、不是犹豫。\n" +
      `商家原话：${t}`;
    let raw = "";
    try {
      raw = String(await llm.complete(this.env, [{ role: "user", content: prompt }], { temperature: 0, max_tokens: 12 })).trim().toLowerCase();
    } catch {
      raw = "";
    }
    let label = "";
    for (const k of ["violation_request", "hangup", "proceed", "commitment", "confirmed_understood", "new_question", "chitchat", "hesitant"]) {
      if (raw.includes(k)) {
        label = k;
        break;
      }
    }
    if (!label) label = this._fallback_intent(t);
    label = this._evidence_gate(t, label);
    this._intent_cache[t] = label;
    return label;
  }

  // ------------------------------------------------------------ LLM generation
  async _build_messages(userText, promptState, intent, evidence = "", extra = "") {
    let sys = await this._system_prompt(promptState, userText);
    if (extra) sys += "\n" + extra;
    if (intent === "NOT_CONTACT") {
      sys +=
        "\n【重要】商家表示自己不是负责人或不认识该公司。请礼貌表示可能联系有误，询问哪位负责人方便接听此电话，并建议商家留意京东商家后台的资质审核通知。保持专业耐心，不要直接结束通话。";
    }
    const msgs = [{ role: "system", content: sys }];
    if (evidence) msgs.push({ role: "system", content: evidence });
    for (const h of this.history.slice(-12)) msgs.push({ role: h.role, content: h.content });
    msgs.push({ role: "user", content: userText || "（开场）" });
    return msgs;
  }

  async _generate(messages, fixedReply = "", onFragment = null, stage = 0) {
    if (fixedReply) {
      const frags = toSentences(fixedReply);
      const out = [];
      for (const f of frags) {
        const safe = RP.safeSentence(f, stage);
        if (!safe) continue;
        out.push(safe);
        if (onFragment) onFragment(safe);
      }
      return [out.join(""), out, 0];
    }
    let buf = "";
    let firstFlushed = false;
    let safeHits = 0;
    const fragments = [];
    const emit = (frag) => {
      const before = fragments.length;
      const safe = RP.safeSentence(frag, stage);
      if (!safe) return;
      if (safe !== frag) safeHits += 1;
      fragments.push(safe);
      if (onFragment) onFragment(safe);
      return fragments.length > before;
    };
    try {
      await llm.stream(
        this.env,
        messages,
        (delta) => {
          buf += delta;
          if (!firstFlushed && [...buf].length >= 12) {
            let pos = 0;
            const m = buf.match(/[，,、]/g);
            if (m) {
              const idx = buf.search(/[，,、]/);
              if (idx >= 0 && idx + 1 >= 12) pos = idx + 1;
            }
            if (!pos && [...buf].length >= 26) {
              const win = buf.slice(20, 26);
              const j = win.search(/[，,、。！？；!?;]/);
              pos = j >= 0 ? 20 + j + 1 : 20;
            }
            if (pos) {
              emit(buf.slice(0, pos));
              buf = buf.slice(pos);
              firstFlushed = true;
            }
          }
          if (/[。！？；!?;]/.test(delta)) {
            const sents = toSentences(buf);
            if (sents.length > 1) {
              for (const s of sents.slice(0, -1)) emit(s);
              buf = sents[sents.length - 1];
            }
          }
        },
        { temperature: 0.3, max_tokens: 120 },
      );
      if (buf.trim()) emit(buf);
    } catch {
      return ["抱歉，系统繁忙，请稍后再试。", [], 0];
    }
    return [fragments.join(""), fragments, safeHits];
  }

  // ------------------------------------------------------------ preview / commit
  async preview_reply(userText, interrupted = false, asrConfidence = 1.0, onFragment = null, auto = false) {
    if (this.finished) {
      return {
        ai_reply: "感谢您的接听，再见。",
        fragments: [],
        user_text: userText,
        prompt_state: this.state,
        next_state: END,
        intent: "COMPLETED",
        semantic_intent: this.semantic_intent,
        emotion: "neutral",
        state: this.state,
        turn: this.turn_no,
        auto,
        finished: true,
        action: "HANGUP",
        error: null,
        aux: {},
        tool_calls: [],
      };
    }
    if (this.call_start_ts === null) this.call_start_ts = _NOW();
    const aux = {};
    const collected = [];
    const emitOut = (s) => {
      collected.push(s);
      if (onFragment) onFragment(s);
    };
    let semanticIntent = this.semantic_intent || "other";
    let t = RP.asrNorm(String(userText || ""));
    if (this.client && !this.offline && t) {
      try {
        semanticIntent = await this._classify_user_intent(t);
      } catch {
        semanticIntent = this._fallback_intent(t);
      }
    } else if (t) {
      semanticIntent = this._fallback_intent(t);
    }
    const ps0 = this.state;
    const looksViolation = RP.classifyRedline(t).hit && !(await this._redline_is_question(t, null, semanticIntent, false));
    if (looksViolation) {
      // ensure semantic veto decisions use the async path (no-op fallback above returned false)
    }
    let ps = ps0;
    let ns = ps0;
    let intent = "NEED_CLARIFICATION";
    let vio = null;
    if (this.client && !this.offline && t && looksViolation) {
      const res = await this._routeAsync(`__VETO_REDLINE__ ${t}`);
      if (res && res.intent) {
        // routed synchronously already; keep the main path below
      }
    }
    const routed = this._route(t, interrupted, false, aux, semanticIntent);
    ps = routed.ps;
    ns = routed.ns;
    intent = routed.intent;
    vio = aux._vio || this._vio || null;
    const stage = STAGE_OF_STATE[ps] || STAGE_OF_STATE[this.state] || 1;
    let fixed = this._fixed_reply(ps, ns, intent, vio, aux);
    if (fixed) {
      const frags = toSentences(fixed);
      for (const f of frags) emitOut(RP.safeSentence(f, stage));
    } else {
      let extra = "";
      if (ns === S4 && (ps === S2 || ps === S3)) {
        extra =
          '【本轮特别注意】商家本轮已表示理解。请用一到两句话确认整改要求（补齐什么材料、去哪里提交），**不要出现"再见/祝您/感谢您的接听"等结束语**，结束语留到下一轮再说。';
      }
      const messages = await this._build_messages(t, ps, intent, "", extra);
      const [reply, frags, hits] = await this._generate(messages, "", emitOut, stage);
      if (String(reply).startsWith("抱歉，系统繁忙")) throw new Error("llm_failed");
      if (hits && !onFragment) {
        const guard =
          "上一版回答出现了承诺性或越界表述，已被系统拦截。请重写这一段：不得出现\"保证/一定/肯定/百分百/包过/必须能过\"等承诺，不得索要身份证号、银行卡号、验证码，不得替商家承诺审核结果。保持与当前阶段一致，一到两句、不超过50个汉字。";
        const msgs2 = messages.concat([{ role: "assistant", content: reply }, { role: "system", content: guard }]);
        const [reply2, , hits2] = await this._generate(msgs2, "", null, stage);
        if (hits2 < hits) {
          collected.length = 0;
          collected.push(reply2);
          aux._regen = true;
          if (onFragment) onFragment(reply2);
        } else {
          collected.length = 0;
          collected.push(reply);
        }
      }
    }
    let replyFull = collected.join("");
    const [merged, topped] = this._topup_elements(replyFull, ps, ns, intent);
    if (topped) {
      const extra = merged.startsWith(replyFull) ? merged.slice(replyFull.length) : "";
      replyFull = merged;
      if (extra && onFragment) {
        for (const s of RP.toSentences(extra)) collected.push(s);
      }
    }
    if (!this.identity_confirmed && ps === S1 && String(this.scenario.level2_category || "") && replyFull.includes(this.scenario.level2_category)) {
      replyFull = this._ctx(RP.IDENTITY_REPLIES?.REFUSE_VERIFY || FIXED_VERIFY_AGAIN);
    }
    replyFull = RP.sanitizeReply(replyFull);
    return {
      ai_reply: replyFull,
      fragments: collected,
      user_text: t,
      prompt_state: ps,
      next_state: ns,
      intent,
      semantic_intent: semanticIntent,
      emotion: RP.classifyEmotion(t),
      asr_confidence: asrConfidence,
      interrupted,
      state: this.state,
      turn: this.turn_no,
      auto,
      finished: ns === END,
      action: ns === END ? "HANGUP" : "SPEAK",
      error: null,
      aux,
      tool_calls: [],
    };
  }

  async _routeAsync() {
    return null;
  }

  commit_reply(pv) {
    if (this.finished && !pv.finished) {
      return { ...pv, state: this.state, action: "HANGUP", finished: true };
    }
    const aux = pv.aux || {};
    const awaitingBefore = this._await_ack;
    for (const [k, v] of Object.entries(aux)) {
      if (["_last_flag", "_rescue", "_vio", "_vio_count", "reschedule_at"].includes(k)) continue;
      this[k] = v;
    }
    if (aux.reschedule_at) this.reschedule_at = aux.reschedule_at;
    if (aux._last_flag) {
      this.flag_log.push(aux._last_flag);
      this._last_flag = null;
    }
    if (aux._rescue) {
      this._rescue_log.push(aux._rescue);
      this._rescue = null;
    }
    const lf = aux._last_flag;
    let offOut = null;
    if (lf && lf.kind === "offtopic") {
      const count = lf.count || 1;
      offOut = {
        seq: count,
        count,
        text: String(lf.text || pv.user_text || "").slice(0, 60),
        ts: Math.round((_NOW() - (this.call_start_ts || _NOW())) * 10) / 10,
        done_before: this._flow_done_ids().size,
        level: pv.intent === "OFFTOPIC_CLOSE" ? "END" : pv.intent === "OFFTOPIC_WARN" ? "WARN" : "PULL",
        outcome:
          pv.intent === "OFFTOPIC_CLOSE"
            ? "已礼貌收线（第 4 次业务外闲聊）"
            : pv.intent === "OFFTOPIC_WARN"
              ? "已红线提醒（第 3 次）"
              : "未展开业务外作答，已拉回资质审核主线",
        reply: String(pv.ai_reply || "").slice(0, 80),
        alert: true,
      };
      this.offtopic_log.push(offOut);
    }
    const st = STAGE_OF_STATE[pv.prompt_state] || STAGE_OF_STATE[this.state] || 1;
    if (!pv.auto) {
      this.history.push({ role: "user", content: pv.user_text || "（开场）", identity_confirmed: this.identity_confirmed });
    }
    this.history.push({ role: "assistant", content: pv.ai_reply, stage: st });

    const vio = aux._vio || null;
    const vioAck = aux._vio_ack || this._vio_ack;
    let vioOut = null;
    if (vioAck) {
      const tgt = [...this.violation_log].reverse().find((v) => v.awaiting);
      if (tgt) {
        tgt.awaiting = false;
        tgt.branch = "A";
        tgt.ack = true;
        tgt.ack_text = String(pv.user_text || "").slice(0, 40);
        tgt.outcome = "✅ 已认识到问题 · AI 已拉回主线并追问业务疑问";
      }
    } else if (awaitingBefore && !(vio && vio.hit)) {
      const tgt = [...this.violation_log].reverse().find((v) => v.awaiting);
      if (tgt) {
        tgt.awaiting = false;
        tgt.outcome = "已教育 · 商家未明确认错，已回到业务主线";
      }
    }
    if (vio && vio.hit) {
      const tgt = [...this.violation_log].reverse().find((v) => v.awaiting);
      if (tgt) {
        tgt.awaiting = false;
        tgt.branch = tgt.key !== vio.key ? "B" : "B2";
        tgt.outcome = tgt.key !== vio.key ? "商家换了一种违规 → 已弹新告警继续教育" : "同一违规再次出现 → 已再次教育";
      }
      const severe = !!vio.severe;
      vioOut = { ...vio, text: String(pv.user_text || "").slice(0, 60) };
      vioOut.branch = severe ? "C" : (vio.count || 1) >= 2 ? "B" : "";
      const c = vio.correct || {};
      if (c.say) vioOut.say = c.say;
      if (c.guide) vioOut.guide = c.guide;
      const entry = {
        seq: vio.seq || this.violation_log.length + 1,
        turn: this.turn_no + 1,
        key: vio.key,
        name: vio.name,
        level: vio.level,
        phrase: vio.phrase,
        how: vio.how,
        excel: vio.excel,
        action: vio.action,
        text: String(pv.user_text || "").slice(0, 60),
        reply: String(pv.ai_reply || "").slice(0, 80),
        say: vioOut.say || "",
        guide: vioOut.guide || "",
        close: vio.close || "",
        tag: vio.tag || "",
        mark: !!vio.mark,
        count: vio.count || 1,
        stage5: vio.stage5 || 1,
        done_before: vio.done_before || 0,
        ts: vio.ts || 0,
        alert: vio.alert !== false,
        severe,
        branch: vioOut.branch,
        ack: false,
        ack_text: "",
        awaiting: !severe && vio.key !== "REFUSE_CALL",
        outcome: severe
          ? "🚨 严重违规 · 屡教不改，已转人工并结束通话"
          : vio.key === "REFUSE_CALL"
            ? "商家明确拒接，已礼貌收线（尊重意愿）"
            : "已拦截并教育，等待商家回应",
      };
      this.violation_log.push(entry);
      vioOut.seq = entry.seq;
      this.vio_seq = Math.max(this.vio_seq, entry.seq);
    }
    this._vio_ack = false;
    this.turn_no += 1;
    if (pv.user_text && String(pv.user_text).trim()) this.merchant_turns += 1;
    this.state = pv.next_state;
    this.finished = pv.next_state === END;
    this.last_intent = pv.intent;
    if (pv.semantic_intent) this.semantic_intent = pv.semantic_intent;
    if (pv.intent === "HUMAN_REQUEST" || pv.intent === "ID_HUMAN") {
      this.transfer_human = true;
    }
    const comp = RP.checkCompliance(this.history, this.identity_confirmed);
    this.done_elements = new Set(Object.entries(comp.elements).filter(([, v]) => v).map(([k]) => k));
    this._record_flow_timestamps();
    const flow5 = this.flow_progress();
    return {
      ai_reply: pv.ai_reply,
      state: pv.next_state,
      action: this.finished ? "HANGUP" : "SPEAK",
      finished: this.finished,
      emotion: pv.emotion || "neutral",
      intent: pv.intent,
      semantic_intent: pv.semantic_intent,
      turn: this.turn_no,
      merchant_turns: this.merchant_turns,
      asr_confidence: pv.asr_confidence,
      interrupted: !!pv.interrupted,
      compliance: comp,
      violation: vioOut,
      offtopic: offOut,
      tool_calls: pv.tool_calls || [],
      flow5,
    };
  }

  // 开场白：不落库、只返回固定话术文本（对应原版 start 动作里的首轮播报）
  // 关键：开场播完必须把状态从 PRE 推进到 S1（inbound 到 S3），否则商家第一句话
  // 会被当成"首次接话"重新播开场白，导致永远卡在 S1。
  openingReply() {
    const text = this.inbound ? this._ctx(FIXED_INBOUND_OPENING) : this._ctx(FIXED_OPENING);
    if (this.state === "PRE") {
      this.state = this.inbound ? S3 : S1;
      this.history.push({ role: "assistant", content: text });
      this.turn_no += 1;
      this.agent_turns = (this.agent_turns || 0) + 1;
      this._mark_stage(1);
    }
    return text;
  }

  async chat_stream(userText, interrupted = false, asrConfidence = 1.0, onSentence = null, auto = false) {
    if (this.finished) {
      return { ai_reply: "感谢您的接听，再见。", state: END, action: "HANGUP", finished: true, emotion: "neutral", turn: this.turn_no };
    }
    let pv;
    try {
      pv = await this.preview_reply(userText, interrupted, asrConfidence, onSentence, auto);
    } catch (e) {
      this._llm_errors += 1;
      const ctx = this._ctx(FIXED_SERVICE_ERROR);
      const frags = toSentences(ctx);
      const joined = [];
      for (const f of frags) {
        const safe = RP.safeSentence(f, 4);
        joined.push(safe);
        if (onSentence) onSentence(safe);
      }
      pv = {
        ai_reply: joined.join(""),
        fragments: joined,
        user_text: userText,
        prompt_state: this.state,
        next_state: END,
        intent: "SERVICE_ERROR",
        semantic_intent: this.semantic_intent,
        emotion: "neutral",
        state: this.state,
        turn: this.turn_no,
        auto,
        finished: true,
        action: "HANGUP",
        aux: { _llm_errors: this._llm_errors + 1 },
        tool_calls: [],
        error: String(e && e.message ? e.message : e),
      };
    }
    return this.commit_reply(pv);
  }

  // ------------------------------------------------------------ flow / summary
  _mark_stage(sid) {
    if (sid in this.stage_ts) return;
    if (this.call_start_ts === null) this.call_start_ts = _NOW();
    this.stage_ts[sid] = Math.round((_NOW() - this.call_start_ts) * 10) / 10;
    if (!(sid in this._turn_done)) this._turn_done[sid] = this.turn_no;
  }

  _record_flow_timestamps() {
    // 与 summary 保持同一套判定：先严格（带 stage 门槛），不足时退回宽松匹配，
    // 避免 history 缺 stage 标记时把「情况说明」永久判成未完成。
    let comp = RP.checkCompliance(this.history, this._identity_settled());
    if (comp.rate < 1) {
      const loose = RP.checkCompliance(this.history, this._identity_settled(), false);
      if (loose.rate > comp.rate) comp = loose;
    }
    const el = comp.elements;
    if (this.state !== "PRE") this._mark_stage(1);
    if (this.identity_confirmed) this._mark_stage(2);
    if (el["驳回原因"] && el["整改动作"]) this._mark_stage(3);
    if (this.qa_confirmed && this.qa_asked) {
      if (!(4 in this.stage_ts)) {
        const t3 = this._turn_done[3];
        if (t3 === undefined || t3 < this.turn_no || !this._stage_locked.has(4)) this._mark_stage(4);
      }
    }
    if (this.will_confirmed || this.will_rescheduled) this._mark_stage(5);
  }

  flow_progress() {
    const comp = RP.checkCompliance(this.history, this._identity_settled());
    const el = comp.elements;
    const done = {
      1: this.state !== "PRE",
      2: this.identity_confirmed,
      3: !!(el["驳回原因"] && el["整改动作"]),
      4: this.qa_confirmed && this.qa_asked,
      5: this.will_confirmed || this.will_rescheduled,
    };
    const t3 = this.stage_ts[3];
    const t4 = this.stage_ts[4];
    if (done[3] && done[4] && t3 !== undefined && t4 !== undefined && t3 >= t4) done[4] = false;
    for (const id of [2, 3, 4, 5]) if (done[id]) this._stage_locked.add(id);
    for (const id of [3, 4, 5]) if (this._stage_locked.has(id)) done[id] = true;
    let seqOk = true;
    for (const s of STAGES_5) {
      if (!seqOk && !this._stage_locked.has(s.id)) done[s.id] = false;
      seqOk = done[s.id] || this._stage_locked.has(s.id);
    }
    if (this.finished && this._identity_settled()) done[2] = true;
    const cur = [1, 2, 3, 4, 5].find((id) => !done[id]) || 5;
    if (this.inbound) {
      return [
        { id: 1, name: "热线问候", key: "greet", done: this.state !== "PRE", current: this.state === "PRE", ts: this.stage_ts[1] },
        {
          id: 2,
          name: "商家答疑",
          key: "qa",
          done: this.qa_confirmed || this.qa_asked,
          current: this.state === S3 && !(this.qa_confirmed || this.qa_asked),
          ts: this.stage_ts[4],
        },
        {
          id: 3,
          name: "结束收线",
          key: "close",
          done: this.finished,
          current: !this.finished && this.state === END,
          ts: this.finished ? this.stage_ts[5] : undefined,
        },
      ];
    }
    return STAGES_5.map((s) => ({
      id: s.id,
      name: s.name,
      key: s.key,
      done: !!done[s.id],
      current: !done[s.id] && s.id === cur,
      ts: this.stage_ts[s.id],
    }));
  }

  _pending_reason() {
    const parts = [];
    if (!this.finished) parts.push("流程未走完");
    const comp = RP.checkCompliance(this.history, this._identity_settled());
    if (!comp.elements["驳回原因"] || !comp.elements["整改动作"]) {
      const miss = REQUIRED_ELEMENTS.filter((k) => !comp.elements[k]);
      if (miss.length) parts.push("必说话术缺失：" + miss.join("、"));
    }
    if (this.merchant_turns < MIN_TURNS_TO_CLOSE) parts.push(`对话轮次不足（${this.merchant_turns}/${MIN_TURNS_TO_CLOSE}）`);
    if (this.non_task_end) parts.push(`非任务型结束（${this._close_reason}）`);
    return parts.join("；") || "未达成闭环条件";
  }

  _dominant_emotion() {
    const texts = this.history.filter((h) => h.role === "user").map((h) => String(h.content || ""));
    if (texts.some((t) => RP.classifyEmotion(t) === "angry")) return "angry";
    if (texts.some((t) => RP.classifyEmotion(t) === "anxious")) return "anxious";
    const last = texts[texts.length - 1];
    if (last && RP.classifyEmotion(last) === "satisfied") return "satisfied";
    return (last && RP.classifyEmotion(last)) || "neutral";
  }

  _tool_summary() {
    const items = (this.tool_log || []).map((t) => ({
      tool: t.tool,
      name: t.name || t.tool,
      title: t.title || "",
      summary: t.summary || "",
      ok: !!t.ok,
      ms: t.ms || 0,
      args: t.args || {},
    }));
    return {
      count: items.length,
      ok: items.filter((i) => i.ok).length,
      tools: [...new Set(items.map((i) => i.tool))],
      items,
    };
  }

  summary() {
    let comp = RP.checkCompliance(this.history, this._identity_settled());
    if (comp.rate < 1) {
      // 兼容：历史里缺 stage 标记（例如从 DB 回放或旧数据）时，退回宽松判定，
      // 否则一通完整合规的通话会被判成 0 分未闭环。
      const loose = RP.checkCompliance(this.history, this._identity_settled(), false);
      if (loose.rate > comp.rate) comp = loose;
    }    const elementsOk = comp.rate >= 1.0 && comp.violations.length === 0;
    const turnsOk = this.merchant_turns >= MIN_TURNS_TO_CLOSE;
    const taskEnd = !this.non_task_end;
    const closed = this.finished && elementsOk && turnsOk && taskEnd;
    const doneEls = REQUIRED_ELEMENTS.filter((k) => comp.elements[k]);
    let resolved = [];
    let pending = [];
    let stopStage;
    if (closed) {
      resolved = [
        `已向商家说明驳回原因：${this.scenario.level2_category || "未提供"}`,
        "已告知整改动作与重新提交流程",
        "已确认商家理解整改要求",
        "已完成结束话术与结构化回写",
      ];
      pending = [];
      stopStage = "已完成";
    } else {
      if (doneEls.length) resolved.push("已覆盖必说要素：" + doneEls.join("、"));
      const acked = this.violation_log.filter((v) => v.branch === "A");
      if (acked.length) {
        resolved.unshift(
          `已纠正商家「${acked[0].name || acked[0].key}」类违规，商家已认识到问题（✅ 已认识到问题），已把话题拉回资质审核主线继续沟通`,
        );
      }
      const others = this.violation_log.filter((v) => v.branch !== "A" && !v.severe);
      if (others.length) {
        resolved.unshift(
          `已拦截并教育商家违规诉求：${others.map((v) => v.name || v.key).join("、")}（在进度条对应环节弹【红线违规警告】、暂停业务纠正后拉回主线）`,
        );
      }
      if (this.severe_violation) {
        resolved.unshift("商家同一违规屡教不改，已打【严重违规】标签、转人工专员跟进并终止本次通话");
      }
      pending = [
        `未闭环原因：${this._pending_reason()}`,
        `停在环节：${STAGE_NAMES_4[STAGE_OF_STATE[this.state]] || this.state}`,
        "商家需重新提交材料后等待再次审核",
      ];
      stopStage = this.non_task_end ? `非任务结束（${this._close_reason}）` : STAGE_NAMES_4[STAGE_OF_STATE[this.state]] || this.state;
    }
    if (this.reschedule_at) {
      resolved.unshift(`商家近期没空，已约定【${this.reschedule_at}】再跟进提交，请按该时间安排提醒`);
    } else if (this._close_reason === "NO_TIME_TRANSFER") {
      resolved.unshift("商家表示哪一天都没空（无可约时间），已登记转人工专员，由人工同事另约时间跟进整改，本次通话未强行追问日期");
    } else if (this._close_reason === "RESCHEDULE_NO_TIME") {
      resolved.unshift("商家表示近期没空，未约定具体改约时间（已请其自行择期提交）");
    }
    const bHits = this.violation_log.filter((v) => v.level === "B");
    return {
      resolved,
      pending,
      compliance: comp,
      emotion: this._dominant_emotion(),
      closed_loop: closed,
      close_decision: this.close_decision,
      slots: this.slots,
      slot_completeness: null,
      semantic_rescue: { count: this._rescue_log.length, items: this._rescue_log.slice(-5) },
      closure_checks: {
        finished: this.finished,
        elements_complete: elementsOk,
        no_violation: comp.violations.length === 0,
        turns_ok: turnsOk,
        merchant_turns: this.merchant_turns,
        task_end: taskEnd,
        writeback: "由 server.finalize_call 同事务保证",
      },
      stop_stage: stopStage,
      close_reason: this._close_reason,
      transfer_human: this.transfer_human,
      state: this.state,
      llm_errors: this._llm_errors,
      violations: this.violation_log,
      violation_count: this.violation_log.length,
      flags: [...new Set(this.flag_log.map((f) => f.kind))],
      offtopic: {
        count: this.offtopic_log.length,
        items: this.offtopic_log.slice(-5),
        warned: this.offtopic_log.some((o) => o.level === "WARN"),
        ended: this.offtopic_log.some((o) => o.level === "END"),
        final_outcome: this.offtopic_log.length ? this.offtopic_log[this.offtopic_log.length - 1].outcome : "",
      },
      violation_blocked: bHits.length,
      violation_severe: this.severe_violation,
      reschedule_at: this.reschedule_at,
      will_rescheduled: this.will_rescheduled,
      risk_tagged: this.risk_tagged || this.violation_log.some((v) => v.level === "B"),
      merchant_risk: this.risk || {},
      tool_calls: this._tool_summary(),
      flow5: this.flow_progress(),
    };
  }
}

export const AGENT_IDENTITY_TEXT = `是的，我是京东的AI数字员工，负责这次资质审核结果的回访。身份您可以拨打官方热线${HOTLINE}核实。那咱们继续说正题。`;
export const AGENT_IDENTITY_TEXT2 = `对，我不是真人，是京东的AI数字员工。核实可以拨打热线${HOTLINE}，京麦后台也能查到本次审核记录。咱们接着说正题吧。`;
