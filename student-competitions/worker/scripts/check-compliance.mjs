import * as RP from "../src/rules.js";

// 模拟：开场 + 4 轮对话后的 history，检查 checkCompliance 是否按 gate 判定
const S2 = RP.S2, S3 = RP.S3, S4 = RP.S4, END = RP.END, S1 = RP.S1;
const history = [
  { role: "assistant", content: "您好，我是京东商家资质审核专员，本次来电是关于贵店资质审核结果，请问您是某公司的负责人吗？", stage: RP.STAGE_OF_STATE[S1] },
  { role: "user", content: "是我", identity_confirmed: true },
  { role: "assistant", content: "好的，感谢您的确认。下面我为您说明本次资质审核的驳回原因和整改要求。", stage: RP.STAGE_OF_STATE[S2] },
  { role: "user", content: "授权书是电子版可以吗", identity_confirmed: true },
  {
    role: "assistant",
    content: "您这次的驳回原因是商标状态不符合要求。需要您登录京麦后台，按驳回意见把材料补齐后重新提交。以上是本次的驳回原因和整改方法，您这边还有没有疑问？",
    stage: RP.STAGE_OF_STATE[S3],
  },
  { role: "user", content: "没有其他疑问了", identity_confirmed: true },
  { role: "assistant", content: "那您后续方便尽快去京麦后台重新提交吗？", stage: RP.STAGE_OF_STATE[S4] },
  { role: "user", content: "我明天就去弄", identity_confirmed: true },
  { role: "assistant", content: "好的，如需咨询可拨打京东商家服务热线4006180092。感谢您的接听，祝您工作顺利，再见。", stage: RP.STAGE_OF_STATE[END] },
];

const comp = RP.checkCompliance(history, true);
console.log("STAGE_OF_STATE", RP.STAGE_OF_STATE);
console.log("elements", comp.elements);
console.log("rate", comp.rate);
console.log("early", comp.early_mentioned);
console.log("stage", comp.stage);

// 再测：不带 stage 的 history（模拟 stage 丢失）
const history2 = history.map((h) => ({ role: h.role, content: h.content, identity_confirmed: h.identity_confirmed }));
const comp2 = RP.checkCompliance(history2, true);
console.log("no-stage elements", comp2.elements, "rate", comp2.rate);
