# -*- coding: utf-8 -*-
"""智语外呼主服务：FastAPI + WebSocket。

实时链路（真链路）：
  商家端浏览器 getUserMedia(16k PCM) ──binary WS──▶ 本服务
      ├─▶ 火山流式ASR（partial/final 回调）
      ├─▶ 能量VAD 抢话检测（TTS播放期间说话 → 立即打断）
      ├─ final文本 ─▶ CallAgent（智谱GLM流式） ─▶ 按句 ─▶ 智谱GLM-TTS
      └─◀ binary WAV 音频帧 + JSON 控制事件 ──▶ 商家端播放（边合成边播）
  同步广播：/ws/monitor 给运营后台（双向字幕/情绪/合规/状态）

降级链路：火山未配置时，商家端可用文字输入直接对话（测试兜底）。

11/12 三项指标口径（任务闭环率 / 必说话术合规率 / 平均轮次）：
  * 统计单元 = 已终结的业务任务，不是「发起过的通话次数」；
  * 商家有故挂断、要求二次/择日回电 → status='pending_followup'，任务未终结，
    不计入分子也不计入分母，看板单列「待跟进业务」；
  * 未接 / 拒接 / 链路中断 / 服务重启僵尸 → status='failed'，同样排除；
  * AI 流程走完但要素缺失 / 轮次不足 / 回写失败 → status='completed' + closed_loop=0，
    **必须留在分母算失败**，否则把所有未闭环都踢掉会让闭环率恒为 100%；
  * 二次呼叫复用同一张工单（一业务任务一工单），闭环只在工单终态判一次。
  达标线 80% / 95% / 3-6 轮保持不变。
"""

import asyncio
import base64
import io
import json
import math
import os
import re
import statistics
import sys
import threading
import time
import uuid
import wave
from typing import Dict, Optional

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import db
import company_import
import import_data
import orders
import redline
import risk
import tools_registry
import flags as call_marks  # 11/15 第七轮：工单看板 / 历史记录统一标记规则（纯函数）
from agent import (CallAgent, check_compliance, classify_emotion, is_farewell,
                   is_farewell_after_closing,
                   S2 as AGENT_EXPLAIN_STATE, S3 as AGENT_QA_STATE)
# 【火山云TTS·冉 11/13】按 TTS_BACKEND 选厂商（volcano/zhipu），接口名不变
from tts_client import TTSClient, PREWARM_TEXTS
from asr_volcano import (VolcanoASRSession, frame_f0, segment_voice_feats)
# frame_f0 / segment_voice_feats：音纹VAD联动（11/21 抗双讲·不降速门）
from speculative import SpeculativeEngine
from barge_gate import BargeGate          # 【11/22】能量VAD抢话判定门（回合对齐）

# 【WebRTC 弱网传输·11/19】aiortc 媒体面：音频走 SRTP/Opus，信令/字幕/打断/文字兜底仍走本 WS。
# env ZHIYU_WEBRTC=1（缺省开）；缺 aiortc 依赖时自动降级回 WS PCM 旧链路，行为与原版一致。
WEBRTC_ENABLED = os.environ.get("ZHIYU_WEBRTC", "1") == "1"
RTC_MEDIA_CLS = None
RTC_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
if WEBRTC_ENABLED:
    try:
        from webrtc import CallMedia as RTC_MEDIA_CLS
        from webrtc import set_weaknet_loss as RTC_SET_LOSS
        from webrtc import get_weaknet_loss as RTC_GET_LOSS
        print("[WEBRTC] 媒体面就绪（aiortc）——浏览器可选 WebRTC 传输")
    except Exception as _rtc_e:
        print(f"[WEBRTC] 媒体面不可用（{_rtc_e}），继续使用 WS PCM 链路")
        RTC_MEDIA_CLS = None
        RTC_SET_LOSS = None
        RTC_GET_LOSS = None

app = FastAPI(title="智语外呼 - 语音外呼客服数字员工")
app.include_router(orders.router)   # 工单域：/api/orders/*（江 11/12）

FRONTEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "frontend")


def closing_spoken(ag) -> bool:
    """上一轮 AI 是否已播过结束语（以真实播报文本为准）。

    11/15：告别短路原来只认 `agent.finished`；结束语后商家只要再开口一次，
    会话就被恢复成答疑态（finished=False），「拜拜拜拜」随即绕过短路掉进
    含糊分支。这里与 finished 取或，保证结束语之后的告别一律静默收线。
    """
    try:
        for h in reversed(list(getattr(ag, "history", None) or [])):
            if h.get("role") == "assistant":
                c = h.get("content") or ""
                return any(k in c for k in ("感谢您的接听", "祝您工作顺利", "再见"))
    except Exception:
        pass
    return False

# ---------------- 硬性入围指标（罗 11/12） ----------------
# 赛题原文：浏览器端可与 Agent 完成不少于 3 轮连续实时语音对话，
# 端到端首字响应时延 P50 不超过 2 秒（不满足者不进入排名）。
THRESHOLD_MS = 2000
LATENCY_DEFINITION = "用户停止说话（浏览器本地最后人声）→ 浏览器首个音频帧开始播放"

# ---------------- 全局状态 ----------------
tts: Optional[TTSClient] = None
try:
    tts = TTSClient()
except RuntimeError as e:
    print(f"[TTS] {e} —— 将以纯文本模式运行")

# 进行中的通话会话：call_id -> session dict
SESSIONS: Dict[str, dict] = {}
# 运营后台监听连接
MONITOR_CLIENTS: set[WebSocket] = set()

# ---------------- 双向字幕同步：服务端唯一真值源（2026/11/12） ----------------
# 商家端「流式实时转写字幕」#subLive 与运营后台「双向实时字幕」#monSub
# 此前各自按不同事件流（final/agent_reply/turn/partial）独立渲染，内容与顺序必然不一致。
# 现改为：两端都只把字幕行上报到这里，这里生成唯一一份有序快照，再同时推给两端；
# 两端用同一段渲染代码输出同一份 HTML，从根本上保证「同时显示同样的东西」。
TRANSCRIPT_LINES: Dict[str, list] = {}   # call_id -> 有序字幕行
TRANSCRIPT_REV = {"n": 0}               # 全局递增版本号（不随新通话归零，供两端判新旧）
TRANSCRIPT_CURRENT = {"call_id": ""}    # 当前通话
TRANSCRIPT_EPOCH = {"id": uuid.uuid4().hex[:8]}  # 服务实例标识：重启后两端能识别并重新对齐

# ---------------- 指标口径（11/11 修正 + 11/12 业务任务口径） ----------------
# 「有效通话 / 已终结业务」= AI 流程已走完(status=completed) 且商家确实说过话。
# 三项指标（闭环率 / 合规率 / 平均轮次）统一使用这一份分母，不再各算各的。
# 11/12：未完成或异常的业务一律排除 —— 待跟进（商家挂断/择日回电）、链路中断、
# 未接、拒接、服务重启僵尸、被二次呼叫承接的旧通话都不进分母。
# 注意：completed 且 closed_loop=0 属于「系统本该做完却没做完」，必须留在分母算失败。
VALID_CALLS_SQL = (
    "SELECT c.call_id FROM calls c WHERE c.status='completed' "
    "AND c.status NOT IN ('pending_followup','failed','rejected','superseded','missed') "
    # 11/19：来电（direction=inbound）是热线咨询，不走外呼五段式，不进外呼指标分母
    "AND c.direction='outbound' "
    "AND EXISTS (SELECT 1 FROM call_turns t WHERE t.call_id=c.call_id AND t.role='merchant')"
)
# 待跟进工单：同一商家 + 同一场景的最近一通「未完成业务」，供二次呼叫复用
FOLLOWUP_TICKET_SQL = (
    "SELECT t.id, t.call_id FROM tickets t JOIN calls cc ON cc.call_id=t.call_id "
    "WHERE t.status='pending_followup' AND cc.merchant_id=? AND cc.scenario_id=? "
    "ORDER BY t.id DESC LIMIT 1"
)
# 目标达标线（当前沿用运营后台原有硬编码值，后续可按组委会口径一处改全）
TARGET_CLOSED_LOOP_RATE = 80.0
TARGET_COMPLIANCE_RATE = 95.0
TARGET_TURNS_MIN = 3
# 收尾前的固定缓冲（秒），与「按 WAV 时长推算的播完时刻」取较大值：
# 有音频帧时以真实播完时刻为准，无音频帧（纯文字链路）时退回这里的固定值。
PLAYBACK_BUFFER_AUDIO = 2.5
PLAYBACK_BUFFER_TEXT = 0.3
# 11/12：AI 说完最后一句结束语后的「最后回应窗口」（秒）。
#   窗口内识别到商家语音 → 取消自动挂断，AI 继续回答；
#   窗口内全程静默       → 沿用原 finalize_call 路径自动挂断。
#   商家主动挂断（WS/REST hangup）不受此窗口约束，随时可挂。
# 计时起点 = 结束语最后一帧音频在前端真正播放完毕（按 WAV 头累计的排空时刻）。
CLOSING_SILENCE_SEC = 5.0
# ④ 功能补丁（11/15）：排空安全阀。①②两段（等合成完 + 等播完）合计最多等
# CLOSING_DRAIN_MAX_SEC 秒，超时按「已播完」处理——否则 TTS 卡在途时
# 自动挂断永远不会触发，电话挂着不收线。
CLOSING_DRAIN_MAX_SEC = 30.0
# 最后一帧音频下发后，留给前端逐帧 decodeAudioData / 调度 / 起播的余量（秒）。
# 宁可多等一点，也不能在「再见」还没播完时发 call_finished 让前端 stopPlayback。
PLAYBACK_TAIL_SEC = 1.0
# 13/13 单步推进：身份确认轮说完后，商家没开口时 AI 主动续讲「情况说明」。
# 等待时长 = 本轮音频播完 + 这个间隙（留给商家插话，插话就取消续讲）。
AUTO_CONTINUE_GAP = 1.0


def _wav_seconds(chunk: bytes) -> float:
    """读 WAV 头取这一帧的真实播放时长；非标准头按 16k/16bit 单声道估算。

    只用于推算「前端顺序播放队列何时排空」，不参与任何下发 / 打断 / 落库逻辑。
    """
    try:
        with wave.open(io.BytesIO(chunk), "rb") as w:
            rate = w.getframerate() or 16000
            return max(0.0, w.getnframes() / float(rate))
    except Exception:
        return max(0.0, (len(chunk) - 44) / 32000.0)


# ---------------- 调试文件日志（排查现场问题用） ----------------
import datetime as _dt
def dlog(msg: str) -> None:
    """关键链路日志：追加到 backend/server_debug.log（带毫秒时间戳）。"""
    try:
        ts = _dt.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "server_debug.log"),
                  "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass


dlog("========== 服务启动 ==========")


@app.on_event("startup")
def startup_cleanup():
    """服务每次启动时：清理上次运行遗留的僵尸通话。

    服务重启后所有内存会话已丢失，遗留的 in_progress/ringing 通话是"幽灵状态"
    （后台显示通话中但实际无人接听）。11/11 起统一标记为 failed（链路中断），
    而不是 completed —— 这样它们不会混进三项指标的有效通话分母；
    同时仍补一张 pending_resubmit 工单，保证看板与商家侧的待办不丢。
    """
    rows = db.query("SELECT call_id, merchant_id FROM calls WHERE status IN ('in_progress','ringing')")
    for r in rows:
        n = db.query_one("SELECT COUNT(*) AS n FROM call_turns WHERE call_id=?", (r["call_id"],))["n"]
        db.execute(
            "UPDATE calls SET status='failed', closed_loop=0, ended_at=datetime('now','localtime'), "
            "stop_stage=? WHERE call_id=?", (f"服务重启链路中断（共{n}轮）", r["call_id"]))
        if not db.query_one("SELECT id FROM tickets WHERE call_id=?", (r["call_id"],)):
            db.execute(
                "INSERT INTO tickets(call_id,merchant_id,resolved_json,pending_json,"
                "emotion_summary,compliance_rate,status) VALUES(?,?,?,?,?,?,?)",
                (r["call_id"], r["merchant_id"], "[]",
                 json.dumps(["通话链路中断，需再次外呼确认"], ensure_ascii=False),
                 "neutral", 0.0, "pending_resubmit"))
    if rows:
        print(f"[启动清理] 已标记 {len(rows)} 通遗留通话为 failed（不计入指标分母）")

    # 字幕同步：服务重启后把「当前通话」指向最近一通，两个页面刷新即可拿到同一份快照
    last = db.query_one("SELECT call_id FROM calls WHERE status IN ('ringing','in_progress') "
                        "ORDER BY id DESC LIMIT 1") or db.query_one(
                        "SELECT call_id FROM calls ORDER BY id DESC LIMIT 1")
    TRANSCRIPT_CURRENT["call_id"] = last["call_id"] if last else ""
    TRANSCRIPT_LINES.clear()
    # 【11.21-14·对话消失修复】从 db 恢复最近一通的字幕：内存字幕随服务重启清空，
    # 前端按空快照整体重绘会让「前面对话全消失」。call_turns 是持久真值源，
    # 恢复后换机/重启都能看到完整对话。新通话 start 仍会清空自己的桶，不受影响。
    _restored = transcript_restore_from_db(TRANSCRIPT_CURRENT["call_id"])
    if _restored:
        print(f"[启动恢复] 从 call_turns 恢复 {_restored} 条字幕行 "
              f"(call_id={TRANSCRIPT_CURRENT['call_id']})")

    # 【延时2秒·罗 11/12】后台预合成固定话术并预热 TLS 连接，命中缓存时首帧 0ms 下发。
    # 放在 daemon 线程里，不阻塞服务就绪（实测预热约 10 秒）。
    def _warmup_tts_background():
        try:
            n = tts.warmup() if tts else 0
            dlog(f"TTS-WARMUP 预合成 {n}/{len(PREWARM_TEXTS)} 条固定话术")
        except Exception as e:
            dlog(f"TTS-WARMUP-FAIL {type(e).__name__}: {str(e)[:100]}")

    if tts:
        threading.Thread(target=_warmup_tts_background, daemon=True).start()

    # 工单域：建表 + 首次为每个商家补齐工单（已有则跳过）（江 11/12）
    try:
        orders.ensure_schema()
        made = orders.seed()
        if made:
            print(f"[工单] 初始化 {made} 张工单")
    except Exception as e:
        print(f"[工单] 初始化跳过：{e}")


async def broadcast_monitor(call_id: str, event: dict) -> None:
    """实时事件广播给所有运营后台监听页。"""
    payload = json.dumps({"call_id": call_id, **event}, ensure_ascii=False)
    dead = []
    for ws in list(MONITOR_CLIENTS):
        try:
            # 【11/22】后台监听页可能卡住/被挂起；原实现无超时，一旦某个 ws.send 阻塞，
            # 会把调用方（甚至音频帧循环）一起卡死。加 0.4s 超时，慢客户端直接踢掉。
            await asyncio.wait_for(ws.send_text(payload), timeout=0.4)
        except Exception:
            dead.append(ws)
    for ws in dead:
        MONITOR_CLIENTS.discard(ws)


# ---------------- 双向字幕同步：快照 / 行变更 / 广播 ----------------

def transcript_add_tool(call_id: str, tc: dict) -> dict:
    """把一次「工具调用」写成字幕里的一行（role=tool），返回新快照。

    13/13 修「后台实时转写的工具调用一闪而过」：原来工具条是前端收到
    tool_call 事件后自己 append 进 DOM 的，而字幕区每来一帧快照就
    `innerHTML = ...` 整体重绘 → 工具条立刻被冲掉，只闪一下。
    改成让工具调用成为快照里的一行，随快照一起重绘，**永久停留**，
    刷新页面重放历史时也还在（与「Agent 真的查了数据」的证据链一致）。
    """
    cid = call_id or TRANSCRIPT_CURRENT["call_id"] or "-"
    lines = TRANSCRIPT_LINES.setdefault(cid, [])
    TRANSCRIPT_REV["n"] += 1
    item = {
        "key": "tool-%d" % TRANSCRIPT_REV["n"],
        "role": "tool",
        "ts": _dt.datetime.now().strftime("%H:%M:%S"),
        "tool": {
            "name": tc.get("name") or tc.get("tool") or "工具调用",
            "title": tc.get("title") or "",
            "summary": tc.get("summary") or "",
            "ok": bool(tc.get("ok")),
            "ms": tc.get("ms", 0),
            "args": tc.get("args") or {},
        },
    }
    lines.append(item)
    return transcript_snapshot(cid)


async def broadcast_tool_calls(call_id: str, result: dict) -> None:
    """把本轮的「工具调用」推给运营后台监控页（商家端不显示）。
    11/12 新增：模型在通话中自己决定查了工单 / 档案 / 知识库时，
    后台实时监控会多出一条可展开的调用卡片 —— 让「Agent 真的在查数据」看得见。
    13/13：同时写进字幕快照，卡片常驻不再一闪而过。"""
    for tc in (result.get("tool_calls") or []):
        transcript_add_tool(call_id, tc)
        await broadcast_monitor(call_id, {
            "type": "tool_call",
            "tool": tc.get("tool", ""), "name": tc.get("name", "工具调用"),
            "title": tc.get("title", ""), "summary": tc.get("summary", ""),
            "ok": bool(tc.get("ok")), "ms": tc.get("ms", 0),
            "args": tc.get("args") or {},
        })
    if result.get("tool_calls"):
        await transcript_broadcast(call_id)

def transcript_snapshot(call_id: str = "") -> dict:
    """两端渲染的唯一输入：同一份有序行 + 全局版本号。"""
    cid = call_id or TRANSCRIPT_CURRENT["call_id"]
    return {"call_id": cid, "lines": list(TRANSCRIPT_LINES.get(cid, [])),
            "rev": TRANSCRIPT_REV["n"], "epoch": TRANSCRIPT_EPOCH["id"]}


def transcript_restore_from_db(call_id: str) -> int:
    """【11.21-14·对话消失修复】服务重启/换机后从 call_turns 恢复字幕行。

    TRANSCRIPT_LINES 是纯内存，服务一重启就全空；前端每次收到快照都用
    `innerHTML=...` 整体重绘，于是「出了噪音/重启后前面对话全消失」。
    call_turns 才是持久真值源——把最近一通的轮次按 id 顺序还原成字幕行，
    行格式与运行时 addLine 完全一致（role/text/tags），前端无需任何改动。

    只在恢复「当前通话」时调用；新通话 start 仍会清空自己的桶，互不干扰。
    返回恢复的行数（0=无历史，正常）。
    """
    if not call_id:
        return 0
    try:
        rows = db.query(
            "SELECT role, text, asr_confidence FROM call_turns "
            "WHERE call_id=? ORDER BY id ASC", (call_id,)) or []
    except Exception:
        return 0
    lines = []
    for r in rows:
        role = (r["role"] if isinstance(r, dict) else r[0]) or ""
        text = (r["text"] if isinstance(r, dict) else r[1]) or ""
        conf = (r["asr_confidence"] if isinstance(r, dict) else r[2])
        if role not in ("agent", "merchant") or not text:
            continue                      # 系统/工具行不恢复，与运行时口径一致
        tags = []
        if role == "merchant" and isinstance(conf, (int, float)) and conf > 0:
            tags.append("识别置信 %d%%" % round(conf * 100))
        lines.append({"key": "restore-%d-%d" % (len(lines) + 1, TRANSCRIPT_REV["n"]),
                      "role": role, "text": text, "tags": tags,
                      "ts": ""})           # ts 留空：历史轮次不冒充实时时间
    if lines:
        TRANSCRIPT_LINES[call_id] = lines
        TRANSCRIPT_REV["n"] += 1
    return len(lines)


def transcript_mutate(action: str, line: dict) -> dict:
    """append 追加 / upsert 按 key 覆盖或续写 / remove 按 key 删除 → 返回新快照。

    仅服务于字幕同步；不读写数据库、不改动通话状态与任何既有事件。
    """
    cid = TRANSCRIPT_CURRENT["call_id"] or "-"
    lines = TRANSCRIPT_LINES.setdefault(cid, [])
    item = dict(line or {})
    key = item.get("key")
    # 时间戳统一由服务端生成，两个窗口不会出现时钟差异
    item["ts"] = item.get("ts") or _dt.datetime.now().strftime("%H:%M:%S")
    if action == "remove":
        TRANSCRIPT_LINES[cid] = [x for x in lines if x.get("key") != key]
    elif action == "upsert" and key:
        hit = next((x for x in lines if x.get("key") == key), None)
        if hit is None:
            item.pop("append", None)          # 首次创建行时续写标记无意义
            lines.append(item)
        elif item.get("append"):
            # AI 一轮回复由多个流式片段组成：同 key 续写，两端看到的是同一条气泡
            hit["text"] = (hit.get("text") or "") + (item.get("text") or "")
            hit["ts"] = item["ts"]
        else:
            for k in ("role", "text", "tags"):
                if item.get(k) is not None:
                    hit[k] = item[k]
            hit["ts"] = item["ts"]
    else:
        if not key:
            item["key"] = "L%d" % (TRANSCRIPT_REV["n"] + 1)
        # 出现确定的商家/AI 文本后，「识别中」临时行自动消失（两端同一时刻消失）
        if item.get("role") in ("merchant", "agent"):
            TRANSCRIPT_LINES[cid] = [x for x in lines if x.get("key") != "partial"]
            TRANSCRIPT_LINES[cid].append(item)
        else:
            lines.append(item)
    TRANSCRIPT_REV["n"] += 1
    return transcript_snapshot(cid)


async def transcript_broadcast(call_id: str = "") -> None:
    """同一份快照同时下发给运营后台 /ws/monitor 与所有商家端 /ws/call。"""
    snap = transcript_snapshot(call_id)
    payload = json.dumps({"type": "transcript", **snap}, ensure_ascii=False)
    for ws in list(MONITOR_CLIENTS):
        try:
            await ws.send_text(payload)
        except Exception:
            MONITOR_CLIENTS.discard(ws)
    for sess in list(SESSIONS.values()):
        w = sess.get("ws")
        if w is not None:
            try:
                await w.send_text(payload)
            except Exception:
                pass


def get_active_version() -> Optional[dict]:
    v = db.query_one("SELECT * FROM rsi_versions WHERE status='active' ORDER BY id DESC")
    return v


def version_note_text(v: Optional[dict]) -> str:
    if not v:
        return ""
    changes = json.loads(v["changes_json"] or "[]")
    notes = [f"场景「{c.get('scenario','')}」话术优化：{c.get('after','')}" for c in changes[:5]]
    return "；".join(notes)


def active_version_id() -> Optional[int]:
    v = get_active_version()
    return v["id"] if v else None


# ---------------- 页面 ----------------

@app.post("/api/debug/client-error")
async def api_client_error(body: dict):
    """浏览器端黑匣子上报：JS异常/WS生命周期事件写入 server_debug.log。"""
    dlog(f"CLIENT[{body.get('msg','')[:280]}]")
    return {"ok": True}


@app.get("/")
def index():
    return FileResponse(os.path.join(FRONTEND_DIR, "admin.html"), media_type="text/html",
                        headers={"Cache-Control": "no-store"})


@app.get("/merchant")
def merchant_page():
    return FileResponse(os.path.join(FRONTEND_DIR, "merchant.html"), media_type="text/html",
                        headers={"Cache-Control": "no-store"})


@app.get("/rag")
def rag_knowledge_view():
    # RAG 知识库与话术约束展示页（纯静态自包含，秦版前端入口链接指向此页）
    return FileResponse(os.path.join(FRONTEND_DIR, "rag_knowledge_view.html"), media_type="text/html",
                        headers={"Cache-Control": "no-store"})


# ---------------- REST：双向字幕同步（新增，不影响任何既有接口） ----------------

@app.get("/api/transcript")
def api_transcript(call_id: str = ""):
    """页面打开 / 定时校准时拉取同一份字幕快照。"""
    return transcript_snapshot(call_id)


@app.post("/api/transcript/line")
async def api_transcript_line(body: dict):
    """两端把字幕行写进唯一真值源；返回值与广播内容是同一份快照。"""
    snap = transcript_mutate(str(body.get("action") or "append"), body.get("line") or {})
    await transcript_broadcast(snap["call_id"])
    return snap


# ---------------- REST：商家 / 场景 ----------------

@app.get("/api/merchants")
def api_merchants():
    rows = db.query("SELECT * FROM merchants WHERE status='active' ORDER BY id")
    # 11/12：带上风险标记，商家名单页也能看到「这家被重点关注」
    try:
        rks = risk.profiles([r["id"] for r in rows])
    except Exception:
        rks = {}
    for r in rows:
        r["risk"] = rks.get(r["id"], {})
    return rows


@app.get("/api/merchants/{merchant_id}")
def api_merchant_detail(merchant_id: int):
    """京东服务台看板：点一个商家 → 该商家所有实时数据。"""
    m = db.query_one("SELECT * FROM merchants WHERE id=?", (merchant_id,))
    if not m:
        raise HTTPException(404, "商家不存在")
    calls = db.query(
        "SELECT * FROM calls WHERE merchant_id=? ORDER BY id DESC LIMIT 50", (merchant_id,))
    call_marks.attach(calls)  # 商家详情里的工单卡也用同一套标记
    tickets = db.query(
        "SELECT t.*, c.reject_reason FROM tickets t JOIN calls c ON c.call_id=t.call_id "
        "WHERE t.merchant_id=? ORDER BY t.id DESC", (merchant_id,))
    # 汇总
    total = len(calls)
    done = sum(1 for c in calls if c["status"] == "completed")
    return {
        "merchant": m,
        "calls": calls,
        "tickets": tickets,
        # 商家风险标记：名单页 / 详情页红标，B 级违规沉淀而来
        "risk": risk.profile(merchant_id),
        "summary": {
            "total_calls": total,
            "completed_calls": done,
            "closed_loop": sum(1 for c in calls if c["closed_loop"]),
            "pending_items": sum(len(json.loads(t["pending_json"] or "[]")) for t in tickets),
        },
    }


@app.get("/api/scenarios")
def api_scenarios():
    return db.query("SELECT * FROM scenarios ORDER BY id")


# ---------------- REST：呼叫控制 ----------------

@app.post("/api/calls")
async def api_create_call(body: dict):
    """运营后台「一键发起外呼」。body: {merchant_id, scenario_id, direction}"""
    merchant = db.query_one("SELECT * FROM merchants WHERE id=?", (body.get("merchant_id"),))
    if not merchant:
        raise HTTPException(404, "商家不存在")
    scenario = db.query_one("SELECT * FROM scenarios WHERE id=?", (body.get("scenario_id"),))
    if not scenario:
        # 11/11：场景为空时取首条场景兜底（商家端主动来电传 scenario_id:null 也走这里），
        # 避免 script_text='未提供' 导致模型说不出驳回原因与整改动作。
        scenario = db.query_one("SELECT * FROM scenarios WHERE script_text<>'' ORDER BY id LIMIT 1")
    if not scenario:
        raise HTTPException(400, "场景库为空或缺少参考话术，请先在「07 数据一键导入」导入场景")
    call_id = uuid.uuid4().hex[:12]
    db.execute(
        "INSERT INTO calls(call_id,merchant_id,scenario_id,direction,status,version_id,reject_reason,started_at) "
        "VALUES(?,?,?,?,?,?,?,datetime('now','localtime'))",
        (call_id, merchant["id"], scenario["id"],
         body.get("direction", "outbound"), "ringing", active_version_id(),
         scenario.get("level2_category", "")))
    # 新通话开始：清空字幕真值源并同时推空快照，两个窗口的字幕区一起回到初始态
    TRANSCRIPT_CURRENT["call_id"] = call_id
    TRANSCRIPT_LINES[call_id] = []
    TRANSCRIPT_REV["n"] += 1
    await transcript_broadcast(call_id)
    await broadcast_monitor(call_id, {"type": "call_ringing", "merchant": merchant["company_name"]})
    return {"call_id": call_id, "merchant": merchant, "scenario": scenario}


@app.post("/api/calls/{call_id}/answer")
async def api_answer(call_id: str):
    call = db.query_one("SELECT * FROM calls WHERE call_id=?", (call_id,))
    if not call:
        raise HTTPException(404, "通话不存在")
    db.execute("UPDATE calls SET status='in_progress', answered_at=datetime('now','localtime') WHERE call_id=?", (call_id,))
    # 【11/14 修复·回拨字幕】外呼走 /api/call/start 会切换字幕真值源（TRANSCRIPT_CURRENT）
    # 并清桶；回拨由 orders._open_redial 直插数据库、不经过该接口 —— 若不在接听时对齐，
    # 回拨通话的全部字幕行（含 agent 气泡）都会写进上一通通话的桶，且 agent 行 key
    # （agent-<epoch>-<seq>）与旧桶冲突被 append 拼进旧气泡 → 商家端看不到本通京东文字。
    if TRANSCRIPT_CURRENT["call_id"] != call_id:
        TRANSCRIPT_CURRENT["call_id"] = call_id
        TRANSCRIPT_LINES[call_id] = []
        TRANSCRIPT_REV["n"] += 1
        await transcript_broadcast(call_id)
    dlog(f"ANSWER {call_id} ← 接听请求处理完成")
    await broadcast_monitor(call_id, {"type": "call_answered"})
    return {"ok": True}


@app.post("/api/calls/{call_id}/hangup")
async def api_call_hangup(call_id: str):
    """统一挂断入口（11/12 按业务终态分流，三项指标分母的唯一入口之一）。

    * 会话仍在 = 商家端主动挂断（有事、要求二次/择日回电）→ park_call()
      → status='pending_followup'，任务未终结，不进分子也不进分母；
    * 会话已丢失 = 服务重启 / 链路中断 → mark_link_failed()
      → status='failed'，排除出分母；
    * AI 流程自然收尾（说完结束语）→ finalize_call() → status='completed'，
      闭环成功或「系统没做完」都留在分母，由 closed_loop 区分。
    """
    call = db.query_one("SELECT status FROM calls WHERE call_id=?", (call_id,))
    if not call:
        raise HTTPException(404, "通话不存在")
    if call["status"] in ("completed", "failed", "rejected",
                          "pending_followup", "superseded"):
        return {"ok": True, "already_final": True}
    sess = SESSIONS.get(call_id)
    if sess:
        await park_call(call_id, sess, "商家中途挂断（REST）")
    else:
        await mark_link_failed(call_id, "会话丢失链路中断")
    return {"ok": True}


@app.post("/api/calls/{call_id}/reject")
async def api_reject(call_id: str):
    db.execute("UPDATE calls SET status='rejected', ended_at=datetime('now','localtime') WHERE call_id=?", (call_id,))
    await broadcast_monitor(call_id, {"type": "call_rejected"})
    return {"ok": True}


# 11/11 删除了此处重复的 POST /api/calls/{call_id}/hangup 路由。
# 合并后的唯一实现见上方 api_call_hangup()（含 status/ended_at 落库）。


async def park_call(call_id: str, sess: dict, reason: str = "商家中途挂断") -> None:
    """11/12：商家有故挂断 / 要求二次或择日呼叫 —— 业务任务尚未终结。

    写 status='pending_followup' 而不是 completed，因此不进入三项指标分母；
    对应工单保持 pending_followup，等二次呼叫完成由 finalize_call() 复用并判一次闭环。
    「系统本该做完却没做完」的失败仍由 finalize_call() 写 completed + closed_loop=0，
    留在分母算失败，避免把所有未闭环都排除后闭环率恒为 100%。
    """
    agent: CallAgent = sess["agent"]
    # 【真相一致性 · 11/13】流程其实已经走完（五环节全绿 / AI 已说结束语）时点挂断，
    # 属于「正常收尾」，必须走 finalize_call 落 completed，而不是当成中途挂断。
    # 旧版一刀切 park，会把 5/5 的通话写成 pending_followup 且丢掉 flow5，
    # 于是运营端实时是 5/5、工单/历史却显示 3/5 —— 即「挂断后看板全变」的根因。
    try:
        flow_now = agent.flow_progress()
        if agent.finished or all(s.get("done") for s in flow_now):
            await finalize_call(call_id, sess)
            return
    except Exception:
        pass
    base = agent.summary()          # ★ 复用同一真相源：flow5 / compliance / emotion 全部带上
    comp = base.get("compliance") or check_compliance(agent.history)
    pending = [f"{reason}，需二次外呼或择日回电确认",
               "任务尚未终结，不计入本期任务闭环率分母"]
    summary = {
        "resolved": base.get("resolved") or [],
        "pending": pending,
        "compliance": comp,
        "emotion": base.get("emotion", "neutral"),
        "closed_loop": False, "parked": True, "reason": reason,
        "stop_stage": "待跟进（未终结）",
        "state": base.get("state"),
        "flow5": base.get("flow5") or [],      # ★ 挂断瞬间的真实进度，看板据此保持
        "closure_checks": base.get("closure_checks") or {},
        "violations": base.get("violations") or [],
        # 11/15 真机修复②：中途挂断也要保留闲聊预警轨迹（与红线 violations 并列）
        "offtopic": base.get("offtopic") or {},
        "merchant_risk": base.get("merchant_risk") or {},
        "tool_calls": base.get("tool_calls") or {},
        "llm_errors": base.get("llm_errors", 0),
        "barge_events": session.get("barge_count", 0),
        "close_reason": reason,
    }
    with db.atomic() as c:
        c.execute(
            "UPDATE calls SET status='pending_followup', closed_loop=0, "
            "ended_at=datetime('now','localtime'), stop_stage=?, result_json=? WHERE call_id=?",
            (reason, json.dumps(summary, ensure_ascii=False), call_id))
        row = c.execute("SELECT id FROM tickets WHERE call_id=?", (call_id,)).fetchone()
        if row:
            c.execute("UPDATE tickets SET resolved_json=?, pending_json=?, compliance_rate=?, "
                      "status='pending_followup' WHERE id=?",
                      (json.dumps(summary["resolved"], ensure_ascii=False),
                       json.dumps(pending, ensure_ascii=False),
                       comp.get("rate", 0.0), row[0]))
        else:
            cr = c.execute("SELECT merchant_id FROM calls WHERE call_id=?", (call_id,)).fetchone()
            c.execute(
                "INSERT INTO tickets(call_id,merchant_id,resolved_json,pending_json,"
                "emotion_summary,compliance_rate,status) VALUES(?,?,?,?,?,?,?)",
                (call_id, cr[0] if cr else None,
                 json.dumps(summary["resolved"], ensure_ascii=False),
                 json.dumps(pending, ensure_ascii=False), summary["emotion"],
                 comp.get("rate", 0.0), "pending_followup"))
    await broadcast_monitor(call_id, {"type": "call_finalized",
                                      "summary": summary})
    await broadcast_monitor(call_id, {"type": "call_ended"})
    # 中途挂断同样把红线处置结果回填工单轨迹（否则只会留下「已教育」的中间态）
    try:
        orders.sync_violations(call_id, summary.get("violations") or [])
    except Exception as e:
        dlog(f"VIOLATION-SYNC-FAIL {call_id} {type(e).__name__}")
    # ① 功能补丁（11/15）：特殊标记 / 闲聊分级留痕（不计红线 KPI）
    try:
        _m = db.query_one("SELECT merchant_id FROM calls WHERE call_id=?", (call_id,))
        orders.record_flags(call_id, (_m or {}).get("merchant_id"), summary.get("flags") or [])
    except Exception as e:
        dlog(f"FLAG-WRITE-FAIL {call_id} {type(e).__name__}")


async def mark_link_failed(call_id: str, reason: str = "通话链路中断") -> None:
    """链路中断 / 服务重启：不是一次业务尝试，标 failed 排除出三项指标分母。"""
    n = db.query_one("SELECT COUNT(*) AS n FROM call_turns WHERE call_id=?", (call_id,))["n"]
    cr = db.query_one("SELECT merchant_id FROM calls WHERE call_id=?", (call_id,))
    sess = SESSIONS.get(call_id) or {}
    agent = sess.get("agent")
    # 11/13：链路中断也要把「断线那一刻」的真实进度写进 result_json，
    # 否则工单/历史会 fallback 成推算值，与实时看板对不上。
    flow5, st_code, comp = [], None, {}
    if agent is not None:
        try:
            flow5 = agent.flow_progress()
            st_code = agent.state
            comp = check_compliance(agent.history)
        except Exception:
            flow5, st_code, comp = [], None, {}
    fail_summary = {"closed_loop": False, "parked": False, "reason": reason,
                    "stop_stage": f"{reason}（共{n}轮）", "state": st_code,
                    "flow5": flow5, "compliance": comp}
    # 11/15 真机修复②：断线兜底同样保留闲聊预警轨迹，避免复盘里凭空消失
    if agent is not None:
        try:
            fail_summary["offtopic"] = agent.summary().get("offtopic") or {}
        except Exception:
            fail_summary["offtopic"] = {}
    with db.atomic() as c:
        c.execute(
            "UPDATE calls SET status='failed', closed_loop=0, "
            "ended_at=datetime('now','localtime'), stop_stage=?, result_json=? WHERE call_id=?",
            (f"{reason}（共{n}轮）", json.dumps(fail_summary, ensure_ascii=False), call_id))
        if not c.execute("SELECT id FROM tickets WHERE call_id=?", (call_id,)).fetchone():
            c.execute(
                "INSERT INTO tickets(call_id,merchant_id,resolved_json,pending_json,"
                "emotion_summary,compliance_rate,status) VALUES(?,?,?,?,?,?,?)",
                (call_id, cr["merchant_id"] if cr else None, "[]",
                 json.dumps(["通话链路中断，需再次外呼确认"], ensure_ascii=False),
                 "neutral", 0.0, "pending_resubmit"))
    await broadcast_monitor(call_id, {"type": "call_finalized",
                                      "summary": {"closed_loop": False}})
    await broadcast_monitor(call_id, {"type": "call_ended"})


async def finalize_call(call_id: str, sess: dict) -> None:
    """通话结束：Agent summary → calls.result_json + tickets（结构化回写，事务保护）。

    11/11 修正：
    * 闭环判定以 Agent 的「要素齐全 + 已完成结构化回写」为唯一真值源；
    * 统一在此把 calls.status 落为 completed / ended_at，杜绝 WS 收尾后残留
      in_progress 导致有效通话分母漏计（原先只有挂断兜底才写 status）；
    * calls 与 tickets 两次写入放进同一事务，避免"有通话没工单"的半截状态。
    """
    agent: CallAgent = sess["agent"]
    summary = agent.summary()
    summary["barge_events"] = sess.get("barge_count", 0)
    call = db.query_one("SELECT merchant_id, scenario_id FROM calls WHERE call_id=?", (call_id,))
    comp = summary.get("compliance", {})
    closed = bool(summary["closed_loop"] and comp.get("rate", 0) >= 1.0)
    summary["closed_loop"] = closed
    ticket_status = "resolved" if closed else "pending_resubmit"
    with db.atomic() as c:
        # 11/12：同一「商家+场景」若已有待跟进工单，说明本次是二次/择日呼叫，
        # 复用该工单并把锚点转移到本次完成的通话 —— 一业务任务一工单，
        # 闭环只在工单终态判一次；旧通话标 superseded，不会重复计入分母。
        prev = None
        if call:
            prev = c.execute(FOLLOWUP_TICKET_SQL,
                             (call["merchant_id"], call["scenario_id"])).fetchone()
        if prev:
            summary["followup_of"] = prev[1]
        c.execute(
            "UPDATE calls SET status='completed', ended_at=datetime('now','localtime'), "
            "closed_loop=?, stop_stage=?, result_json=? WHERE call_id=?",
            (1 if closed else 0, summary["stop_stage"],
             json.dumps(summary, ensure_ascii=False), call_id))
        if prev:
            c.execute(
                "UPDATE tickets SET call_id=?, merchant_id=?, resolved_json=?, pending_json=?, "
                "emotion_summary=?, compliance_rate=?, status=? WHERE id=?",
                (call_id, call["merchant_id"] if call else None,
                 json.dumps(summary["resolved"], ensure_ascii=False),
                 json.dumps(summary["pending"], ensure_ascii=False),
                 summary["emotion"], comp.get("rate", 0.0), ticket_status, prev[0]))
            c.execute("UPDATE calls SET status='superseded' WHERE call_id=? "
                      "AND status='pending_followup'", (prev[1],))
        elif not c.execute("SELECT id FROM tickets WHERE call_id=?", (call_id,)).fetchone():
            c.execute(
                "INSERT INTO tickets(call_id,merchant_id,resolved_json,pending_json,"
                "emotion_summary,compliance_rate,status) VALUES(?,?,?,?,?,?,?)",
                (call_id, call["merchant_id"] if call else None,
                 json.dumps(summary["resolved"], ensure_ascii=False),
                 json.dumps(summary["pending"], ensure_ascii=False),
                 summary["emotion"], comp.get("rate", 0.0), ticket_status))
    await broadcast_monitor(call_id, {"type": "call_finalized", "summary": summary})
    # 13/13：红线三分支的最终处置（认错 / 换违规 / 屡教不改）回填工单轨迹
    try:
        orders.sync_violations(call_id, summary.get("violations") or [])
    except Exception as e:
        dlog(f"VIOLATION-SYNC-FAIL {call_id} {type(e).__name__}")
    # ① 功能补丁（11/15）：特殊标记 / 闲聊分级留痕（不计红线 KPI）
    try:
        _m = db.query_one("SELECT merchant_id FROM calls WHERE call_id=?", (call_id,))
        orders.record_flags(call_id, (_m or {}).get("merchant_id"), summary.get("flags") or [])
    except Exception as e:
        dlog(f"FLAG-WRITE-FAIL {call_id} {type(e).__name__}")


# ---------------- REST：历史 / 工单 / 指标 ----------------

@app.get("/api/calls")
def api_calls():
    rows = db.query(
        "SELECT c.*, m.company_name, s.level2_category FROM calls c "
        "LEFT JOIN merchants m ON m.id=c.merchant_id "
        "LEFT JOIN scenarios s ON s.id=c.scenario_id ORDER BY c.id DESC LIMIT 100")
    # 11/15 第七轮：标记由后端统一判定，历史记录与工单看板消费同一份 flags
    return call_marks.attach(rows)


@app.get("/api/calls/{call_id}")
def api_call_detail(call_id: str):
    call = db.query_one(
        "SELECT c.*, m.company_name, m.shop_name, s.level2_category FROM calls c "
        "LEFT JOIN merchants m ON m.id=c.merchant_id "
        "LEFT JOIN scenarios s ON s.id=c.scenario_id WHERE c.call_id=?", (call_id,))
    if not call:
        raise HTTPException(404, "通话不存在")
    call["flags"] = call_marks.call_flags(call)  # 与历史记录列表同源
    turns = db.query("SELECT * FROM call_turns WHERE call_id=? ORDER BY turn_no", (call_id,))
    ticket = db.query_one("SELECT * FROM tickets WHERE call_id=?", (call_id,))
    return {"call": call, "turns": turns, "ticket": ticket}


# ---------------- 端到端时延：分段预算与精确落库（罗 11/12） ----------------

def _median(arr: list) -> int:
    """真中位数。上一版用 nearest-rank 取 arr[ceil(0.5n)-1]，n=4 时取到第 2 小值，
    会把 P50 从 1558ms 误报成 453ms 造成「假达标」，这里改为严格中位数。"""
    if not arr:
        return 0
    v = sorted(arr)
    n = len(v)
    return round(v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2)


def _p95(arr: list) -> int:
    if not arr:
        return 0
    v = sorted(arr)
    return round(v[max(0, min(len(v) - 1, math.ceil(0.95 * len(v)) - 1))])


def _stage_snapshot(session: dict, e2e_ms: float, asr_tail_ms: float) -> dict:
    """把服务端可见耗时与前端上报的端到端耗时拼成时延预算。

    投机命中时 LLM/TTS 已在用户停说前完成，服务端段接近 0，用 spec_hit 如实标注。
    """
    spec_hit = bool(session.get("spec_hit"))
    t_final = session.get("t_final")
    t_audio = session.get("t_audio_first")
    server_ms = round((t_audio - t_final) * 1000) if (t_final and t_audio) else 0
    return {
        "asr_tail_ms": round(max(0.0, asr_tail_ms)),
        "server_ms": max(0, server_ms),
        "llm_first_ms": 0 if spec_hit else round(session.get("llm_first_ms") or 0),
        "tts_first_ms": 0 if spec_hit else round(session.get("tts_first_ms") or 0),
        "ws_play_ms": max(0, round(e2e_ms - max(0.0, asr_tail_ms) - max(0, server_ms))),
        "spec_hit": spec_hit,
    }


def _save_e2e(call_id: str, turn_no: int, e2e_ms: float, stage: dict) -> None:
    """按 (call_id, turn_no) 精确定位本轮 agent 记录。

    上一版用「最近一条 agent 轮」定位，导致同一轮两次上报互相覆盖
    （日志 5 条只落库 4 条，2056ms 被 453ms 盖掉）。这里用 turn_no 精确定位，
    并且只在该轮尚无 e2e_ms 时写入，彻底避免覆盖。
    """
    try:
        db.execute(
            "UPDATE call_turns SET e2e_ms=?, stage_ms=? "
            "WHERE id=(SELECT id FROM call_turns WHERE call_id=? AND role='agent' "
            "AND turn_no=? AND COALESCE(e2e_ms,0)=0 ORDER BY id DESC LIMIT 1)",
            (e2e_ms, json.dumps(stage, ensure_ascii=False), call_id, turn_no))
    except Exception as e:
        dlog(f"LATENCY-SAVE-FAIL {call_id}: {type(e).__name__}: {str(e)[:100]}")


@app.get("/api/metrics")
def api_metrics():
    """指标看板（独立板块）：时延 / 合规 / 闭环 / 轮次 / 成本。

    11/11 口径修正（三项指标全部统一到同一分母，冉）：
      * 有效通话 = status='completed' 且至少有一条 role='merchant' 的发言；
        未接 / 拒接 / 链路中断 / 服务重启遗留僵尸通话都不计入。
      * 合规率按工单实际分值求均值，空值按 0 计，不再用 (rate or 1) 把 0 分算成满分。
      * 平均轮次只数商家发言轮数，不再把 agent/system 消息和开场白一起数进去。
    11/12 业务任务口径（冉）：
      * 分母 = 已终结的业务任务（AI 流程走完且有商家发言）；
        商家挂断 / 择日回电的 pending_followup、被二次呼叫承接的 superseded 一律排除。
      * 分母内 completed 且 closed_loop=0 属「系统本该做完却没做完」，
        单独统计为 failed_system_calls 但仍计失败，不允许通过排除来抬高闭环率。
      * 闭环率 = 闭环工单数 / 分母；合规率、平均轮次共用同一分母。
    11/12 时延口径（罗）：P50/P95 只统计 e2e_ms>0 的赛题口径样本
      （用户停说 → 浏览器首帧播放），旧 latency_ms 保留兼容但不参与入围判定。
    """
    try:
        e2e_turns = db.query("SELECT e2e_ms, stage_ms FROM call_turns "
                             "WHERE role='agent' AND e2e_ms>0")
    except Exception:
        e2e_turns = []
    lats = sorted(t["e2e_ms"] for t in e2e_turns)
    try:
        legacy = db.query("SELECT latency_ms FROM call_turns "
                          "WHERE role='agent' AND latency_ms>0")
    except Exception:
        legacy = []
    stages = []
    for row in e2e_turns:
        try:
            obj = json.loads(row.get("stage_ms") or "{}")
            if isinstance(obj, dict):
                stages.append(obj)
        except Exception:
            pass

    def stage_avg(key):
        vals = [x[key] for x in stages if isinstance(x.get(key), (int, float))]
        return round(sum(vals) / len(vals)) if vals else 0

    calls = db.query("SELECT * FROM calls")
    valid_ids = [r["call_id"] for r in db.query(VALID_CALLS_SQL)]
    valid_set = set(valid_ids)
    valid_calls = [c for c in calls if c["call_id"] in valid_set]
    total = len(calls)

    closed = sum(1 for c in valid_calls if c["closed_loop"])
    # 11/12：未终结业务、被承接的旧通话、系统能力失败分列展示，供看板当场核对分母
    pending_followup_calls = sum(1 for c in calls if c["status"] == "pending_followup")
    superseded_calls = sum(1 for c in calls if c["status"] == "superseded")
    failed_system_calls = sum(1 for c in valid_calls if not c["closed_loop"])
    rates = [t["compliance_rate"] for t in db.query("SELECT call_id, compliance_rate FROM tickets")
             if t["call_id"] in valid_set]
    compliance = (round(sum((r if r is not None else 0.0) for r in rates) / len(rates) * 100, 1)
                  if rates else 0.0)
    turn_map = {r["call_id"]: r["n"] for r in db.query(
        "SELECT call_id, COUNT(*) AS n FROM call_turns WHERE role='merchant' GROUP BY call_id")}
    avg_turns = (round(statistics.mean([turn_map.get(cid, 0) for cid in valid_ids]), 1)
                 if valid_ids else 0.0)
    try:
        turn_counts = db.query("SELECT call_id, COUNT(*) AS n FROM call_turns "
                               "WHERE role='agent' GROUP BY call_id")
    except Exception:
        turn_counts = []
    calls_3plus = sum(1 for r in turn_counts if (r.get("n") or 0) >= 3)
    p50 = _median(lats)

    return {
        "total_calls": total,
        "valid_calls": len(valid_ids),
        "excluded_calls": total - len(valid_ids),
        "completed": sum(1 for c in calls if c["status"] == "completed"),
        "pending_followup_calls": pending_followup_calls,
        "superseded_calls": superseded_calls,
        "failed_system_calls": failed_system_calls,
        "closed_loop_rate": round(closed / len(valid_ids) * 100, 1) if valid_ids else 0.0,
        "compliance_rate": compliance,
        "avg_turns": avg_turns,
        "low_turn_calls": sum(1 for cid in valid_ids if turn_map.get(cid, 0) < TARGET_TURNS_MIN),
        "p50_ms": p50,
        "p95_ms": _p95(lats),
        "est_cost_yuan": round(total * 0.012, 3),  # glm-4-flash+TTS 粗估
        "emotion_dist": _emotion_dist(),
        # ---- 硬性入围门槛（罗 11/12）----
        "latency_samples": len(lats),
        "tool_stats": {**tools_registry.stats(),
                       "db_events": tools_registry.db_stats()["events"],
                       "db_calls": tools_registry.db_stats()["calls"]},
        # 11/12 红线拦截统计（商家侧）：A 级拦回、B 级终止，与输出侧禁语分开计
        "redline_stats": _redline_stats(),
        "legacy_samples": len(legacy),
        "threshold_ms": THRESHOLD_MS,
        "p50_pass": bool(lats) and p50 <= THRESHOLD_MS,
        "pass_rate": round(sum(1 for x in lats if x <= THRESHOLD_MS) / len(lats) * 100, 1) if lats else 0,
        "calls_3plus": calls_3plus,
        "calls_with_turns": len(turn_counts),
        "turns_3plus_pass": calls_3plus > 0,
        "stage_budget": {
            "asr_tail_ms": stage_avg("asr_tail_ms"),
            "server_ms": stage_avg("server_ms"),
            "llm_first_ms": stage_avg("llm_first_ms"),
            "tts_first_ms": stage_avg("tts_first_ms"),
            "ws_play_ms": stage_avg("ws_play_ms"),
            "spec_hit_rate": round(sum(1 for x in stages if x.get("spec_hit")) / len(stages) * 100, 1) if stages else 0,
        },
        "latency_definition": LATENCY_DEFINITION,
        "targets": {"closed_loop_rate": TARGET_CLOSED_LOOP_RATE,
                    "compliance_rate": TARGET_COMPLIANCE_RATE,
                    "turns_min": TARGET_TURNS_MIN},
        "metric_def": "分母=已终结业务任务(AI流程走完且商家有发言)；商家挂断/择日回电/链路中断等未终结任务不计入分母；流程走完但要素缺失或轮次不足仍留在分母算失败；合规率取同一分母内工单实际分值均值；平均轮次取同一分母内商家发言轮数",
    }


def _emotion_dist() -> dict:
    rows = db.query("SELECT emotion_summary, COUNT(*) AS n FROM tickets GROUP BY emotion_summary")
    return {r["emotion_summary"] or "neutral": r["n"] for r in rows}


def _redline_stats() -> dict:
    """红线拦截统计（商家侧输入）：严重违规转人工 / 已认错 / 分类别计数 / 拦截率。

    与 compliance.red_line_violations（数字员工自己说禁语）分开统计 ——
    一个是"商家提了违规要求被拦住"，一个是"AI 自己说了不该说的话"，
    混成一个指标会让答辩时说不清。

    13/13 口径：不再「B 级一律终止」，只有**同一违规屡教不改**（分支 C / severe）
    才转人工并计入 blocked；分支 A（商家认错）单独统计，体现柔性拦截的效果。

    11/15 看板口径改版：原「红线拦截 N 次」是绝对计数，换成**红线拦截率**。
      红线拦截率 = 成功拦截次数 ÷ 红线诉求命中次数 × 100%
      · 分母 triggered = work_order_events 中 kind='violation' 的事件数
        （每条 = 一次商家侧违规诉求被分类器识别，含 A 认错 / B 教育拉回 / C 转人工）
      · 分子 intercepted = 其中留下「已按企标处置」证据的次数
        （payload 含 say / guide / reply / outcome / action / branch / close / ack 任一）；
        命中却没有任何处置留痕的记为漏拦 missed，会真实拉低拦截率
      · 闲聊警告（kind='offtopic'）自 11/15 起明确不算红线，分子分母均不计入
      · 分母为 0（暂无红线诉求）→ 显示 0%，与看板其它率类指标一致，不虚高为 100%
      · 保留 total / blocked / accused_acked 等原字段，历史明细与事件流不受影响
    """
    try:
        rows = db.query("SELECT payload FROM work_order_events WHERE kind='violation'")
    except Exception:
        rows = []
    by_name: dict = {}
    blocked = 0
    acked = 0
    intercepted = 0
    _HANDLED_KEYS = ("say", "guide", "reply", "outcome", "action", "branch", "close", "ack")
    for r in rows:
        try:
            p = json.loads(r.get("payload") or "{}")
        except Exception:
            p = {}
        nm = p.get("name") or "未分类"
        by_name[nm] = by_name.get(nm, 0) + 1
        # 旧数据没有 branch 字段：沿用 B 级=终止 的口径，保证历史统计不跳变
        if p.get("severe") or p.get("branch") == "C" or (p.get("level") == "B" and not p.get("branch")):
            blocked += 1
        if p.get("branch") == "A" or p.get("ack"):
            acked += 1
        if any(p.get(k) for k in _HANDLED_KEYS):
            intercepted += 1
    triggered = len(rows)
    rate = round(intercepted / triggered * 100, 1) if triggered else 0.0
    try:
        offtopic = len(db.query("SELECT id FROM work_order_events WHERE kind='offtopic'"))
    except Exception:
        offtopic = 0
    return {"total": triggered, "triggered": triggered,
            "intercepted": intercepted, "missed": max(0, triggered - intercepted),
            "rate": rate,
            "definition": "红线拦截率 = 成功拦截次数 ÷ 红线诉求命中次数 × 100%；"
                          "命中以 kind='violation' 留痕为准，成功拦截以企标处置留痕为准，"
                          "闲聊警告不计入；无样本显示 0%",
            "offtopic_excluded": offtopic,
            "blocked": blocked, "accused_acked": acked,
            "warn": max(0, triggered - blocked), "by_name": by_name, "strict": redline.STRICT,
            # B 级沉淀下来的商家长期标记（下次外呼会被 Agent 读到）
            "risk": risk.stats(),
            "categories": redline.categories()}


@app.get("/api/failures")
def api_failures():
    """失败归因聚类（RSI 造用例依据）。"""
    rows = db.query(
        "SELECT c.call_id, m.company_name, c.stop_stage, c.status, c.closed_loop, "
        "c.result_json, t.compliance_rate "
        "FROM calls c LEFT JOIN merchants m ON m.id=c.merchant_id "
        "LEFT JOIN tickets t ON t.call_id=c.call_id "
        "WHERE c.status IN ('completed','failed') AND c.closed_loop=0 ORDER BY c.id DESC LIMIT 50")
    # 11/15 第七轮：失败归因页的「未闭环」也用统一标记（含原因），不再自判
    return call_marks.attach(rows)


# ---------------- 实时工具调用（Function Calling，11/12） ----------------

@app.get("/api/tools")
def api_tools():
    """工具清单与运行统计：答辩现场可直接打开这一页证明「模型真的在调工具」。

    schemas 是真正传给大模型的 JSON Schema（不是文档），
    memory 为本次进程内的调用统计，persisted 为落库的轨迹条数（跨重启）。
    """
    return {
        "enabled": tools_registry.enabled(),
        "timeout_ms": int(tools_registry.timeout_s() * 1000),
        "model": os.getenv("ZHIPU_MODEL", "glm-4-flash"),
        "tools": [{"name": t["function"]["name"],
                   "label": tools_registry.TOOL_LABELS.get(t["function"]["name"], ""),
                   "description": t["function"]["description"],
                   "schema": t["function"]["parameters"]} for t in tools_registry.TOOLS],
        "memory": tools_registry.stats(),
        "persisted": tools_registry.db_stats(),
    }


@app.get("/api/tools/events")
def api_tool_events(limit: int = 30):
    """最近的工具调用留痕（与工单处理轨迹同源，便于现场核对）。"""
    rows = db.query(
        "SELECT e.id,e.order_id,e.kind,e.title,e.detail,e.actor,e.ref,e.payload,e.created_at "
        "FROM work_order_events e WHERE e.kind='tool' ORDER BY e.id DESC LIMIT ?", (int(limit),))
    for r in rows:
        try:
            r["payload"] = json.loads(r.get("payload") or "{}")
        except Exception:
            r["payload"] = {}
    return {"items": rows, "total": tools_registry.db_stats()}

# ---------------- REST：独立评估 v2（冉 11/12，只读） ----------------
@app.get("/api/metrics/v2")
def api_metrics_v2(strict: bool = False):
    """独立评估器在线版（A 方案最小接入）：三个指标分开遍历 + 样本对账。

    与 /api/metrics（实时看板口径）并存互不覆盖；strict=True 启用资料核实硬门槛。
    """
    import evaluators
    try:
        rep = evaluators.MetricEvaluator(db.DB_PATH, strict_materials=strict).report()
        return {"ok": True, "strict_materials": strict, **rep}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)[:200]}, status_code=500)

@app.get("/api/webrtc/stats")
def api_webrtc_stats(limit: int = 200):
    """【WebRTC·11/19】弱网遥测汇总：浏览器 getStats 每 2s 上报的 jsonl 聚合。

    返回最近 limit 条 + 分位统计（RTT/抖动/丢包率），供运营后台「弱网指标」卡片
    与技术方案书的 WS vs WebRTC 对比表使用。
    """
    path = os.path.join(RTC_DATA_DIR, "rtc_stats.jsonl")
    rows = []
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()[-max(1, min(limit, 2000)):]
            for line in lines:
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass
        except Exception:
            pass

    def pct(vals, q):
        if not vals:
            return None
        vals = sorted(v for v in vals if v is not None)
        if not vals:
            return None
        idx = min(len(vals) - 1, max(0, int(round(q * (len(vals) - 1)))))
        return round(vals[idx], 1)

    rtts = [r.get("rtt_ms") for r in rows if r.get("rtt_ms") is not None]
    jits = [r.get("jitter_ms") for r in rows if r.get("jitter_ms") is not None]
    losses = [r.get("loss_pct") for r in rows if r.get("loss_pct") is not None]
    return {
        "ok": True,
        "samples": len(rows),
        # 11/21：前端弱网开关当前生效值（0=关闭），便于现场核验开关是真注入而非装饰
        "weaknet_loss_pct": RTC_GET_LOSS() if RTC_GET_LOSS else None,
        "rtt_ms": {"p50": pct(rtts, .5), "p95": pct(rtts, .95), "max": max(rtts) if rtts else None},
        "jitter_ms": {"p50": pct(jits, .5), "p95": pct(jits, .95)},
        "loss_pct": {"p50": pct(losses, .5), "p95": pct(losses, .95), "max": max(losses) if losses else None},
        "recent": rows[-20:],
    }


# ---------------- REST：RSI 自进化 ----------------

RSI_LOG_TAIL = 120          # 诊断时读取 server_debug.log 的最近行数
RSI_ASR_ERR_PAT = re.compile(r"(错误码|error|4500\d{3}|5500\d{3}|重建|rebuild)", re.I)

RSI_ALLOWED_DIMS = {"时延", "ASR", "TTS", "状态机", "话术", "前端", "数据"}
RSI_NUM_PAT = re.compile(r"\d")

# 已完成的优化清单（11/12）：诊断师看不到代码，不知道哪些能力已经存在——
# 不喂这份清单，它会把"已经做过的事"再建议一遍（实测教训：e2e 2318ms 超标时
# 它建议"启用流式首包"，而罗早已实现）。改代码/新增优化后要同步维护这份清单。
# （11/13 合并江打磨2.0：恢复本清单并补录江本轮 4 项新优化）
DONE_OPTIMIZATIONS = [
    "流式TTS首包——GLM-TTS流式SSE分片，首包音频生成即下发，不等整句（罗，tts_zhipu.py）",
    "投机预生成引擎——商家说话期间用partial文本提前跑LLM+首句TTS并缓存，命中轮毫秒级回复（罗，speculative.py）",
    "ASR温和尾点end_window_size=600 + 200ms静音保活帧防空闲报错（罗，asr_volcano.py）",
    "reply_epoch防串句——打断后旧片段作废、同轮片段合并成一次TTS请求（罗）",
    "实时Function Calling——3个只读工具（工单状态/商家档案/知识库检索），1600ms超时熔断+关键词门控（江，tools_registry.py）",
    "工单看板+事件轨迹——工具调用、回拨、备注全落轨迹可展开查看（江，orders.py）",
    "双向字幕同步——服务端唯一真值源，商家端/后台逐字相同渲染+1秒校准（秦）",
    "结束语播完才挂断——按WAV头累计真实播放时长+播完后静默5秒回应窗口+ASR回声保护（秦）",
    "浏览器播放队列防卡死——挂断先停播、onended兜底超时、上下文关闭守卫（冯）",
    "指标口径修正——有效通话分母/合规率如实计0/轮次只数商家发言（冉）+独立评估器evaluators.py（三指标分开遍历+样本对账）",
    "要素补句机制——每轮合规检查发现要素缺失自动追加固定短句（agent.py _topup_elements）",
    "诊断师硬校验——层面七类白名单/症状必须含数字/话术类才许带补丁/落点必填（本轮）",
    "对话单步推进——S1强确认拆分CONFIRMED_ONLY+服务端转场自动续讲auto_continue_job，一轮只亮一个进度灯（江，agent.py/server.py）",
    "红线违规三分支处置——A认错继续/B继续教育/C严重3次转人工，纠正话术redline.py+违规收尾回填sync_violations（江）",
    "改约两段式——商家说没空先问时间，_parse_time_expr解析后天/下周X/具体日期，reschedule_at落库（江）",
    "告别语识别与工具卡片常驻——is_farewell告别后零回复直接收线；工具调用写字幕快照常驻后台、商家端过滤不上屏（江）",
]


def _validate_diagnosis(d: dict) -> list:
    """诊断师输出的硬校验（11/12 方案二）：提示词会被模型漂移绕过，代码校验不会。

    校验四条：①层面必须在七类白名单内 ②症状必须含数字证据
    ③script_patch 仅"话术"层面允许 ④fix_target 必须给出落点。
    返回错误列表；空列表 = 通过。
    """
    errs = []
    dim = (d.get("dimension") or "").strip()
    if dim not in RSI_ALLOWED_DIMS:
        errs.append(f"层面'{dim}'不在七类白名单内")
    if not RSI_NUM_PAT.search(d.get("symptom") or ""):
        errs.append("症状无数字证据（纯对话引用无效）")
    if d.get("script_patch") and dim != "话术":
        errs.append(f"层面'{dim}'不允许带话术补丁")
    if not (d.get("fix_target") or "").strip():
        errs.append("未给出修复落点 fix_target")
    return errs


def _asr_recent_errors(limit: int = 10) -> list:
    """从 server_debug.log 提取最近的 ASR/链路错误（技术证据之一）。"""
    log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "server_debug.log")
    hits = []
    try:
        with open(log_path, encoding="utf-8", errors="ignore") as fh:
            lines = fh.readlines()[-RSI_LOG_TAIL:]
        for ln in lines:
            if RSI_ASR_ERR_PAT.search(ln):
                hits.append(ln.strip()[:160])
        hits = hits[-limit:]
    except OSError:
        pass
    return hits


def _tech_evidence(call: dict) -> dict:
    """单通失败通话的技术证据包：时延分段 / 状态机轨迹 / 打断 / 合规要素缺口。

    诊断师升级（11/12）：不再只喂对话文本——
    LLM 只看对话就只会"话术往长里改"；给它看技术数据才能挑时延/状态机/ASR的毛病。
    """
    cid = call["call_id"]
    turns = db.query("SELECT turn_no, role, text, interrupted, e2e_ms, stage_ms, latency_ms "
                     "FROM call_turns WHERE call_id=? ORDER BY turn_no", (cid,))
    e2es = [t["e2e_ms"] for t in turns if (t.get("e2e_ms") or 0) > 0]
    stages = []
    for t in turns:
        try:
            obj = json.loads(t.get("stage_ms") or "{}")
            if isinstance(obj, dict) and obj:
                stages.append(obj)
        except Exception:
            pass

    def avg(key: str) -> int:
        vals = [s[key] for s in stages if isinstance(s.get(key), (int, float))]
        return round(sum(vals) / len(vals)) if vals else 0

    interrupts = sum(1 for t in turns if t.get("interrupted"))
    merchant_turns = sum(1 for t in turns if t["role"] == "merchant")
    tick = db.query_one("SELECT compliance_rate, emotion_summary, resolved_json, pending_json "
                        "FROM tickets WHERE call_id=?", (cid,))
    try:
        result = json.loads(call.get("result_json") or "{}")
        if not isinstance(result, dict):
            result = {}
    except Exception:
        result = {}
    evidence = {
        "call_id": cid,
        "stop_stage": call.get("stop_stage", ""),
        "closed_loop": call.get("closed_loop", 0),
        "merchant_turns": merchant_turns,
        "merchant_interrupts": interrupts,
        "interrupt_ratio": round(interrupts / merchant_turns, 2) if merchant_turns else 0,
        "compliance_rate": (tick or {}).get("compliance_rate") if tick else None,
        "emotion": (tick or {}).get("emotion_summary") if tick else "",
        "resolved": (tick or {}).get("resolved_json") if tick else "",
        "pending": (tick or {}).get("pending_json") if tick else "",
        "e2e_samples": len(e2es),
        "e2e_avg_ms": round(sum(e2es) / len(e2es)) if e2es else 0,
        "e2e_max_ms": max(e2es) if e2es else 0,
        "stage_avg_ms": {"asr_tail": avg("asr_tail_ms"), "llm": avg("server_ms"),
                         "tts": avg("tts_ms"), "ws_play": avg("ws_play_ms")} if stages else {},
        "result_summary": result,
    }
    return evidence


def _system_tech_snapshot() -> dict:
    """系统级技术快照：给诊断师看全局时延/命中率/ASR错误，找共性问题。"""
    m = api_metrics()
    return {
        "p50_ms": m.get("p50_ms"), "p95_ms": m.get("p95_ms"),
        "p50_pass_rate": m.get("pass_rate"), "threshold_ms": m.get("threshold_ms"),
        "stage_budget_ms": m.get("stage_budget"),
        "latency_samples": m.get("latency_samples"),
        "closed_loop_rate": m.get("closed_loop_rate"),
        "compliance_rate": m.get("compliance_rate"),
        "avg_turns": m.get("avg_turns"),
        "low_turn_calls": m.get("low_turn_calls"),
        "failed_system_calls": m.get("failed_system_calls"),
        "pending_followup_calls": m.get("pending_followup_calls"),
        "asr_recent_errors": _asr_recent_errors(),
    }

@app.get("/api/rsi/versions")
def api_rsi_versions():
    return db.query("SELECT * FROM rsi_versions ORDER BY id DESC")


# ---------------- REST：未知词台账（RSI 待审核队列，11/22） ----------------
# 商家反复问、但既不是已知业务词、知识库也查不到的词（多半是 ASR 听错）。
# 审核通过后写进 term_map，从下一通电话起自动替换 —— 词表是"长出来"的，不靠人手敲。

@app.get("/api/rsi/unknown-terms")
def api_unknown_terms(limit: int = 50):
    """待审核列表 + 统计。管理员在后台看到"哪些词商家反复问但系统没词"。"""
    import unknown_terms
    return {"ok": True, "items": unknown_terms.pending(limit),
            "stats": unknown_terms.stats(),
            "hint": "approve 后立即生效：误听词 → 正确业务词（无需改代码、无需重启理解逻辑）"}


@app.post("/api/rsi/unknown-terms/{term_id}/approve")
def api_unknown_terms_approve(term_id: str, body: dict):
    """审核通过：{term_id} 可以是台账 id 或词本身；body 需带 right（正确业务词）。"""
    import unknown_terms
    res = unknown_terms.approve(term_id, (body or {}).get("right", ""))
    return res


@app.post("/api/rsi/unknown-terms/{term_id}/reject")
def api_unknown_terms_reject(term_id: str):
    """审核不通过：确实是与业务无关的外词，标记掉不再打扰。"""
    import unknown_terms
    return unknown_terms.reject(term_id)


# ============ 改01：补齐前端已接、后端原本缺失的三个 RSI 接口 ============
RSI_HINT_KEY = "rsi_hint_consumed_at"   # 记录上次提示时已计入的失败会话数


@app.post("/api/rsi/reset")
def api_rsi_reset():
    """重置为 v1.0 基础版：清空全部版本历史，重建一条 v1.0 基线记录。

    前端「重置为 v1.0」按钮原本调用本接口但后端没有，点了必报 404。
    只动 rsi_versions 一张表，不碰通话/工单/知识库。
    """
    try:
        db.execute("DELETE FROM rsi_versions")
        snap = _metrics_snapshot()
        db.execute(
            "INSERT INTO rsi_versions(version,parent_version,metrics_json,changes_json,summary,status) "
            "VALUES('v1.0','',?,?,?, 'active')",
            (json.dumps({"baseline": snap}, ensure_ascii=False), "[]",
             "v1.0 基础版（改01重置）：系统初始状态，尚无自进化诊断。"))
        db.execute("DELETE FROM settings WHERE key=?", (RSI_HINT_KEY,))
        return {"ok": True, "version": "v1.0"}
    except Exception as e:
        return JSONResponse({"ok": False, "msg": f"重置失败: {str(e)[:160]}"}, status_code=200)


@app.get("/api/rsi/hint")
def api_rsi_hint(consume: bool = False):
    """RSI 就绪提示：统计未闭环失败会话，超过上次已读数量时提示红点/浮层。

    前端 rsiHintPoll / rsiHintConsume 调用，原本 404 导致红点与浮层永远不出现。
    """
    try:
        total = db.query_one(
            "SELECT COUNT(*) AS n FROM calls WHERE closed_loop=0 "
            "AND status IN ('completed','failed')")["n"]
        row = db.query_one("SELECT value FROM settings WHERE key=?", (RSI_HINT_KEY,))
        try:
            last = int((row or {}).get("value") or 0)
        except (ValueError, TypeError):
            last = 0
        new = max(0, total - last)
        if consume:
            db.execute(
                "INSERT INTO settings(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (RSI_HINT_KEY, str(total)))
            new = 0
        return {"ok": True, "notify": new > 0, "new_since_last": new, "failed_total": total}
    except Exception as e:
        return {"ok": False, "notify": False, "new_since_last": 0,
                "failed_total": 0, "msg": str(e)[:120]}


@app.get("/api/rsi/evidence")
def api_rsi_evidence(version_id: int, index: int):
    """单条诊断的「进化对照」：问题现场 vs 同场景改进后通话 + 效果账。

    前端 openEvidence 原本调用本接口但后端没有，点「进化对照」必弹"加载失败"。
    返回结构严格对齐前端 _evDlg/_evMet/cmpRow 的字段：
    diagnosis / before{scenario,call_id,metrics,dialogue} /
    after{metrics,matched[{kind,call_id,scenario,metrics,dialogue}]} / note。
    """
    v = db.query_one("SELECT * FROM rsi_versions WHERE id=?", (version_id,))
    if not v:
        return JSONResponse({"ok": False, "msg": "版本不存在"}, status_code=200)
    try:
        changes = json.loads(v["changes_json"] or "[]")
    except Exception:
        changes = []
    if not (0 <= index < len(changes)):
        return JSONResponse({"ok": False, "msg": "诊断序号不存在"}, status_code=200)
    diag = changes[index] or {}

    def _dialogue(cid: str, limit: int = 16) -> list:
        rows = db.query(
            "SELECT role,text,e2e_ms,latency_ms FROM call_turns "
            "WHERE call_id=? ORDER BY turn_no", (cid,))
        return [{"role": r["role"], "text": r["text"],
                 "e2e_ms": r.get("e2e_ms") or 0, "latency_ms": r.get("latency_ms") or 0}
                for r in rows[:limit]]

    def _metrics(cid: str) -> dict:
        turns = db.query("SELECT role,e2e_ms FROM call_turns WHERE call_id=?", (cid,))
        e2es = [t["e2e_ms"] for t in turns if t["role"] == "agent" and (t.get("e2e_ms") or 0) > 0]
        mturns = sum(1 for t in turns if t["role"] == "merchant")
        tick = db.query_one("SELECT compliance_rate FROM tickets WHERE call_id=?", (cid,))
        call = db.query_one("SELECT closed_loop FROM calls WHERE call_id=?", (cid,))
        return {"e2e_avg_ms": round(sum(e2es) / len(e2es)) if e2es else None,
                "e2e_max_ms": max(e2es) if e2es else None,
                "merchant_turns": mturns,
                "compliance_rate": (tick or {}).get("compliance_rate") if tick else None,
                "closed_loop": bool((call or {}).get("closed_loop"))}

    cid = str(diag.get("call_id") or "")
    scen = str(diag.get("scenario") or "")
    before = {"call_id": cid, "scenario": scen,
              "metrics": _metrics(cid) if cid else None,
              "dialogue": _dialogue(cid) if cid else []}

    # 改进后：优先同场景（停点一致）且已闭环的通话；不足则退回任意已闭环通话
    closed = db.query(
        "SELECT call_id, stop_stage FROM calls WHERE closed_loop=1 "
        "AND call_id<>? ORDER BY id DESC LIMIT 40", (cid,))
    same = [c for c in closed if scen and (c.get("stop_stage") or "") == scen]
    pool = (same or closed)[:2]
    matched = []
    for c in pool:
        m = _metrics(c["call_id"])
        if not m or m.get("e2e_avg_ms") is None:
            continue
        matched.append({"kind": "同场景" if c in same else "其他闭环",
                        "call_id": c["call_id"], "scenario": c.get("stop_stage") or "",
                        "metrics": m, "dialogue": _dialogue(c["call_id"])})

    eff = None
    if matched:
        avgs = [m["metrics"]["e2e_avg_ms"] for m in matched if m["metrics"].get("e2e_avg_ms")]
        maxs = [m["metrics"]["e2e_max_ms"] for m in matched if m["metrics"].get("e2e_max_ms")]
        rates = [m["metrics"]["compliance_rate"] for m in matched
                 if m["metrics"].get("compliance_rate") is not None]
        eff = {"e2e_avg_ms": round(sum(avgs) / len(avgs)) if avgs else None,
               "e2e_max_ms": max(maxs) if maxs else None,
               "merchant_turns": round(sum(m["metrics"].get("merchant_turns") or 0
                                           for m in matched) / len(matched), 1),
               "compliance_rate": round(sum(rates) / len(rates)) if rates else None,
               "closed_rate": 100}
    after = {"samples": len(matched), "metrics": eff, "matched": matched}
    note = ("对照通话自动收集：优先匹配与问题现场同一停滞环节的已闭环通话。"
            + ("激活新版后继续外呼，对照会持续积累。" if matched
               else "当前暂无可对照的已闭环通话，激活新版后再回看此页。"))
    return {"ok": True, "version": v["version"], "diagnosis": diag,
            "before": before, "after": after, "note": note}


# ============ 改02：进化前后对比 · 评委展示聚合接口（全程只读） ============
@app.get("/api/rsi/showcase")
def api_rsi_showcase():
    """一屏拿到「自进化前 vs 自进化后」的真实对比素材，供评委展示弹窗渲染。

    全部来自系统真实数据，不编造：
    * script  话术：版本 engineer_json.patches 的真实 old→new 代码 diff
                   + changes_json 里带 after 的话术补丁建议；
    * capability 能力：诊断维度分布 + 已沉淀优化清单（DONE_OPTIMIZATIONS）；
    * metric  指标：真实通话库聚合（闭环率/合规率/e2e样本/P50），按「全部通话」与
                   「v1.1 及以后」两段对照，体现自进化上线前后的实测口径差异；
    * summary 概览：版本数、诊断数、采纳数、补丁数，以及一句话结论。
    """
    versions = db.query("SELECT * FROM rsi_versions ORDER BY id")

    # ---- 概览 ----
    total_diag = total_selected = total_patches = 0
    dim_count = {}
    script_rows = []           # 改08：真正的前后对比行（自进化前→自进化后）
    dim_selected = {}          # 各维度已采纳数（自进化后产出）
    for v in versions:
        try:
            ch = json.loads(v["changes_json"] or "[]")
        except Exception:
            ch = []
        total_diag += len(ch)
        total_selected += sum(1 for c in ch if c.get("selected"))
        for c in ch:
            dim = c.get("dimension") or "技术归因"
            dim_count[dim] = dim_count.get(dim, 0) + 1
            if c.get("selected"):
                dim_selected[dim] = dim_selected.get(dim, 0) + 1
            # 话术对比行：有 after 的诊断（自进化前=沿用原话术，自进化后=建议话术）
            after = c.get("after") or ""
            if after:
                script_rows.append({
                    "version": v["version"],
                    "scenario": c.get("scenario") or "",
                    "before": "（沿用源码原话术 · 自进化前基线）",
                    "after": after,
                    "source": "诊断建议",
                    "status": "已采纳" if c.get("selected") else "待采纳",
                })
        try:
            ej = json.loads(v.get("engineer_json") or "{}")
        except Exception:
            ej = {}
        for p in (ej.get("patches") or []):
            total_patches += 1
            # 代码补丁：自进化前=old，自进化后=new（真实 diff）
            script_rows.append({
                "version": v["version"],
                "scenario": p.get("desc") or p.get("file") or "",
                "before": p.get("old") or "（无原文）",
                "after": p.get("new") or "（无新文）",
                "source": "代码补丁 diff",
                "status": "已应用" if ej.get("status") == "applied" else (ej.get("status") or "提案"),
            })

    evolved_versions = sum(1 for v in versions if v["version"] != "v1.0")
    summary = {
        "versions": len(versions),
        "evolved_versions": evolved_versions,
        "diagnoses": total_diag,
        "adopted": total_selected,
        "patches": total_patches,
        "baseline_caps": len(DONE_OPTIMIZATIONS),
        "conclusion": (f"自进化前系统自带 {len(DONE_OPTIMIZATIONS)} 项工程能力(基线)；"
                       f"自进化后 RSI 产出 {total_diag} 条诊断、采纳 {total_selected} 条、"
                       f"生成 {total_patches} 份补丁，覆盖 {len(dim_count)} 个问题层面。"),
    }

    # ---- 能力：自进化前(系统自带基线) vs 自进化后(RSI产出) ----
    baseline_caps = []
    for item in DONE_OPTIMIZATIONS:
        cut = item.split("——", 1)
        baseline_caps.append({"name": cut[0].strip(),
                              "desc": (cut[1].strip() if len(cut) > 1 else "")})
    evolved_caps = [{"dim": k, "count": n, "adopted": dim_selected.get(k, 0)}
                    for k, n in sorted(dim_count.items(), key=lambda x: -x[1])]
    capability = {
        "baseline": baseline_caps,
        "evolved": evolved_caps,
        "note": "自进化前 = 系统已沉淀的工程能力（基线自带，非 RSI 产出）；"
                "自进化后 = RSI 自进化新覆盖的问题层面（诊断维度分布）。",
    }

    # ---- 指标：自进化前(v1.0基线快照) vs 自进化后(最新自进化版本快照) ----
    def _snap(v):
        try:
            return (json.loads(v["metrics_json"] or "{}").get("baseline") or {})
        except Exception:
            return {}
    before_snap = None
    after_snap = None
    for v in versions:
        s = _snap(v)
        if not s:
            continue
        if v["version"] == "v1.0":
            before_snap = {"version": v["version"], "status": v["status"], **s}
        else:
            after_snap = {"version": v["version"], "status": v["status"], **s}  # 取最新的非 v1.0
    # 对比行：逐指标 before / after / 变化
    metric_keys = [("p50_ms", "首响 P50 (ms)"), ("p95_ms", "P95 (ms)"),
                   ("closed_loop_rate", "任务闭环率 (%)"), ("compliance_rate", "话术合规率 (%)"),
                   ("avg_turns", "平均轮次"), ("valid_calls", "有效通话 (通)")]
    metric_rows = []
    for key, label in metric_keys:
        b = before_snap.get(key) if before_snap else None
        a = after_snap.get(key) if after_snap else None
        delta = None
        if b is not None and a is not None:
            try:
                d = float(a) - float(b)
                delta = ("↑" if d > 0 else "↓" if d < 0 else "→") + f" {abs(d):.1f}"
            except Exception:
                delta = None
        metric_rows.append({"label": label, "before": b, "after": a, "delta": delta})

    # 整体实测达成（保留作参考，非前后对比）
    def _agg(where: str, params: tuple = ()) -> dict:
        base = "FROM calls c LEFT JOIN tickets t ON t.call_id=c.call_id " + where
        row = db.query_one(
            "SELECT COUNT(*) AS n, "
            "SUM(CASE WHEN c.closed_loop=1 THEN 1 ELSE 0 END) AS closed, "
            "AVG(t.compliance_rate) AS compliance " + base, params)
        n = row["n"] or 0
        closed = row["closed"] or 0
        comp = row["compliance"]
        ids = [r["call_id"] for r in db.query("SELECT c.call_id " + base, params)]
        e2es = []
        if ids:
            ph = ",".join("?" * len(ids))
            e2es = [r["e2e_ms"] for r in db.query(
                f"SELECT e2e_ms FROM call_turns WHERE role='agent' AND e2e_ms>0 "
                f"AND call_id IN ({ph})", tuple(ids))]
        e2es_sorted = sorted(e2es)
        return {
            "calls": n,
            "closed_rate": round(closed / n * 100, 1) if n else None,
            "compliance_rate": round(comp * 100, 1) if comp is not None else None,
            "e2e_samples": len(e2es),
            "e2e_p50_ms": (e2es_sorted[len(e2es_sorted) // 2] if e2es_sorted else None),
        }
    all_metric = _agg("WHERE c.status IN ('completed','failed')")
    all_metric["goal_e2e_ms"] = 2000
    all_metric["e2e_pass"] = (all_metric.get("e2e_p50_ms") is not None
                              and all_metric["e2e_p50_ms"] <= 2000)

    return {
        "ok": True,
        "summary": summary,
        "script": {"rows": script_rows,
                   "note": "自进化前 = 补丁 old 或源码原话术(基线)；自进化后 = 补丁 new 或诊断建议 after。"
                           "无代码补丁时 before 标注「沿用源码原话术」，如实呈现、不编造。"},
        "capability": capability,
        "metric": {"before": before_snap, "after": after_snap, "rows": metric_rows,
                   "overall": all_metric,
                   "note": "自进化前 = v1.0 基线版本生成时点的指标快照；自进化后 = 最新自进化版本快照。"
                           "快照缺失时如实标注「无快照」，不强行造对比。下方整体实测为当前全量通话聚合，仅作参考。"},
    }


@app.post("/api/rsi/run")
async def api_rsi_run(call_ids: str = ""):
    """一键自进化（11/12 升级）：技术证据包 + 对话文本 → 结构化双维度诊断 → 待审核版本。

    诊断师定位升级：从"话术教练"改为"技术+话术双维度审查"。
    * 输入：每通失败通话的技术证据（分段时延/打断率/合规缺口/状态机停点）+ 系统级快照
      （P50/P95/分段预算/ASR近期错误）+ 对话文本；
    * 输出：结构化 JSON 诊断（层面/症状/根因假设/建议修复/置信度），话术补丁仅在
      确属话术问题时给出，且必须比原话短或等长、五要素一个不丢——禁止无脑加长。
    * 兼容性：changes_json 同时保留旧字段（root_cause/evidence/after），旧前端渲染不破坏。
    * 测试钩子：可选 ?call_ids=a,b,c 指定本轮诊断哪几通（最多 5 通），缺省行为不变。
    """
    picked = [x.strip() for x in (call_ids or "").split(",") if x.strip()][:5]
    if picked:
        ph = ",".join("?" * len(picked))
        failures = db.query(
            "SELECT c.*, t.compliance_rate FROM calls c LEFT JOIN tickets t ON t.call_id=c.call_id "
            f"WHERE c.closed_loop=0 AND c.call_id IN ({ph})",
            tuple(picked))
    else:
        failures = db.query(
            "SELECT c.*, t.compliance_rate FROM calls c LEFT JOIN tickets t ON t.call_id=c.call_id "
            "WHERE c.closed_loop=0 AND c.status='completed' LIMIT 10")
    if not failures:
        return JSONResponse({"ok": False, "msg": "暂无失败会话，无法进化。先跑几通失败外呼再试。"}, status_code=200)

    from zhipuai import ZhipuAI
    client = ZhipuAI(api_key=os.getenv("ZHIPU_API_KEY", ""))
    rsi_model = os.getenv("RSI_MODEL", "glm-4-flash")
    sys_snap = _system_tech_snapshot()
    patches = []
    rejected = []          # 方案二：被硬校验拦下的诊断（留痕，写进版本摘要）
    for f in failures[:5]:
        turns = db.query("SELECT role,text FROM call_turns WHERE call_id=? ORDER BY turn_no", (f["call_id"],))
        dlg = "\n".join(f"{'客服' if t['role']=='agent' else '商家'}：{t['text']}" for t in turns[:16])
        tech = _tech_evidence(f)
        prompt = f"""你是外呼系统的技术诊断师，职责是"挑毛病"而不是"夸系统"。系统是京东资质审核外呼AI（火山ASR→GLM生成→TTS→WebSocket下发），判定由本地状态机完成。

【这通失败通话的技术证据】
{json.dumps(tech, ensure_ascii=False, indent=1)}

【系统级技术快照（全量通话汇总）】
{json.dumps(sys_snap, ensure_ascii=False, indent=1)}

【系统已完成的优化清单（这些能力全部已实现，禁止再建议做这些）】
{chr(10).join(f'- {item}' for item in DONE_OPTIMIZATIONS)}

【对话文本】
{dlg}

请从技术数据里找真问题。输出JSON（不要多余文字）：
{{"diagnoses": [
  {{"dimension": "问题层面（七选一：时延/ASR/TTS/状态机/话术/前端/数据）",
    "symptom": "症状（必须引用证据里的数字：毫秒/百分比/次数/轮数，纯对话引用无效）",
    "root_cause": "根因假设（指向具体环节，如'TTS整句合成未用流式首包'）",
    "fix_target": "修复落点（四选一：agent.py话术常量 / server.py TARGET_*参数 / knowledge知识库 / 白名单外需人工）",
    "fix_suggestion": "建议修复（技术改法或验证步骤，一句话）",
    "confidence": 0.8,
    "script_patch": null}}]}}
要求：
1. 最多3条诊断，按置信度降序；优先从技术证据找问题，对话只用来佐证；
2. 【落点约束】每条诊断的 fix_target 必须落在工程师白名单内：话术常量（agent.py 顶部固定话术）、
   达标线参数（server.py 的 TARGET_*）、或 knowledge（知识库补一条业务口径）；
   改函数逻辑/改前端/改数据库全部标"白名单外需人工"；
   若是"知识缺口"（商家问的业务口径知识库里没有、检索未命中），fix_target 写 knowledge，
   fix_suggestion 直接给可入库的完整口径（含数字/条件，不要占位符）；
3. 【证据约束】symptom 必须含数字（来自技术证据包），不含数字的诊断会被系统直接丢弃；
4. 【数据类约束】"数据"层面（如知识缺口）只有在证据显示检索未命中/字段缺失时才允许提；
   没有数据证据就降级写"数据层待查"，禁止凭对话感觉推断；
5. script_patch 仅在确属"话术不清/要素缺失"时给出，且必须比原话更短或等长、必说要素一个不能丢
   ——禁止把话术改长；非话术问题一律 null；
6. 若证据里 e2e_samples=0 说明该通话没有时延样本，不要臆造时延问题；
7. 【禁止过时建议】上面"已完成的优化清单"里的能力都已实现——若你的根因假设指向其中某项：
   a) root_cause 措辞必须是"该项已实现，但疑似未生效/参数不合理"，禁止写"未实现/未使用/缺失"；
   b) fix_suggestion 改为"验证该项是否实际生效、参数是否合理"（如验证投机命中率、TTS分片下发间隔），
      并在 symptom 里用数字说明为何怀疑它没生效。
8. 【工具证据】若 result_summary.tool_calls 显示工具已返回具体结果（如"工单 3/5""通话 4 通"），
   但 AI 回复仍让商家"自己去后台查"、没有引用该结果，必须报一条"话术/工具策略"诊断；
   fix_target 写 "prompts.py TOOL_DESC"，并要求回复优先引用工具返回的进度/次数/下一步。"""
        try:
            resp = client.chat.completions.create(model=rsi_model, messages=[
                {"role": "user", "content": prompt}], temperature=0.2, max_tokens=900)
            raw = resp.choices[0].message.content.strip()
            raw = raw[raw.find("{"): raw.rfind("}") + 1]
            data = json.loads(raw)
            diag_list = (data.get("diagnoses") or [])[:3]
        except Exception as e:
            diag_list = [{"dimension": "诊断失败", "symptom": str(e)[:80],
                          "root_cause": "模型调用/解析失败", "fix_suggestion": "检查 RSI_MODEL 与 API key",
                          "confidence": 0.0, "script_patch": None}]

        # 组装补丁：一条 diagnosis = 一条补丁记录；旧字段兼容旧前端渲染
        # 方案二：先过硬校验，不合格直接丢弃并留痕——提示词会被模型漂移绕过，代码校验不会
        for d in diag_list:
            if d.get("dimension") != "诊断失败":       # 模型调用失败的占位记录不参与校验
                errs = _validate_diagnosis(d)
                if errs:
                    rejected.append({"call_id": f["call_id"], "diagnosis": d.get("symptom", "")[:60],
                                     "reasons": errs})
                    continue
            dim = d.get("dimension", "")
            fix_target = (d.get("fix_target") or "").strip()
            patch_after = d.get("script_patch") or ""
            patches.append({
                "call_id": f["call_id"],
                "dimension": dim,
                "symptom": d.get("symptom", ""),
                "root_cause": f"[{dim}] {d.get('root_cause', '')}" if dim else d.get("root_cause", ""),
                "evidence": d.get("symptom", ""),
                "fix_target": fix_target,
                # 落点并入建议修复，前端无需改动即可见；工程师 Agent 也直接可读
                "fix_suggestion": (f"[落点:{fix_target}] " if fix_target else "") + d.get("fix_suggestion", ""),
                "confidence": d.get("confidence"),
                "after": patch_after,
                "scenario": f.get("stop_stage") or "失败通话",
                "tech_evidence": tech,
            })

    # 版本号：v1.0 → v1.1 → ...
    cur = get_active_version() or db.query_one("SELECT * FROM rsi_versions ORDER BY id DESC")
    ver_num = 1.0
    if cur:
        try:
            ver_num = float(cur["version"][1:]) + 0.1
        except ValueError:
            ver_num = 1.1
    new_ver = f"v{ver_num:.1f}"
    parent = cur["version"] if cur else ""

    before_metrics = _metrics_snapshot()
    changes = [{k: p.get(k) for k in ("scenario", "root_cause", "evidence", "after", "call_id",
                                      "dimension", "symptom", "fix_suggestion", "fix_target",
                                      "confidence", "tech_evidence")} for p in patches]
    dims = sorted({p.get("dimension", "") for p in patches if p.get("dimension")})
    rej_note = (f"；硬校验拦截 {len(rejected)} 条不合格诊断（无数字证据/越出白名单/层面不合规）"
                if rejected else "")
    db.execute(
        "INSERT INTO rsi_versions(version,parent_version,metrics_json,changes_json,summary,status) "
        "VALUES(?,?,?,?,?, 'inactive')",
        (new_ver, parent, json.dumps({"baseline": before_metrics, "system_snapshot": sys_snap}, ensure_ascii=False),
         json.dumps(changes, ensure_ascii=False),
         f"基于 {len(failures)} 通失败会话，输出 {len(changes)} 条技术诊断（含话术补丁 "
         f"{sum(1 for p in patches if p.get('after'))} 条，均为技术审查产物，待审核）{rej_note}。"
         "涉及层面：" + "、".join(dims)))
    await broadcast_monitor("-", {"type": "rsi_new_version", "version": new_ver})
    # 改01：补齐前端 runRSI() 读取但原后端未返回的字段（原本 alert 显示 undefined）
    # 本版为「每次新建版本」语义：accumulated=False、duplicates=0、added=本轮诊断条数
    return {"ok": True, "version": new_ver, "patches": changes, "rejected": rejected,
            "added": len(changes), "accumulated": False, "duplicates": 0}


def _metrics_snapshot() -> dict:
    """RSI 版本对比用的指标快照：与 /api/metrics 共用同一套口径（11/11）。"""
    m = api_metrics()
    return {"p50_ms": m["p50_ms"], "p95_ms": m["p95_ms"],
            "closed_loop_rate": m["closed_loop_rate"], "compliance_rate": m["compliance_rate"],
            "avg_turns": m["avg_turns"], "valid_calls": m["valid_calls"]}


@app.post("/api/rsi/versions/{version_id}/activate")
async def api_rsi_activate(version_id: int):
    """审核通过 → 激活新版本（旧版本自动降为 inactive）。"""
    v = db.query_one("SELECT * FROM rsi_versions WHERE id=?", (version_id,))
    if not v:
        raise HTTPException(404, "版本不存在")
    db.execute("UPDATE rsi_versions SET status='inactive' WHERE status='active'")
    db.execute("UPDATE rsi_versions SET status='active' WHERE id=?", (version_id,))
    return {"ok": True, "active": v["version"]}


@app.post("/api/rsi/versions/{version_id}/rollback")
async def api_rsi_rollback(version_id: int):
    """安全回滚：激活 parent 版本。"""
    v = db.query_one("SELECT * FROM rsi_versions WHERE id=?", (version_id,))
    if not v or not v["parent_version"]:
        raise HTTPException(400, "无父版本可回滚")
    parent = db.query_one("SELECT id FROM rsi_versions WHERE version=?", (v["parent_version"],))
    if not parent:
        raise HTTPException(404, "父版本不存在")
    db.execute("UPDATE rsi_versions SET status='inactive' WHERE status='active'")
    db.execute("UPDATE rsi_versions SET status='active' WHERE id=?", (parent["id"],))
    db.execute("UPDATE rsi_versions SET status='rolled_back' WHERE id=?", (version_id,))
    return {"ok": True, "active": v["parent_version"]}


# ============ 改07：人工删除（误触/重复版本、误采纳诊断方向） ============
@app.post("/api/rsi/versions/{version_id}/delete")
async def api_rsi_delete_version(version_id: int):
    """删除一个误触/重复的版本（例如没勾选上又多生成了一个 v1.1）。

    - 只动 rsi_versions 一张表，与「重置」一致：不碰通话 / 工单 / 知识库。
    - 当前生效版本不允许删除（否则系统会没有 active 版本），需先激活别的版本。
    - 删除不可恢复；前端已做二次确认并显示版本号与诊断数。
    """
    v = db.query_one("SELECT * FROM rsi_versions WHERE id=?", (version_id,))
    if not v:
        raise HTTPException(404, "版本不存在")
    if v["status"] == "active":
        return JSONResponse({"ok": False,
                             "msg": "当前生效版本不能删除，请先激活其他版本或回滚后再删"},
                            status_code=200)
    changes = json.loads(v["changes_json"] or "[]")
    db.execute("DELETE FROM rsi_versions WHERE id=?", (version_id,))
    return {"ok": True, "deleted": v["version"], "version_id": version_id,
            "diagnostics": len(changes)}


@app.post("/api/rsi/versions/{version_id}/change/{index}/delete")
async def api_rsi_delete_change(version_id: int, index: int):
    """从某版本里彻底删除一条诊断方向（误触采纳 / 重复条目）。

    删除后该条从 changes_json 移除，累计诊断、已采纳、工程师补丁生成都不再包含它。
    index 是 changes 数组下标；前端每次删除后都会 reload 重算下标。
    """
    v = db.query_one("SELECT * FROM rsi_versions WHERE id=?", (version_id,))
    if not v:
        raise HTTPException(404, "版本不存在")
    changes = json.loads(v["changes_json"] or "[]")
    if index < 0 or index >= len(changes):
        raise HTTPException(400, "诊断序号不存在（可能已被删除，请刷新后重试）")
    removed = changes.pop(index)
    db.execute("UPDATE rsi_versions SET changes_json=? WHERE id=?",
               (json.dumps(changes, ensure_ascii=False), version_id))
    return {"ok": True, "removed_scenario": removed.get("scenario") or "",
            "remaining": len(changes)}


@app.put("/api/rsi/versions/{version_id}/patch")
async def api_rsi_edit_patch(version_id: int, body: dict):
    """人工修改补丁内容（index, after）。"""
    v = db.query_one("SELECT * FROM rsi_versions WHERE id=?", (version_id,))
    if not v:
        raise HTTPException(404, "版本不存在")
    changes = json.loads(v["changes_json"] or "[]")
    idx = body.get("index", -1)
    if 0 <= idx < len(changes):
        changes[idx]["after"] = body.get("after", changes[idx].get("after", ""))
        changes[idx]["manual_edited"] = True
    db.execute("UPDATE rsi_versions SET changes_json=? WHERE id=?",
               (json.dumps(changes, ensure_ascii=False), version_id))
    return {"ok": True}


@app.put("/api/rsi/versions/{version_id}/select")
async def api_rsi_select(version_id: int, body: dict):
    """人工勾选优化方向（11/12）：selected=勾中的诊断下标列表，其余置为未勾选。

    勾选结果持久化在 changes_json 每条的 selected 字段里，供后续工程师 Agent
    只对被勾选的诊断生成补丁（工程师模块预留，模型待接 Qwen）。
    """
    v = db.query_one("SELECT * FROM rsi_versions WHERE id=?", (version_id,))
    if not v:
        raise HTTPException(404, "版本不存在")
    changes = json.loads(v["changes_json"] or "[]")
    picked = {i for i in (body.get("selected") or []) if isinstance(i, int) and 0 <= i < len(changes)}
    for i in range(len(changes)):
        changes[i]["selected"] = i in picked
    db.execute("UPDATE rsi_versions SET changes_json=? WHERE id=?",
               (json.dumps(changes, ensure_ascii=False), version_id))
    return {"ok": True, "selected": sorted(picked)}


@app.put("/api/rsi/versions/{version_id}/manual-test")
async def api_rsi_manual_test(version_id: int, body: dict):
    """保存某版本的人工实测记录（对比窗口用）：通数/成功数/备注。"""
    v = db.query_one("SELECT * FROM rsi_versions WHERE id=?", (version_id,))
    if not v:
        raise HTTPException(404, "版本不存在")
    record = {"calls": int(body.get("calls") or 0),
              "success": int(body.get("success") or 0),
              "notes": str(body.get("notes") or ""),
              "tester": str(body.get("tester") or ""),
              "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    db.execute("UPDATE rsi_versions SET manual_test_json=? WHERE id=?",
               (json.dumps(record, ensure_ascii=False), version_id))
    return {"ok": True, "record": record}


def _version_real_stats(version_id: int) -> dict:
    """聚合某版本下真实通话的实测数据（calls.version_id 关联）。"""
    calls = db.query("SELECT call_id, closed_loop, status FROM calls WHERE version_id=?", (version_id,))
    ids = [c["call_id"] for c in calls]
    n = len(ids)
    closed = sum(1 for c in calls if c["closed_loop"])
    turn_map = {}
    rates = []
    e2es = []
    if ids:
        ph = ",".join("?" * len(ids))
        turn_map = {r["call_id"]: r["n"] for r in db.query(
            f"SELECT call_id, COUNT(*) AS n FROM call_turns WHERE role='merchant' "
            f"AND call_id IN ({ph}) GROUP BY call_id", tuple(ids))}
        rates = [t["compliance_rate"] for t in db.query(
            f"SELECT compliance_rate FROM tickets WHERE call_id IN ({ph})", tuple(ids))
            if t["compliance_rate"] is not None]
        e2es = [t["e2e_ms"] for t in db.query(
            f"SELECT e2e_ms FROM call_turns WHERE role='agent' AND e2e_ms>0 "
            f"AND call_id IN ({ph})", tuple(ids))]
    return {
        "calls": n,
        "closed": closed,
        "closed_rate": round(closed / n * 100, 1) if n else None,
        "avg_turns": round(statistics.mean([turn_map.get(cid, 0) for cid in ids]), 1) if ids else None,
        "avg_compliance": round(sum(rates) / len(rates) * 100, 1) if rates else None,
        "p50_e2e_ms": (sorted(e2es)[max(0, math.ceil(0.5 * len(e2es)) - 1)] if e2es else None),
        "e2e_samples": len(e2es),
    }


@app.get("/api/rsi/compare")
def api_rsi_compare(a: int, b: int):
    """版本对比窗口数据（11/12）：A/B 两版的指标快照 + 真实通话聚合 + 人工实测记录。"""
    out = {}
    for key, vid in (("a", a), ("b", b)):
        v = db.query_one("SELECT * FROM rsi_versions WHERE id=?", (vid,))
        if not v:
            raise HTTPException(404, f"版本{vid}不存在")
        try:
            metrics = json.loads(v["metrics_json"] or "{}").get("baseline") or {}
        except Exception:
            metrics = {}
        try:
            manual = json.loads(v.get("manual_test_json") or "{}")
        except Exception:
            manual = {}
        out[key] = {"id": v["id"], "version": v["version"], "status": v["status"],
                    "summary": v["summary"], "metrics": metrics,
                    "real": _version_real_stats(vid), "manual": manual}
    return out


# ---------------- REST：工程师 Agent（11/12 骨架，模型待切 Qwen） ----------------

@app.post("/api/rsi/versions/{version_id}/engineer")
async def api_engineer_run(version_id: int):
    """工程师出补丁：只吃该版本下被勾选「采纳」的诊断，生成补丁提案（不落盘）。"""
    import engineer
    v = db.query_one("SELECT * FROM rsi_versions WHERE id=?", (version_id,))
    if not v:
        raise HTTPException(404, "版本不存在")
    try:
        changes = json.loads(v["changes_json"] or "[]")
    except Exception:
        changes = []
    picked = [c for c in changes if c.get("selected")]
    if not picked:
        return JSONResponse({"ok": False,
                             "msg": "该版本没有勾选任何诊断。先在诊断卡上勾选「采纳此优化方向」，再让工程师出补丁。"},
                            status_code=200)
    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None, lambda: engineer.run_engineer(picked))
    except Exception as e:
        result = {"ok": False, "msg": f"工程师调用失败: {str(e)[:150]}"}
    record = {"status": "proposed" if result.get("ok") else "failed",
              "patches": result.get("patches", []), "rejected": result.get("rejected", []),
              "msg": result.get("msg", ""), "model": result.get("model", ""),
              "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    db.execute("UPDATE rsi_versions SET engineer_json=? WHERE id=?",
               (json.dumps(record, ensure_ascii=False), version_id))
    return {"ok": result.get("ok", False), "record": record}


@app.post("/api/rsi/versions/{version_id}/engineer/apply")
async def api_engineer_apply(version_id: int):
    """人工批准 → 应用补丁（备份→应用→语法+冒烟→失败自动回滚）。需重启服务生效。"""
    import engineer
    v = db.query_one("SELECT * FROM rsi_versions WHERE id=?", (version_id,))
    if not v:
        raise HTTPException(404, "版本不存在")
    try:
        rec = json.loads(v.get("engineer_json") or "{}")
    except Exception:
        rec = {}
    if rec.get("status") not in ("proposed", "failed"):
        return JSONResponse({"ok": False, "msg": "没有待应用的补丁提案"}, status_code=200)
    result = await asyncio.get_event_loop().run_in_executor(
        None, lambda: engineer.apply_patches(rec.get("patches") or []))
    rec["status"] = "applied" if result.get("ok") else "failed"
    rec["apply_log"] = result.get("log", [])
    rec["backup_dir"] = result.get("backup_dir", "")
    # 11/21：知识补丁的 chunk id 落进版本记录，撤销时按 id 精确删除（含 FTS 索引）
    rec["knowledge_ids"] = result.get("knowledge_ids", [])
    rec["rolled_back"] = result.get("rolled_back", False)
    rec["msg"] = result.get("msg", "")
    rec["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    db.execute("UPDATE rsi_versions SET engineer_json=? WHERE id=?",
               (json.dumps(rec, ensure_ascii=False), version_id))
    await broadcast_monitor("-", {"type": "engineer_applied", "version": v["version"],
                                  "ok": result.get("ok", False)})
    return {"ok": result.get("ok", False), "record": rec}


@app.post("/api/rsi/versions/{version_id}/engineer/rollback")
async def api_engineer_rollback(version_id: int):
    """一键撤销：还原文件备份 + 删除本版本写入的检索知识（11/21 加知识侧）。"""
    import engineer
    v = db.query_one("SELECT * FROM rsi_versions WHERE id=?", (version_id,))
    if not v:
        raise HTTPException(404, "版本不存在")
    try:
        rec = json.loads(v.get("engineer_json") or "{}")
    except Exception:
        rec = {}
    backup_dir = rec.get("backup_dir") or ""
    knowledge_ids = rec.get("knowledge_ids") or []
    if not backup_dir and not knowledge_ids:
        return JSONResponse({"ok": False, "msg": "该版本没有可撤销的补丁备份"}, status_code=200)
    result = await asyncio.get_event_loop().run_in_executor(
        None, lambda: engineer.rollback_backup(backup_dir, knowledge_ids))
    if result.get("ok"):
        rec["status"] = "rolled_back"
        rec["knowledge_ids"] = []
        rec["msg"] = result.get("msg", "")
        rec["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        db.execute("UPDATE rsi_versions SET engineer_json=? WHERE id=?",
                   (json.dumps(rec, ensure_ascii=False), version_id))
    return result


# ---------------- REST：数据一键导入 ----------------

@app.post("/api/import")
async def api_import(file: UploadFile = File(None)):
    """比赛现场新数据一键导入（双写：结构化 + 知识库索引）。"""
    tmp_path = os.path.join(os.path.dirname(db.DB_PATH), f"upload_{uuid.uuid4().hex[:8]}.xlsx")
    os.makedirs(os.path.dirname(tmp_path), exist_ok=True)
    if file:
        with open(tmp_path, "wb") as f:
            f.write(await file.read())
        import_data.XLSX_MAIN = tmp_path  # 当作场景库文件替换导入
        n = import_data.import_scenarios(clear=False)
        return {"ok": True, "scenarios_imported": n, "note": "已按场景库格式解析并双写知识库"}
    # 无文件：重跑默认导入
    import_data.import_scenarios(clear=True)
    import_data.import_qa(clear=True)
    return {"ok": True, "note": "已重导组委会默认数据"}


# ---------------- REST：新公司信息导入 / 公司信息管理（秦 11/11 新增） ----------------
# 说明：6 个新接口只服务 07 页「新公司信息导入 / 公司信息管理」两张卡，
#       原有 /api/import（场景话术库导入）及其余接口一字未改。
#       统一返回 {ok:false, error} 而不是抛 HTTP 异常，前端按 r.ok 判断。

@app.post("/api/companies/preview")
async def api_companies_preview(payload: dict):
    """多格式解析预览：只解析不写库。入参 {filename, content_b64, text}。"""
    try:
        data = None
        b64 = (payload or {}).get("content_b64") or ""
        if b64:
            data = base64.b64decode(b64)
        result = company_import.parse_input(
            filename=(payload or {}).get("filename") or "",
            data=data, text=(payload or {}).get("text") or "")
        return {"ok": True, "rows": result["rows"], "meta": result["meta"]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/companies/commit")
async def api_companies_commit(payload: dict):
    """两阶段提交：decisions=null 只查重返回冲突；带决策才写库。"""
    try:
        result = import_data.commit_companies(
            (payload or {}).get("rows") or [], (payload or {}).get("decisions"))
        return {"ok": True, **result}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/api/companies/backups")
async def api_companies_backups():
    """可恢复存档列表（含 kind、知识库条数）。异常时返回空列表，前端不会崩。"""
    try:
        return {"ok": True, "items": import_data.list_history_backups()}
    except Exception as e:
        return {"ok": False, "items": [], "error": str(e)}


@app.post("/api/companies/backups/restore")
async def api_companies_backups_restore(payload: dict):
    """一键恢复历史 / 公司及全部信息。"""
    try:
        result = import_data.restore_history_backup((payload or {}).get("key") or "")
        return {"ok": True, **result}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/api/companies/list")
async def api_companies_list(keyword: str = ""):
    """公司管理表格数据源（支持关键词搜索）。"""
    try:
        return {"ok": True, "items": import_data.list_companies_for_table(keyword)}
    except Exception as e:
        return {"ok": False, "items": [], "error": str(e)}


@app.post("/api/companies/delete")
async def api_companies_delete(payload: dict):
    """删除公司全部信息（后端再次校验公司全称，不一致直接拒绝）。"""
    try:
        result = import_data.delete_company(
            (payload or {}).get("id"), (payload or {}).get("confirm_name") or "")
        return {"ok": True, **result}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ---------------- WebSocket：商家端实时语音 ----------------

@app.websocket("/ws/call/{call_id}")
async def ws_call(ws: WebSocket, call_id: str):
    """商家接听端音频通道。
    客户端→服务端：binary=16k/16bit/单声道PCM(约200ms/包)；text=JSON控制({"action":"text","text":...}文本兜底)
    服务端→客户端：binary=WAV音频帧；text=JSON事件(subtitle/partial/agent_reply/状态等)
    """
    await ws.accept()
    dlog(f"WS-ACCEPT {call_id} ← WebSocket握手完成")
    call = db.query_one(
        "SELECT c.*, m.company_name, m.shop_name, m.contact_name, s.level2_category, s.close_method, "
        "s.script_text FROM calls c LEFT JOIN merchants m ON m.id=c.merchant_id "
        "LEFT JOIN scenarios s ON s.id=c.scenario_id WHERE c.call_id=?", (call_id,))
    if not call:
        await ws.send_text(json.dumps({"type": "error", "msg": "通话不存在"}))
        await ws.close()
        return

    ver = get_active_version()
    agent = CallAgent(call_id, dict(call), dict(call) if call["level2_category"] else None,
                      version_note=version_note_text(ver))
    loop = asyncio.get_running_loop()
    session = {"agent": agent, "ws": ws, "tts_count": 0, "interrupted": False,
               "asr": None, "merchant_ws_busy": False,
               # 【11/21 抗双讲】默认开启：只认第一说话人（商家）的声音，
               # 旁人人声零上屏/零落库/零触发；前端可用开关实时关掉做对比演示。
               "anti_doubletalk": True, "speaker_lock": None,
               "t_final": None, "t_audio_first": None, "t_llm_first": None,
               "t_tts_first": None, "spec_hit": False,
               "llm_first_ms": 0, "tts_first_ms": 0, "turn_no": 0,
               "reply_epoch": 0,
               # 11/12：按 WAV 头推算的前端播放队列排空时刻（单调时钟）与在途音频帧数。
               # 纯内存计数，只用于收尾时判断「最后一句是否真的播完」，不写库。
               "play_end_at": 0.0, "audio_pending": 0,
               # 11/12：结束语后「最后回应窗口」状态（纯内存，只决定挂断时机，不写库）
               # protecting：结束语播报中暂时关闭能量VAD抢话，防止把自己的回声
               #             当成商家插话而掐掉「…再见」；只在结束轮生效。
               "closing": {"active": False, "cancel": False, "token": 0,
                           "ended": False, "protecting": False},
               # WebRTC 媒体会话（浏览器发起 rtc_offer 时创建；None=WS PCM 链路）
               "rtc": None,
               # 保活自动收尾协程的强引用；完成后自动移除，避免一通电话内累积
               "closing_tasks": set()}

    # 【11/22 无声根因修复】能量VAD抢话判定门：把窗口/计数/冷却绑到回合边界。
    # 设计见 backend/barge_gate.py 顶部注释；阈值 550 / 连续 3 块 / 守护 0.25s 不变。
    barge_gate = BargeGate()

    # 投机预生成（罗 11/12）：利用用户说话期间的 partial 文本，在后台提前跑完 LLM + 首片段 TTS
    spec = SpeculativeEngine(agent, tts, loop, log=dlog)
    session["spec"] = spec

    # 句队列：(epoch, 文本, 是否补字幕)；None = 结束哨兵
    # 音频队列：(epoch, WAV分片)；epoch 用于丢弃被打断的上一轮残留
    sentence_queue: asyncio.Queue = asyncio.Queue()
    audio_out: asyncio.Queue = asyncio.Queue()

    def epoch_now() -> int:
        return session["reply_epoch"]

    def drop_stale() -> None:
        """商家插话/新一轮开始：上一轮还没播的片段与音频一律作废。"""
        for q in (sentence_queue, audio_out):
            while not q.empty():
                item = q.get_nowait()
                if item is None:          # 结束哨兵不能丢
                    q.put_nowait(None)
                    break
        # 【WebRTC·11/19】同步清空下行轨在途帧，与 WS 链路 stopPlayback 语义对齐
        if session.get("rtc"):
            session["rtc"].clear()

    async def audio_sender():
        last_audio_epoch = None
        while True:
            item = await audio_out.get()
            if item is None:
                return
            epoch, chunk = item
            session["audio_pending"] += 1
            try:
                if epoch != epoch_now():     # 上一轮残留，直接丢弃
                    continue
                if session.get("t_audio_first") is None:
                    session["t_audio_first"] = time.perf_counter()
                    # 【11/20 时延埋点】服务端权威口径：本轮 final → 首个音频块推送。
                    # 口径即「LLM 首句 + TTS 首块」的服务端段（SPEC 命中≈0ms 属实），
                    # 浏览器抖动缓冲/播放起播延迟不在此内。供测试与看板直接消费。
                    _tf = session.get("t_final")
                    if _tf:
                        try:
                            await send_json({"type": "tts_first_audio",
                                             "turn": session.get("turn_no"),
                                             "final_to_audio_ms": round(
                                                 (session["t_audio_first"] - _tf) * 1000)})
                        except Exception:
                            pass
                # 【WebRTC·11/19】RTC 会话在线时音频进下行轨（SRTP/Opus，浏览器抖动缓冲）；
                # 否则走 WS 二进制 WAV 旧链路。两条链路共用同一套播放时长记账。
                rtc = session.get("rtc")
                # 【11/20 无声修复】轨已死（浏览器关闭/ICE failed/disconnected）时
                # 及时释放并切回 WS：否则 push_wav 静默返回，音频进黑洞，
                # 现场表现「说一句话后 AI 永久没声音」。
                if rtc is not None and not rtc.is_healthy():
                    dlog(f"RTC-DEAD {call_id} ← 下行轨失活，自动切回 WS PCM；"
                         f"stats={rtc.stats()}")
                    session["rtc"] = None
                    session["rtc_epoch"] = None   # 交接标记一并失效（新协商时重新记录）
                    dead = rtc
                    rtc = None
                    try:
                        await dead.close()
                    except Exception:
                        pass
                # 【11/21 开场白无声修复】ICE 尚未连通（new/connecting）时，
                # 推进轨的 RTP 帧会被传输层直接丢弃——开场白整句消失。
                # 此时本块音频先走 WS 兜底（浏览器在 rtcConnected 前也接收 WS 音频），
                # 轨保留不释放，连通后自动恢复 RTC 链路。
                if rtc is not None and not rtc.is_connected():
                    rtc = None
                # 【11/21 防截断·按轮切换】上午版在浏览器连通瞬间清掉 WS 播放队列
                # 防双路叠声，实测把开场白截断了（第一句没播完就被打断）。
                # 改为「按轮切换」：连接建立瞬间**正在播的这一轮**（epoch 相同）
                # 继续走 WS 播完，从**下一轮回复**开始才切 RTC——WS 与 RTC 的
                # 交接只发生在轮次边界，既不截断也不叠声。
                if rtc is not None:
                    _sw = session.get("rtc_epoch")
                    if _sw is None:
                        session["rtc_epoch"] = epoch   # 记录交接轮：本轮继续 WS
                        rtc = None
                    elif _sw == epoch:
                        rtc = None                     # 同一轮：继续 WS 播完
                    # _sw != epoch → 之后的轮次走 RTC
                if rtc is not None:
                    sent = rtc.push_wav(chunk) > 0
                else:
                    sent = False
                    try:
                        await ws.send_bytes(chunk)
                        sent = True
                    except Exception:
                        pass
                if sent:
                    # 【11/21 四轮·抗双讲根因B】AI 开始出声：标记播报时钟。
                    # ASR 层据此把「AI 播报期（含尾音）到达的 definite」当回声/
                    # 旁人抢先丢弃——接通即放播音时绝不把播音员锁成目标。
                    _asr_m = session.get("asr")
                    if _asr_m is not None:
                        try:
                            _asr_m.mark_ai_speaking()
                        except Exception:
                            pass
                    # 前端 audioQ/RTC 播放逐帧顺序：本帧排在前面所有帧播完之后。
                    # 累计 WAV 真实时长，收尾时才知道「最后一句」何时真正说完。
                    # epoch 变化 = 新一轮：上一轮的预计播完时刻作废，从这一帧重新起算。
                    now = time.monotonic()
                    if last_audio_epoch != epoch:
                        session["play_end_at"] = now
                        last_audio_epoch = epoch
                    session["play_end_at"] = max(session["play_end_at"], now) \
                        + _wav_seconds(chunk)
            finally:
                session["audio_pending"] -= 1

    async def _emit_stream(epoch: int, sentence: str) -> None:
        """流式合成一句并按分片顺序下发；首片到达即发。失败降级整句。"""
        dlog(f"TTS-EMIT epoch={epoch} chars={len(sentence)}")
        cached = tts.get_cached(sentence)
        if cached is not None:
            audio_out.put_nowait((epoch, cached))
            return

        def on_chunk(b: bytes):
            if epoch == epoch_now():
                loop.call_soon_threadsafe(audio_out.put_nowait, (epoch, b))

        try:
            await asyncio.to_thread(tts.synthesize_stream, sentence, on_chunk)
        except Exception as e:
            dlog(f"TTS-STREAM-FAIL {type(e).__name__}: {str(e)[:100]}")
            try:
                audio = await asyncio.to_thread(tts.synthesize, sentence)
                if epoch == epoch_now():
                    audio_out.put_nowait((epoch, audio))
            except Exception as e2:
                dlog(f"TTS-FAIL {type(e2).__name__}: {str(e2)[:100]}")

    async def sentence_worker():
        while True:
            item = await sentence_queue.get()
            if item is None:
                return
            epoch, sentence, show_text = item
            if epoch != epoch_now():
                continue
            # 【连句修复·罗 11/12】同一轮的多个片段攒批成一次 TTS 请求：
            # 原来每个片段都要重新等一次 TTS 首包(0.6~1.2s)，听起来就是句中间的大空隙。
            batch = [sentence]
            deferred = []
            await asyncio.sleep(0.12)
            while True:
                try:
                    nxt = sentence_queue.get_nowait()
                except Exception:
                    break
                if nxt is None:
                    deferred.append(None)
                    break
                e2, t2, s2 = nxt
                if e2 == epoch:
                    batch.append(t2)
                    show_text = show_text or s2
                else:
                    deferred.append(nxt)
            for d in deferred:
                sentence_queue.put_nowait(d)
            full = "".join(batch)
            display = full if show_text else ""
            session["tts_count"] += 1
            if not tts:
                if display:
                    await send_json({"type": "agent_reply", "text": display,
                                     "tts": False, "epoch": epoch})
                session["tts_count"] -= 1
                continue
            try:
                if display:
                    await send_json({"type": "agent_reply", "text": display, "tts": True,
                                     "epoch": epoch, "state": agent.state,
                                     "done_elements": sorted(agent.done_elements)})
                await _emit_stream(epoch, full)
            except Exception as e:
                if display:
                    await send_json({"type": "agent_reply", "text": display, "tts": False,
                                     "epoch": epoch, "tts_error": str(e)[:100]})
            finally:
                session["tts_count"] -= 1

    sentence_worker_task = asyncio.create_task(sentence_worker())
    audio_sender_task = asyncio.create_task(audio_sender())
    SESSIONS[call_id] = session

    async def send_json(obj):
        try:
            await ws.send_text(json.dumps(obj, ensure_ascii=False))
        except Exception:
            pass

    # ---------------- 11/12：结束语后 5 秒「最后回应窗口」 ----------------
    # 只决定「什么时候自动挂断」：AI 说完最后一句结束语后给商家 5 秒回应时间，
    # 期间识别到商家语音就取消挂断并让 AI 继续回答；全程静默才按原路径收尾。
    # finalize_call / park_call 的业务口径、指标、工单、字幕、语音链路一律不动。
    def _looks_like_self_echo(text: str) -> bool:
        """窗口内：排除「AI 刚播报的结束语被麦克风回收」的 AEC 残留。

        当前基线只有浏览器 echoCancellation，没有服务端回声过滤；残留回声若被
        ASR 识别成文字并当成商家开口，自动挂断会被一直取消。
        只在结束语窗口内生效，任何其它轮次的识别结果不受影响。
        """
        a = re.sub(r"[^\u4e00-\u9fff0-9a-zA-Z]", "", text or "")
        if len(a) < 4:
            return False
        last_ai = next((h["content"] for h in reversed(agent.history)
                        if h["role"] == "assistant"), "")
        b = re.sub(r"[^\u4e00-\u9fff0-9a-zA-Z]", "", last_ai or "")
        if not b:
            return False
        if a in b or b in a:
            return True
        # ASR 往往只回收到结束语里的一小段，或前后带语气词 / 错字；
        # 任意连续 6 字命中即判回声，避免整句比对漏判后触发新一轮停播。
        for i in range(max(0, len(a) - 5)):
            if a[i:i + 6] in b:
                return True
        sa, sb = set(a), set(b)
        return len(sa & sb) / max(1, len(sa | sb)) >= 0.8

    def is_closing_echo(text: str) -> bool:
        """窗口内是否只是 AI 结束语的回声（唯一判据，ASR 两路共用）。"""
        return bool(session["closing"]["active"] and text and _looks_like_self_echo(text))

    def note_merchant_voice(text: str = "") -> bool:
        """识别到商家语音：取消自动挂断；若 AI 已处于结束态则恢复为可继续答疑。

        返回 True 表示本次刚把 AI 从「已结束」恢复 —— 这一轮不能复用 END 状态下
        投机预生成的「再见」缓存，否则会答非所问并立刻又收尾。
        正常轮次调用不会做任何事（active=False 且 finished=False）。
        text 为空表示文字兜底链路（人手输入，不存在回声，故不做回声判定）。
        """
        c = session["closing"]
        if c["ended"] or is_closing_echo(text):
            return False
        # 【告别语不重开】结束语之后商家回一句「再见再见 / 拜拜 / 挂了」是**接受收尾**，
        # 不是还有疑问。旧版无条件恢复答疑 → 模型把两个字的告别判成"含糊"，
        # 反问"您看是哪一部分还有疑虑？"，商家的感受就是"我都说再见了它还在黏着我"。
        if (agent.finished or closing_spoken(agent)) and is_farewell_after_closing(text):
            dlog(f"CLOSING-BYE {call_id} 商家告别「{(text or '')[:12]}」→ 保持结束态，直接收线")
            return False
        if c["active"]:
            c["cancel"] = True
        if agent.finished:
            if agent.followup_after_end():
                spec.clear()               # 作废结束态下预生成的候选片段
                dlog(f"CLOSING-CANCEL {call_id} ← 结束语后识别到商家新问题，AI 继续答疑（不回退环节）")
                return True
            dlog(f"CLOSING-BYE {call_id} 通话未走完五环节，结束态不恢复")
            return False
        return False

    def audio_inflight() -> bool:
        """本轮是否还有句子 / 音频帧未下发完（① ② 共用的唯一判据）。"""
        return (not sentence_queue.empty() or session["tts_count"] > 0
                or not audio_out.empty() or session["audio_pending"] > 0)

    async def wait_for_closing_silence(buffer_sec: float) -> bool:
        """等结束语播完 → 再观察 5 秒。True=静默满 5 秒可自动挂断，False=继续通话。"""
        c = session["closing"]
        c["token"] += 1
        token = c["token"]
        c["active"] = True
        c["cancel"] = False

        def aborted() -> bool:
            """商家已开口 / 通话已终结 / 新窗口接管 → 本次不再自动挂断。"""
            return c["cancel"] or c["ended"] or c["token"] != token

        try:
            # ① 等句子与音频帧全部下发完。tts_count 归零只代表「合成结束」，
            #    audio_out 里可能还压着结束语分片；连续 3 次确认，避开
            #    sentence_worker 0.12s 攒批与跨线程投递的空窗。
            # ④ 排空安全阀：整段最多等 CLOSING_DRAIN_MAX_SEC 秒，超时视为播完。
            stable = 0
            drain_deadline = time.monotonic() + CLOSING_DRAIN_MAX_SEC
            for _ in range(600):        # 最多等 60 秒
                if aborted():
                    return False
                if time.monotonic() > drain_deadline:
                    break
                if audio_inflight():
                    stable = 0
                else:
                    stable += 1
                    if stable >= 3:
                        break
                await asyncio.sleep(0.1)
            if aborted():
                return False
            # ② 等最后一帧真正播完再计时：前端逐帧顺序播放，按 WAV 头累计的
            #    排空时刻才是「说完」；固定 2.5s 缓冲扛不住 50~60 字结束语。
            #    纯文字链路无音频帧，自动退回 buffer_sec 固定缓冲。
            started = time.monotonic()
            while True:
                if aborted():
                    return False
                # 流式 TTS 分片可能迟到：只要在途就继续等、每轮重算 target，
                # 绝不把「播完时刻」锁死在旧值上。
                if audio_inflight():
                    await asyncio.sleep(0.1)
                    continue
                target = max(started + buffer_sec,
                             session["play_end_at"] + PLAYBACK_TAIL_SEC)
                left = target - time.monotonic()
                if left <= 0:
                    break
                await asyncio.sleep(min(0.1, left))
            if aborted():
                return False
            # ③ 从「说完」起算 5 秒，期间任一次商家语音都取消挂断。
            deadline = time.monotonic() + CLOSING_SILENCE_SEC
            while time.monotonic() < deadline:
                if aborted():
                    return False
                await asyncio.sleep(0.1)
            if aborted():
                return False
            # 窗口内主动挂断（WS 或 REST）后通话已是终态，不能再按自动收尾写 completed
            row = db.query_one("SELECT status FROM calls WHERE call_id=?", (call_id,))
            if row and row["status"] != "in_progress":
                return False
            c["ended"] = True
            return True
        finally:
            if c["token"] == token:
                c["active"] = False
                c["protecting"] = False   # 恢复正常的抢话/打断能力

    async def auto_finalize_after_silence(buffer_sec: float) -> None:
        """后台执行自动收尾：不能阻塞 WS 主循环，否则窗口内点挂断要等窗口跑完。"""
        if await wait_for_closing_silence(buffer_sec):
            await finalize_call(call_id, session)
            await send_json({"type": "call_finished"})
            await broadcast_monitor(call_id, {"type": "call_ended"})

    def schedule_auto_hangup(buffer_sec: float) -> None:
        # 从这一刻起整段结束语必须完整播完：期间不做能量VAD抢话检测，
        # 由 wait_for_closing_silence 的 finally 恢复。
        session["closing"]["protecting"] = True
        task = asyncio.create_task(auto_finalize_after_silence(buffer_sec))
        tasks = session["closing_tasks"]     # 强引用防回收；完成后自动移除
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    # ---------------- 13/13 单步推进：转场轮之后的自动续讲 ----------------
    # 用户规则：【身份确认】与【情况说明】绝不允许在同一回合点亮，情况说明必须
    # 「下一回合」念完才亮灯。这一轮 AI 只做确认承接，如果商家没有马上接话，
    # 靠人开口催会让进度卡在 2/5、体验也像死机 —— 所以由服务端在**本轮音频播完后**
    # 主动续讲下一环节：不伪造商家发言、不写假用户消息，商家一插话就立刻让位。
    cont_token = {"n": 0}

    async def auto_continue_job(expected_turn: int) -> None:
        cont_token["n"] += 1
        token = cont_token["n"]
        try:
            # ① 等句子与音频帧全部下发完 ② 再等最后一帧**真正播完** + 一小段间隙
            # 注意不能只看 audio_inflight()：它只代表「下发完」，前端还在逐帧播放，
            # 此时续讲会把上一句的尾巴掐掉。以 play_end_at（按 WAV 时长累计的排空时刻）为准。
            started = time.monotonic()
            stable = 0
            for _ in range(1200):           # 最多等 96 秒
                if token != cont_token["n"] or agent.finished:
                    return
                if session["turn_no"] != expected_turn:
                    return                   # 商家已经开口 → 交给正式链路
                if audio_inflight():
                    stable = 0
                    await asyncio.sleep(0.08)
                    continue
                if stable < 3:              # 连续 3 次确认，避开 TTS 攒批/投递的空窗
                    stable += 1
                    await asyncio.sleep(0.08)
                    continue
                target = max(started + AUTO_CONTINUE_GAP,
                             (session.get("play_end_at") or 0) + AUTO_CONTINUE_GAP)
                left = target - time.monotonic()
                if left <= 0:
                    break
                await asyncio.sleep(min(0.1, left))
            if (token != cont_token["n"] or agent.finished
                    or session["turn_no"] != expected_turn):
                return
            row = db.query_one("SELECT status FROM calls WHERE call_id=?", (call_id,))
            if not row or row["status"] != "in_progress":
                return
            session["reply_epoch"] += 1
            epoch = session["reply_epoch"]
            session["t_llm_first"] = None
            session["t_tts_first"] = None
            session["t_audio_first"] = None
            t0 = time.time()
            # 【11.21-18】续讲句回调同样固化 epoch：生成期间商家开口
            # （epoch 前进）时，续讲剩余句子直接丢弃，不串进正式回复。
            result = await loop.run_in_executor(
                None, lambda: agent.chat_stream(
                    "", interrupted=False, asr_confidence=1.0,
                    on_sentence=sentence_cb_for(epoch), auto=True))
            if session["reply_epoch"] != epoch:
                return                       # 生成期间商家插话，本轮作废
            latency_ms = (time.time() - t0) * 1000
            comp = check_compliance(agent.history)
            try:
                db.execute(
                    "INSERT OR IGNORE INTO call_turns(call_id,turn_no,role,text,state,compliance,latency_ms) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (call_id, result["turn"], "agent", result["ai_reply"],
                     result.get("state", ""), json.dumps(comp, ensure_ascii=False), latency_ms))
            except Exception:
                pass
            await send_json({"type": "agent_reply_meta", "state": result.get("state", ""),
                             "finished": result.get("finished", False),
                             "emotion": "neutral", "latency_ms": round(latency_ms),
                             "compliance": comp, "flow5": agent.flow_progress(),
                             "auto_continue": True})
            await broadcast_monitor(call_id, {
                "type": "turn", "role": "agent", "text": result["ai_reply"],
                "state": result.get("state", ""), "emotion": "", "latency_ms": latency_ms,
                "compliance": comp, "flow5": agent.flow_progress(), "auto_continue": True})
            if result.get("finished"):
                schedule_auto_hangup(PLAYBACK_BUFFER_AUDIO)
        except Exception as e:
            dlog(f"AUTO-CONT-FAIL {call_id} {type(e).__name__}: {str(e)[:80]}")

    def schedule_auto_continue(expected_turn: int) -> None:
        task = asyncio.create_task(auto_continue_job(expected_turn))
        tasks = session["closing_tasks"]
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    async def on_partial(text: str):
        # 注：ASR会话异常的重建已由 asr_volcano 的监督者自动处理（含音频缓冲），
        # 这里只做转发；"[ASR"开头的消息由商家端路由到系统行展示
        # 窗口内 ASR 中间结果即视为商家语音；"[ASR" 是链路状态提示、不算人声。
        # 放在投机预生成之前，保证预测基于恢复后的答疑状态而非「再见」。
        if text and not text.startswith("[ASR"):
            if is_closing_echo(text):
                return   # 结束语回声：不上临时行、不取消挂断、不预生成
            note_merchant_voice(text)
        spec.on_partial(text)          # 【延时2秒·罗 11/12】后台投机预生成
        await send_json({"type": "partial", "text": text})
        await broadcast_monitor(call_id, {"type": "partial", "role": "merchant", "text": text})

    def on_spec_hint(text: str):
        """【11/21 九轮·提速】投机预生成专用旁路：只把"像业务"的 partial 喂给
        投机引擎提前备答案。与 on_partial 严格分离——绝不 send_json 上屏、
        绝不落库、绝不 note_merchant_voice、绝不取消挂断。这样：
          · 商家说话期间 LLM+TTS 已在后台备好 → definite 命中缓存首字毫秒级；
          · 噪音 partial 由 ASR 内容判据挡在门外 → 不白跑 LLM、不污染候选池；
          · 就算喂错，最坏只是投机 miss，旁人一个字都上不了屏。
        spec.on_partial 内部自带 0.35s 节流 + ≥6字门槛 + 并发上限，安全。"""
        if not text or text.startswith("[ASR") or is_closing_echo(text):
            return
        try:
            spec.on_partial(text)
        except Exception:
            pass

    async def on_final(text: str, conf: float):
        """商家一句话说完 → 优先取用投机预生成结果 → 落库 → 收尾。（罗 11/12）"""
        # 结束语之后又开口 → 取消自动挂断并恢复答疑状态；
        # AEC 残留回声直接忽略：不落库、不回复、也不重开收尾窗口。
        if is_closing_echo(text):
            return
        # 【告别不续聊】结束语已经播完，商家回「再见再见 / 拜拜 / 挂了」= 接受收尾。
        # 只把这一行记进通话记录，AI 不再开口（否则又要重复一遍再见，或被拉回答疑），
        # 交给既有的静默挂断窗口把线路收掉。
        if (agent.finished or closing_spoken(agent)) and is_farewell_after_closing(text):
            bye_turn = agent.turn_no + 1
            db.execute(
                "INSERT OR IGNORE INTO call_turns(call_id,turn_no,role,text,asr_confidence,"
                "intent,state,emotion,compliance) VALUES(?,?,?,?,?,?,?,?,?)",
                (call_id, bye_turn, "merchant", text, conf, "BYE", "END", "neutral", "PASS"))
            await send_json({"type": "final", "text": text, "confidence": conf,
                             "turn": bye_turn})
            await broadcast_monitor(call_id, {"type": "turn", "role": "merchant",
                                              "text": text, "confidence": conf,
                                              "intent": "BYE"})
            dlog(f"CLOSING-BYE {call_id} 结束语后商家告别「{text[:12]}」→ 不再恢复答疑")
            return
        reopened = note_merchant_voice(text)
        t0 = time.time()
        # 新一轮开始：上一轮尚未播完的片段/音频全部作废，避免插话时串句
        session["reply_epoch"] += 1
        epoch = session["reply_epoch"]
        drop_stale()
        barge_gate.reset()      # 【11/22】新一轮开始：上一轮的在途/余量证据作废
        session["t_final"] = time.perf_counter()
        session["t_llm_first"] = None
        session["t_tts_first"] = None
        session["t_audio_first"] = None
        session["spec_hit"] = False
        turn_no = agent.turn_no + 1
        session["turn_no"] = turn_no

        # 【11/22 修正 D04】不要把 play_end_at 并进 was_playing：它按 WAV 时长累计，
        # 正常应答（AI 已说完）也会落在窗口内，导致每个商家轮都被误标 interrupted=1。
        # 首句抢话由 barge_suspect 负责（只在"音频还在播时检测到人声"才置位）。
        was_playing = session["tts_count"] > 0
        if was_playing:
            await send_json({"type": "interrupt_tts"})   # 客户端立刻停播
            if session.get("rtc"):                        # 【WebRTC·11/19】下行轨同步清帧
                session["rtc"].clear()
            await broadcast_monitor(call_id, {"type": "interrupt", "by": "merchant"})
        # final 消息带服务端 turn 号：浏览器回传后按 (call_id, turn_no) 精确落库，
        # 修掉上一版「最近一条 agent 轮」互相覆盖造成的假达标。
        await send_json({"type": "final", "text": text, "confidence": conf,
                         "turn": turn_no})
        await broadcast_monitor(call_id, {"type": "turn", "role": "merchant", "text": text,
                                          "confidence": conf, "interrupted": was_playing})

        # 刚从结束态恢复的这一轮不能复用 END 状态下的「再见」缓存，走正式回答链路
        hit = None if reopened else spec.take(text)
        if reopened:
            spec.clear()
        if hit:
            # 【命中】LLM + 首片段 TTS 已在用户停说前完成，直接下发缓存音频。
            # 首帧播的就是针对本轮问题的真实回复，不是填充词。
            pv = dict(hit["pv"])
            pv["user_text"] = text              # history / 落库一律用真实 definite 文本
            pv["emotion"] = classify_emotion(text)
            result = agent.commit_reply(pv)
            session["spec_hit"] = True
            for chunk in hit["audio"]:
                audio_out.put_nowait((epoch, chunk))
            frags = pv.get("fragments") or []
            if frags:
                await send_json({"type": "agent_reply", "text": frags[0], "tts": True,
                                 "epoch": epoch, "state": result["state"],
                                 "done_elements": sorted(agent.done_elements)})
            rest = "".join(frags[1:])
            if rest.strip():
                # 剩余片段合并成一次 TTS，与首片段播放并行起步，消除句中间空隙
                sentence_queue.put_nowait((epoch, rest, True))
            dlog(f"SPEC-USE {call_id} turn={turn_no} 覆盖={hit['coverage']:.2f} "
                 f"缓存分片={len(hit['audio'])} 剩余合并={len(frags) - 1}片段")
        else:
            # 【未命中】校验不通过，丢弃缓存走原链路，保证准确性优先。
            # on_sentence 用本轮固化 epoch 的闭包：生成期间商家再插话
            # （reply_epoch 前进）时，本轮后续句子在回调处直接丢弃。
            result = await loop.run_in_executor(
                None, lambda: agent.chat_stream(
                    text, interrupted=was_playing, asr_confidence=conf,
                    on_sentence=sentence_cb_for(epoch)))
        latency_ms = (time.time() - t0) * 1000

        # 【11.21-18 根治·续】生成期间商家又开了新一轮：本轮已被作废。
        # 商家轮照常落库（interrupted=1 真实留痕），但 agent 回复不落库、
        # 不发 meta / 不广播 —— 否则旧轮 meta 后到会覆盖前端新一轮的
        # 状态/五环节灯（错乱感来源之一），旧轮回复文字也会重复进字幕。
        if session["reply_epoch"] != epoch:
            dlog(f"TURN-STALE {call_id} turn={turn_no} 生成期间被新一轮取代，"
                 f"旧轮回复不下发")
            comp = check_compliance(agent.history)
            db.execute(
                "INSERT OR IGNORE INTO call_turns(call_id,turn_no,role,text,asr_confidence,intent,state,emotion,"
                "compliance,interrupted) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (call_id, result["turn"], "merchant", text, conf,
                 result.get("intent", ""), result.get("state", ""),
                 result.get("emotion", "neutral"), "PASS", 1))
            return

        # 记录商家轮
        # 【11/22】VAD 打断会把 tts_count 清零，final 到达时 was_playing 已是 False；
        # 若只看 was_playing，打断率永远落不了库（RSI 的 tech_evidence 也永远为 0）。
        barge_flag = 1 if (was_playing or session.pop("barge_pending", False)
                           or session.pop("barge_suspect", False)) else 0
        db.execute(
            "INSERT OR IGNORE INTO call_turns(call_id,turn_no,role,text,asr_confidence,intent,state,emotion,"
            "compliance,interrupted) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (call_id, result["turn"], "merchant", text, conf, result.get("intent", ""),
             result.get("state", ""), result.get("emotion", "neutral"), "PASS",
             barge_flag))
        comp = check_compliance(agent.history)
        db.execute(
            "INSERT OR IGNORE INTO call_turns(call_id,turn_no,role,text,state,compliance,latency_ms) "
            "VALUES(?,?,?,?,?,?,?)",
            (call_id, result["turn"], "agent", result["ai_reply"], result.get("state", ""),
             json.dumps(comp, ensure_ascii=False), latency_ms))
        await send_json({"type": "agent_reply_meta", "state": result.get("state", ""),
                         "finished": result.get("finished", False),
                         "emotion": result.get("emotion", "neutral"),
                         "latency_ms": round(latency_ms),
                         "compliance": comp,
                         "flow5": agent.flow_progress()})
        await broadcast_monitor(call_id, {"type": "turn", "role": "agent",
                                          "text": result["ai_reply"], "state": result.get("state", ""),
                                          "emotion": result.get("emotion", ""), "latency_ms": latency_ms,
                                          "compliance": comp,
                                          "flow5": agent.flow_progress()})
        # 【工具调用】本轮模型自己决定查了什么 → 推给运营后台（商家端不显示）
        await broadcast_tool_calls(call_id, result)
        # 11/15 真机修复②：闲聊分级实时预警 —— 与红线违规警告并列推监控页，
        # 前端据此在任务进度条插琥珀色「闲聊警告」卡并写事件流（不进红线 KPI）。
        if result.get("offtopic"):
            await broadcast_monitor(call_id, {"type": "offtopic",
                                              "offtopic": result["offtopic"]})
        # 【红线拦截】商家侧违规诉求：写进工单处理轨迹（可追溯）+ 实时推监控页
        if result.get("violation"):
            vio = result["violation"]
            try:
                orders.record_violations(
                    call_id,
                    agent.merchant.get("merchant_id") or agent.merchant.get("id"),
                    [vio])
            except Exception as e:
                dlog(f"VIOLATION-WRITE-FAIL {call_id} {type(e).__name__}")
            # B 级会同步给商家打风险标记，这里把最新画像一并推给监控页
            rk = None
            if vio.get("level") == "B":
                try:
                    rk = risk.profile(agent.merchant.get("merchant_id") or agent.merchant.get("id"))
                except Exception:
                    rk = None
                dlog(f"RISK-MARK {call_id} {vio.get('key')} → {(rk or {}).get('level')}")
            await broadcast_monitor(call_id, {"type": "violation",
                                              "violation": vio, "risk": rk,
                                              "state": result.get("state", "")})
        if result.get("finished"):
            # 播完后再给 5 秒回应窗口；放后台跑，主循环仍可随时接收挂断 / 新语音。
            schedule_auto_hangup(PLAYBACK_BUFFER_AUDIO)
        elif agent.state == AGENT_EXPLAIN_STATE and not getattr(agent, "_rejection_delivered", True):
            # 【单步推进】身份确认轮之后的一轮：情况说明还没播，商家也没接话 →
            # AI 在本轮音频播完后主动续讲（商家一插话就取消），避免卡在 2/5 空麦。
            schedule_auto_continue(turn_no)

    # Agent 句回调需要跨线程 → 投递到串行队列（合成与播放均按序）
    # 【11.21-18 根治·AI 回复重复几遍】原实现动态读 session["reply_epoch"]：
    # 商家插话 → 新一轮 on_final 把 reply_epoch+1 并 drop_stale() 清空队列，
    # 但旧一轮的 chat_stream 还在 executor 线程里继续生成（LLM 流式 1~3s），
    # 旧轮后续句子回调时读到【新】epoch → 挂新轮身份进队列，drop 白清；
    # sentence_worker 攒批把「旧轮剩余句 + 新轮句」拼成一坨下发 →
    # 实机表现即「AI 一句话里内容重复几遍、两轮文字交错错乱」。
    # 修复：回调生成时【固化】本轮 epoch；投递前校验 epoch 未前进，
    # 前进了 = 本轮已被插话作废 → 旧轮句子一律丢弃，绝不复活串轮。
    # （复现与对照见 11.21-18/_work/sim_reply_dup.py）
    def sentence_cb_for(fixed_epoch: int):
        def cb(sentence: str):
            if session["reply_epoch"] != fixed_epoch:
                return               # 旧轮作废：句子直接丢，不上屏不出声
            if session.get("t_llm_first") is None:
                session["t_llm_first"] = time.perf_counter()
            loop.call_soon_threadsafe(
                sentence_queue.put_nowait, (fixed_epoch, sentence, True))
        return cb

    def record_turns(user_text: str, result: dict, latency_ms: float) -> None:
        """文字模式/开场白模式下把双方轮次写入 call_turns（工单明细）。"""
        if user_text:
            db.execute(
                "INSERT OR IGNORE INTO call_turns(call_id,turn_no,role,text,asr_confidence,intent,state,emotion) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (call_id, result["turn"], "merchant", user_text, 1.0,
                 result.get("intent", ""), result.get("state", ""),
                 result.get("emotion", "neutral")))
        comp = check_compliance(agent.history)
        db.execute(
            "INSERT OR IGNORE INTO call_turns(call_id,turn_no,role,text,state,compliance,latency_ms) "
            "VALUES(?,?,?,?,?,?,?)",
            (call_id, result["turn"], "agent", result["ai_reply"], result.get("state", ""),
             json.dumps(comp, ensure_ascii=False), latency_ms))
        if result.get("violation"):
            try:
                orders.record_violations(
                    call_id, agent.merchant.get("merchant_id") or agent.merchant.get("id"),
                    [result["violation"]])
            except Exception:
                pass

    # 链路握手立即确认（不等ASR）——ASR在后台并行初始化，完成后补发asr_ready
    await send_json({"type": "ready", "asr": "connecting"})
    dlog(f"WS-READY {call_id} ← ready已下发")

    # 【11/22】冷却/计数/窗口统一由 barge_gate 维护（原 rms_hold 已移除）。

    async def init_asr():
        """后台初始化火山ASR：不阻塞开场白，完成/失败都补发状态。
        会话对象先挂到 session 上——音频立刻进队列缓冲，连接成功后自动续传。"""
        # 【11/21 抗双讲】ASR 层的锁定/拦截事件回传前端，让开关效果看得见；
        # 事件只更新小字提示与日志，绝不把旁人原话文本下发（零文本上屏）。
        def on_speaker_event(event: str, speaker, text: str = ""):
            if event == "locked":
                session["speaker_lock"] = speaker
                dlog(f"SPEAKER-LOCK {call_id} 目标说话人={speaker} 首句={(text or '')[:30]}")
            elif event == "blocked":
                dlog(f"SPEAKER-BLOCK {call_id} 已拦截旁人说话人={speaker}")
            try:
                asyncio.create_task(send_json({"type": "speaker_guard", "event": event,
                                               "speaker": speaker, "text": (text or "")[:40]}))
            except Exception:
                pass

        asr_sess = VolcanoASRSession(call_id, on_partial, on_final,
                                     on_speaker_event=on_speaker_event,
                                     on_spec_hint=on_spec_hint)
        # 默认开启抗双讲（前端按钮初始也是开）；relock=False：新建会话无需清参考
        asr_sess.set_anti_doubletalk(bool(session.get("anti_doubletalk", True)),
                                     relock=False)
        if not asr_sess.available:
            await send_json({"type": "asr_ready", "asr": "unavailable",
                             "msg": "火山ASR未配置，可用文字输入对话（兜底）"})
            return
        session["asr"] = asr_sess          # 先挂上：音频即刻缓冲，不丢开头
        try:
            await asr_sess.start()
            await send_json({"type": "asr_ready", "asr": "volcano"})
        except Exception as e:
            session["asr"] = None
            await send_json({"type": "asr_ready", "asr": "unavailable", "err": str(e)[:120]})

    asyncio.create_task(init_asr())

    async def process_uplink_pcm(data: bytes):
        """上行 PCM 统一入口（WebRTC·11/19 抽取）：
        WS 二进制链路与 WebRTC 解码泵共用 —— 能量VAD抢话检测 + 火山ASR喂帧，
        保证两条传输链路的打断行为、识别行为完全一致。"""
        # 能量VAD抢话检测：TTS播放期间连续人声 → 打断（双讲/抢话）。
        # 结束语播报期间临时关闭：纯能量判据分不清 AI 播报声与商家人声，
        # 误判会 stopPlayback 掐掉「…再见」；仅本窗口生效，其它轮次不变。
        # 【11/20 无声修复】加打断冷却：真机 AI 播报的声学回声会回流到麦克风，
        # 被纯能量判据误判为「商家抢话」而反复 clear 下行轨（现场 interrupted=3，
        # 一句话后 AI 越掐越短直至无声）。冷却期内不打断，且需更长持续人声。
        now_mono = time.monotonic()
        # 【11/22 无声根因修复】窗口/计数/冷却统一交给 BargeGate：
        #   · 结束语保护、打断冷却、AI 未在播报 → 窗口关闭（并清零计数）；
        #   · 新一句开始（窗口由关→开）→ 计数从零起算，且头 0.25s 不计（只可能是余音）。
        gate_open = barge_gate.update(
            now_mono, session["tts_count"] > 0,
            protecting=session["closing"]["protecting"],
            play_end_at=session.get("play_end_at") or 0.0)
        # 【11.21-13 首句打断根因修复】开场白/任何回复走流式合成：sentence_worker
        # 合成完就把 tts_count 归零，但音频块还在 audio_out 队列里、audio_sender
        # 正逐帧发送/浏览器正在播。旧版打断门只看 tts_count>0 → "AI 声音还在响、
        # 插话却打不断"。改用 play_end_at（audio_sender 按 WAV 真实时长累计的
        # 排空时刻）兜底：只要 AI 音频还没播完，就允许能量打断。
        if gate_open and len(data) >= 800:
            samples = len(data) // 2
            s = sum(abs(int.from_bytes(data[i:i + 2], "little", signed=True))
                    for i in range(0, len(data), 2)) // samples
            voice_ok = True
            _asr = session.get("asr")
            # 【11/21 十轮·手机打断修复】能量阈值 1100→700、连续 5→3 帧：
            # 手机/蓝牙耳机的麦克风增益远低于电脑麦，实测整轮 0 次 VAD-BARGE
            # ——连能量门都没过，用户只能等 AI 说完。降到手机可达范围，且
            # 3 帧（约 0.6s）持续人声即可打断。回声仍由 cooldown(2.5s)吸收。
            # 【11/22】700→550：实机首句打断时人声 rms 常在 500–650（后两句
            # 703/818 刚过 700 线），首句永远过不了能量门。手机麦/近距离说话
            # 都更低；回声由 cooldown 2.5s 与结束语 protecting 窗口吸收。
            _barge_energy = barge_gate.energy    # 550，与旧版一致（阈值收在 BargeGate 内）
            if (s > _barge_energy and _asr is not None
                    and getattr(_asr, "anti_doubletalk", False)
                    and getattr(_asr, "_ref_f0", None) is not None):
                # 打断判定与"文字拦截"彻底解耦：打断只负责让本人随时掐断 AI。
                # 音纹门用打断专用判据（八度兼容）：参考若记成半频，本人真声
                # 仍能放行打断；只有明确是另一个人（且非八度）才拒绝。
                # 旁人即使偶尔掐断 AI，其文字仍在 definite 层被音纹拦截——
                # 不上屏/不落库/不触发，故放宽打断绝不破坏抗双讲底线。
                try:
                    _f0 = frame_f0(data)
                    # 打断专用：八度兼容 + 宽松同频；提不出特征(None)一律放行
                    bmatch = (_asr.voice_match_f0_for_barge(_f0)
                              if _f0 is not None else None)
                    voice_ok = not (bmatch is False)
                    # 【11/22】不再因音纹判"旁人"而清零打断计数：
                    # 音纹参考一旦跑偏（八度误锁），本人会"既打不断、文字也被拦"，
                    # 实机已复现。打断门只认能量；旁人抢话的文字仍会在 definite 层被拦。
                except Exception:
                    voice_ok = True   # 异常绝不阻塞打断
            if s > _barge_energy:
                # 【11/22 首句打断】走到这里已经说明「AI 音频还在播」（外层 gate_open 已用
                # tts_count/play_end_at 判过），所以只要人声过阈值就先记嫌疑，
                # 不再用 audio_inflight()——它只代表"下发完"，此时往往已为 False。
                session["barge_suspect"] = True
                # 【11.21-13 首句锁定保底】检测到麦克风人声的"第一帧"就先给 ASR
                # 一个短宽限：云端 definite 常在 VAD 攒够 3 帧(~0.6s)之前就到，
                # 若不提前放行，首句会被 ASR 的 ai_echo 门当 AI 回声吞掉→永远锁不上。
                # 只在"尚未锁定"时生效（ai_echo 门也只在未锁定时才吞句），
                # 已锁定后不影响任何拦截。正式打断成功时会再续成 2s 长宽限。
                if _asr is not None and getattr(_asr, "_ref_f0", None) is None:
                    try:
                        _asr._barge_grace_until = max(
                            getattr(_asr, "_barge_grace_until", 0.0), now_mono + 1.5)
                    except Exception:
                        pass
                # 【11/22】计数/守护窗/冷却交给 BargeGate：窗口关闭即清零，
                # 新一句出声后头 0.25s 的能量不计，避免「上一句余音」杀掉下一句。
                _since_open_ms = int((now_mono - (barge_gate.opened_at or now_mono)) * 1000)
                if barge_gate.observe(now_mono, s):
                    session["interrupted"] = True
                    # 【11/22 双讲留痕】能量打断以前只通知商家端，后台看板不计、call_turns 也不记，
                    # 于是"打断/双讲事件 0 次"和 RSI 看不到打断率。这里补三件事：
                    # ① 计数 ② 广播给运营后台 ③ 打标记，等本轮 ASR final 落库时记 interrupted=1。
                    session["barge_pending"] = True
                    session["barge_count"] = int(session.get("barge_count", 0) or 0) + 1
                    # 不 await：音频帧循环里绝不能因为后台看板慢而阻塞（11/22 实机卡死教训）
                    asyncio.create_task(broadcast_monitor(
                        call_id, {"type": "interrupt", "by": "merchant", "source": "vad"}))
                    _tf = session.get("t_final")
                    _since_final_ms = (int((time.perf_counter() - _tf) * 1000)
                                       if _tf else -1)
                    dlog(f"VAD-BARGE {call_id} rms={s} rtc={bool(session.get('rtc'))} "
                         f"epoch={session['reply_epoch']} since_open={_since_open_ms}ms "
                         f"since_final={_since_final_ms}ms ← 能量抢话打断，冷却2.5s")
                    await send_json({"type": "interrupt_tts"})
                    # 【WebRTC·11/19】下行轨同步清帧（浏览器侧抖动缓冲靠 PLC 收尾）
                    if session.get("rtc"):
                        session["rtc"].clear()
                    # 【11.21-12 首句打断修复·根因】旧版打断只清零播报时钟、tts_count，
                    # 却没作废服务端 audio_out 里正在下发的开场白残留帧。audio_sender
                    # 每发一帧就 mark_ai_speaking() 把时钟刷回当前时间 → 用户首句
                    # definite 一到 ASR 就被 ai_echo 分支（未锁定+AI播报中）当回声丢弃
                    # ——表现就是"第一句打不断/插话没反应"。正常 final 分支早就有
                    # reply_epoch+=1 + drop_stale()（见 on_final），打断入口独缺这两步。
                    # 这里补齐：作废旧轮残留帧（epoch 不匹配即被 audio_sender 丢弃，
                    # 不再 mark_ai_speaking），让清零后的时钟保持到首句 definite 到达。
                    session["reply_epoch"] += 1
                    drop_stale()
                    session["tts_count"] = 0   # 立即清零，后续final按"打断"处理
                    # 【11/21 五轮 + 11.21-12 兜底】打断成功 = AI 已闭嘴：清零播报时钟，
                    # 并设 2s 打断宽限——宽限期内 ASR 的 ai_echo 分支即使因个别在途帧
                    # 把时钟刷新，也不丢弃商家 definite，双保险保住首句话。
                    try:
                        if session.get("asr") is not None:
                            session["asr"]._ai_last_speak_at = 0.0
                            session["asr"]._barge_grace_until = now_mono + 2.0
                    except Exception:
                        pass
            else:
                barge_gate.observe(now_mono, s)  # 能量不足：内部衰减计数
        if session["asr"]:
            await session["asr"].feed_audio(data)

    try:
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            if (data := msg.get("bytes")) is not None:
                await process_uplink_pcm(data)
            elif (text := msg.get("text")) is not None:
                try:
                    obj = json.loads(text)
                except json.JSONDecodeError:
                    continue
                action = obj.get("action")
                if action == "start":  # 接听后触发开场白；若为断线重连（已有轮次）则只恢复链路
                    dlog(f"WS-START {call_id} ← 收到start（开场/恢复）")
                    resumed = bool(db.query_one(
                        "SELECT COUNT(*) AS n FROM call_turns WHERE call_id=?", (call_id,))["n"])
                    if resumed:
                        # 恢复：还原状态机进度，不重复开场白
                        # 11/11：同时还原轮次号，避免重连后 turn_no 从 0 重新开始
                        # 撞上唯一索引导致新一轮记录被忽略。
                        agent.turn_no = db.query_one(
                            "SELECT COALESCE(MAX(turn_no),0) AS n FROM call_turns WHERE call_id=?",
                            (call_id,))["n"]
                        last = db.query_one(
                            "SELECT state, compliance FROM call_turns WHERE call_id=? AND role='agent' "
                            "ORDER BY id DESC LIMIT 1", (call_id,))
                        if last and last["state"]:
                            agent.state = last["state"]
                            try:
                                for k, v in (json.loads(last["compliance"] or "{}")
                                             .get("elements", {})).items():
                                    if v:
                                        agent.done_elements.add(k)
                            except Exception:
                                pass
                        await send_json({"type": "resumed"})
                        continue
                    t0 = time.time()
                    # 【11.21-18】开场白生成期间商家开口/打断（VAD 抢话把
                    # reply_epoch 前进）时，开场白剩余句子不挂新 epoch 复活
                    # 串进第一轮回复 —— 「接通就重复」的根治点。
                    result = await asyncio.get_event_loop().run_in_executor(
                        None, lambda: agent.chat_stream(
                            "", on_sentence=sentence_cb_for(session["reply_epoch"])))
                    record_turns("", result, (time.time() - t0) * 1000)
                    comp = check_compliance(agent.history)
                    await send_json({"type": "agent_reply_meta", "state": result.get("state", ""),
                                     "finished": result.get("finished", False),
                                     "emotion": "neutral", "compliance": comp,
                                     "flow5": agent.flow_progress()})
                    await broadcast_monitor(call_id, {"type": "turn", "role": "agent",
                                                      "text": result["ai_reply"], "state": "开场",
                                                      "flow5": agent.flow_progress()})
                elif action == "anti_doubletalk":
                    # 【11/21 抗双讲】现场开关：开=只认第一说话人的声音；关=恢复原行为
                    # （所有说话人正常转写）。关闭时后端真实停用——清空音纹参考与
                    # 判重历史，从此刻起全部声音恢复上屏，不只是改 UI 标签。
                    # 重新开启时也清空锁定，让下一句 definite 重新选目标说话人。
                    enabled = bool(obj.get("enabled"))
                    session["anti_doubletalk"] = enabled
                    if not enabled:
                        session["speaker_lock"] = None
                    if session["asr"]:
                        session["asr"].set_anti_doubletalk(enabled)
                    await send_json({"type": "anti_doubletalk_ack", "enabled": enabled})
                    dlog(f"WS-ANTI-DOUBLE {call_id} ← 抗双讲{'开启' if enabled else '关闭'}")
                elif action == "relock_speaker":
                    # 【11/22】手动重锁：视频/旁人先说话抢了音纹时，本人在安静环境点一下，
                    # 清空参考，等下一句本人声音重新锁定；不必先关再开。
                    if session["asr"]:
                        session["asr"].set_anti_doubletalk(True, relock=True)
                        session["anti_doubletalk"] = True
                    await send_json({"type": "anti_doubletalk_ack", "enabled": True,
                                     "relocked": True})
                    await send_json({"type": "system",
                                     "text": "（音纹已重锁：请本人说一句，重新建立目标）"})
                    dlog(f"WS-RELOCK {call_id} ← 手动重锁音纹")
                    await broadcast_monitor(call_id, {"type": "speaker_guard",
                                                      "event": "toggle", "enabled": enabled})
                elif action == "diag_dump":
                    # 【11/21 诊断版】导出音纹锁定判决日志：纯旁路，不改任何判定。
                    # 前端"复制诊断"按钮发此 action，拿到真实链路里每句 definite
                    # 的实测 f0/spread/参考/相似度/判定，用来定位锁错人还是没锁上。
                    _asr_d = session.get("asr")
                    _snap = _asr_d.diag_snapshot() if _asr_d is not None else {
                        "error": "ASR 会话未建立"}
                    await send_json({"type": "diag_data", "snapshot": _snap})
                    dlog(f"DIAG-DUMP {call_id} ← 导出 {len(_snap.get('events', []))} 条判决日志")
                elif action == "latency_report":
                    # 罗 11/12：浏览器上报「用户停说 → 首个音频帧 src.start()」的端到端首字响应
                    try:
                        e2e = float(obj.get("e2e_ms") or 0)
                        asr_tail = float(obj.get("asr_tail_ms") or 0)
                        turn = int(obj.get("turn") or session.get("turn_no") or 0)
                    except (TypeError, ValueError):
                        e2e, asr_tail, turn = 0.0, 0.0, 0
                    if e2e > 0 and turn > 0:
                        stage = _stage_snapshot(session, e2e, asr_tail)
                        _save_e2e(call_id, turn, e2e, stage)
                        dlog(f"LATENCY {call_id} turn={turn} e2e={e2e:.0f}ms "
                             f"stage={json.dumps(stage, ensure_ascii=False)}")
                        await broadcast_monitor(call_id, {"type": "latency",
                                                          "e2e_ms": e2e, "stage": stage})
                elif action == "rtc_offer":
                    # 【WebRTC·11/19】浏览器发起媒体协商：音频换 SRTP/Opus，控制事件仍走本 WS
                    # 【11/22 计时诊断】把「收到 offer → 媒体会话创建 → handle_offer 返回」
                    # 三段分开计时，用于区分 5 秒是卡在本函数、卡在构造 aiortc 会话，
                    # 还是卡在 handle_offer 内部（纯日志，不改行为）。
                    _t_recv = time.monotonic()
                    dlog(f"RTC-OFFER-RECV {call_id} ← 收到浏览器 offer")
                    if RTC_MEDIA_CLS is None:
                        await send_json({"type": "rtc_state", "state": "unavailable",
                                         "msg": "后端 WebRTC 未启用（缺 aiortc 或 ZHIYU_WEBRTC=0），保持 WS PCM 链路"})
                        continue
                    if session.get("rtc"):
                        await session["rtc"].close()
                    media = RTC_MEDIA_CLS(call_id, on_pcm=process_uplink_pcm,
                                          send_json=send_json, log=dlog)
                    _t_media = time.monotonic()
                    dlog(f"RTC-OFFER-MEDIA {call_id} 媒体会话创建 "
                         f"{( _t_media - _t_recv) * 1000:.0f}ms")
                    ok, err = await media.handle_offer(obj.get("sdp") or {})
                    dlog(f"RTC-OFFER-DONE {call_id} handle_offer 返回 "
                         f"{(time.monotonic() - _t_media) * 1000:.0f}ms ok={ok}")
                    if ok:
                        session["rtc"] = media
                        session["rtc_epoch"] = None   # 【防截断】新协商：交接标记重置
                    else:
                        await media.close()
                        await send_json({"type": "rtc_state", "state": "failed",
                                         "msg": f"协商失败：{err[:120]}"})
                elif action == "rtc_candidate":
                    cand = obj.get("candidate")
                    if session.get("rtc") and cand:
                        await session["rtc"].add_candidate(cand)
                elif action == "rtc_close":
                    # 【11/20 无声修复】浏览器关闭/回退 RTC 时显式释放媒体会话：
                    # 清空 session["rtc"] 让 audio_sender 立刻切回 WS 二进制链路，
                    # 否则音频持续 push 进已死的轨 → 前端永久收不到 AI 回复。
                    old = session.get("rtc")
                    session["rtc"] = None
                    session["rtc_epoch"] = None       # 【防截断】交接标记一并清掉
                    if old:
                        dlog(f"RTC-CLOSE {call_id} ← 浏览器关闭轨，切回 WS PCM；"
                             f"stats={old.stats()}")
                        try:
                            await old.close()
                        except Exception:
                            pass
                elif action == "rtc_stats":
                    # 浏览器 getStats 每 2s 上报：RTT/丢包/抖动 → 追加 jsonl + 推监控页
                    st = obj.get("stats") or {}
                    rec = {"call_id": call_id, "ts": time.time(),
                           "turn": session.get("turn_no") or 0}
                    for k in ("rtt_ms", "jitter_ms", "loss_pct", "loss_pkts",
                              "bytes_in", "bytes_out"):
                        if st.get(k) is not None:
                            rec[k] = st[k]
                    try:
                        os.makedirs(RTC_DATA_DIR, exist_ok=True)
                        with open(os.path.join(RTC_DATA_DIR, "rtc_stats.jsonl"), "a",
                                  encoding="utf-8") as f:
                            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    except Exception:
                        pass
                    await broadcast_monitor(call_id, {"type": "rtc_stats",
                                                      "stats": {k: rec.get(k) for k in
                                                                ("rtt_ms", "jitter_ms", "loss_pct")}})
                elif action == "rtc_weaknet":
                    # 【11/20 答辩弱网开关】浏览器点选丢包档位（0/5/15/30%），
                    # 服务端在 aioice 收发层注入双向 RTP/SRTP 丢包，真机即时演示弱网。
                    # 只丢媒体包，STUN/DTLS 不动 → ICE 保持连通；pct=0 立即恢复。
                    pct = obj.get("pct", 0)
                    if RTC_SET_LOSS is None:
                        await send_json({"type": "rtc_weaknet_state", "pct": 0,
                                         "msg": "后端 WebRTC 未启用，无法模拟弱网"})
                    else:
                        try:
                            applied = RTC_SET_LOSS(pct, log=dlog)
                            await send_json({"type": "rtc_weaknet_state",
                                             "pct": applied,
                                             "msg": f"弱网模拟 {applied:g}% 已生效"})
                        except Exception as _we:
                            dlog(f"WEAKNET-FAIL {call_id} {type(_we).__name__}: {_we}")
                            await send_json({"type": "rtc_weaknet_state", "pct": 0,
                                             "msg": f"设置失败：{str(_we)[:60]}"})
                elif action == "text":  # 文字兜底对话（ASR不可用时）
                    text_in = obj.get("text", "")
                    # 【告别不续聊】与语音同规则：结束语之后手打「再见」= 接受收尾，
                    # 只记一行、AI 不再开口，直接把通话收掉（绝不拉回业务答疑）。
                    if (agent.finished or closing_spoken(agent)) and is_farewell_after_closing(text_in):
                        bye_turn = agent.turn_no + 1
                        db.execute(
                            "INSERT OR IGNORE INTO call_turns(call_id,turn_no,role,text,"
                            "asr_confidence,intent,state,emotion,compliance) "
                            "VALUES(?,?,?,?,?,?,?,?,?)",
                            (call_id, bye_turn, "merchant", text_in, 1.0, "BYE", "END",
                             "neutral", "PASS"))
                        await send_json({"type": "final", "text": text_in,
                                         "confidence": 1.0, "turn": bye_turn})
                        await broadcast_monitor(call_id, {"type": "turn", "role": "merchant",
                                                          "text": text_in, "intent": "BYE"})
                        dlog(f"CLOSING-BYE {call_id} 文字「{text_in[:12]}」→ 收线")
                        if not session["closing"]["ended"]:
                            session["closing"]["ended"] = True
                            session["closing"]["cancel"] = True
                            await finalize_call(call_id, session)
                        await send_json({"type": "call_finished"})
                        await broadcast_monitor(call_id, {"type": "call_ended"})
                        break
                    # 文字兜底同样算商家开口；人手输入无回声，故不传文本
                    note_merchant_voice()
                    # 罗 11/12：文字输入同样是新一轮，作废上一轮残留片段/音频
                    session["reply_epoch"] += 1
                    drop_stale()
                    # 11/19 顺序修复：final（商家气泡）必须**先于**回复生成发送——
                    # chat_stream 期间 agent_reply 已经逐句推出去了，final 若留在
                    # 后面，商家气泡会排到 AI 回复下面（实测截图：21:17:04 的提问
                    # 显示在 21:17:05 的回答之下）。语音路径 final 来自 ASR 本来
                    # 就在回复前，只有文字兜底路径存在此倒序。
                    await send_json({"type": "final", "text": text_in, "confidence": 1.0})
                    t0 = time.time()
                    # 【11.21-18】文字兜底同样用固化 epoch 闭包（生成期间新的
                    # 打断/文字输入作废本轮时，剩余句子不复活串轮）
                    result = await asyncio.get_event_loop().run_in_executor(
                        None, lambda: agent.chat_stream(
                            text_in, on_sentence=sentence_cb_for(session["reply_epoch"])))
                    record_turns(text_in, result, (time.time() - t0) * 1000)
                    session["turn_no"] = result["turn"]   # 自动续讲用它判断商家是否已开口
                    await broadcast_tool_calls(call_id, result)
                    # 11/15 真机修复②：文字兜底模式同样实时推闲聊预警，链路与语音一致
                    if result.get("offtopic"):
                        await broadcast_monitor(call_id, {"type": "offtopic",
                                                          "offtopic": result["offtopic"]})
                    comp = check_compliance(agent.history)
                    await send_json({"type": "agent_reply_meta", "state": result.get("state", ""),
                                     "finished": result.get("finished", False),
                                     "emotion": result.get("emotion", "neutral"),
                                     "compliance": comp,
                                     "flow5": agent.flow_progress()})
                    if result.get("finished"):
                        # 与 ASR final 一致；放后台，否则会占住主循环、挂断要等 5 秒。
                        schedule_auto_hangup(PLAYBACK_BUFFER_TEXT)
                    elif (agent.state == AGENT_EXPLAIN_STATE
                          and not getattr(agent, "_rejection_delivered", True)):
                        schedule_auto_continue(session.get("turn_no") or agent.turn_no)
                elif action == "hangup":
                    # 11/12：商家端主动挂断 = 业务任务未终结，挂起为待跟进，
                    # 不再走 finalize_call() 写成 completed 污染三项指标分母。
                    # 11/12：同时作废「结束语后 5 秒」窗口，避免残留收尾协程把
                    # 已经挂断的通话又按自动收尾写成 completed。
                    session["closing"]["cancel"] = True
                    session["closing"]["ended"] = True
                    dlog(f"WS-HANGUP {call_id} ← 收到挂断动作（挂起待跟进）")
                    await park_call(call_id, session, "商家中途挂断（WS）")
                    await send_json({"type": "call_finished"})
                    break
                elif action == "silence_reset":
                    barge_gate.reset()       # 【11/22】静默重置：打断计数一并清零
    except WebSocketDisconnect:
        dlog(f"WS-DISCONNECT {call_id} ← 商家端主动断开")
        pass
    finally:
        sentence_queue.put_nowait(None)          # 结束句worker
        audio_out.put_nowait(None)               # 结束音频下发协程（罗 11/12）
        spec.close()                             # 关闭投机预生成后台线程（罗 11/12）
        if session.get("asr"):
            await session["asr"].close()
        if session.get("rtc"):                   # 【WebRTC·11/19】关闭媒体会话
            await session["rtc"].close()
        call_row = db.query_one("SELECT status FROM calls WHERE call_id=?", (call_id,))
        if call_row and call_row["status"] == "in_progress":
            # 11/12：异常断开不是「一次未闭环的已完成通话」，标 failed 排除出分母。
            try:
                await mark_link_failed(call_id, "WebSocket 异常断开")
            except Exception:
                pass
        SESSIONS.pop(call_id, None)


# ---------------- WebSocket：运营后台实时监控 ----------------

@app.websocket("/ws/monitor")
async def ws_monitor(ws: WebSocket):
    await ws.accept()
    MONITOR_CLIENTS.add(ws)
    try:
        while True:
            await ws.receive_text()  # 心跳
    except WebSocketDisconnect:
        pass
    finally:
        MONITOR_CLIENTS.discard(ws)


@app.get("/api/kb/stats")
def api_kb_stats():
    """知识库实时统计（供 /rag 页面显示，取自运行中的数据库）。"""
    try:
        total = db.query_one("SELECT COUNT(*) AS c FROM knowledge_chunks")["c"]
        rows = db.query("SELECT source, COUNT(*) AS n FROM knowledge_chunks "
                        "GROUP BY source ORDER BY n DESC")
        by_source = {r["source"]: r["n"] for r in rows}
        return {"total": total, "sources": len(by_source), "by_source": by_source}
    except Exception as e:
        return {"total": 0, "sources": 0, "by_source": {}, "error": str(e)}


# 静态资源（如需）
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8900)