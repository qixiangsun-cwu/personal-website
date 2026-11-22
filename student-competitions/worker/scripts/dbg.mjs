import { CallAgent } from "../src/call-agent.js";

const merchant = { id: 2, merchant_id: 2, company_name: "上海云帆贸易有限公司", shop_name: "云帆优选", direction: "outbound" };
const scenario = { level1_category: "资质审核", level2_category: "营业执照经营范围不符", close_method: "电话" };

async function run(name, script) {
  const a = new CallAgent({ callId: "t-" + name, merchant, scenario, env: {}, db: null, offline: true });
  a.call_start_ts = Date.now() / 1000;
  a.state = "PRE";
  console.log("==== " + name);
  for (const text of script) {
    const pv = await a.preview_reply(text, false, 1.0, null, false);
    const cm = a.commit_reply(pv);
    console.log(`> ${text || "(开场)"} => ${cm.state} ${cm.intent} viol=${cm.violation ? cm.violation.key : "-"}`);
    console.log("  AI:", cm.ai_reply);
  }
  const s = a.summary();
  console.log("CLOSED", s.closed_loop, "| violations", s.violation_count, "| stop", s.stop_stage);
  console.log("FLOW", a.flow_progress().map((x) => x.id + ":" + (x.done ? "D" : "-")).join(" "), "\n");
}

await run("happy", ["", "是我", "材料我补了，授权书是电子版可以吗？", "没有其他疑问了", "我明天就去弄"]);
await run("redline", ["", "是的", "那你帮我P一下营业执照吧", "不好意思我错了，按规矩来", "没有其他疑问了", "我下周去改"]);
await run("hangup-early", ["", "是我", "算了先不聊了，我这边正忙着呢，再见"]);
await run("offtopic", ["", "是我", "今天天气挺热的啊", "你们那边中午吃什么", "行了说正事吧，我补材料"]);
