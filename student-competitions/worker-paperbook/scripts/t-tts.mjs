import { synthesize } from "../src/tts.js";

// 用一个极简 WebSocket 适配层验证「Worker 风格」的 ws 用法：
// 运行环境没有环境级 WebSocket 升级能力时，至少验证失败信息是否清晰
const env = {};
const t0 = Date.now();
const r = await synthesize(env, { text: "小兔在森林里迷路了。它遇到了一只大灰狼。", voice: "xiaoxiao", rate: 1 });
console.log("ms", Date.now() - t0, "ok", r.ok, "engine", r.engine, "bytes", r.bytes ? r.bytes.length : 0);
console.log("msg", (r.message || "").slice(0, 400));
