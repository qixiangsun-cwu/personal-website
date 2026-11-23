# 纸书智伴 · Cloudflare 线上版说明

本目录把 `纸书智伴-上线部署包` 的 Python/Node 本地后端，重写为可在 Cloudflare Workers 上运行的版本。
前端 `纸书智伴/*.html` 与 `assets/*.js` **一个字都没改**，只是换了后端实现。

## 线上地址

| 用途 | 地址 |
| --- | --- |
| 应用入口（正式） | https://paperbook.qixiangsun.com.cn/ |
| 应用入口（备用） | https://cwu-paperbook.qixiangsun.workers.dev/ |
| 主站作品卡片 | https://www.qixiangsun.com.cn/student-competitions |

## 架构

```
浏览器 ──► Worker (cwu-paperbook)
              ├── D1  cwu-paperbook-db     书库元数据 / book.json / index.json 分块 / 日志
              ├── R2  paperbook-assets     绘本页图、整本动画 mp4、tesseract 语言包
              └── 外部 API                  DeepSeek（问答/识字/翻译）、火山 TTS（可选）
```

- 书库数据不是文件系统，而是每次请求从 D1 重建内存态（`src/runtime.js` 的 `loadState`）。
- `book.json` 的 `pages` 字段打包进 `books.analysis_json` 的 `__pages` 键（D1 表没有 pages 列）。
- `index.json` 按 4 万字符切块存 `book_blobs`，规避 D1 单条 SQL 约 100KB 的限制。
- 视频/页图走 R2，`src/runtime.js` 的 `r2Response` 实现了 Range/206，支持拖动进度条。

## 与本地版的功能差异（如实说明）

| 能力 | 本地版 | 线上版 | 说明 |
| --- | --- | --- | --- |
| 翻页 / 点读 / 热区 | ✅ | ✅ | 完全一致 |
| 整本动画播放 | ✅ | ✅ | R2 + Range，支持进度拖动 |
| 页面问答（`/api/qa`） | ✅ | ✅ | 有 DeepSeek 密钥时为模型作答；无密钥降级为本地检索并显式标注 `degraded`，**不编造** |
| 识字 / 翻译 / 拓展 | ✅ | ✅ | 同上，无密钥降级本地 |
| 朗读 | 火山 TTS / edge-tts | **浏览器内置语音** | 见下节 |
| 上传绘本建书 | ✅ | ⚠️ 关闭 | `PB_ALLOW_UPLOAD=0`；无状态运行时无法落盘并在前端做 OCR |
| 照片 OCR | ✅ | ✅ | tesseract.js 在浏览器本地跑，不依赖后端 |
| Remotion 渲染动画 | ✅ | ❌ | CPU 密集，需要本机 ffmpeg 与渲染工程 |
| 豆包图生视频 | ✅ | ❌ | 需轮询 + 下载 25MB 级视频落盘 |
| 专属音色克隆 | ✅ | ❌ | Chatterbox 3.1GB 模型，需 GPU 本机 |

## 朗读为什么变成浏览器语音

免密钥的 edge-tts 走 WebSocket 连 `speech.platform.bing.com`。从 Cloudflare Worker 出口实测：

| 端点 | 结果 |
| --- | --- |
| `speech.platform.bing.com` | 403 `Invalid Request` |
| `api.msedgeservices.com` | 403 `Forbidden` |
| `eastus.api.msedgeservices.com` | 530 `error code: 1016` |

三个端点都拒绝数据中心 IP，与代码无关。前端 `assets/audio-bus.js`、`assets/book-bridge.js`
本来就有 `speechSynthesis` 兜底，所以朗读不会中断，只是音色换成系统语音。
`/api/tts/voices` 会如实返回 `configured:false` 与说明文字。

**要恢复云端音色**：申请火山引擎 TTS 密钥后执行

```bash
wrangler secret put VOLC_TTS_API_KEY --cwd worker-paperbook
wrangler secret put VOLC_APP_ID      # 如使用 appid/token 方式
```

并在 `wrangler.toml` 的 `[vars]` 里设 `PB_TTS_PROVIDER="volc"` 后重新部署。

## 未配置密钥时的行为

`/api/health` 里 `adapters.deepseek=false` 表示 DeepSeek 未配置。此时所有依赖模型的能力都会：
1. 退回本地算法（检索 / 模板 / `WORD_FALLBACK` / `HANZI_FALLBACK`）；
2. 在响应里带 `degraded:true` 与 `reason`；
3. 页面明确标注降级。

**不会伪造结果**——这是原项目的红线，线上版保持不变。

配置 DeepSeek：
```bash
wrangler secret put DEEPSEEK_API_KEY --cwd worker-paperbook
```

## ⚠️ 维护红线（血泪教训）

1. **绝不要**在 `worker-paperbook/` 目录里执行 `wrangler delete <别的名字>`。
   wrangler 以 `wrangler.toml` 的 `name` 为准，位置参数会被忽略——本次因此误删了生产 Worker `cwu-paperbook`。
   删除临时 Worker 必须 `cd` 进它自己的目录。
2. 本机 `*.workers.dev` 的 DNS 被污染，Node/curl 全部超时，**只有 PowerShell `Invoke-WebRequest` 能访问**。
3. wrangler 命令恒返回 exit code 1（升级提示），判断成功看 `Uploaded` / `Deployed` / `Current Version ID`。
4. `wrangler r2 object` 在 3.114.17 只有 get/put/delete，**没有 list**。
5. 本机是 Cloudflare Free 计划，`wrangler.toml` 里**不能写** `[limits] cpu_ms`。
6. 生成脚本里拼接路径必须用 `fileURLToPath(import.meta.url)`，
   直接用 `new URL(...).pathname` 会把中文路径百分号编码，导致 wrangler 静默失败。

## 本地自检

```bash
node scripts/smoke.mjs
```

用最小 D1/R2 桩件 + 真实书库数据直接打 Worker 的 `fetch`，当前 **41/41 通过**。

## 重建流程

```bash
node scripts/build-data.mjs      # 生成 data-books.sql / data-assets.sql / upload-manifest.json
node scripts/build-static.mjs    # 生成 src/static.gen.js（内联 34 个静态文件）
node scripts/upload-r2-api.mjs   # 上传 R2（注意：会消耗 OAuth refresh_token）
wrangler deploy
```
