import { CallAgent } from "../src/call-agent.js";
import * as R from "../src/rules.js";

const merchant = {
  id: 1,
  merchant_id: 1,
  company_name: "北京星辰科技有限公司",
  shop_name: "星辰数码专营店",
  contact_name: "张三",
  direction: "outbound",
};
const scenario = {
  level1_category: "资质审核",
  level2_category: "品牌授权链路不完整",
  close_method: "电话沟通",
  script_text: "您好，关于贵店资质审核，品牌授权链路不完整，请补充授权书后重新提交。",
};

const a = new CallAgent({ callId: "t1", merchant, scenario, env: {}, db: null, offline: true });
a.call_start_ts = Date.now() / 1000;
a.state = "PRE";

async function turn(text, interrupted = false) {
  const pv = await a.preview_reply(text, interrupted, 1.0, null, false);
  const cm = a.commit_reply(pv);
  console.log(`> ${text || "(开场)"}  => [${cm.state}] intent=${cm.intent} closed=${cm.compliance.rate.toFixed(2)}`);
  console.log(`  AI: ${cm.ai_reply}`);
  return cm;
}

await turn("");
await turn("是我");
await turn("材料我补了，但是授权书是品牌方给的电子版，可以吗？");
await turn("没有其他疑问了");
await turn("我明天就去弄");
console.log("SUMMARY", JSON.stringify(a.summary().closure_checks), a.summary().closed_loop, a.summary().stop_stage);
console.log("FLOW", a.flow_progress().map((s) => s.id + ":" + (s.done ? "D" : "-")).join(" "));
