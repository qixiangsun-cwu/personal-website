# -*- coding: utf-8 -*-
"""TTS 厂商选择器 —— 只决定「谁来念稿」，不碰 LLM、ASR、前端与埋点。

对 server.py 暴露与原版完全相同的 TTSClient / PREWARM_TEXTS，
因此 server.py 只需把导入行从 tts_zhipu 改到这里。

切换方式（backend/.env）：
    TTS_BACKEND=volcano   火山豆包语音合成 2.0（默认推荐，无开头水印音）
    TTS_BACKEND=zhipu     回到智谱 GLM-TTS（不填时即为此项，保持原行为）
"""

import os

from dotenv import load_dotenv

_HERE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_HERE, ".env"))
load_dotenv(os.path.join(_HERE, "..", "backend", ".env"))
load_dotenv()

_BACKEND = os.getenv("TTS_BACKEND", "zhipu").strip().lower()

if _BACKEND == "volcano":
    from tts_volcano import TTSClient
else:
    from tts_zhipu import TTSClient

from tts_zhipu import PREWARM_TEXTS

__all__ = ["TTSClient", "PREWARM_TEXTS"]
