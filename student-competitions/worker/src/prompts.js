// Prompt templates ported from backend/prompts.py
import { SYS_TEMPLATE, SYS_TEMPLATE_INBOUND, INBOUND_HINT, STATE_HINTS, TOOL_DESC, VERSION_NOTE_PREFIX } from "./literals.gen.js";

export { SYS_TEMPLATE, SYS_TEMPLATE_INBOUND, INBOUND_HINT, STATE_HINTS, TOOL_DESC, VERSION_NOTE_PREFIX };

export function stateHintOf(state) {
  return STATE_HINTS[state] || "";
}

export function toolDescOf(name, fallback = "") {
  return TOOL_DESC[name] || fallback;
}

export function inboundPrompt(company, shop, hotline, rag) {
  const body = rag || `（本轮无命中知识：按常识引导登录京麦后台查看驳回意见，或拨打 ${hotline} 核实）`;
  return SYS_TEMPLATE_INBOUND
    .replace(/\{company\}/g, company)
    .replace(/\{shop\}/g, shop)
    .replace(/\{hotline\}/g, hotline)
    .replace(/\{rag_context\}/g, body);
}
