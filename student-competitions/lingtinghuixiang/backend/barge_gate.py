# -*- coding: utf-8 -*-
"""能量VAD抢话判定门：把「是否该打断」和「回合边界」绑在一起。

实机问题（2026/11/22 11:41:03，server_debug.log）：
    [11:41:03.778] TTS-EMIT epoch=11 chars=19
    [11:41:03.834] 首个音频块入队（RTC 下行轨）
    [11:41:03.843] VAD-BARGE rms=879  ← 9ms 后判「商家抢话」，整句一个音都没播

根因不是 TTS、也不是浏览器播放，而是打断判据与回合边界没对齐：
  1) 判据是「连续 3 个 100ms 上行块 RMS > 550」，比 ASR 定句慢约 300ms；
     商家说完一句（如「没有」）后服务端已开新一轮并开始播报，
     上一句的余音/上行积压这时才被处理，被算成「打断新一轮」。
  2) 原计数跨轮不清零：上一轮残留 1~2 个计数，新一轮「一个块就打断」。

本类负责三件事：
  - 维护「AI 本句是否在播」的窗口（结束语保护 / 冷却 / tts_count / play_end_at）；
  - 窗口由关→开（新一句开始）时清零计数并记 opened_at，窗口关闭时也清零；
  - 窗口开启后的 guard 秒（默认 0.25s）内不计入能量：
    这段时间麦克风里只可能是上一句的余音，AI 的新一句还没到耳朵。

阈值/帧数/守护窗默认值与旧版一致（550 / 3 / 0），只新增了「守护 0.25s」。
要调松紧，改构造函数参数即可，别再在 server.py 里散落常量。
"""


class BargeGate:
    """能量判据 + 回合对齐。纯内存、无 IO，可直接单测。"""

    def __init__(self, energy: int = 550, need: int = 3,
                 guard: float = 0.25, cooldown: float = 2.5):
        self.energy = int(energy)      # RMS 阈值（与旧版 _barge_energy 一致）
        self.need = max(1, int(need))  # 连续多少块才算打断（100ms/块 → 300ms）
        self.guard = max(0.0, float(guard))
        self.cooldown = float(cooldown)
        self.count = 0                 # 窗口内连续过阈值的块数
        self.opened_at = None          # 本句窗口打开的时刻（单调时钟）
        self.cooldown_until = 0.0      # 打断后的冷却截止
        self.barges = 0                # 本通电话累计打断次数（日志/看板可读）

    # ---------- 窗口 ----------
    def update(self, now: float, ai_playing: bool,
               protecting: bool = False, play_end_at: float = 0.0) -> bool:
        """每块上行音频进来先调用；返回窗口是否打开（=AI 本句在播）。"""
        open_ = (not protecting) and now >= self.cooldown_until and (
            bool(ai_playing) or now < (play_end_at or 0.0))
        if not open_:
            self.count = 0
            self.opened_at = None
            return False
        if self.opened_at is None:     # 由关→开：新一句开始，从零起算
            self.opened_at = now
            self.count = 0
        return True

    def reset(self) -> None:
        """新一轮开始（ASR 定句）时调用：上一轮的证据不得判到下一轮。"""
        self.count = 0
        self.opened_at = None

    # ---------- 观测 ----------
    def observe(self, now: float, rms: float) -> bool:
        """窗口内每块调用一次；返回 True = 本次应打断（内部已置冷却）。"""
        if self.opened_at is None:
            return False
        if now - self.opened_at < self.guard:
            return False               # 本句刚出声：只是上一句的余音，不算抢话
        if rms > self.energy:
            self.count += 1
            if self.count >= self.need:
                self.count = 0
                self.opened_at = None
                self.cooldown_until = now + self.cooldown
                self.barges += 1
                return True
        else:
            self.count = max(0, self.count - 1)
        return False
