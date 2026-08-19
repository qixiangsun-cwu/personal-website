# ============================================================
# 个人网站 · AI 资讯每日抓取（待审核模式）
# 抓取 RSS -> 生成 ai-news.pending.json + 预览页 ai-news-preview.html
# 不提交、不推送；生成后自动打开本地审批台 http://localhost:8866/approve
# 由 Windows 计划任务每天 08:00 调用；-Headless 供 CI/手动无界面调用
# ============================================================
param([switch]$Headless)

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

$keywords = 'AI|人工智能|大模型|智能体|GPT|Gemini|Claude|OpenAI|Anthropic|DeepSeek|机器人|算力|芯片|自动驾驶|多模态|AIGC|LLM|Agent|NVIDIA|英伟达|Hugging Face|Qwen|Kimi|豆包|元宝'

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
    if ($t -and -not $titleSeen.ContainsKey($t)) {
        $titleSeen[$t] = $true
        $obj = [PSCustomObject]@{
            title    = $t
            title_en = [string]$it.title_en
            summary  = [string]$it.summary
            source   = [string]$it.source
            url      = [string]$it.url
            date     = [string]$it.date
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
$capPerSource = 3
foreach ($it in @($merged | Where-Object { $_.date -ge $latestCutoff })) {
    if ($currentList.Count -ge 15) { break }
    $src = [string]$it.source
    if (-not $srcCount.ContainsKey($src)) { $srcCount[$src] = 0 }
    if ($srcCount[$src] -ge $capPerSource) { continue }
    $currentList.Add($it)
    $srcCount[$src]++
}
$current = @($currentList)
$archive = @($merged | Where-Object { $_.date -lt $latestCutoff } | Select-Object -First 300)

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
Write-Log "===== 抓取结束 ====="
