# 个人网站 · 每日 AI 动态流程

## 一、日常流程（现在的模式）

1. 每天 08:00，Windows 计划任务 `PersonalWebsite_AI_News_Daily` 运行
   `scripts\update-ai-news.ps1`：
   - 抓取 RSS，生成待审稿 `ai-news.pending.json` + 预览页 `ai-news-preview.html`；
   - 自动启动本地审批服务器并打开审批台 `http://localhost:8866/approve`。
2. 你在审批台检查「本期新增」，确认无误后点「批准发布并推送」：
   - 待审稿复制为正式数据 `ai-news.json`，git 提交并推送到 GitHub；
   - 发布成功后待审稿与预览页自动删除。

## 二、批准路径（三选一）

### 方式 A：浏览器审批台（推荐）

- 地址：`http://localhost:8866/approve`
- 服务器未运行时，双击 `scripts\start-approve.bat` 启动；
- 页面列出本期新增条目，点「批准发布并推送」→ 确认 → 完成。

### 方式 B：在 Codex 里批准

- 在本项目目录打开 Codex，直接说「批准今天的 AI 动态」；
- Codex 会读取待审稿、与你确认后运行 `scripts\approve-ai-news.ps1 -Headless`。

### 方式 C：命令行（兜底）

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\approve-ai-news.ps1
```

只校验不发布（演练）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\approve-ai-news.ps1 -DryRun
```

## 三、常见问题

- **审批台打不开**：检查端口是否在监听
  `Get-NetTCPConnection -LocalPort 8866 -State Listen`；没有监听就运行
  `scripts\start-approve.bat`。
- **git 推送失败**：多为本地 git 凭据过期，先手动 `git push origin main`
  或重新登录 Windows 凭据管理器。
- **计划任务被中断**：上次运行结果 `0xC000013A` 是交互式任务在锁屏/休眠时被
  终止；已把日志中的大段 HTML 错误截断，避免日志被刷爆。
- **想从手机审批（局域网）**：管理员运行一次
  `netsh http add urlacl url=http://+:8866/ user=Everyone`，再以
  `powershell -File scripts\approve-server.ps1 -Lan` 启动，手机访问
  `http://<电脑IP>:8866/approve`。注意：局域网模式下任何能访问该地址的人都能审批。

## 四、文件说明

| 文件 | 作用 |
| --- | --- |
| `scripts\update-ai-news.ps1` | 每日抓取，生成待审稿与预览页，打开审批台 |
| `scripts\approve-ai-news.ps1` | 发布待审稿（支持 `-Headless` / `-DryRun`） |
| `scripts\approve-server.ps1` | 本地审批服务器（静态托管 + 审批 API） |
| `scripts\start-approve.bat` | 一键启动审批服务器 |
| `approve.html` | 审批台页面（由审批服务器提供） |
| `ai-news.pending.json` | 待审稿（gitignore） |
| `ai-news-preview.html` | 预览页（gitignore） |
| `.approve-token` | 本地审批 token（gitignore，自动生成） |
