# research/ · 研究数据收集页

「图像大模型的性别化分工预设与人的接受」项目（中华女子学院）的问卷与访谈页面。
纯静态页面 + Cloudflare Pages Functions，数据写入 D1（无 D1 时自动退回 KV）。

## 页面

| 路径 | 用途 | 说明 |
| --- | --- | --- |
| `/research/survey/` | 匿名问卷 | 4 份「AI 分工建议」卡片（顺序随机）× 5 题 + 一般看法 + 人口学 + 开放题；约 8–10 分钟 |
| `/research/interview/` | 在线自填访谈 | 5 个模块 / 18 道开放题，答满 12 题可提交；约 20–30 分钟 |
| `/research/admin/` | 数据管理台 | 输入管理密钥后查看汇总、明细，导出 JSON / CSV |

三个页面均：移动端优先、无外部依赖（无 CDN / 字体 / 统计脚本）、`noindex`、不在页面里存任何密钥。

## 接口

| 方法与路径 | 鉴权 | 说明 |
| --- | --- | --- |
| `POST /api/survey` | 公开 | 提交一份问卷；服务端做字段白名单与取值范围校验，honeypot 字段 `hp` 非空直接拒绝 |
| `GET /api/survey?key=KEY&format=json\|csv` | 需密钥 | 导出全部问卷；CSV 为 UTF-8 BOM，4 卡宽表 |
| `POST /api/interview` | 公开 | 提交一份自填访谈；至少 12 题有内容 |
| `GET /api/interview?key=KEY&format=json\|csv` | 需密钥 | 导出全部访谈；CSV 为 18 题宽表 |

鉴权：服务端只保存管理密钥的 SHA-256（`wrangler.toml` 的 `[vars] RESEARCH_KEY_HASH`），请求时比对 `?key=` 的哈希；
管理密钥明文不进入仓库，单独线下交付。IP 只落 16 位哈希，不存原始地址。

## 存储

- 主：D1 绑定 `DB`，表 `survey_responses` / `interview_responses`，首次请求自动建表并建索引。
- 兜底：KV 绑定 `PROPOSALS`，键 `research:{survey|interview}:{ISO 时间}:{id}`。
- 每份答卷整包 JSON 存 `data` 列（`v / consent / cards[4] / gen / demo / open / durationMs / startedAt / submittedAt / ua`）。

## 本地预览

```bash
# 在仓库根目录
npx wrangler pages dev . --port 8787
# 打开 http://127.0.0.1:8787/research/survey/
```

## 部署

`main` 分支 push 后由 Cloudflare Pages 自动构建（`pages_build_output_dir = "."`）。
上线后自检顺序：

1. `/research/survey/` 提交一份测试答卷 → 页面显示编号；
2. `/research/admin/` 输入密钥 → 计数 +1、明细可见；
3. 导出问卷 CSV → 用 Excel 打开，中文不乱码；
4. `/api/survey?key=wrong` 应返回 401。

## 目录

```
research/
├── survey/index.html      问卷页
├── interview/index.html   自填访谈页
├── admin/index.html       管理台（密钥在页面内输入，仅存 sessionStorage）
├── gallery.html           既有可视化页（与本项目共用 assets/）
└── assets/                结论图
functions/api/
├── _research-lib.js       公共库（鉴权、D1/KV 存储适配、CSV）
├── survey.js              问卷接口
└── interview.js           访谈接口
```

## 研究伦理

- 页面首屏即为知情同意，未勾选不能提交；不满 18 岁不可提交。
- 匿名：不收集姓名、手机号、身份证号；联系方式为**选填**且可留空。
- 数据仅用于学术论文，导出后请存放在加密目录，不要转发到公共群。
