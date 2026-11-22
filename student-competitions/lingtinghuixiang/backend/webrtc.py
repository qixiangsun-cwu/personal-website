# -*- coding: utf-8 -*-
"""WebRTC 弱网传输媒体面（aiortc）· 2026/11/19

目标：把「浏览器 ↔ 后端」的音频媒体从裸 WebSocket PCM 换成 WebRTC（SRTP/Opus），
获得弱网四件套：Opus in-band FEC、NACK 重传、丢包隐藏 PLC、自适应抖动缓冲；
同时保留 WS 作为信令/字幕/打断/文字兜底通道（业界标准分工）。

架构（与 server.py 的对接约定）：
  上行  浏览器麦克风 → RTCPeerConnection(Opus/SRTP) → aiortc 解码
        → 重采样 16k/16bit/mono PCM → on_pcm 回调
        （server.ws_call 的 process_uplink_pcm：能量VAD抢话检测 + 火山ASR feed，
          与 WS 二进制链路完全同一段代码，零业务改动）
  下行  TTS WAV 分片(24k PCM + 44字节头) → push_wav()
        → 剥头 + 2倍上采样到 48k → 切 960 样本(20ms)帧 → PacedAudioTrack 按墙钟匀速
        → aiortc Opus 编码 → SRTP → 浏览器 <audio> 远端轨播放（浏览器自带抖动缓冲）
  信令  复用 /ws/call JSON：rtc_offer / rtc_candidate / rtc_answer / rtc_state

弱网参数说明（如实）：
  * 上行 Opus FEC：由 Chrome 编码端开启（offer fmtp 带 useinbandfec=1，应答接受即生效）；
  * 下行抗丢包：aiortc 的 Opus 编码器未暴露 FEC 开关，下行依赖浏览器 NACK 重传 + PLC，
    对 24~32kbps 语音在 ≤15% 丢包下可保持对话闭环（Phase 5 实测矩阵验证）；
  * ICE 纯局域网 host candidates 直连，不搭 STUN/TURN（赛题禁止外网穿透/代理）。
"""
import asyncio
import fractions
import os
import struct
import time

try:
    import av
    from aiortc import (RTCPeerConnection, RTCSessionDescription,
                        RTCIceCandidate, RTCConfiguration)
    from aiortc.mediastreams import MediaStreamTrack, MediaStreamError
    from aiortc.sdp import candidate_from_sdp
    _DEPS_OK = True
    _DEPS_ERR = ""
except Exception as _e:                       # pragma: no cover - 仅在缺依赖环境触发
    _DEPS_OK = False
    _DEPS_ERR = f"{type(_e).__name__}: {_e}"

SAMPLES_PER_FRAME = 960                       # 20ms @ 48kHz
BYTES_PER_FRAME = SAMPLES_PER_FRAME * 2       # s16 mono
WAV_HEADER_LEN = 44
UP_CHUNK_SAMPLES = 1600                       # 100ms @ 16k：与 WS 链路 ~200ms/包 同量级
# 【11/21 语速忽快忽慢修复】断流时补发的静音帧（全零 20ms）。
# 旧实现队列为空时 recv 挂起 → RTP 流断流 → 浏览器 NetEQ 抖动缓冲对「包到达间隙」
# 做 expand(PLC 拉伸)+accelerate(追赶加速) 补偿，TTS 分片到达有快有慢，
# 于是 AI 语音听起来忽快忽慢。改为断流时按 20ms 节拍发静音帧：RTP 连续匀速，
# NetEQ 缓冲水位恒定，不再做变速补偿；真实语音间隙就是干净的静音。
_SILENT_FRAME = bytes(BYTES_PER_FRAME)


class PacedTtsTrack(MediaStreamTrack):
    """下行 TTS 音频轨：按墙钟 20ms 匀速出帧；队列空 = 补静音帧（RTP 永不断流）。

    pacing 采用「消费端相对时钟」：连续供帧时按 due 序列匀速推进；供帧断流后
    恢复时立即重对齐（绝不追赶积压），保证打断/改轮后语音不会突然快放。
    """
    kind = "audio"

    def __init__(self):
        super().__init__()
        self._frames = []                     # 20ms 帧队列（bytes）
        self._buf = bytearray()               # 不足 960 样本的余量
        self._pts = 0
        self._time_base = fractions.Fraction(1, 48000)
        self._due = None                      # 下一帧应发出的单调时刻
        self._alive = True
        self._interrupted = 0                 # 次数统计（看板可读）
        # 【遥测】供帧/出帧计数：close 时对账，定位"推了帧但浏览器没声音"
        self._pushed_frames = 0
        self._sent_frames = 0
        self._sent_rms_acc = 0                # 每 10 帧采样一次 |样本| 均值之和（内容遥测）
        self._sent_rms_n = 0

    # ---------- 供帧侧（server audio_sender 调用，同步、事件循环线程内） ----------
    def push_wav(self, wav_bytes: bytes) -> int:
        """TTS WAV 分片(24k PCM + 44B 头) → 2 倍上采样 48k → 切 20ms 帧入队。
        返回入队帧数；非 WAV 或空帧返回 0。"""
        if not self._alive or not wav_bytes:
            return 0
        if wav_bytes[:4] != b"RIFF" or len(wav_bytes) <= WAV_HEADER_LEN:
            return 0
        pcm24 = wav_bytes[WAV_HEADER_LEN:]
        if len(pcm24) < 2:
            return 0
        n24 = len(pcm24) // 2
        # 每个样本重复一次 = 24k→48k 零阶保持上采样（语音带内无影响，分块无状态）
        vals = struct.unpack(f"<{n24}h", pcm24[:n24 * 2])
        pcm48 = struct.pack(f"<{n24 * 2}h", *[v for v in vals for _ in (0, 1)])
        self._buf += pcm48
        n_frames = 0
        while len(self._buf) >= BYTES_PER_FRAME:
            self._frames.append(bytes(self._buf[:BYTES_PER_FRAME]))
            del self._buf[:BYTES_PER_FRAME]
            n_frames += 1
        self._pushed_frames += n_frames
        return n_frames

    def clear(self) -> int:
        """打断/换轮：清空在途帧（与 WS 链路 stopPlayback 语义对齐）。"""
        n = len(self._frames)
        self._frames.clear()
        self._buf.clear()
        self._due = None                      # 下帧恢复时立即重对齐
        self._interrupted += 1
        return n

    def pending_frames(self) -> int:
        return len(self._frames)

    def stop(self):
        self._alive = False
        super().stop()

    # ---------- 消费侧（aiortc Opus 编码器调用） ----------
    def _build_frame(self, data: bytes):
        frame = av.AudioFrame(format="s16", layout="mono", samples=SAMPLES_PER_FRAME)
        plane = frame.planes[0]
        try:
            plane.update(data)
        except Exception:                     # 极少数 PyAV 版本接口差异
            plane.write(data)                 # noqa
        frame.sample_rate = 48000
        frame.pts = self._pts
        frame.time_base = self._time_base
        self._pts += SAMPLES_PER_FRAME
        return frame

    async def recv(self):
        """按墙钟 20ms 匀速出帧；到点有帧出真帧，没帧出静音帧（RTP 永不断流）。

        【11/21 语速忽快忽慢修复】旧实现队列为空时 recv 挂起等 push，RTP 流出现
        到达间隙 → 浏览器 NetEQ 对间隙做 expand(PLC 拉伸)+accelerate(加速追赶)，
        TTS 分片到达节奏不均时听感就是「忽快忽慢」。现在 recv 变成严格的
        20ms 节拍器：每一拍都出帧（真帧或静音帧），NetEQ 缓冲水位恒定，
        播放速率恒定 = 语速稳定；打断/换轮由 clear() 清帧 + 重对齐处理。
        """
        if not self._alive:
            raise MediaStreamError
        now = time.monotonic()
        due = self._due
        # 首帧 / clear() 打断重对齐 / 事件循环长停顿（>20ms 落后）→ 从当下重新起步，
        # 绝不追赶积压（追赶 = 一次连发多帧 = 变速）
        if due is None or due < now - 0.02:
            due = now
        wait = due - now
        if wait > 0:
            await asyncio.sleep(wait)
        if not self._alive:
            raise MediaStreamError
        if self._frames:
            data = self._frames.pop(0)
        else:
            data = _SILENT_FRAME          # 断流补静音：RTP 连续，NetEQ 不做变速补偿
        # clear() 若在 sleep 期间把 _due 置 None（打断/换轮），尊重之：从当下重新对齐
        self._due = (time.monotonic() + 0.02) if self._due is None else (due + 0.02)
        self._sent_frames += 1
        if self._sent_frames % 10 == 1 and data is not _SILENT_FRAME:
            n = len(data) // 2            # 内容遥测：确认出帧非全零
            if n:
                vals = struct.unpack(f"<{n}h", data[:n * 2])
                self._sent_rms_acc += sum(abs(v) for v in vals) // n
                self._sent_rms_n += 1
        return self._build_frame(data)


class CallMedia:
    """一通通话的 WebRTC 媒体会话。

    用法（server.ws_call 内）：
      media = CallMedia(call_id, on_pcm=process_uplink_pcm, send_json=send_json, log=dlog)
      ok, err = await media.handle_offer(sdp_dict)     # 收到 rtc_offer 后
      media.push_wav(chunk) / media.clear()            # audio_sender / 打断
      await media.close()                              # 会话终结
    """

    def __init__(self, call_id: str, on_pcm, send_json, log=print):
        if not _DEPS_OK:
            raise RuntimeError(f"aiortc 不可用: {_DEPS_ERR}")
        self.call_id = call_id
        self._on_pcm = on_pcm                 # async def(pcm16k: bytes)
        self._send_json = send_json
        self._log = log
        self._pump = None
        self._remote_track = None
        self.started_at = time.time()
        # 【11/22 关键修复·5 秒延迟的元凶】必须显式传 iceServers=[]。
        # aiortc 的默认值（rtcicetransport.py:228）是
        #     getDefaultIceServers() -> [RTCIceServer("stun:stun.l.google.com:19302")]
        # 而 RTCIceGatherer.__init__ 只在 `iceServers is None` 时才套默认值。
        # 于是 gather 阶段会去请求谷歌公网 STUN，内网/受限网络下要等满超时：
        # 实测 server_debug.log 里 setLocalDescription=5019ms（两次都是精确 5.0s），
        # 之后 ICE 只花 100ms 就 connected —— 也就是说 4~5 秒全耗在等 STUN 超时。
        # 传空列表 → 只做局域网 host candidates 直连，符合赛题"禁止外网穿透/代理"。
        self.pc = RTCPeerConnection(RTCConfiguration(iceServers=[]))
        self.track = PacedTtsTrack()
        self.pc.addTrack(self.track)

        @self.pc.on("track")
        def on_track(track):
            if track.kind != "audio":
                return
            self._remote_track = track
            resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
            pending = bytearray()

            async def pump():
                try:
                    while True:
                        frame = await track.recv()
                        for fr in resampler.resample(frame):
                            plane = fr.planes[0]
                            raw = plane.to_bytes() if hasattr(plane, "to_bytes") \
                                else bytes(plane)
                            pending.extend(raw)
                            while len(pending) >= UP_CHUNK_SAMPLES * 2:
                                chunk = bytes(pending[:UP_CHUNK_SAMPLES * 2])
                                del pending[:UP_CHUNK_SAMPLES * 2]
                                try:
                                    await self._on_pcm(chunk)
                                except Exception as e:
                                    self._log(f"RTC-UP-ONPCM-FAIL {type(e).__name__}: {str(e)[:80]}")
                except MediaStreamError:
                    pass                      # 轨道正常结束
                except Exception as e:
                    self._log(f"RTC-PUMP-FAIL {call_id} {type(e).__name__}: {str(e)[:80]}")

            self._pump = asyncio.ensure_future(pump())
            self._log(f"RTC-TRACK {call_id} ← 远端音频轨接入，上行泵启动")

        @self.pc.on("connectionstatechange")
        async def on_conn_state():
            st = self.pc.connectionState
            self._log(f"RTC-CONN {call_id} state={st}")
            try:
                await self._send_json({"type": "rtc_state", "state": st})
            except Exception:
                pass

        @self.pc.on("iceconnectionstatechange")
        async def on_ice_state():
            self._log(f"RTC-ICE {call_id} state={self.pc.iceConnectionState}")

        @self.pc.on("icecandidate")
        async def on_ice_cand(cand):
            if cand is None:
                return
            line = "candidate:" + _candidate_to_sdp_line(cand)
            try:
                await self._send_json({"type": "rtc_candidate", "candidate": {
                    "candidate": line,
                    "sdpMid": cand.sdpMid,
                    "sdpMLineIndex": cand.sdpMLineIndex}})
            except Exception:
                pass

    # ---------------- 信令 ----------------
    async def handle_offer(self, sdp_dict) -> tuple:
        """浏览器 offer → 应答。返回 (ok, err)。

        【11/22 计时诊断】现场实测两通电话的 offer→answer 都是**精确 5.0 秒**，
        而 answer 下发后 100~160ms 就 connected —— 说明那 4-5 秒全耗在协商这一步内部，
        既不是网络也不是 ICE。这里逐步计时，定位到底卡在
        setRemoteDescription / createAnswer / setLocalDescription 哪一步。
        **纯日志，不改任何行为。**
        """
        t0 = time.monotonic()
        laps = []

        def _lap(name, t_prev):
            now = time.monotonic()
            laps.append(f"{name}={(now - t_prev) * 1000:.0f}ms")
            return now

        try:
            sdp_txt = sdp_dict.get("sdp") or ""
            self._log(f"RTC-OFFER-BEGIN {self.call_id} sdp={len(sdp_txt)}B "
                      f"audio={'m=audio' in sdp_txt} t={time.strftime('%H:%M:%S')}")
            t = time.monotonic()
            offer = RTCSessionDescription(sdp_txt, sdp_dict.get("type") or "offer")
            await self.pc.setRemoteDescription(offer)
            t = _lap("setRemote", t)
            answer = await self.pc.createAnswer()
            t = _lap("createAnswer", t)
            await self.pc.setLocalDescription(answer)
            t = _lap("setLocal", t)
            ld = self.pc.localDescription
            await self._send_json({"type": "rtc_answer",
                                   "sdp": {"type": ld.type, "sdp": ld.sdp}})
            _lap("sendJson", t)
            self._log(f"RTC-OFFER {self.call_id} ← 协商应答已下发 "
                      f"总={(time.monotonic() - t0) * 1000:.0f}ms " + " ".join(laps))
            return True, ""
        except Exception as e:
            import traceback
            self._log(f"RTC-OFFER-FAIL {self.call_id} "
                      f"已耗时={(time.monotonic() - t0) * 1000:.0f}ms "
                      + traceback.format_exc()[-600:])
            return False, f"{type(e).__name__}: {e}"

    async def add_candidate(self, cand_dict):
        try:
            line = (cand_dict or {}).get("candidate") or ""
            if not line:
                return
            cand = candidate_from_sdp(line if line.startswith("candidate:") else "candidate:" + line)
            cand.sdpMid = (cand_dict or {}).get("sdpMid")
            cand.sdpMLineIndex = (cand_dict or {}).get("sdpMLineIndex")
            await self.pc.addIceCandidate(cand)
        except Exception as e:
            self._log(f"RTC-CAND-FAIL {self.call_id} {type(e).__name__}: {str(e)[:80]}")

    # ---------------- 下行音频 ----------------
    def push_wav(self, wav_bytes: bytes) -> int:
        try:
            return self.track.push_wav(wav_bytes)
        except Exception:
            return 0

    def clear(self):
        try:
            self.track.clear()
        except Exception:
            pass

    def pending_frames(self) -> int:
        return self.track.pending_frames()

    def is_connected(self) -> bool:
        """ICE/DTLS 是否已连通。

        【11/21 开场白无声修复】connectionState 还在 new/connecting 时，
        编码出的 RTP 帧会被传输层直接丢弃——push_wav「成功」但浏览器听不到，
        PacedTtsTrack 还按墙钟把音频消耗掉了，等连通后开场白已经播完。
        server.audio_sender 据此决定：未连通的音频块先走 WS 兜底，绝不开天窗。
        """
        try:
            return self.pc.connectionState == "connected"
        except Exception:
            return False

    def is_healthy(self) -> bool:
        """下行轨是否仍在正常出帧/可推送（供 server 自愈检测）。

        轨被 stop() 后 _alive=False，或 PeerConnection 已 closed/failed 时，
        继续 push_wav 只会把音频丢进黑洞（前端再也收不到 AI 声音）。
        """
        if not self.track._alive:
            return False
        try:
            st = self.pc.connectionState
            if st in ("closed", "failed", "disconnected"):
                return False
        except Exception:
            return False
        return True

    # ---------------- 生命周期 ----------------
    def stats(self) -> dict:
        """下行供帧/出帧对账（排查"推了帧但没声音"）。"""
        return {"pushed_frames": self.track._pushed_frames,
                "sent_frames": self.track._sent_frames,
                "pending": len(self.track._frames),
                "interrupted": self.track._interrupted,
                "sent_rms": (self.track._sent_rms_acc // max(self.track._sent_rms_n, 1))
                if self.track._sent_rms_n else -1}

    async def close(self):
        try:
            self._log(f"RTC-STATS {self.call_id} {self.stats()}")
        except Exception:
            pass
        try:
            self.track.stop()
        except Exception:
            pass
        if self._pump:
            self._pump.cancel()
        try:
            await self.pc.close()
        except Exception:
            pass


def _candidate_to_sdp_line(cand) -> str:
    """aiortc RTCIceCandidate → SDP candidate 行（不含 'candidate:' 前缀）。"""
    comp = getattr(cand, "component", 1) or 1
    typ = getattr(cand, "type", "host") or "host"
    ip = getattr(cand, "ip", "") or ""
    port = getattr(cand, "port", 0) or 0
    proto = getattr(cand, "protocol", "udp") or "udp"
    prio = getattr(cand, "priority", 0) or 0
    foundation = getattr(cand, "foundation", "") or "0"
    related = getattr(cand, "relatedAddress", None)
    rport = getattr(cand, "relatedPort", None)
    line = (f"{foundation} 1 {proto} {prio} {ip} {port} typ {typ}")
    if related:
        line += f" raddr {related} rport {rport or 0}"
    return line


def available() -> tuple:
    """(是否可用, 原因)——供 server 启动时打日志。"""
    return _DEPS_OK, _DEPS_ERR


# ---------------------------------------------------------------------------
# 弱网演示开关（11/20）：服务端 aioice 收发层丢包注入
#
# 与 tools/_test_webrtc_live.py 的 install_loss 完全同原理：丢弃首字节
# 0x80/0x81 的 RTP/SRTP 包，STUN/DTLS/RTCP 不受影响 → ICE 与媒体安全通道
# 保持完整，接收端看到的是真实 RTP 序号缺口，NACK/FEC/PLC/抖动缓冲全链路
# 真实生效。区别仅在进程：测试脚本注入在测试客户端，本开关注入在服务端，
# 因此浏览器真机点一下开关即可现场演示弱网，无需 clumsy / 管理员权限。
#
# 幂等：重复调用只改比例，不叠加 monkey-patch（否则丢包率会翻倍）。
# pct=0 时热路径直接透传，零 random 开销，正常通话零影响。
# ---------------------------------------------------------------------------
_WEAKNET = {"p": 0.0, "patched": False}


def set_weaknet_loss(pct: float, log=print) -> float:
    """设置双向 RTP/SRTP 丢包率（0-100），返回实际生效值。"""
    import random
    try:
        from aioice import ice as _ice
    except Exception as e:
        log(f"WEAKNET-N-AIOICE {type(e).__name__}: {e}")
        return -1.0
    p = max(0.0, min(100.0, float(pct)))
    _WEAKNET["p"] = p
    if not _WEAKNET["patched"]:
        orig_sendto = _ice.Connection.sendto
        orig_received = _ice.Connection.data_received

        def _is_media(data) -> bool:
            return len(data) > 1 and 0x80 <= data[0] <= 0x81

        async def _sendto(self, data, component):
            if _WEAKNET["p"] > 0 and _is_media(data) and \
                    random.random() * 100 < _WEAKNET["p"]:
                return                                   # 下行丢包
            return await orig_sendto(self, data, component)

        def _received(self, data, component):
            if _WEAKNET["p"] > 0 and _is_media(data) and \
                    random.random() * 100 < _WEAKNET["p"]:
                return                                   # 上行丢包
            return orig_received(self, data, component)

        _ice.Connection.sendto = _sendto
        _ice.Connection.data_received = _received
        _WEAKNET["patched"] = True
    log(f"WEAKNET-SET p={p:g}% (aioice RTP/SRTP 双向丢包，幂等注入)")
    return p


def get_weaknet_loss() -> float:
    """当前生效的演示丢包率（0=关闭）。"""
    return _WEAKNET["p"]
