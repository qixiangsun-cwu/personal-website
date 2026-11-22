# 智语外呼 · 语音外呼客服数字员工

基于 WebRTC 网页端的实时语音外呼系统，面向京东 POP 商家资质审核（驳回关单）场景：
**发起外呼 → 多轮实时语音交互 → 任务闭环 → 结果结构化回写工单**。

---

## 一、目录结构

```
zhiyu-callcenter/
├── 一键启动.bat            # 启动后端 + 自动打开运营后台
├── 一键关闭.bat            # 关闭 8900 端口服务
├── 一键弱网验证.bat         # 弱网注入验证（可选）
├── requirements.txt        # Python 依赖
├── README.md               # 本文件
│
├── backend/                # 后端（FastAPI + WebRTC + Agent 编排）
│   ├── server.py           # 主服务：REST / WebSocket / WebRTC / 指标
│   ├── agent.py            # 对话编排、五环节状态机、槽位、合规校验
│   ├── reply_policy.py     # 话术策略（追问、纠错、收线、红线拉回）
│   ├── redline.py          # 合规红线分级处置
│   ├── close_decision.py   # 关单方式判定（电话 / 京麦 / 回拨）
│   ├── tools_registry.py   # 只读工具：订单 / 商家画像 / 知识检索
│   ├── asr_volcano.py      # 流式语音识别
│   ├── tts_volcano.py      # 流式语音合成（豆包 seed-tts-2.0）
│   ├── tts_zhipu.py        # 备用语音合成（可切换）
│   ├── webrtc.py           # 媒体面：SRTP/Opus 协商
│   ├── barge_gate.py       # 能量 VAD 抢话判定门（回合对齐，防"整句无声"）
│   ├── engineer.py         # RSI：诊断 → 补丁 → 校验 → 版本 / 回滚
│   ├── shadow_eval.py      # 自建评测集三模式评测
│   ├── slots.py            # 槽位体系
│   ├── unknown_terms.py    # 未知词台账（待审核入库）
│   ├── db.py               # SQLite 访问层
│   ├── .env.example        # 配置模板
│   └── run_server.bat      # 后端启动脚本（被一键启动调用）
│
├── frontend/               # 前端
│   ├── admin.html          # 运营后台：指标看板 / 质检 / RSI / 话术管理
│   ├── merchant.html       # 商家接听端（WebRTC 采音 + 对话）
│   ├── rag_knowledge_view.html
│   └── *.svg
│
├── data/
│   ├── zhiyu.db            # SQLite 数据库（含演示数据，开箱可用）
│   ├── eval_set.json       # 自建评测用例集
│   └── tts_cache_volcano/  # 语音合成缓存（加速首播）
│
├── tools/                  # 复现与自检脚本
│   ├── test_closure.py          # 离线闭环自检（不依赖网络）
│   ├── test_closure_http.py     # 真实链路闭环自检（需服务已启动）
│   ├── test_eval_scope.py       # 评测口径自检
│   ├── test_metric_scope.py     # 指标口径自检
│   ├── eval_report.py           # 评测集批量跑分
│   ├── export_results.py        # 导出 results/（每张图配同名 JSON）
│   ├── record_baseline.py       # 记录基线
│   ├── reset_demo.py            # 清空演示通话数据
│   └── test_tts_*.py            # 语音合成连通性自检
│
├── results/                # 测试结果（图片 + 同名 JSON，一一对应）
│   ├── 01_核心指标.png / .json
│   ├── 02_通话构成.png / .json
│   ├── 03_时延分布.png / .json
│   ├── 04_轮次分布.png / .json
│   ├── 05_评测集三模式.png / .json
│   └── 06_闭环率口径.png / .json
│
└── docs/
    ├── 自测报告.pdf / .docx      # 自测报告（自建评测集、原始记录、RSI 前后对比）
    └── evidence/                # 评测原始结果（shadow_eval_*.json）
```

---

## 二、环境要求

| 项 | 要求 |
|---|---|
| 操作系统 | Windows 10/11（启动脚本为 .bat；Linux/macOS 可直接用 uvicorn 启动） |
| Python | **推荐 3.12**（3.10+ 可用；**3.14/3.15 装不上 aiortc/Pillow，会缺 WebRTC**） |
| 浏览器 | Chrome / Edge（需支持 WebRTC 与麦克风权限） |
| 麦克风 | 演示时使用，或改用商家端的"文字输入"降级链路 |
| 网络 | 需可访问智谱（LLM）与火山引擎（ASR / TTS）云服务 |

---

## 三、快速启动（3 步）

**1. 安装依赖**

```bash
python -m pip install -r requirements.txt
```

**2. 配置 `backend/.env`** —— **已预置可用密钥，开箱即可启动**；如需换成自己的 Key，按下节表格替换后重启服务。

**3. 双击 `一键启动.bat`**

启动后自动打开浏览器：

| 页面 | 地址 | 用途 |
|---|---|---|
| 运营后台 | http://localhost:8900/ | 指标看板、质检、RSI、话术与场景管理 |
| 商家接听端 | http://localhost:8900/merchant | 模拟商家接听，采音对话 |
| 知识库视图 | http://localhost:8900/rag | 检索命中与知识来源 |

---

## 四、配置说明（`backend/.env`）

> 当前仓库内 `backend/.env` **已预置可用密钥**，可直接启动；如需更换为自己的账号，按下表替换后重启服务。模板见 `backend/.env.example`。

| 变量 | 用途 | 必填 |
|---|---|---|
| `ZHIPU_API_KEY` | 大模型（对话生成） | ✅ |
| `ZHIPU_MODEL` | 模型名，默认 `glm-4-flashx` | 可选 |
| `VOLCANO_APP_KEY` / `VOLCANO_RESOURCE_ID` | 火山引擎**流式语音识别** | ✅（语音链路） |
| `VOLCANO_TTS_API_KEY` / `VOLCANO_TTS_RESOURCE_ID` / `VOLCANO_TTS_SPEAKER` | 火山引擎**流式语音合成**（豆包 seed-tts-2.0） | ✅（语音链路） |
| `TTS_BACKEND` | `volcano` 或 `zhipu`，一键切换语音合成厂商 | 可选 |
| `ENGINEER_API_KEY` / `ENGINEER_BASE_URL` / `ENGINEER_MODEL` | RSI 自进化诊断用的模型 | 可选（不填则 RSI 不可用） |
| `TOOL_CALL` / `TOOL_TIMEOUT_MS` | 实时工具调用开关与超时 | 可选 |

> 只跑**文字链路**（不用麦）时，仅需 `ZHIPU_API_KEY` 即可完整走通全流程。

---

## 五、验证清单（建议按顺序跑一遍）

1. 打开运营后台 → 指标看板应显示已有通话数据（演示库自带记录）。
2. 打开商家接听端 → 选择商家 → 点"接听"。
3. 后台发起一通外呼 → 对着麦克风回答 → 观察实时字幕、五环节进度、话术合规点亮。
4. 结束通话 → 后台"工单"应出现结构化回写（已解决事项 / 待跟进 / 情绪 / 合规率）。
5. 命令行自检：

```bash
# 离线闭环自检（不需要服务与网络）
python tools/test_closure.py

# 真实链路闭环自检（需服务已启动）
python tools/test_closure_http.py

# 评测与指标口径自检
python tools/test_eval_scope.py
python tools/test_metric_scope.py
```

---

## 六、数据与评测

- **数据来源**：商家、场景、话术、知识条目均为**脱敏或合成**数据，不使用任何真实用户个人信息，不向真实号码发起外呼。
- **自建评测集**：`data/eval_set.json`；评测实现见 `backend/shadow_eval.py`，支持 `--rules` / `--gate` / `--model` 三种模式。
- **指标口径**：闭环率 = 闭环工单数 ÷ 已终结业务任务；合规率 = 工单实际分值均值（缺项如实计 0）；首响时延 = 用户停说 → 浏览器首个音频帧播放，起止同用同一时钟。
- **重置演示数据**：先停服务，再执行 `python tools/reset_demo.py --full`（保留商家 / 场景 / 知识库，只清清通话记录）。
- **结果导出**：`python tools/export_results.py` 会从当前数据库重新生成 `results/` 下 6 张图，并为每张图写一个**同名 JSON**（含口径定义、样本量、数值、生成时间），符合「图片 + 同名 JSON」的提交要求。依赖仅 Pillow。

---

## 七、常见问题

| 现象 | 处理 |
|---|---|
| 启动报 `address already in use` | 先运行 `一键关闭.bat`，或改端口后重启 |
| 缺少模块 `uvicorn` / `fastapi` 等 | 执行 `python -m pip install -r requirements.txt` |
| 语音没声音 / 一直"正在听" | 检查浏览器麦克风权限；或改用商家端"文字输入"链路 |
| 提示 API Key 无效 | `.env` 未填写或填写错误，填好后**重启服务** |
| WebRTC 协商失败 | 需要 `aiortc`（已列入 requirements）；缺失时自动降级为 WebSocket 音频链路 |
| 页面样式没更新 | `Ctrl + F5` 硬刷新 |

---

## 八、相关文档

- `docs/自测报告.pdf`：自建评测集说明、原始记录、RSI 迭代前后对比数据
- `docs/evidence/`：影子评测三模式原始输出
- 赛题要求的其余材料（技术方案书、演示视频、开源组件清单）随参赛材料一并提交，不在本仓库内

---

## 九、边界说明

- 本系统是**演示与评测用**原型：商家名单、订单、工单均在本地模拟，不对接真实京东业务系统。
- 工具层仅提供三个**只读**查询工具（订单状态 / 商家画像 / 知识检索），工单回写由编排层完成，模型无写权限。
- 合规红线分为"拉回继续（不计成功）"与"终止转高级专家（不计成功）"两级，处置全程留痕。
