# 个人网站 · 每日 AI 动态流程

## 一、日常流程（Codex 对话框审批模式）

1. 每天 08:00，Windows 计划任务 `PersonalWebsite_AI_News_Daily` 静默生成
   待审稿 `ai-news.pending.json` + 预览页 `ai-news-preview.html`（不弹任何窗口）。
2. 你在 Codex 对话框中打开本项目，回复「批准今天的 AI 动态」：
   - Codex 会把本期新增条目列在对话框里，并询问是否发布；
   - 非中文的标题/摘要会先翻译成中文（原标题存入 `title_en` 字段）；
   - 你回复「批准」，Codex 运行 `scripts\approve-ai-news.ps1 -Headless`，
     完成 待审稿 → `ai-news.json` → git 提交推送；
   - 发布成功后待审稿与预览页自动删除。

## 二、批准路径

### 方式 A：Codex 对话框（推荐，默认）
- 在 Codex 中打开本项目目录，输入：`批准今天的 AI 动态`
- Codex 会在对话框里展示待审内容，等你在对话里确认后再发布。

### 方式 B：设为每日自动提醒（可选，替代 Windows 计划任务）
- 在 Codex 桌面端侧边栏的「自动化」面板新建一个 thread automation，
  每天 08:05 执行，提示词可用：
  > 运行 scripts\update-ai-news.ps1 -Headless；然后读取 ai-news.pending.json，
  > 在对话框里列出本期新增条目并询问我是否批准发布；
  > 我回复「批准」后运行 scripts\approve-ai-news.ps1 -Headless 发布。
- 创建后可停用 Windows 计划任务 `PersonalWebsite_AI_News_Daily`，避免重复生成。

### 方式 C：命令行兜底
```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\approve-ai-news.ps1
```
只校验不发布（演练）：加 `-DryRun`。

## 三、常见问题

- **怎么查看待审内容**：在 Codex 里说「看看今天的 AI 动态」。
- **git 推送失败**：多为本地 git 凭据过期，先手动 `git push origin main`
  或重新登录 Windows 凭据管理器。
- **浏览器审批台已废弃为可选**：`scripts\approve-server.ps1` 仍保留可用
  （http://localhost:8866），但默认流程不再使用浏览器审批。
- **计划任务被中断**：历史退出码 `0xC000013A` 是交互式任务在锁屏/休眠时被终止；
  已改为 `-Headless` 静默运行，日志中大段 HTML 错误也已截断。

## 四、文件说明

| 文件 | 作用 |
| --- | --- |
| `scripts\update-ai-news.ps1` | 每日抓取，生成待审稿与预览页（`-Headless` 静默） |
| `scripts\approve-ai-news.ps1` | 发布待审稿（支持 `-Headless` / `-DryRun`） |
| `scripts\approve-server.ps1` | 可选：本地浏览器审批台（默认不再使用） |
| `scripts\approve-ai-news.bat` | 命令行审批兜底入口 |
| `approve.html` | 可选审批台页面 |
| `ai-news.pending.json` | 待审稿（gitignore） |
| `ai-news-preview.html` | 预览页（gitignore） |