# -*- coding: utf-8 -*-
"""智谱 GLM-TTS 文字转语音模块（延时2秒·重做版）。

接口（官方文档 docs.bigmodel.cn）：
    POST https://open.bigmodel.cn/api/paas/v4/audio/speech
    普通: {"model":"glm-tts","input","voice","response_format","speed"}
    流式: 再加 "stream": true → text/event-stream，
          每个 choices[i].delta.content 是 base64 编码的**独立 WAV 分片**
          （自带 RIFF....WAVE 头，可单独 decodeAudioData 播放）。

三种能力：
  1. synthesize()          整句非流式（保留给开场白/兜底）
  2. synthesize_stream()   流式分片，首包到手立即回调（首字响应关键路径）
  3. warmup()/get_cached() 固定话术预合成缓存，命中时 0ms 下发

注意：不使用浏览器 Web Speech API（用户新要求第 1 条：直接接大模型）。
不新增任何第三方依赖：只用项目已有的 requests。
"""

import base64
import json
import os
import re
import threading
import time
from typing import Callable, Optional

import requests

API_URL = "https://open.bigmodel.cn/api/paas/v4/audio/speech"
MODEL = "glm-tts"

# 可选音色（智谱文档公开音色；全通话统一用同一个）
VOICES = ["tongtong", "female", "male"]

# 启动时预合成的固定话术：命中缓存后首帧 0ms 下发
PREWARM_TEXTS = [
    "您好，我是京东商家资质审核专员",
    "不好意思，刚才没听清，您能再说一遍吗？",
    "理解您的心情。",
    "相关情况您可以拨打京东商家服务热线反馈。",
    "抱歉，这个问题超出资质审核范围，您可以拨打商家服务热线咨询。",
    "感谢您的接听，再见。",
]


def load_api_key() -> str:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend", ".env"))
    load_dotenv()  # 兜底：当前工作目录
    return os.getenv("ZHIPU_API_KEY", "")


class TTSClient:
    """线程安全的按句合成客户端。每句返回 bytes（WAV）。"""

    def __init__(self, api_key: str = "", voice: str = "tongtong",
                 response_format: str = "wav", speed: float = 1.0):
        self.api_key = api_key or load_api_key()
        if not self.api_key:
            raise RuntimeError("未配置 ZHIPU_API_KEY，无法使用 GLM-TTS")
        self.voice = voice          # 全通话统一音色
        self.response_format = response_format
        self.speed = speed
        self._cache: dict[str, bytes] = {}   # 整句音频缓存
        self._cache_lock = threading.Lock()
        # 复用 TLS 连接：流式/预热/整句共用，减少每次建连开销
        self._session = requests.Session()

    # ---------------- 缓存 ----------------
    def get_cached(self, text: str) -> Optional[bytes]:
        with self._cache_lock:
            return self._cache.get((text or "").strip())

    def _put_cache(self, text: str, audio: bytes) -> None:
        with self._cache_lock:
            if len(self._cache) < 200 and audio:
                self._cache[text] = audio

    # ---------------- 整句非流式 ----------------
    def synthesize(self, text: str) -> bytes:
        """同步合成一句文本 → 音频 bytes。失败抛异常，由调用方降级。"""
        text = (text or "").strip()
        if not text:
            return b""
        cached = self.get_cached(text)
        if cached is not None:
            return cached
        resp = self._session.post(
            API_URL,
            headers={"Authorization": f"Bearer {self.api_key}",
                     "Content-Type": "application/json"},
            json={"model": MODEL, "input": text, "voice": self.voice,
                  "response_format": self.response_format, "speed": self.speed},
            timeout=30,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"GLM-TTS HTTP {resp.status_code}: {resp.text[:200]}")
        audio = resp.content
        if resp.headers.get("Content-Type", "").startswith("application/json"):
            data = resp.json()
            audio = base64.b64decode(data.get("data") or data.get("audio") or "")
        if not audio:
            raise RuntimeError("GLM-TTS 返回空音频")
        self._put_cache(text, audio)
        return audio

    # ---------------- 流式分片（首字响应关键路径） ----------------
    def synthesize_stream(self, text: str, on_chunk: Callable[[bytes], None]) -> int:
        """流式合成：每拿到一个可独立播放的 WAV 分片就回调一次。

        返回分片数；无任何有效分片时抛异常，由调用方降级到 synthesize()。
        """
        text = (text or "").strip()
        if not text:
            return 0
        cached = self.get_cached(text)
        if cached is not None:
            on_chunk(cached)
            return 1
        payload = {"model": MODEL, "input": text, "voice": self.voice,
                   "response_format": self.response_format, "speed": self.speed,
                   "stream": True}
        n = 0
        with self._session.post(
                API_URL,
                headers={"Authorization": f"Bearer {self.api_key}",
                         "Content-Type": "application/json",
                         "Accept": "text/event-stream"},
                json=payload, timeout=30, stream=True) as resp:
            if resp.status_code != 200:
                raise RuntimeError(f"GLM-TTS 流式 HTTP {resp.status_code}: {resp.text[:200]}")
            for raw_line in resp.iter_lines():
                if not raw_line:
                    continue
                if isinstance(raw_line, bytes):
                    line = raw_line.decode("utf-8", "replace").strip()
                else:
                    line = raw_line.strip()
                if line.startswith("data:"):
                    line = line[5:].strip()
                if not line or line == "[DONE]":
                    continue
                try:
                    obj = json.loads(line)
                except (ValueError, TypeError):
                    continue
                for choice in (obj.get("choices") or []):
                    content = ((choice.get("delta") or {}).get("content") or "")
                    if not content:
                        continue
                    try:
                        audio = base64.b64decode(content)
                    except Exception:
                        continue
                    # 每个分片自带 RIFF/WAVE 头，是可独立解码的 WAV
                    if audio[:4] == b"RIFF" and len(audio) > 44:
                        n += 1
                        on_chunk(audio)
        if n == 0:
            raise RuntimeError("GLM-TTS 流式返回无有效音频分片")
        return n

    # ---------------- 启动预热 ----------------
    def warmup(self, texts: Optional[list[str]] = None) -> int:
        """后台预合成固定话术 + 预热 TLS 连接。返回成功条数。"""
        ok = 0
        for t in (texts if texts is not None else PREWARM_TEXTS):
            try:
                if self.get_cached(t) is None:
                    self.synthesize(t)
                ok += 1
            except Exception:
                # 预热失败不阻塞服务，运行时会按需重试
                pass
        return ok

    def close(self) -> None:
        self._cache.clear()
        try:
            self._session.close()
        except Exception:
            pass


def split_sentences(text: str) -> list[str]:
    """把一段回复切成适合 TTS 的短句（保留标点）。"""
    parts = re.split(r"(?<=[。！？；!?;])", text)
    out = []
    for p in parts:
        p = p.strip()
        if p:
            out.append(p)
    return out or ([text] if text else [])
