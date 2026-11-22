# -*- coding: utf-8 -*-
"""火山引擎豆包流式语音识别（大模型 SAUC 2.0）WebSocket 客户端——已实测调通版。

端点: wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async
鉴权: 新版控制台 X-Api-Key（豆包语音控制台>API Key管理）+ X-Api-Resource-Id
资源: volc.seedasr.sauc.duration（豆包流式语音识别模型2.0·小时版，免费额度20小时）

【11/21 抗双讲移植】本文件在 11.19 WebRTC 弱网分支基础上并入第六代「音纹锁定 +
延迟释放修复」：纯 Python 自相关基频(F0)+过零率(ZCR)+频谱倾斜(tilt)三维音纹，
第一句 definite 锁定第一说话人（锁声音不锁内容），旁人句子零上屏/零落库/零触发；
utterance.end_time + 云端字节时钟精确定位句音频段提特征；15s 会话级判重挡幽灵
重句；会话重建保留音纹。离线仿真 13/13 验证（见 11.21/抗双讲_*_改动说明）。
与本分支原有保活/监督者/热词表完全兼容，WS 与 WebRTC 两条上行链路同样生效
（音纹逻辑全部在 ASR 层，与传输通道无关）。

协议要点（2026-11 实测验证，与官方 demo 对齐）：
1. V1 二进制帧：头(4字节) + [序号(4字节,若flags带序号)] + 载荷长度(4字节) + 载荷
2. 头布局: byte0=版本(1)<<4|头长(1)；byte1=消息类型<<4|flags；byte2=序列化<<4|压缩；byte3=保留
3. bigmodel_async 模式必须带递增序号：
   - 全量请求: mtype=0b0001, flags=POS_SEQUENCE(1), seq=1, JSON+gzip
   - 音频包:   mtype=0b0010, flags=POS_SEQUENCE(1), seq=2..N, RAW+gzip（200ms/包最优）
   - 最后一包: mtype=0b0010, flags=NEG_WITH_SEQUENCE(3), seq=-N(负), 载荷=gzip(b'')
4. 响应: mtype=0b1001 全量结果(JSON)；mtype=0b1111 错误（载荷=[4字节code]+[4字节size]+JSON）
5. 请求体: {"user":.., "audio":{format/rate/bits/channel}, "request":{model_name:"bigmodel",...}}
   —— 注意不是旧文档的 req_params 结构，enable_punc（非 enable_punct）

会话管理：监督者模式——致命错误（内部错误/超时/配额）自动重建新会话，
音频队列在重建期间持续缓冲，商家说话不中断。
"""

import asyncio
import gzip
import json
import os
import re
import struct
import time
import uuid
from collections import deque
from typing import Callable, Optional

import websockets

WS_URL = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async"
RESOURCE_ID_DEFAULT = "volc.seedasr.sauc.duration"

# 帧头常量（已实测）
MSG_FULL_REQ = 0b0001
MSG_AUDIO_REQ = 0b0010
MSG_SERVER_RESP = 0b1001
MSG_SERVER_ERR = 0b1111
F_NO_SEQ = 0b0000
F_POS_SEQ = 0b0001
F_NEG_SEQ = 0b0010
F_NEG_WITH_SEQ = 0b0011
SER_JSON = 0b0001
SER_RAW = 0b0000
COMP_GZIP = 0b0001

# 京东资审外呼领域词（热词上下文，提升识别准确率）
DOMAIN_CONTEXT = "京东商家资质审核外呼对话，涉及商标注册证、品牌授权书、授权链路、资质上传、发票资质、行业资质许可证、京麦后台、驳回原因、整改"


# ============================================================================
# 【11/21 九轮·内容轻量分流】纯本地正则判据，微秒级，绝不调用 LLM。
# 目的只有一个：给投机预生成引擎喂「干净的业务 partial」，让它在商家说话
# 期间提前备好答案，消除 definite 到达后现场等大模型的 16.8 秒。
# 严格边界：本判据只用于「partial 提速分流」+「definite 兜底加固」，
#          绝不参与认人——谁是目标说话人仍 100% 由音纹锁定决定（锁声音不锁内容）。
# 词表来源：复用 reply_policy 的业务信号/流程应答思路，这里内联一份轻量版，
#          避免 ASR 层依赖话术模块（仿真/单文件运行零外部依赖）。
# ============================================================================
_HINT_STRICT_NOISE_RE = re.compile(
    # 极窄噪音表：这些词绝不可能出现在"京东商家资质审核"业务里，
    # 仅用于 definite 兜底加固，误杀风险趋近于零。
    r"广播站|校园广播|学弟|学妹|学长|学姐|同学们|上课|下课|早读|晚自习|"
    r"作业|考试|宿舍|食堂|操场|社团|学生会|招生|毕业典礼|课间")
_HINT_NOISE_RE = re.compile(
    # 宽噪音表：partial 分流用，宁可多挡一句（只浪费一次投机预测，不上屏）。
    r"广播站|校园广播|学弟|学妹|学长|学姐|同学们|同学习|老师好|谢谢老师|"
    r"经济学院|工商管理|学院|专业|上课|下课|早读|晚自习|作业|考试|"
    r"宿舍|食堂|操场|社团|学生会|招生|毕业典礼|课间|讲座")
_HINT_BUSINESS_RE = re.compile(
    r"资质|审核|驳回|材料|补|提交|后台|京麦|商标|授权|类目|发票|申诉|费用|"
    r"复审|文审|品牌|入驻|店铺|证件|证明|执照|上传|修改|整改|过审|结果|"
    r"进度|通知|工单|热线|专员|工号|实名|结算|账号|法人|登录|注册|官网|"
    r"平台|规则|要求|标准|流程|疑问|问题|什么意思|怎么办|怎么弄|帮我|"
    r"公司|企业|负责人|商家|我想问|请问|咨询|了解一下")
_HINT_ACK_RE = re.compile(
    r"^(好的?|是的?|行|嗯+|哦+|噢+|对对?|明白了?|知道了?|清楚了?|懂了?|"
    r"没问题|好嘞|谢谢|多谢|感谢|喂|你好|您好|哈喽|我是|本人|没错|可以|"
    r"没有疑问|没什么问题|就这些|先这样|不用了|没了)[。！!？?,，、~…\s]*$")


def content_hint(text: str, strict_noise: bool = False) -> str:
    """内容轻量判据：返回 'noise' | 'business' | 'gray'。

    只做两件事：① partial 阶段决定是否喂投机引擎提前备答案；
    ② definite 阶段作为「音纹放行后的兜底加固」（仅 strict_noise=True 的极窄表）。

    绝不返回"放行/认人"结论——认人 100% 由音纹决定。这是用户硬口径：
    锁声音不锁内容，第一句话说什么都认定为第一个说话人的声音。
    """
    s = re.sub(r"[\s，,、。！!？?~…·\-—]", "", (text or "").strip())
    if not s:
        return "gray"
    noise_re = _HINT_STRICT_NOISE_RE if strict_noise else _HINT_NOISE_RE
    if noise_re.search(s):
        return "noise"
    if _HINT_BUSINESS_RE.search(s):
        return "business"
    if _HINT_ACK_RE.match(s):
        return "business"
    return "gray"


def load_volcano_conf() -> dict:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
    return {
        "app_key": os.getenv("VOLCANO_APP_KEY", ""),        # 新版控制台 API Key
        "app_id": os.getenv("VOLCANO_APP_ID", ""),          # 旧版 APP ID（可选）
        "access_key": os.getenv("VOLCANO_ACCESS_KEY", ""),  # 旧版 Access Token（可选）
        "resource_id": os.getenv("VOLCANO_RESOURCE_ID", RESOURCE_ID_DEFAULT),
    }


def _hdr(mtype: int, flags: int, serial: int, comp: int) -> bytes:
    return bytes([0x11, (mtype << 4) | flags, (serial << 4) | comp, 0x00])


def _frame_with_seq(mtype: int, seq: int, serial: int, comp: int, payload: bytes) -> bytes:
    return (_hdr(mtype, F_POS_SEQ, serial, comp)
            + struct.pack(">i", seq)
            + struct.pack(">I", len(payload))
            + payload)


def _build_request() -> bytes:
    req = {
        "user": {"uid": "zhiyu-agent", "did": "zhiyu-web"},
        "audio": {"format": "pcm", "codec": "raw", "rate": 16000, "bits": 16, "channel": 1},
        "request": {
            "model_name": "bigmodel",
            "enable_itn": True,
            "enable_punc": True,
            "enable_ddc": True,
            "show_utterances": True,      # 分句+说话人（双讲区分依赖此项）
            "enable_speaker_info": True,  # 双讲说话人分离
            "enable_nonstream": True,     # 二遍识别：实时上屏+句尾更准（保准确率，不关闭）
            # 【延时2秒】官方文档：end_window_size 是强制判停的静音阈值，默认 800ms。
            # 【11/14 支线实验】600ms → 450ms：再压 ASR 云端尾点（asr_tail 实测 950~1220ms，
            # 其中 600ms 是固定静音窗）。⚠️ 注意：≤400ms 会禁用语义分句（把
            # 「你好，我想问一下…」切碎成「你好。」），450ms 贴着安全下限，上线观察：
            # ① 句中停顿是否被误判截断（AI 抢话）② 识别文本是否被切碎。
            # 备份：支线_基于11.13.1/备份/asr_volcano.py.end_window600.bak
            "end_window_size": 450,
            # 注意：不要带 corpus/boosting 热词上下文——实测触发服务端 55000000 内部错误
            #
            # 【WebRTC 弱网·11/19】领域热词表（提升弱网丢字下的关键词识别）：
            # 正确姿势 = 火山控制台「自学习平台」创建热词表（含 商标注册证/授权书/资质
            # /驳回/京麦/类目/增值税发票/4006180092 等），把词表 ID 填入 .env：
            #   VOLCANO_ASR_BOOSTING_ID=xxxxxxxx
            # 未配置本 env 时不传该参数，行为与旧版完全一致（零风险）。
            # 配置后请先真机验证服务端不再返回 55000000 再用于比赛。
            **({"boosting_table_id": os.environ["VOLCANO_ASR_BOOSTING_ID"].strip()}
               if os.environ.get("VOLCANO_ASR_BOOSTING_ID", "").strip() else {}),
        },
    }
    return gzip.compress(json.dumps(req).encode())


def parse_response(raw: bytes) -> dict:
    """解析服务端响应帧（结果/错误）。"""
    if len(raw) < 8:
        return {}
    b1, b2 = raw[1], raw[2]
    mtype, flags, serial, comp = b1 >> 4, b1 & 0xF, b2 >> 4, b2 & 0xF
    rest = raw[4:]
    if mtype == MSG_SERVER_ERR:
        # 错误帧实测布局: [4字节code] + [4字节size] + JSON(明文)
        try:
            code = struct.unpack(">I", rest[:4])[0]
            size = struct.unpack(">I", rest[4:8])[0]
            msg = rest[8:8 + size].decode("utf-8", "ignore")
            return {"type": "error", "code": code, "msg": msg}
        except Exception:
            return {"type": "error", "code": -1, "msg": rest[:80].hex()}
    if mtype == MSG_SERVER_RESP:
        try:
            off = 4 if flags in (F_POS_SEQ, F_NEG_SEQ, F_NEG_WITH_SEQ) else 0
            seq = struct.unpack(">i", rest[:4])[0] if off else None
            size = struct.unpack(">I", rest[off:off + 4])[0]
            off += 4
            payload = rest[off:off + size]
            if comp == COMP_GZIP:
                payload = gzip.decompress(payload)
            body = json.loads(payload)
            return {"type": "result", "seq": seq, "body": body}
        except Exception as e:
            return {"type": "error", "code": -1, "msg": f"响应解析失败: {e}"}
    return {"type": "unknown", "mtype": mtype}


# ============================================================
# 【11/19 音纹锁定（方案A做实）】轻量声纹特征：基频(F0) + 过零率(ZCR)
# 纯 Python 实现（环境无 numpy），只在"确认句落地"和"TTS 播报中的音频帧"两处
# 按需计算，负载经实测控制在毫秒级，不阻塞音频转发。
# ============================================================
VOICE_SR = 16000                 # 前端上行的 PCM 采样率
VOICE_DS = 8000                  # 提特征前降采样到 8k（减一半计算量）
VOICE_FMIN, VOICE_FMAX = 60, 420 # 人声基频范围（Hz）
VOICE_WINDOW_MS = 50             # 特征窗长
VOICE_RMS_GATE = 900             # 有效声窗能量门限（16bit 平均幅值，与 server VAD 同源）


def _pcm16_to_samples(data: bytes, step: int = 2) -> list:
    """PCM16 字节 → 采样值列表，并按 step 抽取降采样（step=2 即 16k→8k）。"""
    n = len(data) // 2
    out = []
    for i in range(0, n, step):
        out.append(int.from_bytes(data[i * 2:i * 2 + 2], "little", signed=True))
    return out


def _zcr(x: list) -> float:
    """过零率：符号翻转次数占比（0~1）。清音/摩擦音占比高，是音色的粗指纹。"""
    if len(x) < 2:
        return 0.0
    cross = 0
    for i in range(1, len(x)):
        if (x[i] >= 0) != (x[i - 1] >= 0):
            cross += 1
    return cross / (len(x) - 1)


def _window_f0(x: list) -> Optional[float]:
    """单窗基频：自相关法（粗扫 lag 步长2 + 峰邻域精扫）。
    返回 Hz；无明显周期性（纯噪声/静音）返回 None。"""
    n = len(x)
    lag_min = VOICE_DS // VOICE_FMAX          # 8k/420 ≈ 19
    lag_max = VOICE_DS // VOICE_FMIN          # 8k/60  ≈ 133
    if n < lag_max * 2:
        return None
    mean = sum(x) / n
    # 去直流 + 能量（一次遍历）
    e = 0.0
    for v in x:
        d = v - mean
        e += d * d
    if e <= 0.0:
        return None
    best_lag, best_r = 0, 0.0
    # 粗扫
    for lag in range(lag_min, lag_max + 1, 2):
        s = 0
        for i in range(n - lag):
            s += x[i] * x[i + lag]
        if s > best_r:
            best_r, best_lag = s, lag
    if best_lag == 0:
        return None
    # 峰邻域 ±2 精扫，提精度到 ~±5Hz
    for lag in range(max(lag_min, best_lag - 2), min(lag_max, best_lag + 2) + 1):
        if lag == best_lag:
            continue
        s = 0
        for i in range(n - lag):
            s += x[i] * x[i + lag]
        if s > best_r:
            best_r, best_lag = s, lag
    # 【11/21 五轮·倍频校正】自相关峰在 2T 处可能与 T 处同高甚至更高
    # （包络起伏/叠加噪声下实测 240Hz 人声被报成 121Hz "半频"）——锁定
    # 参考一旦取到半频，整条音纹链路全部错位。若半周期 lag 处的相关值
    # 达到峰值 85% 以上，基频取更小的周期（真实基频优先）。
    if best_lag >= 2 * lag_min:
        half = best_lag // 2
        best_half_r, best_half_lag = 0.0, 0
        for lag in range(max(lag_min, half - 2), min(lag_max, half + 2) + 1):
            if lag == best_lag:
                continue
            s = 0
            for i in range(n - lag):
                s += x[i] * x[i + lag]
            if s > best_half_r:
                best_half_r, best_half_lag = s, lag
        if best_half_lag and best_half_r >= best_r * 0.85:
            best_r, best_lag = best_half_r, best_half_lag
    # 归一化自相关强度：太低说明不是周期信号（噪声），不可信
    norm = best_r / e
    if norm < 0.30:
        return None
    return VOICE_DS / best_lag


def _window_f0_clarity(x: list) -> tuple:
    """【11.21-17 B版·响度/清晰度裁决】单窗基频 + 清晰度(norm)。

    清晰度 = 归一化自相关峰强度 best_r/e（0~1）：周期信号（清晰人声）高，
    宽带噪声/含混叠播低。与 _window_f0 同一套自相关、同一倍频/半频校正，
    只是额外把 norm 带出来。返回 (f0_or_None, norm)。
    仅 B 版双讲裁决调用，A 版路径完全不走这里——零风险。
    """
    n = len(x)
    lag_min = VOICE_DS // VOICE_FMAX
    lag_max = VOICE_DS // VOICE_FMIN
    if n < lag_max * 2:
        return None, 0.0
    mean = sum(x) / n
    e = 0.0
    for v in x:
        d = v - mean
        e += d * d
    if e <= 0.0:
        return None, 0.0
    best_lag, best_r = 0, 0.0
    for lag in range(lag_min, lag_max + 1, 2):
        s = 0
        for i in range(n - lag):
            s += x[i] * x[i + lag]
        if s > best_r:
            best_r, best_lag = s, lag
    if best_lag == 0:
        return None, 0.0
    for lag in range(max(lag_min, best_lag - 2), min(lag_max, best_lag + 2) + 1):
        if lag == best_lag:
            continue
        s = 0
        for i in range(n - lag):
            s += x[i] * x[i + lag]
        if s > best_r:
            best_r, best_lag = s, lag
    if best_lag >= 2 * lag_min:
        half = best_lag // 2
        best_half_r, best_half_lag = 0.0, 0
        for lag in range(max(lag_min, half - 2), min(lag_max, half + 2) + 1):
            if lag == best_lag:
                continue
            s = 0
            for i in range(n - lag):
                s += x[i] * x[i + lag]
            if s > best_half_r:
                best_half_r, best_half_lag = s, lag
        if best_half_lag and best_half_r >= best_r * 0.85:
            best_r, best_lag = best_half_r, best_half_lag
    norm = best_r / e
    if norm < 0.30:
        return None, norm
    return VOICE_DS / best_lag, norm


def frame_f0(pcm16_bytes: bytes) -> Optional[float]:
    """帧级粗基频（供 server 能量 VAD 联动）：取 ≤800 样本(50ms@16k)降采样后
    自相关。返回 Hz 或 None（静音/无周期性）。计算量 ~数千次乘加，微秒级。"""
    x = _pcm16_to_samples(pcm16_bytes[:1600])
    if len(x) < 40:
        return None
    return _window_f0(x)


def segment_voice_feats(pcm16_bytes: bytes) -> Optional[tuple]:
    """句级音纹特征：对一段 PCM（取尾部 ~1.6s）分窗提 F0/ZCR/频谱倾斜，
    取有声窗的中位数 → (f0, zcr, tilt, spread)；有效声窗不足返回 None。

    tilt（频谱倾斜）= 一阶差分信号能量 / 原信号能量（高频占比的粗测度，
    纯 Python O(n)）。它抓的是音色（共振峰结构/气流噪声）而非音高——
    【11/19 五次修复】实测两个不同人音高落进 F0 模糊区（相对差 22%~38%）
    时，旧版只靠 ZCR 联判会误放行；tilt 是第三个独立维度，模糊区必须
    ZCR+tilt 都像才算同一人。

    spread（F0 四分位距/中位数）【11/21 五轮】：有声窗基频的离散度。
    一个人的稳定浊音窗 F0 波动通常 <15%；窗口里混进第二个人（用户在
    播音声中说话/播音在用户声音上叠播）会出现两个 F0 簇 → spread 飙高。
    锁定时用它把关：spread 大 = 参考被污染，拒绝入锁。
    """
    tail = pcm16_bytes[-int(VOICE_SR * 2 * 1.6):]     # 最近 1.6s
    x = _pcm16_to_samples(tail)
    win = VOICE_DS * VOICE_WINDOW_MS // 1000          # 50ms@8k = 400 样本
    hop = win // 2
    f0s, zcrs, tilts = [], [], []
    i = 0
    while i + win <= len(x):
        w = x[i:i + win]
        rms = (sum(v * v for v in w) / win) ** 0.5
        if rms > VOICE_RMS_GATE:
            f0 = _window_f0(w)
            if f0 is not None:
                # 频谱倾斜：差分能量比（高频占比）。尺度无关，不随音量变化。
                diff_rms = (sum((w[j] - w[j - 1]) ** 2 for j in range(1, win))
                            / (win - 1)) ** 0.5
                f0s.append(f0)
                zcrs.append(_zcr(w))
                tilts.append(diff_rms / max(rms, 1.0))
        i += hop
    if not f0s:
        return None
    f0s.sort(); zcrs.sort(); tilts.sort()
    m = len(f0s) // 2
    # F0 四分位距 / 中位数（len<4 时无法可靠估计离散度，记 0 = 不否决）
    # 【11.21-15】第 5 维 out = 偏离中位数 >25% 的有声窗占比（双峰/离群检测）。
    # 单人说话 F0 连续滑动、离群极少（真实语音实测 all ≤0.10）；
    # 两人叠播出现第二个基频簇 → 一批窗远离中位数 → out 飙高（双讲实测中位 0.23）。
    # out 与 spread 是互补的独立信号：spread 抓"整体变散"，out 抓"另一簇的人"。
    if len(f0s) >= 4:
        q1 = f0s[len(f0s) // 4]
        q3 = f0s[(len(f0s) * 3) // 4]
        med = f0s[m]
        spread = (q3 - q1) / max(med, 1.0)
        out = sum(1 for f in f0s if abs(f - med) / max(med, 1.0) > 0.25) / len(f0s)
    else:
        spread = 0.0
        out = 0.0
    return (f0s[m], zcrs[m if len(zcrs) > m else 0],
            tilts[m if len(tilts) > m else 0], spread, out)


def segment_voiced(pcm16_bytes: bytes) -> bool:
    """这段音频里是否存在"真的有人声能量"的窗（RMS 过门限）。
    【11/19 五次修复】云喘对静音也会吐 definite（幻影定句）：这种句子既不该上屏、
    更不该拿去做音纹判定——用能量门限把它识别出来直接丢弃。"""
    tail = pcm16_bytes[-int(VOICE_SR * 2 * 1.6):]
    x = _pcm16_to_samples(tail)
    win = VOICE_DS * VOICE_WINDOW_MS // 1000
    hop = win // 2
    i = 0
    while i + win <= len(x):
        w = x[i:i + win]
        rms = (sum(v * v for v in w) / win) ** 0.5
        if rms > VOICE_RMS_GATE:
            return True
        i += hop
    return False


# B版响度/清晰度裁决常量。只有"本人明显占优"才让整句上屏；
# 势均力敌或旁人占优一律拦（噪音绝不上屏的红线不破）。
DOM_LOUD_MIN = 1.30        # 本人响度/旁人响度 需 > 1.30（本人明显更响）
DOM_CLAR_MIN = 0.98        # 本人清晰度/旁人清晰度 需 > 0.98（本人不比旁人糊）
DOM_MIN_OTHER = 1          # 旁人簇至少 1 个有声窗才算"有第二个人"（否则判不出双讲）
DOM_MIN_SELF = 2           # 本人簇至少 2 窗才够可信


def segment_dominance(pcm16_bytes: bytes, ref_f0: Optional[float],
                      ref_zcr: Optional[float], ref_tilt: Optional[float]) -> Optional[dict]:
    """【11.21-17 B版】逐窗把一句混音拆成"本人簇/离群簇"，比较谁响、谁清晰。

    判定规则（窗级，独立于整句 f0 中位）：
      - 每窗算 (f0, norm=清晰度, rms=响度)；只有过能量门且有周期性的窗计入。
      - 窗 f0 与锁定参考在八度容差内（含 2x/0.5x）→ 本人簇；否则 → 离群簇（旁人/噪音）。
      - 分别累计：响度 = 簇内窗 RMS 之和；清晰度 = 簇内窗 norm 的均值。
    返回 dict(self_loud, other_loud, self_clar, other_clar, n_self, n_other)；
    像本人窗不足返回 None（无裁决依据，交给上层保守处理）。
    """
    tail = pcm16_bytes[-int(VOICE_SR * 2 * 1.6):]
    x = _pcm16_to_samples(tail)
    win = VOICE_DS * VOICE_WINDOW_MS // 1000
    hop = win // 2
    self_loud = other_loud = 0.0
    self_clar_sum = other_clar_sum = 0.0
    n_self = n_other = 0
    i = 0
    while i + win <= len(x):
        w = x[i:i + win]
        rms = (sum(v * v for v in w) / win) ** 0.5
        if rms > VOICE_RMS_GATE:
            f0, clarity = _window_f0_clarity(w)
            if f0 is not None and ref_f0:
                # 只认同频与 2x（本人真声可能是半频锁定的 2 倍）；
                # 绝不认 ref/2——男播音 119Hz ≈ ref240/2，认了就会把旁人
                # 误归本人簇（与 11.21-12 双讲差频污染同源的陷阱）。
                rel = min(abs(f0 - ref_f0) / max(ref_f0, 1.0),
                          abs(f0 - 2 * ref_f0) / max(2 * ref_f0, 1.0))
                is_self = rel <= 0.25
            else:
                is_self = False
            if is_self:
                self_loud += rms
                self_clar_sum += clarity
                n_self += 1
            else:
                other_loud += rms
                other_clar_sum += clarity
                n_other += 1
        i += hop
    if n_self < 1:
        return None
    return {
        "self_loud": self_loud, "other_loud": other_loud,
        "self_clar": self_clar_sum / n_self, "other_clar": other_clar_sum / max(n_other, 1),
        "n_self": n_self, "n_other": n_other,
    }


class VolcanoASRSession:
    """一通通话对应一个 ASR 会话（asyncio 内使用），监督者模式：致命错误自动重建新会话。

    回调均在事件循环内调用：
      on_partial(text)       中间识别结果（实时字幕）
      on_final(text, conf)   一句话结束（触发 Agent 处理）
    """

    # 致命错误码：会话已死，必须重建（55000000=服务内部错误；45000081=等音频超时）
    FATAL_CODES = {55000000, 45000081, 45000001, 45000202}
    FATAL_KEYWORDS = ("internal error", "new session", "timeout", "invalid", "exceed", "quota")

    # 【链路保活】200ms @16k/16bit/mono 的全零 PCM（6400 B）。
    # 火山 bigmodel_async 在长时间收不到音频包时会以 45000081 关闭会话：
    # 「Timed out waiting for the next audio packet」。真实麦克风静音时浏览器本来
    # 就在持续发全零包，但标签切后台被节流、AudioContext 挂起、设备暂时无数据时
    # 推流会停，后端 _sender 队列一空就一个包都不发，云端随即超时并反复重建，
    # 商家端刷出英文报错。这里在空闲时补一帧等时长静音，等价于「麦克风正常但
    # 没人说话」；真实音频到达时优先发真实包，保活帧不挤占音频队列。
    KEEPALIVE_INTERVAL = 0.2
    KEEPALIVE_PCM = b"\x00" * 6400

    def __init__(self, call_id: str, on_partial: Callable, on_final: Callable,
                 on_speaker_event: Optional[Callable] = None,
                 on_spec_hint: Optional[Callable] = None):
        self.call_id = call_id
        self.on_partial = on_partial
        self.on_final = on_final
        # 【11/21 九轮】投机专用旁路：只把"像业务的 partial"喂给投机引擎提前备答案。
        # 与 on_partial 完全分离——on_partial 会渲染上屏，这个绝不上屏、不落库。
        # 可选参数：不传时提速功能静默关闭，行为与上一版完全一致（零风险回退）。
        self.on_spec_hint = on_spec_hint
        # 【11/19 抗双讲】默认关闭：关闭时行为与改造前逐字一致，零风险回退。
        # 开启后以「第一句 definite 的说话人」为目标，只放行目标说话人的文本；
        # 旁人的句子直接丢弃（不上字幕、不触发 on_final、不落库、不触发回复）。
        self.anti_doubletalk = False
        self._target_speaker: Optional[str] = None
        # 【11.21-15 噪音绝不上屏】锁定瞬间的说话人编号（身份锚点，全程不被后续句更新带跑）。
        # 只用于双讲灰区"编号冲突"加固，从不做放行依据——编号漂移只会让本人被误拦，
        # 绝不会让旁人溜进来；且灰区还要求 spread 偏高，双重证据才触发。
        self._locked_speaker: Optional[str] = None
        self.on_speaker_event = on_speaker_event   # (event, speaker, text) -> None
        # 【11/19 音纹锁定（方案A做实）】speaker_id 是"会话内第几个开口的人"，不是声纹：
        # 目标沉默时旁人拿同号穿透、编号漂移时目标被误杀——纯编号过滤做不到"只认第一句话的人"。
        # 现改为：第一句 definite 落地时，从该句音频段提取基频(F0)+过零率(ZCR)作为目标音纹参考；
        # 之后每个确认句都做音纹比对——像目标才放行，不像一律静默拦截（无论编号是多少）。
        # 无需预注册（首句自学习），保留"任意电脑可复现"；参考只在会话重建/手动开关时重建。
        self._ref_f0: Optional[float] = None       # 目标基频参考（Hz），None=尚未锁定
        self._block_streak = 0                     # 连续被判"旁人"的次数（防永久锁死）
        self._ref_zcr: Optional[float] = None      # 目标过零率参考
        self._ref_tilt: Optional[float] = None     # 目标频谱倾斜参考（音色指纹）
        # 【11/21 八轮·半频自愈】首句超短(「是的」)时自相关易报半频(240→120)，
        # 参考记错后男播音115Hz 会被当本人放行。标记短句锁定为"待校准"，
        # 后续若出现八度关系且音色同人 → 自动把参考纠正到真实基频。
        self._ref_oct_unconfirmed = False
        # 【11.21-12】升频自愈不受此计数限制（升频方向安全、纠正到真频后 sim=True
        # 自然停止）；保留字段仅为兼容旧诊断/开关重置。11.21-8 曾用它限制"双向"
        # 八度纠正，而其中的降频分支正是双讲污染参考的根因，已在本版删除。
        self._OCT_CORRECT_MAX = 2
        self._pcm_ring = bytearray()               # 最近 ~30s 原始音频（16k 16bit），供句尾提特征
        self._PCM_RING_MAX = 32000 * 30            # 32000 B/s × 30s
        self.ws = None
        self._run_task: Optional[asyncio.Task] = None
        self._audio_queue: asyncio.Queue = asyncio.Queue(maxsize=300)  # ~60s缓冲
        self._last_final = ""
        self._last_final_at: float = 0.0       # 【11/19 三次修复】上一句 final 的时刻
        self.FINAL_DEDUP_WINDOW = 3.0          # 3 秒内的重复/渐进 definite 合并为一句
        # 【11/19 五次修复·噪音延迟释放】会话级精确判重：AI 播报十几秒后云端才补发
        # 的重复 definite（旧句重放）早已出 3s 窗口，是「重复上一句」的直接来源之一。
        # 凡 15s 内已经落地过的同一句，一律不再落地。
        self.DUP_SUPPRESS_SEC = 15.0
        self._emitted_hist: deque = deque(maxlen=64)   # (norm, t) 已落地文本历史
        # 【11/21 诊断版】纯旁路判决日志：每个 definite 记录实测特征与判定依据，
        # 不参与任何拦截/放行逻辑，只供前端"导出诊断"复盘真实链路。最多留 80 条。
        self.diag_log: deque = deque(maxlen=80)
        self._diag_t0 = time.monotonic()
        # 【11/19 五次修复】云端音频时钟：本会话已发送到云端的音频字节数。
        # utterances[].end_time 是相对音频流起点的毫秒数——有了这个时钟才能把
        # 「提音纹特征」精确定位到那句话自己的音频段，而不是"此刻的尾巴"
        # （云端结果延迟 1~3s，此刻尾巴可能已是静音/AI 回声/噪音，判定必错位）。
        self._fed_bytes = 0        # 已发送的真实+静音音频字节
        self._ka_bytes = 0         # 其中纯保活静音字节（环形缓存里没有这段，映射要扣除）
        # 【11/21 四轮·根因B】AI 播报状态时钟：server 每下发一帧 TTS 音频就 mark 一次。
        # 锁定候选只认「AI 闭嘴期间的应答」——AI 播报中（含 1.5s 尾音余量）到达的
        # definite 是回声/旁人抢先（接通即放播音的真实时序），一律丢弃不入锁不上屏。
        self._ai_last_speak_at: float = 0.0
        self.AI_SPEAK_GUARD_SEC = 0.9
        # 【11.21-12 首句打断兜底】VAD 打断成功时 server 设此截止时刻；宽限期内
        # ai_echo 分支不因"AI播报中"丢弃商家 definite——保住首句插话，避免残留帧
        # 刷新时钟把首句当回声吞掉（根因见 server.py 打断块注释）。
        self._barge_grace_until: float = 0.0
        self._seq = 0
        self.closed = False
        self._session_dead: asyncio.Event = asyncio.Event()
        self._restarts = 0
        self._last_error_code = 0          # 供监督者区分「空闲超时」与真实故障
        self.conf = load_volcano_conf()
        self.available = bool(self.conf["app_key"])

    async def start(self) -> None:
        if not self.available:
            raise RuntimeError("火山ASR未配置：需要 VOLCANO_APP_KEY（豆包语音控制台 API Key）")
        asyncio.create_task(self._sender())      # 常驻发送协程（跨会话重建存活）
        asyncio.create_task(self._supervise())

    async def _connect_once(self) -> None:
        """建立一次底层会话：连接→发全量请求→起接收循环。"""
        headers = {"X-Api-Key": self.conf["app_key"],
                   "X-Api-Resource-Id": self.conf["resource_id"],
                   "X-Api-Request-Id": str(uuid.uuid4())}
        if self.conf.get("app_id") and not self.conf["app_key"]:
            headers.pop("X-Api-Key")
            headers["X-Api-App-Key"] = self.conf["app_id"]
            headers["X-Api-Access-Key"] = self.conf["access_key"]
        self.ws = await websockets.connect(WS_URL, additional_headers=headers, max_size=None)
        self._seq = 1
        # 【11/19 五次修复·根因之一】旧版在这里把 _ref_f0 一并清空——实测这是
        # 「AI 说完话后噪音影响被释放出来」的根因：长通话中云端会话重建（保活超时/
        # 网络）后，第一句 definite（往往是云端延迟补发的旧句或正在播放的噪音）直接
        # 走「尚未锁定→建立参考」分支，既把旧句重复上屏、又把音纹错锁到噪音身上，
        # 之后噪音全部被当成目标放行。
        # 实际上音纹参考来自原始音频的 F0/ZCR，与会话无关——重建后目标还是同一副
        # 嗓子，参考必须保留。只清编号（新会话重新分配）、重置云端音频时钟；
        # 判重历史(_last_final/_emitted_hist)也保留，杜绝重建后旧 definite 重放被
        # 当成"第一句"重新入锁。
        if self.anti_doubletalk and (self._target_speaker is not None
                                     or self._ref_f0 is not None):
            self._target_speaker = None
            self._fed_bytes = 0
            self._ka_bytes = 0
            self._emit_speaker("relock", None, "语音链路已重建，音纹保持不变")
        await self.ws.send(_frame_with_seq(MSG_FULL_REQ, self._seq, SER_JSON, COMP_GZIP,
                                           _build_request()))
        self._run_task = asyncio.create_task(self._run())

    async def _supervise(self) -> None:
        """监督循环：会话死亡→自动重建（最多12次），音频队列在重建期间持续缓冲。"""
        while not self.closed:
            self._session_dead.clear()
            try:
                await self._connect_once()
            except Exception as e:
                await self._safe_call(self.on_partial, f"[ASR] 连接失败将重试: {str(e)[:80]}")
                await asyncio.sleep(2)
                continue
            await self._session_dead.wait()      # 等本次会话结束（正常/异常）
            if self.closed:
                break
            self._restarts += 1
            if self._restarts > 12:
                await self._safe_call(self.on_partial,
                                      "[ASR] 反复失败已放弃重建，请检查配额或网络后再试")
                break
            # 45000081（等音频超时）已由静音保活兜底，偶发时静默重建即可；
            # 其余故障才提示用户，避免「一打开页面就刷两条 ASR 报错/重建」。
            idle_restart = self._last_error_code == 45000081
            self._last_error_code = 0
            if not idle_restart:
                await self._safe_call(self.on_partial,
                                      f"[ASR] 语音链路已自动恢复（第{self._restarts}次，说话不受影响）")
            try:
                await self.ws.close()
            except Exception:
                pass
            await asyncio.sleep(1.2)

    async def feed_audio(self, pcm16_bytes: bytes) -> None:
        if not self.closed:
            # 【11/19 音纹锁定】环形缓存最近 ~30s 原始音频；确认句落地时取"刚说完
            # 这句话的那段音频"提基频/过零率做音纹比对。extend+截尾，均摊 O(1)。
            if self.anti_doubletalk:
                self._pcm_ring.extend(pcm16_bytes)
                overflow = len(self._pcm_ring) - self._PCM_RING_MAX
                if overflow > 0:
                    del self._pcm_ring[:overflow]
            try:
                self._audio_queue.put_nowait(pcm16_bytes)
            except asyncio.QueueFull:
                try:
                    self._audio_queue.get_nowait()   # 丢最旧，保实时
                except Exception:
                    pass
                try:
                    self._audio_queue.put_nowait(pcm16_bytes)
                except Exception:
                    pass

    async def finish(self) -> None:
        if not self.closed:
            await self._audio_queue.put(None)  # 哨兵 → 发送最后一包

    # ---------------- 抗双讲：目标说话人锁定（11/19）----------------
    @staticmethod
    def _speaker_of(u: dict) -> Optional[str]:
        """取分句的说话人编号，兼容火山各接口字段位置。

        官方文档：enable_speaker_info 开启后编号在 utterances[].additions.speaker；
        单向 bigmodel_async 亦可能直接给 utterances[].speaker / speaker_id。
        统一转成字符串；取不到返回 None（此时保守放行，避免误杀目标）。
        """
        add = u.get("additions") or {}
        for v in (add.get("speaker"), add.get("speaker_id"),
                  u.get("speaker"), u.get("speaker_id")):
            if v is not None and str(v) != "":
                return str(v)
        return None

    def _emit_speaker(self, event: str, speaker: Optional[str], text: str = "") -> None:
        """把锁定/拦截事件上报给上层（用于前端提示与演示可见性），失败不影响主链路。"""
        if self.on_speaker_event:
            try:
                result = self.on_speaker_event(event, speaker, text)
                if asyncio.iscoroutine(result):
                    asyncio.create_task(result)
            except Exception:
                pass

    # ---------------- 诊断（11/21 诊断版·纯旁路，不改判定）----------------
    def _diag(self, event: str, u: dict, feats, sim, extra: str = "") -> None:
        """记录一条 definite 判决依据。绝不改变放行/拦截行为。"""
        try:
            def rnd(v, n=1):
                return round(v, n) if isinstance(v, (int, float)) else None
            f0 = feats[0] if feats else None
            zcr = feats[1] if feats else None
            tilt = feats[2] if feats else None
            spread = feats[3] if (feats and len(feats) > 3) else None
            out = feats[4] if (feats and len(feats) > 4) else None
            rel = (abs(f0 - self._ref_f0) / max(self._ref_f0, 1.0)
                   if (f0 is not None and self._ref_f0) else None)
            end_ms = rnd(u.get("end_time"))
            self.diag_log.append({
                "t": round(time.monotonic() - self._diag_t0, 2),
                "ev": event,
                "txt": (u.get("text") or "")[:26],
                "spk": self._speaker_of(u),
                "def": bool(u.get("definite", False)),
                "conf": rnd(u.get("confidence"), 2),
                "end_ms": end_ms,
                "f0": rnd(f0), "zcr": rnd(zcr, 3), "tilt": rnd(tilt, 3),
                "spread": rnd(spread, 3), "out": rnd(out, 3),
                "ref_f0": rnd(self._ref_f0),
                "ref_zcr": rnd(self._ref_zcr, 3),
                "ref_tilt": rnd(self._ref_tilt, 3),
                "rel": rnd(rel, 3),
                "sim": sim,
                "ai_speaking": self.ai_speaking,
                "extra": extra,
            })
        except Exception:
            pass

    def diag_snapshot(self) -> dict:
        """导出诊断快照：当前锁定状态 + 全部判决日志。供前端一键复制复盘。"""
        return {
            "call_id": self.call_id,
            "anti_doubletalk": self.anti_doubletalk,
            "locked": self._ref_f0 is not None,
            "ref_f0": round(self._ref_f0, 1) if self._ref_f0 else None,
            "ref_zcr": round(self._ref_zcr, 3) if self._ref_zcr else None,
            "ref_tilt": round(self._ref_tilt, 3) if self._ref_tilt else None,
            "target_speaker": self._target_speaker,
            "F0_NEAR": self.F0_NEAR, "F0_MID": self.F0_MID, "F0_FAR": self.F0_FAR,
            "ring_sec": round(len(self._pcm_ring) / 32000.0, 1),
            "fed_sec": round(self._fed_bytes / 32000.0, 1),
            "events": list(self.diag_log),
        }

    def set_anti_doubletalk(self, enabled: bool, relock: bool = True) -> None:
        """运行时开关抗双讲；开启/重新开启时清空锁定，让下一句 definite 重新选目标。

        relock=False 用于通话中只切开关不改变已锁定的目标。
        """
        self.anti_doubletalk = bool(enabled)
        if relock and self.anti_doubletalk:
            self._target_speaker = None
            self._locked_speaker = None
            self._ref_f0 = None
            self._ref_zcr = None
            self._ref_tilt = None
            self._ref_oct_unconfirmed = False
            self._OCT_CORRECT_MAX = 2
        if not self.anti_doubletalk:
            self._target_speaker = None
            self._locked_speaker = None
            self._ref_f0 = None
            self._ref_zcr = None
            self._ref_tilt = None
            self._ref_oct_unconfirmed = False
            self._OCT_CORRECT_MAX = 2
        # 开关切换=新一轮，清空 final 判重窗口与历史，避免新锁定首句被旧窗口吞
        self._last_final = ""
        self._last_final_at = 0.0
        self._emitted_hist.clear()

    def mark_ai_speaking(self) -> None:
        """【11/21 四轮】server 每下发一帧 TTS 音频调用一次：记录 AI 播报时钟。"""
        self._ai_last_speak_at = time.monotonic()

    @property
    def ai_speaking(self) -> bool:
        """AI 是否正在播报（含尾音余量）。锁定候选只认 AI 闭嘴期间的应答。"""
        return (time.monotonic() - self._ai_last_speak_at) <= self.AI_SPEAK_GUARD_SEC

    # ---------------- 音纹比对（11/19 方案A做实 + 五次修复）----------------
    F0_NEAR = 0.18      # 相对差 ≤18% → 同一人（句级中位基频的稳定波动范围）
    F0_MID = 0.30       # 模糊区内：相对差 ≤30% 才允许靠音色联判放行（五次修复）
    F0_FAR = 0.38       # 相对差 ≥38% → 肯定不是同一人
    ZCR_NEAR = 0.30     # 模糊区联判：ZCR 相对差 ≤30% 记"像"一票
    ZCR_FAR = 0.90      # ZCR 相对差 ≥90% → 一票否决（肯定不像）
    TILT_NEAR = 0.25    # 模糊区联判：频谱倾斜相对差 ≤25% 记"像"一票
    TILT_FAR = 0.60     # 频谱倾斜相对差 ≥60% → 一票否决
    # 【11.21-15 噪音绝不上屏】双讲混音句否决阈值。
    # spread = 一句音频窗内 F0 的四分位距/中位数，即"这一句里基频有多散"。
    # 一个人稳定说话 spread 低（真实 TTS 语音实测中位 0.167、0/10 超 0.35）；
    # 两人同时说话出现两个基频簇 → spread 飙高（实测中位 0.291、最高 0.671）。
    # 0.35 是实测单人零误判的保守线：超线即判"混了第二个人"→ 整句拦，
    # 噪音一个字不上屏；代价是这句里本人的话也一起丢（需安静时重说一遍）。
    DTALK_SPREAD_MAX = 0.35
    # 第 5 维 out（离群窗占比）硬闸：真实单人语音实测全部 ≤0.10，
    # 双讲混音中位 0.23。0.20 是单人零误判线，超线即判混入第二人。
    DTALK_OUT_MAX = 0.20

    def voice_match_f0(self, f0: Optional[float]) -> Optional[bool]:
        """帧级音纹判定（供 server 能量VAD联动）。
        返回 True=像目标 / False=不像（拦断打断计数）/ None=无法判定（维持原逻辑）。"""
        if not self.anti_doubletalk or self._ref_f0 is None or f0 is None:
            return None
        rel = abs(f0 - self._ref_f0) / max(self._ref_f0, 1.0)
        if rel <= self.F0_NEAR:
            return True
        if rel >= self.F0_FAR:
            return False
        return None   # 模糊区：不拦，保持可用性

    def voice_match_f0_for_barge(self, f0: Optional[float]) -> Optional[bool]:
        """【11/21 十轮·打断专用】打断门的音纹判据，与"文字拦截门"刻意分离。

        核心原则：打断是基本指标（本人必须随时能掐断 AI），绝不能因音纹判不准
        而拖慢响应。旁人即使偶尔掐断 AI，其文字仍在 definite 层被音纹拦截——
        不上屏、不落库、不触发 AI——所以打断门放宽不会破坏抗双讲底线。

        与 voice_match_f0 的区别：兼容八度关系。首句超短时参考可能被记成半频
        （本人真声 240Hz、参考记成 120Hz，rel=100% 会被旧判据判"明确不像"→
        拒绝打断，这正是"手机打不断"的根因）。这里只要 f0 与参考构成
        近似同频 / 八度 / 半频关系，一律放行打断。
        """
        if not self.anti_doubletalk or self._ref_f0 is None or f0 is None:
            return None
        ref = self._ref_f0
        # 与参考同频（放宽到 45%，覆盖手机/蓝牙麦的基频漂移）
        if abs(f0 - ref) / max(ref, 1.0) <= 0.45:
            return True
        # 八度兼容只在「参考可能是半频误记」时启用（首句超短、待校准）：
        # 此时本人真声=2×ref 需放行。参考已确认（长句锁定/半频自愈已纠正）时
        # 绝不启用——否则男播音 115Hz≈女声 240Hz 的半频会被误放行打断 AI。
        if getattr(self, "_ref_oct_unconfirmed", False):
            if abs(f0 - 2 * ref) / max(2 * ref, 1.0) <= 0.20:
                return True
        # 明确是另一个人 → 拒绝打断
        return False

    def _match_voice(self, f0: Optional[float], zcr: Optional[float],
                     tilt: Optional[float]) -> Optional[bool]:
        """句级音纹判定。True=目标 / False=旁人 / None=特征无效或模糊区联判不过。

        三维判定：F0（音高）直接判定像/不像；中间模糊区（实测两个不同人可能
        落进来）必须 ZCR（清音质感）+ tilt（音色共振峰）联判——都像才放行，
        任一强不符即拦截。
        【11/19 五次修复】旧版模糊区只看 ZCR 且容差 45%，实测男声 180Hz vs
        119Hz（相对差 35%）被误放行——这就是"AI 说完后噪音内容冒出来"的
        另一个根因：噪音人音高离目标不近不远时 gate 形同虚设。
        【11/21 二轮】None 的语义从"走编号兜底"改为"拦截"：编号在会话重建/
        漂移下不可靠，宁可拦错（商家重说一遍）不可放过（噪音冒充商家）。
        """
        if f0 is None:
            return None
        rel = abs(f0 - self._ref_f0) / max(self._ref_f0, 1.0)
        if rel <= self.F0_NEAR:
            return True
        if rel >= self.F0_FAR:
            return False
        zrel = (abs(zcr - self._ref_zcr) / max(self._ref_zcr, 0.01)
                if (self._ref_zcr and zcr is not None) else None)
        trel = (abs(tilt - self._ref_tilt) / max(self._ref_tilt, 0.01)
                if (self._ref_tilt and tilt is not None) else None)
        if zrel is not None and zrel >= self.ZCR_FAR:
            return False
        if trel is not None and trel >= self.TILT_FAR:
            return False
        # 音色联判放行的前提：音高差 ≤30%（音高是最稳的判据，差 30% 以上
        # 无论如何不该放行——实测男声 180Hz vs 119Hz 差 34% 曾靠音色混过）
        if (rel <= self.F0_MID
                and zrel is not None and zrel <= self.ZCR_NEAR
                and (trel is None or trel <= self.TILT_NEAR)):
            return True
        return None

    # ---------------- utterance 定位提特征（11/19 五次修复 + 11/21 二轮）----------------
    BYTES_PER_MS = 32       # 16kHz × 16bit × mono = 32 字节/毫秒
    UTTER_WIN_MS = 1600     # 提特征窗口：utterance 结束点往前 1.6s
    STALE_MS = 5000         # 【11/21 二轮】definite 晚于其 end_time 5s 以上 = 延迟重发定句

    def _utterance_feats(self, u: dict) -> tuple:
        """取 utterance「自己那段音频」的音纹特征 + 是否真有人声能量 + 是否陈旧。

        【11/19 五次修复·根因之二】旧版固定取环形缓存"此刻的尾巴 1.6s"提特征，
        但云端 definite 结果延迟 1~3s 才到——此刻尾巴早已是静音/AI 回声/另一段
        噪音，判定必错位：噪音句在静音尾巴上拿不到特征 → 落到编号兜底被放行；
        商家自己的句子反而被 AI 回声尾巴判成旁人。
        现在用云端音频时钟 _fed_bytes 把 utterances[].end_time 映射到环形缓存的
        精确位置，特征永远来自那句话自己的音频；end_time 缺失时才退回尾巴。

        【11/21 二轮·根因之四】真实通话实测：AI 长播报后云端把十几秒前的旧句
        （"是的"/"好的，谢谢老师"）重新 definite 一遍——延迟远超 3s 判重窗，
        且其 end_time 早已滑出 16s 环形缓存 → 窗口定位失败 → 又落回"尾巴"提错
        特征 → 编号兜底放行 = 商家没说话却"替商家回答"。现返回 stale=True：
        live 语音的 definite 只会滞后云端时钟 1~3s，凡晚 5s 以上的定句都是
        延迟重发/幽灵定句，调用方直接丢弃，绝不入锁、绝不上屏、绝不走兜底。

        返回 (feats_or_None, voiced_bool, stale_bool)。
        """
        ring = self._pcm_ring
        # 【11/21 五轮·自适应窗】旧版固定回看 1.6s——用户开着播音说首句时
        # （"没有疑问"只有 ~0.6s），窗口 60%+ 是播音的声音 → 提出的参考
        # 基频被污染成播音员 → 之后播音句全被"认作目标"放行（14:24 实测
        # 穿透链路）。现在窗口 = 句子自己的时长（start_time~end_time）+ 0.2s
        # 余量，clamp 到 [0.4s, 1.6s]——特征只来自这句话自己的音频。
        end_ms = u.get("end_time")
        if end_ms is None:
            add = u.get("additions") or {}
            end_ms = add.get("end_time")
        start_ms = u.get("start_time")
        if start_ms is None:
            add = u.get("additions") or {}
            start_ms = add.get("start_time")
        if isinstance(end_ms, (int, float)) and isinstance(start_ms, (int, float)):
            dur_ms = max(0, end_ms - start_ms) + 200
        else:
            dur_ms = self.UTTER_WIN_MS
        win_ms = int(min(max(dur_ms, 400), self.UTTER_WIN_MS))
        win_bytes = int(VOICE_SR * 2 * win_ms / 1000)
        stale = False
        seg = None
        if isinstance(end_ms, (int, float)) and end_ms >= 0 and ring:
            # 云端已听到的真实音频字节 = 已发送字节 - 纯保活静音字节
            real_bytes = self._fed_bytes - self._ka_bytes
            delta_b = real_bytes - int(end_ms) * self.BYTES_PER_MS
            if delta_b > self.STALE_MS * self.BYTES_PER_MS:
                stale = True          # 延迟重发定句：音频早过，不回头提特征
            elif 0 <= delta_b < len(ring):
                end_b = len(ring) - delta_b
                seg = ring[max(0, end_b - win_bytes):end_b]
        if not seg or len(seg) < win_bytes // 4:
            seg = b"" if stale else (ring[-win_bytes:] if ring else b"")
        data = bytes(seg)
        # 【11.21-17 B版】第 4 项随身带着这句自己的音频段，仅供响度/清晰度裁决用；
        # 前 3 项返回值与 A 版完全一致——调用点不解包第 4 项时零影响。
        return segment_voice_feats(data), segment_voiced(data), stale, data

    @staticmethod
    def _norm_text(t: str) -> str:
        """归一化文本用于判重：只保留中英文/数字，去掉标点与空白。
        实测同一句会被火山多次 definite 成 "是" / "是的" / "是的。"，
        旧逻辑按原串比较 → 每个变体都 on_final → 前端 append 出好几个「是的」框。
        """
        return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]", "", t or "")

    def _emit_final(self, text: str, conf: float) -> None:
        """同一句话的多次 definite 只落一次；'是→是的→是的呀' 渐进扩展只保留最新。

        两层判重（11/19 五次修复）：
        ① 会话级精确判重（15s）：AI 播报十几秒后云端延迟补发的同一句 definite
           ——「把我上一句'是的'又发一遍」的直接来源——早已出旧版 3s 窗口，这里
           按已落地文本历史整窗拦掉，无论补发了多少遍。
        ② 3s 渐进合并：毫秒级 definite 洪水（是→是的→是的。）只留最新。
        """
        norm = self._norm_text(text)
        if not norm:
            return
        now = time.monotonic()
        # ① 会话级精确判重
        for h_norm, h_t in self._emitted_hist:
            if h_norm == norm and (now - h_t) <= self.DUP_SUPPRESS_SEC:
                self._last_final = text
                self._last_final_at = now
                return
        prev = self._norm_text(self._last_final)
        within = (now - self._last_final_at) <= self.FINAL_DEDUP_WINDOW
        if prev and within:
            if norm == prev:
                # 完全重复：不新增框，刷新窗口以持续压制洪水
                self._last_final_at = now
                return
            # 渐进扩展/修正（单字也可能：是→是的）：包含关系 + 长度差≤4 视为同句
            if abs(len(norm) - len(prev)) <= 4 and (norm in prev or prev in norm):
                self._last_final = text
                self._last_final_at = now
                return
        self._last_final = text
        self._last_final_at = now
        self._emitted_hist.append((norm, now))
        asyncio.create_task(self._safe_call(self.on_final, text, conf))

    def _parse_result(self, body: dict) -> None:
        result = body.get("result") or {}
        utterances = result.get("utterances") or []

        # ---- 抗双讲关闭：原逻辑逐字保留，行为零变化 ----
        if not self.anti_doubletalk:
            if utterances:
                last_u = utterances[-1]
                u_text = last_u.get("text", "")
                definite = last_u.get("definite", False)
                conf = last_u.get("confidence", 1.0)
                if definite:
                    if u_text and u_text != self._last_final:
                        self._last_final = u_text
                        asyncio.create_task(self._safe_call(self.on_final, u_text, conf))
                elif u_text:
                    asyncio.create_task(self._safe_call(self.on_partial, u_text))
            else:
                text = result.get("text", "")
                if text:
                    asyncio.create_task(self._safe_call(self.on_partial, text))
            return

        # ---- 抗双讲开启：音纹锁定（方案A做实）+ 只认确认句 + 旁人零显示 ----
        # 【11/19 五次修复】在四次修复基础上堵三个实测漏洞（AI 说完话后噪音影响
        # 被"释放"出来：先重复上一句、再冒出噪音念的内容）：
        #  · 根因①会话重建清音纹：重建后第一句 definite（延迟补发的旧句/噪音）
        #    走"尚未锁定"分支重新入锁 → 改为重建保留音纹（见 _connect_once）；
        #  · 根因②提特征用"此刻尾巴"：云端延迟 1~3s，尾巴早不是那句话的音频
        #    → 改为 utterance.end_time 定位到句子自己的音频段提特征；
        #  · 根因③判重窗 3s：AI 播报十几秒后补发的重复 definite 直接重复上屏
        #    → 新增 15s 会话级精确判重（见 _emit_final）。
        #  · 另：云端对静音也会吐 definite（幻影定句）——该句音频窗无任何人声
        #    能量时直接丢弃，绝不入锁、绝不上屏、绝不走编号兜底。
        #  · 中间结果(definite=false)继续一律静默：安静时对话框绝对静止、噪音绝不冒字。
        # 无分句结构的整段文本（实测多为噪音/中间态）：静默，绝不上屏
        if not utterances:
            return

        for u in utterances:
            spk = self._speaker_of(u)
            u_text = u.get("text", "")
            definite = bool(u.get("definite", False))
            conf = u.get("confidence", 1.0)
            if not u_text:
                continue

            # 中间结果（逐字 stream）：绝不上屏、绝不落库（守住旁人零显示底线）。
            # 【11/21 九轮·提速】但不再一刀切丢弃——按本地内容判据把"像业务"的
            # partial 喂给投机引擎，让它在商家说话期间提前跑 LLM+TTS 备好答案；
            # definite 到达时若音纹放行即可命中缓存，首字毫秒级。噪音 partial
            # 不喂投机（省得白跑 LLM、也避免投机候选被噪音文本污染）。
            # 注意：这是纯提速旁路，认人仍由 definite 音纹决定；本分支不触发任何
            # 上屏/落库/回复，喂错了最坏只是投机 miss，不会冒一句噪音字幕。
            if not definite:
                if (self.on_spec_hint is not None
                        and content_hint(u_text) in ("business", "gray")):
                    asyncio.create_task(self._safe_call(self.on_spec_hint, u_text))
                continue

            # ---- 以下均为 definite 确认整句 ----
            # 特征取自"那句话自己的音频段"（end_time 定位），三道先验：
            # ① stale=延迟重发定句（晚于 end_time 5s+）：AI 长播报后云端重放
            #    旧句的幽灵——直接丢弃，不入锁/不上屏/不走兜底（11/21 二轮根因）；
            # ② 整窗无人声能量 = 幻影定句：直接丢弃；
            # ③ 正常：按 A/B 判定。
            feats, voiced, stale, utt_data = self._utterance_feats(u) \
                if self.anti_doubletalk else (None, False, False, b"")
            if stale:
                self._diag("stale", u, feats, None, "延迟重发定句→丢弃")
                self._emit_speaker("stale", spk, "")
                continue
            if not voiced:
                self._diag("no_voiced", u, feats, None, "整窗无人声→幻影定句丢弃")
                continue

            # B0.【11/21 四轮·根因B】AI 正在播报（含尾音余量）：此时到达的
            # definite 是 TTS 回声或旁人抢先——接通即放播音的真实时序正是
            # "AI 开场白播了 10 秒、播音从头响到尾"，第一个 definite 必是播音句。
            # 未锁定时一律丢弃：绝不把播音员的声音锁成目标、绝不上屏。
            # 已锁定则照常走下方音纹判定（女声参考 vs 男播音 → 必拦）。
            # 【11/21 五轮】尾音余量 1.5s→0.9s：四轮的宽余量会误杀"AI 快说完时
            # 的正常抢答"（用户抢答被丢 → 只能再说一遍 → 感知延迟变大）。
            # VAD 打断发生时 server 会清零播报时钟（_ai_last_speak_at=0），
            # 打断后用户的话不再被本分支丢弃。
            _in_barge_grace = time.monotonic() < getattr(self, "_barge_grace_until", 0.0)
            if self._ref_f0 is None and self.ai_speaking and not _in_barge_grace:
                self._diag("ai_echo", u, feats, None,
                           "未锁定且AI播报中→丢弃(距上次播报%.2fs)" %
                           (time.monotonic() - self._ai_last_speak_at))
                self._emit_speaker("ai_echo", spk, "")
                continue
            if _in_barge_grace and self._ref_f0 is None:
                self._diag("barge_grace", u, feats, None,
                           "打断宽限期内首句→放行入锁(不被ai_echo吞)")

            # A. 尚未锁定音纹：AI 闭嘴期间的应答句建立目标音纹。
            # 【11/21 四轮·根因A·假锁定】特征提取失败（首句太短/无声调周期窗）时
            # 旧版照样发 "locked" 事件、但 _ref_f0 依然是 None——前端显示
            # "已锁定您的声音"是假象；下一句 definite（往往正是播音）再进本分支
            # 把播音员的声音锁成目标，此后播音内容全部放行——这就是
            # "本人女声、男播音照样穿透"的直接根因。现在：提不到特征就不锁定、
            # 不发 locked；本句照常上屏（它是应答），发 no_lock 提示再说一句
            # 稍长的话即可完成锁定。
            # 【11/21 五轮·双簇拒绝】feats 第 4 维是 F0 离散度（spread）：
            # 首句若在播音声中说出（14:24 实测"没有疑问"），窗口里同时有
            # 两个声簇 → spread 飙高 → 参考必被污染。spread>0.35 拒绝入锁
            # （发 no_lock，用户下一句在安静时说即可干净锁定）。
            if self._ref_f0 is None:
                # 【11/22】空文本/超短句不建参考：实机出现过"首句为空"却入锁，
                # 参考被噪声带偏，之后本人每句都被判旁人（02:03 真机复现）。
                if (feats is not None and feats[3] <= 0.35
                        and len((u_text or "").strip()) >= 2
                        and 80 <= feats[0] <= 350):
                    self._ref_f0, self._ref_zcr, self._ref_tilt = feats[:3]
                    self._target_speaker = spk
                    # 【11.21-15】记下锁定瞬间的编号锚点：全程不被后续放行更新，
                    # 专供双讲灰区"编号冲突"加固（只收紧不放宽）。
                    self._locked_speaker = spk
                    # 短句(spread==0=有效窗<4)锁定的基频可能是半频，标记待校准
                    self._ref_oct_unconfirmed = (feats[3] == 0.0)
                    self._diag("locked", u, feats, None,
                               "首句建立音纹参考→锁定%s" %
                               ("(短句·待半频校准)" if self._ref_oct_unconfirmed else ""))
                    self._emit_speaker("locked", spk, "")
                else:
                    _why = ("特征提取失败(feats=None)" if feats is None
                            else "spread=%.3f>0.35 双簇拒绝" % feats[3])
                    self._diag("no_lock", u, feats, None, _why)
                    self._emit_speaker("no_lock", spk, "")
                self._emit_final(u_text, conf)
                continue

            # B. 音纹比对：像目标才放行。
            # 【11/21 二轮·根因之五】删除编号兜底放行：speaker_id 是"会话内第
            # 几个开口的人"，会话重建后重新分配、长通话中漂移——实测噪音句正是
            # 靠"编号恰好撞上目标 + 特征模糊"从兜底溜进来的（商家没说话，系统
            # 却把播音腔的"好的，谢谢老师"当商家的话回复了一遍）。锁定后判定
            # 只认音纹：True 放行；False/None（特征无效或模糊区联判不过）一律
            # 拦截——宁可商家偶尔重说一句，绝不让旁人一个字上屏。
            sim = self._match_voice(*feats[:3]) if feats is not None else None
            _verdict = {True: "pass放行(像目标)", False: "blocked拦截(不像)",
                        None: "no_feat拦截(特征无效/模糊区)"}[sim]
            _f0txt = ("f0=%.1f vs ref=%.1f rel=%.3f" %
                      (feats[0], self._ref_f0,
                       abs(feats[0] - self._ref_f0) / max(self._ref_f0, 1.0))
                      if (feats and self._ref_f0) else "f0/ref无效")
            self._diag("pass" if sim is True else ("blocked" if sim is False else "no_feat"),
                       u, feats, sim, _verdict + " | " + _f0txt)

            # 【11.21-12 双讲污染修复·根因实锤】半频自愈原来把两类八度混为一谈：
            #   ① 真半频：首句超短被记成半频(本人240→ref120)，本人真声240≈2×ref
            #      → 升频纠正到240，男播音115立刻被拦。这是要保留的。
            #   ② 双讲差频陷阱：本人240 + 男播音115 同时说话，自相关把混合报成
            #      差频≈119Hz（既非本人也非男声），而 ZCR/tilt 因本人主导仍几乎
            #      等于本人（实测 zrel=0.04/trel=0.09）。119≈ref240/2 命中旧版
            #      "降频"八度判据 → 参考被从240误改成119 → 此后本人真声240 vs
            #      ref119 差100%，被永久判旁人——正是"关掉男声后本人仍被屏蔽"。
            # 修复：只做升频纠正（当前句≈2×参考才动参考）；降频方向一律不碰参考。
            # 这样双讲差频(≈ref/2)再也污染不了参考，真半频仍可自愈。
            # 【11.21-12 补】不再要求 _ref_oct_unconfirmed：旧版污染参考时会把该标志
            # 置 False，而会话重建(_connect_once)又保留被污染的 ref——若仍依赖此标志，
            # 用户升级后"已污染会话"永远恢复不了。升频(当前≈2×ref)+音色同人已是
            # 极强证据：男播音115Hz 不可能是本人240Hz 的2倍，不会误升频。双讲差频
            # (≈ref/2)更不满足升频，故放开标志不会重新引入污染。
            if (sim is not True
                    and feats is not None and feats[0] is not None):
                f0n, zcrn, tiltn = feats[:3]
                # 只认"升频"：当前句是参考的两倍（本人真声），绝不认降频(ref/2)
                up_oct = abs(f0n - 2 * self._ref_f0) / max(2 * self._ref_f0, 1.0) < 0.15
                zrel = (abs(zcrn - self._ref_zcr) / max(self._ref_zcr, 0.01)
                        if (self._ref_zcr and zcrn is not None) else 9.0)
                trel = (abs(tiltn - self._ref_tilt) / max(self._ref_tilt, 0.01)
                        if (self._ref_tilt and tiltn is not None) else 9.0)
                if up_oct and zrel < 0.35 and trel < 0.45:
                    _old = self._ref_f0
                    self._ref_f0, self._ref_zcr, self._ref_tilt = feats[:3]
                    self._ref_oct_unconfirmed = False
                    self._diag("oct_correct", u, feats, None,
                               "半频自愈(仅升频): ref %.1f→%.1fHz (真声2倍+音色同人)" % (_old, f0n))
                    # 纠正后按新参考重判当前句：大概率就是本人，放行上屏
                    sim = self._match_voice(*feats[:3])

            if sim is True:
                # 【11.21-17 B版·响度/清晰度裁决】音纹认出目标后，再查这一句
                # 是不是"本人 + 旁人"的混音。与 A 版（判双讲就一律整句拦）不同：
                #   B 版把这句自己的音频逐窗拆成"本人簇/离群簇"，比较谁更响、
                #   谁更清晰——本人明显更响且不更糊 → 整句放行（本人的话上屏，
                #   但混在里面的旁人字也会一起显示，这是单麦的物理代价）；
                #   势均力敌 / 旁人更响更清晰 → 整句拦（噪音一个字不上屏）。
                #   这正是用户要的"谁声音大谁清晰，谁上屏"。
                # spread/out 仅作"是否疑似双讲"的触发器，不再直接否决。
                _spread = feats[3] if (feats and len(feats) > 3) else None
                _out = feats[4] if (feats and len(feats) > 4) else None
                _dtalk_signal = (_spread is not None and _spread > self.DTALK_SPREAD_MAX) or \
                                (_out is not None and _out > self.DTALK_OUT_MAX)
                if not _dtalk_signal and (_spread is not None
                        and spk is not None and self._locked_speaker is not None
                        and spk != self._locked_speaker and _spread > 0.20):
                    _dtalk_signal = True   # 编号冲突灰区也算疑似双讲
                # 【11/22】短句（"没有了/方便/是我"）逐窗响度不可靠，极易误拦；
                # 短应答不做双讲响度裁决，直接放行（音纹已经认成目标了）。
                if _dtalk_signal and len((u_text or "").strip()) <= 6:
                    _dtalk_signal = False
                if _dtalk_signal:
                    dom = segment_dominance(utt_data, self._ref_f0,
                                            self._ref_zcr, self._ref_tilt)
                    _loud_ratio = _clar_ratio = 0.0
                    _pass = False
                    if dom and dom["n_self"] >= DOM_MIN_SELF:
                        if dom["n_other"] < DOM_MIN_OTHER:
                            # 逐窗根本没分出第二个人（旁人太弱/被本人在每个窗
                            # 盖过）：本句由本人完全主导 → 整句放行。这跟 A 版
                            # "spread 超线一律拦"的关键差别——A 版会因整体
                            # spread 偏高误伤，B 版逐窗核实后放本人。
                            _pass = True
                            _loud_ratio = 99.0   # 仅用于诊断展示
                            _clar_ratio = 1.0
                        else:
                            _loud_ratio = dom["self_loud"] / max(dom["other_loud"], 1e-6)
                            _clar_ratio = dom["self_clar"] / max(dom["other_clar"], 1e-6)
                            _pass = (_loud_ratio > DOM_LOUD_MIN
                                     and _clar_ratio > DOM_CLAR_MIN)
                    _why = ("响度裁决: spread=%.3f out=%.3f | 本人响度=%.0f/%.0f(×%.2f) "
                            "清晰度=%.2f/%.2f(×%.2f) 窗%d/%d → %s" %
                            (_spread or 0, _out or 0,
                             dom["self_loud"] if dom else 0, dom["other_loud"] if dom else 0,
                             _loud_ratio, dom["self_clar"] if dom else 0,
                             dom["other_clar"] if dom else 0, _clar_ratio,
                             dom["n_self"] if dom else 0, dom["n_other"] if dom else 0,
                             "本人占优→整句放行" if _pass else "本人未明显占优→整句拦"))
                    if _pass:
                        self._diag("dominance_pass", u, feats, sim, _why)
                        self._emit_speaker("dominance_pass", spk, "")
                        # 本人占优放行：下面的内容兜底闸照常执行，之后 emit_final
                    else:
                        self._diag("dominance_block", u, feats, sim, _why)
                        self._emit_speaker("blocked_doubletalk", spk, "")
                        continue
                # 【11/21 九轮·内容兜底加固】音纹认出目标后，再加一道极窄内容闸：
                # 若正文铁定是校园广播类噪音（"广播站/学弟学妹/上课/考试…"，绝不可能是
                # 资质审核应答），即使音纹误判放行也拦掉——这是 11.21-8 半频自愈之外的
                # 第二道保险。严格边界：① 用极窄表 strict_noise=True，误杀风险趋近于零；
                # ② 本分支只在"已锁定之后"执行，第一句锁定分支根本不经过这里，
                #    所以"第一句话说什么都认作本人"的硬口径完全不受影响；
                # ③ 内容只做否决、不做放行——音纹拦掉的绝不因内容放回来。
                if content_hint(u_text, strict_noise=True) == "noise":
                    self._diag("content_block", u, feats, sim,
                               "音纹放行但正文铁定校园广播噪音→内容兜底加固拦掉")
                    self._emit_speaker("blocked", spk, "")
                    continue
                # 音纹认出目标：同步跟踪当前编号（供 server 帧级 VAD 联动参考）
                if spk is not None:
                    self._target_speaker = spk
                self._block_streak = 0
                self._emit_final(u_text, conf)
                continue
            # 音纹不像 / 特征无效 / 模糊区联判不过 → 旁人：零文本、不上屏、
            # 不落库、不触发 AI、不冒字幕
            # 【11/22 防永久锁死】连续 3 次被判旁人，认定参考已被污染/嗓子变了，
            # 按当前这句重锁并放行一次；否则实机出现过"本人从此每句都被拦"。
            self._block_streak += 1
            if self._block_streak >= 3 and feats is not None:
                # 【11/22 安全加固】只有“音色像本人、只是音高漂移”才允许重锁；
                # 否则连续说话的第二人（电视/手机视频）会被重锁成目标，从此放行。
                _zrel = (abs(feats[1] - self._ref_zcr) / max(self._ref_zcr, 0.01)
                         if self._ref_zcr else 9.0)
                _trel = (abs(feats[2] - self._ref_tilt) / max(self._ref_tilt, 0.01)
                         if self._ref_tilt else 9.0)
                if _zrel < 0.25 and _trel < 0.30:
                    self._ref_f0, self._ref_zcr, self._ref_tilt = feats[:3]
                    self._target_speaker = spk
                    self._block_streak = 0
                    self._diag("relock", u, feats, None,
                               "连续3次判旁人且音色像本人→重锁并放行（防永久锁死）")
                    self._emit_speaker("relocked", spk, "")
                    self._emit_final(u_text, conf)
                    continue
                self._block_streak = 0
                self._diag("relock_reject", u, feats, None,
                           "连续3次但音色不像本人(zrel=%.2f trel=%.2f)→拒绝重锁，继续拦"
                           % (_zrel, _trel))
            self._emit_speaker("blocked" if sim is False else "no_feat", spk, "")

    async def _safe_call(self, fn, *args):
        try:
            await fn(*args)
        except Exception:
            pass

    def _is_fatal(self, code: int, msg: str) -> bool:
        if code in self.FATAL_CODES:
            return True
        low = (msg or "").lower()
        return any(k in low for k in self.FATAL_KEYWORDS)

    async def _run(self) -> None:
        try:
            async for raw in self.ws:
                resp = parse_response(raw)
                if resp.get("type") == "error":
                    code, msg = resp.get("code", -1), resp.get("msg", "")
                    self._last_error_code = code
                    if code == 45000081:
                        # 等音频超时：只写服务端日志并静默重建，不把云端英文
                        # 报错推给商家端（原行为会连带刷出「会话异常」两条系统行）。
                        print(f"[ASR-IDLE] {self.call_id} 45000081 空闲超时，静默重建",
                              flush=True)
                    else:
                        await self._safe_call(self.on_partial,
                                              f"[ASR错误 {code}] {msg[:120]}")
                    if self._is_fatal(code, msg):
                        self._session_dead.set()   # 通知监督者重建
                        return
                    continue
                if resp.get("type") == "result":
                    self._parse_result(resp.get("body") or {})
        except websockets.exceptions.ConnectionClosed:
            pass
        except Exception as e:
            await self._safe_call(self.on_partial, f"[ASR连接中断] {e}")
        finally:
            self._session_dead.set()

    async def _sender(self) -> None:
        """音频发送协程：200ms/包，递增序号；空闲补静音保活帧，连接重建时不丢队列。"""
        while True:
            try:
                chunk = await asyncio.wait_for(self._audio_queue.get(),
                                               timeout=self.KEEPALIVE_INTERVAL)
            except asyncio.TimeoutError:
                # 队列空 = 浏览器这一刻没推流。补一帧静音保活，走同一套发送逻辑。
                chunk = self.KEEPALIVE_PCM
            if chunk is None:
                try:
                    self._seq += 1
                    g = gzip.compress(b"")
                    await self.ws.send(_hdr(MSG_AUDIO_REQ, F_NEG_WITH_SEQ, SER_RAW, COMP_GZIP)
                                       + struct.pack(">i", -self._seq)
                                       + struct.pack(">I", len(g)) + g)
                    await self.ws.close()
                except Exception:
                    pass
                return
            # 会话正在重建 / 尚未建立时，保活帧没有意义，直接跳过等下次；
            # 真实音频帧仍走下面的等待重试逻辑，绝不丢弃。
            if chunk is self.KEEPALIVE_PCM and (self.ws is None or self._session_dead.is_set()):
                continue
            # 发送：当前连接不可用（正在重建）则等待重试
            while not self.closed:
                if self.ws is None:
                    await asyncio.sleep(0.3)
                    continue
                try:
                    self._seq += 1
                    await self.ws.send(_frame_with_seq(MSG_AUDIO_REQ, self._seq, SER_RAW,
                                                       COMP_GZIP, gzip.compress(chunk)))
                    # 【11/19 五次修复】云端音频时钟按"实际发出去的字节"计——云端
                    # end_time 的原点就是本会话收到的第一包音频。
                    self._fed_bytes += len(chunk)
                    if chunk is self.KEEPALIVE_PCM:
                        self._ka_bytes += len(chunk)
                    break
                except Exception:
                    self._seq = max(1, self._seq - 1)   # 序号回退，等新会话
                    await asyncio.sleep(0.3)

    async def close(self) -> None:
        self.closed = True
        try:
            await self.finish()
        except Exception:
            pass
        if self._run_task:
            self._run_task.cancel()
        try:
            await self.ws.close()
        except Exception:
            pass
