"""线上 WS 通话端到端联调：建通话 -> /ws/call/{id} -> start -> 观察事件与音频。

用法：
  python live_ws.py <base_url> <merchant_id> [turns...]
turns 为可由 ASR 识别的文本；若无 ASR（音频流未灌）也可用 {"action":"text"} 直接走文本链路。
"""
import asyncio
import json
import sys

sys.path.insert(0, r"C:\Users\Legion\AppData\Local\Temp\qxs_pylibs")

import aiohttp


async def main():
    base = sys.argv[1] if len(sys.argv) > 1 else "https://cwu-zhiyu.qixiangsun.workers.dev"
    merchant_id = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    texts = sys.argv[3:]
    async with aiohttp.ClientSession() as s:
        r = await s.post(base + "/api/calls", json={"merchant_id": merchant_id})
        created = await r.json()
        print("CREATE", created, flush=True)
        cid = created["call_id"]
        ws_url = base.replace("https://", "wss://").replace("http://", "ws://") + "/ws/call/" + cid
        audio = {"bytes": 0, "chunks": 0}
        events = []
        finished = asyncio.Event()
        async with s.ws_connect(ws_url, timeout=aiohttp.ClientWSTimeout(ws_close=60)) as ws:
            print("WS OPEN", flush=True)
            await ws.send_str(json.dumps({"action": "start"}))
            sent_next = 0

            async def reader():
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        try:
                            obj = json.loads(msg.data)
                        except Exception:
                            obj = {"raw": msg.data[:100]}
                        events.append(obj)
                        t = obj.get("type")
                        brief = {k: v for k, v in obj.items() if k not in ("compliance", "flow5", "snapshot", "summary")}
                        print("EVT", json.dumps(brief, ensure_ascii=False)[:280], flush=True)
                        if t == "call_finished":
                            finished.set()
                            return
                    elif msg.type in (aiohttp.WSMsgType.BINARY, aiohttp.WSMsgType.BINARY_FRAGMENT):
                        audio["bytes"] += len(msg.data)
                        audio["chunks"] += 1
                        if audio["chunks"] <= 3 or audio["chunks"] % 10 == 0:
                            print("AUDIO", len(msg.data), "total", audio["bytes"], flush=True)
                        if audio["chunks"] >= 30 and sent_next < len(texts):
                            await ws.send_str(json.dumps({"action": "text", "text": texts[sent_next]}))
                            sent_next += 1
                    elif msg.type == aiohttp.WSMsgType.ERROR:
                        print("WS ERROR", ws.exception(), flush=True)
                        return

            task = asyncio.create_task(reader())
            # 等开场音频出来后再依次灌文本
            for i, t in enumerate(texts):
                await asyncio.sleep(6 if i == 0 else 4)
                if audio["chunks"] == 0 and i == 0:
                    print("NOTE: no audio yet, sending text anyway", flush=True)
                print("SEND TEXT:", t, flush=True)
                await ws.send_str(json.dumps({"action": "text", "text": t}))
            try:
                await asyncio.wait_for(finished.wait(), timeout=60)
            except asyncio.TimeoutError:
                print("TIMEOUT waiting call_finished", flush=True)
            task.cancel()
        print("DONE audio", audio, "events", len(events), flush=True)
        d = await (await s.get(base + "/api/calls/" + cid)).json()
        print("CALL status=", d.get("status"), "closed_loop=", d.get("closed_loop"), "turns=", len(d.get("turns") or []), flush=True)
        tr = await (await s.get(base + "/api/transcript", params={"call_id": cid})).json()
        for line in (tr.get("lines") or [])[:8]:
            print("LINE", line.get("role"), (line.get("text") or "")[:80], flush=True)


asyncio.run(main())
