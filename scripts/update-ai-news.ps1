# ============================================================
# 个人网站 · AI 资讯每日抓取（待审核模式）
# 抓取 RSS -> 生成 ai-news.pending.json + 预览页 ai-news-preview.html
# 不提交、不推送；生成后自动打开本地审批台 http://localhost:8866/approve
# 由 Windows 计划任务每天 08:00 调用；-Headless 供 CI/手动无界面调用
# 可选：-DigestUrl <微信文章链接>  额外摄取「腾讯研究院AI速递」当期
#   （仿腾讯研究院模式：人工聚合的 3 要点式中文条目，每期 7-9 条，
#    重复运行安全——标题级去重会挡住同一期）
# ============================================================
param([switch]$Headless, [string]$DigestUrl)

$ErrorActionPreference = 'Stop'
$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$siteDir = Split-Path -Parent $PSScriptRoot
$jsonPath = Join-Path $siteDir 'ai-news.json'
$pendingPath = Join-Path $siteDir 'ai-news.pending.json'
$previewPath = Join-Path $siteDir 'ai-news-preview.html'
$logPath = Join-Path $siteDir '.news-update.log'

function Write-Log([string]$msg) {
    $line = "[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg
    Add-Content -Path $logPath -Value $line -Encoding UTF8
}

Write-Log "===== 抓取开始（待审核模式） ====="

$feeds = @(
    'https://www.ithome.com/rss/',
    'https://www.qbitai.com/feed',
    'https://www.infoq.cn/feed',
    'https://techcrunch.com/category/artificial-intelligence/feed/',
    'https://openai.com/blog/rss.xml',
    'http://export.arxiv.org/rss/cs.AI',
    'https://www.technologyreview.com/feed/',
    'https://www.theverge.com/rss/ai-artificial-intelligence/index.xml',
    'https://www.wired.com/feed/tag/ai/latest/rss',
    'https://blogs.nvidia.com/feed/'
)

$keywords = '\bAI\b|人工智能|大模型|智能体|GPT|Gemini|Claude|OpenAI|Anthropic|DeepSeek|机器人|算力|芯片|自动驾驶|多模态|AIGC|LLM|Agent|NVIDIA|英伟达|Hugging Face|Qwen|Kimi|豆包|元宝'

function Get-ElemText($node) {
    if ($null -eq $node) { return '' }
    try {
        if ($node -is [System.Xml.XmlElement]) {
            $inner = $node.InnerText
            if ([string]::IsNullOrWhiteSpace($inner)) {
                # Atom 源的 <link href="..."> 无文本，取 href 属性
                $href = $node.GetAttribute('href')
                if ($href) { return $href.Trim() }
            }
            return $inner.Trim()
        }
    } catch {}
    return ([string]$node).Trim()
}

$newItems = [System.Collections.Generic.List[object]]::new()
$seen = @{}

foreach ($feed in $feeds) {
    try {
        $resp = Invoke-WebRequest -Uri $feed -UseBasicParsing -TimeoutSec 20
        $bytes = $resp.RawContentStream.ToArray()
        $xmlText = [System.Text.Encoding]::UTF8.GetString($bytes)
        $encMatch = [regex]::Match($xmlText, 'encoding=["'']([A-Za-z0-9\-_]+)["'']')
        if ($encMatch.Success) {
            try { $xmlText = [System.Text.Encoding]::GetEncoding($encMatch.Groups[1].Value).GetString($bytes) } catch {}
        }
        $rss = [xml]$xmlText
        $items = @($rss.rss.channel.item)
        if ($items.Count -eq 0) { $items = @($rss.feed.entry) }

        foreach ($item in $items) {
            $title = Get-ElemText $item.title
            if (-not $title -or $title -notmatch $keywords) { continue }

            $pub = Get-ElemText $item.pubDate
            if (-not $pub) { $pub = Get-ElemText $item.published }
            if (-not $pub) { $pub = Get-ElemText $item.updated }
            if (-not $pub) { $pub = Get-ElemText $item.date }
            if (-not $pub) { continue }

            $dt = [datetime]::MinValue
            if (-not [DateTime]::TryParse([string]$pub, [ref]$dt)) { continue }
            if ($dt -lt (Get-Date).AddDays(-2)) { continue }

            $dateStr = $dt.ToString('yyyy-MM-dd')
            $link = Get-ElemText $item.link
            if (-not $link -or -not $link.StartsWith('http')) { $link = Get-ElemText $item.id }
            if (-not $link -or -not $link.StartsWith('http')) { continue }

            $key = $title.Trim()
            if ($seen.ContainsKey($key)) { continue }
            $seen[$key] = $true

            $desc = Get-ElemText $item.description
            if (-not $desc) { $desc = Get-ElemText $item.summary }
            $summary = ''
            if ($desc) {
                $summary = ($desc -replace '<[^>]+>', ' ' -replace '&nbsp;', ' ' -replace '\s+', ' ').Trim()
                if ($summary.Length -gt 600) { $summary = $summary.Substring(0, 600) + '…' }
            }

            $source = Get-ElemText $item.source
            if (-not $source) { $source = ($feed -replace '^https?://', '') -split '/' | Select-Object -First 1 }

            $newItems.Add([PSCustomObject]@{
                title    = $title.Trim()
                title_en = $title.Trim()
                summary  = $summary
                source   = $source
                url      = $link
                date     = $dateStr
            })
        }
        Write-Log "OK feed: $feed"
    } catch {
        $warnMsg = [string]$_.Exception.Message
        if ($warnMsg.Length -gt 300) { $warnMsg = $warnMsg.Substring(0, 300) + ' …' }
        Write-Log "WARN feed $feed -> $warnMsg"
    }
}

# ---------- 腾讯研究院AI速递摄取（可选，-DigestUrl 传入当期文章链接） ----------
# 解析结构：板块名（短行）→ 一、标题 → 1./2./3. 要点 → 原文链接行
# 条目 source=腾讯研究院AI速递、digest=true、section=板块；url 指向速递中给出的原文
if ($DigestUrl) {
    if ($DigestUrl -notmatch 'mp\.weixin\.qq\.com') {
        Write-Log "WARN digest: 非 mp.weixin.qq.com 链接，跳过 -> $DigestUrl"
    } else {
        try {
            $dResp = Invoke-WebRequest -Uri $DigestUrl -UseBasicParsing -TimeoutSec 25 `
                -UserAgent 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36'
            $dHtml = [System.Text.Encoding]::UTF8.GetString($dResp.RawContentStream.ToArray())

            # 期号日期来自标题（「…AI速递 20261112」），取不到则用今天。
            # og:title 偶发缺失（页面变体），故全文兜底匹配「AI速递 日期」（h1 内同样含期号）
            $dDate = (Get-Date -Format 'yyyy-MM-dd')
            $dHit = [regex]::Match($dHtml, 'AI速递\s*([12]\d{7})')
            if ($dHit.Success) {
                try { $dDate = [datetime]::ParseExact($dHit.Groups[1].Value, 'yyyyMMdd', $null).ToString('yyyy-MM-dd') } catch {}
            }

            # 正文区段：对齐到 <div 开头，避免把开标签属性残行当文本
            $idPos = $dHtml.IndexOf('id="js_content"')
            if ($idPos -lt 0) { throw '未找到正文 js_content' }
            $divPos = $dHtml.LastIndexOf('<div', $idPos)
            $endPos = $dHtml.IndexOf('<script', $idPos)
            if ($endPos -lt 0) { $endPos = $dHtml.IndexOf('<style', $idPos) }
            if ($endPos -lt 0) { $endPos = $dHtml.Length }
            $dContent = $dHtml.Substring($divPos, $endPos - $divPos)

            $dText = $dContent -replace '<br\s*/?>', "`n" -replace '</p>', "`n" -replace '</section>', "`n" -replace '<[^>]+>', ''
            $dText = [System.Net.WebUtility]::HtmlDecode($dText)
            $dLines = @($dText -split "`n" | ForEach-Object { ([string]$_).Trim() } | Where-Object { $_ -ne '' })

            $dTitle = ''; $dPoints = @(); $dSection = ''; $dCount = 0
            foreach ($line in $dLines) {
                if ($line -match '^https?://') {
                    if ($dTitle -and $line -match 'mp\.weixin\.qq\.com' -and -not $seen.ContainsKey($dTitle)) {
                        $dSummary = ($dPoints -join "`n")
                        if ($dSummary.Length -gt 600) { $dSummary = $dSummary.Substring(0, 600) + '…' }
                        $newItems.Add([PSCustomObject]@{
                            title    = $dTitle
                            title_en = $dTitle
                            summary  = $dSummary
                            source   = '腾讯研究院AI速递'
                            url      = $line
                            date     = $dDate
                            digest   = $true
                            section  = $dSection
                        })
                        $seen[$dTitle] = $true
                        $dCount++
                    }
                    $dTitle = ''; $dPoints = @()
                    continue
                }
                if ($line -match '^([一二三四五六七八九十]{1,3}、)(.+)$') {
                    $dTitle = $Matches[2].Trim(); $dPoints = @()
                    continue
                }
                if ($line -match '^[1-9][.、．]') { $dPoints += $line; continue }
                if ($line.Length -le 12) { $dSection = $line; continue }
            }
            Write-Log "OK digest: 解析 $dCount 条（期号 $dDate）"
        } catch {
            $warnMsg = [string]$_.Exception.Message
            if ($warnMsg.Length -gt 300) { $warnMsg = $warnMsg.Substring(0, 300) + ' …' }
            Write-Log "WARN digest $DigestUrl -> $warnMsg"
        }
    }
}

Write-Log "抓取到新条目: $($newItems.Count)"
if ($newItems.Count -eq 0) {
    Write-Log "没有新条目，无需审核"
    exit 0
}

# ---------- 与已发布内容合并 ----------
$old = $null
try { $old = Get-Content -Raw -Encoding UTF8 $jsonPath | ConvertFrom-Json } catch {}

$oldTitles = @{}
$allItems = [System.Collections.Generic.List[object]]::new()
if ($old) {
    foreach ($it in @($old.current) + @($old.archive)) {
        $allItems.Add($it)
        $oldTitles[[string]$it.title] = $true
        if ($it.title_en) { $oldTitles[[string]$it.title_en] = $true }
    }
}
foreach ($it in $newItems) { $allItems.Add($it) }

$merged = @()
$titleSeen = @{}
$newTitles = [System.Collections.Generic.List[object]]::new()
foreach ($it in $allItems) {
    $t = ([string]$it.title).Trim()
    $te = ([string]$it.title_en).Trim()
    $dup = ($t -and $titleSeen.ContainsKey($t)) -or ($te -and $titleSeen.ContainsKey($te))
    if ($t -and -not $dup) {
        $titleSeen[$t] = $true
        if ($te) { $titleSeen[$te] = $true }
        $obj = [PSCustomObject]@{
            title    = $t
            title_en = [string]$it.title_en
            summary  = [string]$it.summary
            source   = [string]$it.source
            url      = [string]$it.url
            date     = [string]$it.date
            digest   = [bool]$it.digest
            section  = [string]$it.section
        }
        $merged += $obj
        if (-not $oldTitles.ContainsKey($t)) { $newTitles.Add($obj) }
    }
}
$merged = $merged | Sort-Object { [datetime]::ParseExact($_.date, 'yyyy-MM-dd', $null) } -Descending

$latestDate = $merged[0].date
$latestCutoff = ([datetime]::ParseExact($latestDate, 'yyyy-MM-dd', $null)).AddDays(-1).ToString('yyyy-MM-dd')
$currentList = [System.Collections.Generic.List[object]]::new()
$srcCount = @{}
$capPerSource = 1
# 腾讯研究院AI速递为人工聚合源，一期即多条高质量条目：放宽到 6 条进 current
$digestCapPerIssue = 6
foreach ($it in @($merged | Where-Object { $_.date -ge $latestCutoff })) {
    if ($currentList.Count -ge 15) { break }
    $src = [string]$it.source
    $cap = if ([bool]$it.digest) { $digestCapPerIssue } else { $capPerSource }
    if (-not $srcCount.ContainsKey($src)) { $srcCount[$src] = 0 }
    if ($srcCount[$src] -ge $cap) { continue }
    $currentList.Add($it)
    $srcCount[$src]++
}
$current = @($currentList)
# 归档 = 未进入 current 的全部条目（含被“每源限 1 条”挤掉的同日条目），
# 避免 date >= cutoff 但未选入 current 的条目既不进 current 也不进 archive 而丢失
$selKeys = @{}
foreach ($it in $currentList) {
    $selU = [string]$it.url; $selT = [string]$it.title
    if ($selU) { $selKeys[$selU] = $true }
    if ($selT) { $selKeys[$selT] = $true }
}
# arXiv 洪峰控制：周一常一次性投递上百篇论文，每天最多归档 5 条（取最新）。
# 关键：已发布条目（存在于 ai-news.json）永远保留——否则同日二次抓取时，新抓条目会
# 挤掉当天早些时候已发布的译文，批准后会从网站上消失。限流只约束尚未发布的新条目。
$pubUrls = @{}
if ($old) { foreach ($it in @($old.current) + @($old.archive)) { if ($it.url) { $pubUrls[[string]$it.url] = $true } } }
$arxivDayCount = @{}
$archivePool = [System.Collections.Generic.List[object]]::new()
# 第一遍：已发布条目先占当日名额并保留（不受 5 条限流约束）
foreach ($it in $merged) {
    $u = [string]$it.url; $t = [string]$it.title
    if (($u -and $selKeys.ContainsKey($u)) -or ($t -and $selKeys.ContainsKey($t))) { continue }
    if ($u -match 'arxiv\.org' -and $u -and $pubUrls.ContainsKey($u)) {
        $dayKey = [string]$it.date
        if (-not $arxivDayCount.ContainsKey($dayKey)) { $arxivDayCount[$dayKey] = 0 }
        $arxivDayCount[$dayKey]++
        $archivePool.Add($it)
    }
}
# 第二遍：新抓取条目（非 arXiv 不限；arXiv 只能用当日剩余名额）
foreach ($it in $merged) {
    $u = [string]$it.url; $t = [string]$it.title
    if (($u -and $selKeys.ContainsKey($u)) -or ($t -and $selKeys.ContainsKey($t))) { continue }
    if ($u -match 'arxiv\.org') {
        if ($u -and $pubUrls.ContainsKey($u)) { continue }  # 第一遍已加入
        $dayKey = [string]$it.date
        if (-not $arxivDayCount.ContainsKey($dayKey)) { $arxivDayCount[$dayKey] = 0 }
        $arxivDayCount[$dayKey]++
        if ($arxivDayCount[$dayKey] -gt 5) { continue }
    }
    $archivePool.Add($it)
}
$archive = @($archivePool | Select-Object -First 300)

$pending = [PSCustomObject]@{
    generated = (Get-Date -Format 'yyyy-MM-dd HH:mm:ss')
    updated   = (Get-Date -Format 'yyyy-MM-dd')
    current   = $current
    archive   = $archive
}
$pendingJson = $pending | ConvertTo-Json -Depth 6
[System.IO.File]::WriteAllText($pendingPath, $pendingJson, (New-Object System.Text.UTF8Encoding($false)))
Write-Log "待审稿已生成: current=$($current.Count) archive=$($archive.Count) 新增=$($newTitles.Count)"

# ---------- 生成预览页 ----------
function HtmlEncode([string]$s) { return [System.Net.WebUtility]::HtmlEncode($s) }

$sb = [System.Text.StringBuilder]::new()
[void]$sb.AppendLine('<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"><title>AI 动态 · 待发布预览</title><style>')
[void]$sb.AppendLine('body{font-family:"Microsoft YaHei",system-ui,sans-serif;background:#f4f6fb;margin:0;padding:32px 16px;color:#1c2430}')
[void]$sb.AppendLine('.wrap{max-width:960px;margin:0 auto}.head{background:linear-gradient(135deg,#1f3a8a,#3b82f6);color:#fff;border-radius:18px;padding:28px 32px;margin-bottom:20px}')
[void]$sb.AppendLine('.head h1{margin:0 0 8px;font-size:24px}.head p{margin:2px 0;opacity:.9;font-size:13px}')
[void]$sb.AppendLine('.stats{display:flex;gap:12px;flex-wrap:wrap;margin:16px 0}')
[void]$sb.AppendLine('.stat{background:#fff;border:1px solid #e2e8f0;border-radius:12px;padding:10px 18px;font-size:13px}')
[void]$sb.AppendLine('.stat b{font-size:18px;color:#1d4ed8}')
[void]$sb.AppendLine('h2{font-size:18px;margin:26px 0 12px}')
[void]$sb.AppendLine('.card{background:#fff;border:1px solid #e2e8f0;border-radius:14px;padding:16px 20px;margin-bottom:10px}')
[void]$sb.AppendLine('.card.new{border-left:4px solid #16a34a;background:#f7fdf8}')
[void]$sb.AppendLine('.badge{display:inline-block;background:#16a34a;color:#fff;font-size:11px;border-radius:999px;padding:2px 8px;margin-left:8px;vertical-align:2px}')
[void]$sb.AppendLine('.date{display:inline-block;background:#eef2ff;color:#4338ca;font-size:11px;border-radius:999px;padding:2px 8px;margin-right:8px}')
[void]$sb.AppendLine('.src{color:#64748b;font-size:12px;margin:4px 0 6px}')
[void]$sb.AppendLine('a{color:#1d4ed8;text-decoration:none}.sum{font-size:13px;color:#475569;line-height:1.6;white-space:pre-line}')
[void]$sb.AppendLine('.note{margin-top:24px;background:#fffbeb;border:1px solid #fde68a;border-radius:12px;padding:14px 18px;font-size:13px;color:#92400e}')
[void]$sb.AppendLine('</style></head><body><div class="wrap">')
[void]$sb.AppendLine('<div class="head"><h1>AI 动态 · 待发布预览</h1><p>生成时间：' + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + '</p><p>以下内容尚未发布，请审核后确认。</p></div>')
[void]$sb.AppendLine('<div class="stats"><span class="stat">最新动态 <b>' + $current.Count + '</b></span><span class="stat">本期新增 <b>' + $newTitles.Count + '</b></span><span class="stat">最新日期 <b>' + $latestDate + '</b></span></div>')

[void]$sb.AppendLine('<h2>本期新增（' + $newTitles.Count + ' 条）</h2>')
foreach ($it in $newTitles) {
    $t = HtmlEncode $it.title; $s = HtmlEncode $it.summary; $src = HtmlEncode $it.source; $url = HtmlEncode $it.url
    [void]$sb.AppendLine('<div class="card new"><span class="date">' + $it.date + '</span><a href="' + $url + '" target="_blank"><b>' + $t + '</b></a><span class="badge">新增</span><div class="src">' + $src + '</div><div class="sum">' + $s + '</div></div>')
}

[void]$sb.AppendLine('<h2>最新动态（前 ' + $current.Count + ' 条）</h2>')
foreach ($it in $current) {
    $t = HtmlEncode $it.title; $s = HtmlEncode $it.summary; $src = HtmlEncode $it.source; $url = HtmlEncode $it.url
    [void]$sb.AppendLine('<div class="card"><span class="date">' + $it.date + '</span><a href="' + $url + '" target="_blank"><b>' + $t + '</b></a><div class="src">' + $src + '</div><div class="sum">' + $s + '</div></div>')
}

[void]$sb.AppendLine('<div class="note">确认内容无误后，在 Codex 对话框中回复「批准今天的 AI 动态」发布；或运行 <b>scripts\approve-ai-news.bat</b>。发布前不会影响线上网站。</div>')
[void]$sb.AppendLine('</div></body></html>')
[System.IO.File]::WriteAllText($previewPath, $sb.ToString(), (New-Object System.Text.UTF8Encoding($false)))

Write-Log "预览页已生成: $previewPath"
$isCI = ($env:GITHUB_ACTIONS -eq 'true')
if ($Headless -or $isCI) {
    Write-Log "Headless 模式：不弹提示"
} else {
    Write-Host ""
    Write-Host "============================================================"
    Write-Host " 今日 AI 动态待审稿已生成（本期新增 $($newTitles.Count) 条）"
    Write-Host " 请在 Codex 对话框中打开本项目，回复「批准今天的 AI 动态」"
    Write-Host "============================================================"
    Write-Log "请到 Codex 对话框中审批（回复「批准今天的 AI 动态」）"
}

# ---------- 每日快照（可视化底座，脚本内部自兜底，失败不影响主流程） ----------
try {
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'news-snapshot.ps1') -Trigger daily
} catch {
    Write-Log ("快照调用异常（已忽略）: {0}" -f $_.Exception.Message)
}

Write-Log "===== 抓取结束 ====="
