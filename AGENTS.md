# 项目约定

## AI 动态内容语言

个人网站 AI 动态板块的对外内容一律使用中文：

- 发布（批准 `ai-news.pending.json` 之前）时，把所有非中文的 `title` 和
  `summary` 翻译成中文；
- 翻译时把原标题写入 `title_en` 字段（抓取脚本已自动写入原始英文标题，
  用于跨日去重，避免翻译成中文后次日被当作新条目重复抓取）；
- 专有名词保留通用中文译名或原文（如 OpenAI、Gemini、Claude、arXiv），
  不强行直译；
- 论文标题按含义译成中文，可在必要时保留英文关键词。

## 每日审批流程

1. 读取 `ai-news.pending.json`，与 `ai-news.json` 对比列出「本期新增」；
2. 把非中文条目翻译成中文（更新 `title` / `summary`，保留 `title_en`）；
3. 在对话框里列出中文待审条目，等用户确认；
4. 用户回复「批准」后运行 `scripts\approve-ai-news.ps1 -Headless` 发布并推送；
   用户指定剔除某几条时，先从待审稿中删除再发布。