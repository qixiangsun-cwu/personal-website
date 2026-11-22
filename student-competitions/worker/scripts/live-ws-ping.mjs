// 线上 WS 联调（PowerShell 负责建通话，Node 负责 WS）
// 用法：node live-ws-ping.mjs <wsUrl> <texts...>
const wsUrl = process.argv[2];
const texts = process.argv.slice(3);

const ws = new WebSocket(wsUrl);
ws.binaryType = "arraybuffer";
let audio = 0;
let chunks = 0;
let finished = false;

const timeout = setTimeout(() => {
  console.log("TIMEOUT no call_finished");
  finish();
}, 90000);

function finish() {
  clearTimeout(timeout);
  try {
    ws.close();
  } catch {}
  console.log("RESULT audio_bytes=" + audio + " chunks=" + chunks + " finished=" + finished);
  process.exit(0);
}

ws.onopen = () => {
  console.log("OPEN");
  ws.send(JSON.stringify({ action: "start" }));
};

ws.onmessage = (ev) => {
  if (typeof ev.data === "string") {
    let o = {};
    try {
      o = JSON.parse(ev.data);
    } catch {
      return;
    }
    const brief = {};
    for (const k of [
      "type",
      "state",
      "text",
      "asr",
      "msg",
      "intent",
      "finished",
      "emotion",
      "closed_loop",
      "event",
      "turn",
    ])
      if (o[k] !== undefined) brief[k] = String(o[k]).slice(0, 120);
    console.log("EVT", JSON.stringify(brief));
    if (o.type === "call_finished") finished = true;
  } else {
    audio += ev.data.byteLength;
    chunks += 1;
  }
};

ws.onerror = () => console.log("WS ERROR");
ws.onclose = () => {
  console.log("CLOSE");
};

// 依次灌文本
(async () => {
  for (let i = 0; i < texts.length; i++) {
    await new Promise((r) => setTimeout(r, i === 0 ? 9000 : 5000));
    console.log("SEND", texts[i]);
    try {
      ws.send(JSON.stringify({ action: "text", text: texts[i] }));
    } catch (e) {
      console.log("SEND FAIL", e.message);
    }
  }
  await new Promise((r) => setTimeout(r, 12000));
  finish();
})();
