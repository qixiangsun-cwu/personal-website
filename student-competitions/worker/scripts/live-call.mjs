// 线上 WS 通话联调：建通话 -> 连 /ws/call -> start -> 观察开场白与音频
const base = process.argv[2] || "https://cwu-zhiyu.qixiangsun.workers.dev";
const merchantId = Number(process.argv[3] || 1);

async function main() {
  const r = await fetch(base + "/api/calls", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ merchant_id: merchantId }),
  });
  const created = await r.json();
  console.log("CREATE", JSON.stringify(created));
  const cid = created.call_id;
  const wsUrl = base.replace(/^http/, "ws") + "/ws/call/" + cid;
  const ws = new WebSocket(wsUrl);
  const events = [];
  let audioBytes = 0;
  let audioChunks = 0;
  ws.binaryType = "arraybuffer";
  const done = new Promise((resolve) => {
    const timer = setTimeout(() => resolve("timeout"), 45000);
    ws.onopen = () => {
      console.log("WS OPEN");
      ws.send(JSON.stringify({ action: "start" }));
    };
    ws.onmessage = (ev) => {
      if (typeof ev.data === "string") {
        let msg;
        try {
          msg = JSON.parse(ev.data);
        } catch {
          msg = { raw: ev.data.slice(0, 80) };
        }
        events.push(msg);
        const slim = { ...msg };
        if (slim.compliance) slim.compliance = "…";
        if (slim.flow5) slim.flow5 = "…";
        console.log("EVT", JSON.stringify(slim).slice(0, 260));
        if (msg.type === "call_finished") {
          clearTimeout(timer);
          resolve("finished");
        }
      } else {
        audioBytes += ev.data.byteLength;
        audioChunks += 1;
        if (audioChunks <= 3 || audioChunks % 10 === 0) console.log("AUDIO chunk", ev.data.byteLength, "total", audioBytes);
      }
    };
    ws.onerror = (e) => console.log("WS ERROR", e && e.message);
    ws.onclose = () => {
      clearTimeout(timer);
      resolve("closed");
    };
  });
  const why = await done;
  console.log("DONE", why, "audioBytes=", audioBytes, "chunks=", audioChunks, "events=", events.length);
  try {
    ws.close();
  } catch {}
  const detail = await (await fetch(base + "/api/calls/" + cid)).json();
  console.log("CALL status=", detail.status, "turn count=", (detail.turns || []).length);
  const tr = await (await fetch(base + "/api/transcript?call_id=" + cid)).json();
  console.log("TRANSCRIPT lines=", (tr.lines || []).length, JSON.stringify((tr.lines || []).slice(0, 4)));
}

main().catch((e) => {
  console.error("FAILED", e && e.stack ? e.stack : e);
  process.exit(1);
});
