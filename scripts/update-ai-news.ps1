# ============================================================
# 个人网站 · AI 资讯每日自动抓取
# 功能：抓取 RSS -> 更新 ai-news.json -> 自动提交并推送 GitHub
# 由 Windows 计划任务每天 08:00 调用
# ============================================================
$ErrorActionPreference = 'Stop'

$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$siteDir = Split-Path -Parent $PSScriptRoot
$jsonPath = Join-Path $siteDir 'ai-news.json'
$logPath  = Join-Path $siteDir '.news-update.log'

function Write-Log([string]$msg) {
    $line = "[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg
    Add-Content -Path $logPath -Value $line -Encoding UTF8
}

Write-Log "===== 开始每日 AI 资讯更新 ====="

# ---------- 定位 git ----------
$git = (Get-Command git -ErrorAction SilentlyContinue).Source
if (-not $git) {
    $candidates = @(
        "$env:LOCALAPPDATA\Programs\Git\cmd\git.exe",
        "C:\Program Files\Git\cmd\git.exe",
        "C:\Users\Legion\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\git\cmd\git.exe",
        "C:\Users\Legion\.workbuddy\vendor\PortableGit\cmd\git.exe"
    )
    foreach ($c in $candidates) {
        if (Test-Path $c) { $git = $c; break }
    }
}
if (-not $git) {
    Write-Log "ERROR: 未找到 git，退出"
    exit 1
}
Write-Log "使用 git: $git"

# ---------- RSS 源 ----------
$feeds = @(
    'https://www.ithome.com/rss/',
    'https://36kr.com/feed',
    'https://www.qbitai.com/feed',
    'https://www.jiqizhixin.com/rss',
    'https://www.cnbeta.com.tw/backend.php',
    'https://techcrunch.com/category/artificial-intelligence/feed/',
    'https://openai.com/blog/rss.xml',
    'https://huggingface.co/blog/feed.xml'
)

$keywords = 'AI|人工智能|大模型|智能体|GPT|Gemini|Claude|OpenAI|Anthropic|DeepSeek|机器人|算力|芯片|自动驾驶|多模态|AIGC|LLM|Agent|NVIDIA|英伟达|Hugging Face|Qwen|Kimi|豆包|元宝'

$newItems = [System.Collections.Generic.List[object]]::new()
$seen = @{}

foreach ($feed in $feeds) {
    try {
        $resp = Invoke-WebRequest -Uri $feed -UseBasicParsing -TimeoutSec 20
        $rss = [xml]$resp.Content
        $items = @($rss.rss.channel.item)
        if ($items.Count -eq 0) { $items = @($rss.feed.entry) }
        foreach ($item in $items) {
            $title = [string]$item.title
            if (-not $title -or $title -notmatch $keywords) { continue }

            $pub = $null
            if ($item.pubDate) { $pub = [string]$item.pubDate }
            elseif ($item.published) { $pub = [string]$item.published }
            elseif ($item.updated) { $pub = [string]$item.updated }
            elseif ($item.date) { $pub = [string]$item.date }
            if (-not $pub) { continue }

            $dt = $null
            if (-not [DateTime]::TryParse($pub, [ref]$dt)) { continue }
            if ($dt -lt (Get-Date).AddDays(-2)) { continue }

            $dateStr = $dt.ToString('yyyy-MM-dd')
            $link = if ($item.link) { [string]$item.link } else { [string]$item.id }
            if (-not $link -or -not $link.StartsWith('http')) { continue }

            $key = $title.Trim()
            if ($seen.ContainsKey($key)) { continue }
            $seen[$key] = $true

            $desc = if ($item.description) { [string]$item.description } else { [string]$item.summary }
            $summary = ''
            if ($desc) {
                $summary = ($desc -replace '<[^>]+>', ' ' -replace '\s+', ' ').Trim()
                if ($summary.Length -gt 120) { $summary = $summary.Substring(0, 120) + '…' }
            }

            $source = if ($item.source) { [string]$item.source } else {
                ($feed -replace '^https?://', '') -split '/' | Select-Object -First 1
            }

            $newItems.Add([PSCustomObject]@{
                title   = $title.Trim()
                summary = $summary
                source  = $source
                url     = $link
                date    = $dateStr
            })
        }
        Write-Log "OK feed: $feed"
    } catch {
        Write-Log "WARN feed $feed -> $($_.Exception.Message)"
    }
}

Write-Log "抓取到新条目: $($newItems.Count)"
if ($newItems.Count -eq 0) {
    Write-Log "没有抓取到新条目，跳过更新"
    exit 0
}

# ---------- 合并进 ai-news.json ----------
try {
    $old = Get-Content -Raw -Encoding UTF8 $jsonPath | ConvertFrom-Json
} catch {
    $old = $null
}

$allItems = [System.Collections.Generic.List[object]]::new()
if ($old) {
    foreach ($it in @($old.current) + @($old.archive)) { $allItems.Add($it) }
}
foreach ($it in $newItems) { $allItems.Add($it) }

$merged = @()
$titleSeen = @{}
foreach ($it in $allItems) {
    $t = ([string]$it.title).Trim()
    if ($t -and -not $titleSeen.ContainsKey($t)) {
        $titleSeen[$t] = $true
        $merged += [PSCustomObject]@{
            title   = $t
            summary = [string]$it.summary
            source  = [string]$it.source
            url     = [string]$it.url
            date    = [string]$it.date
        }
    }
}

$merged = $merged | Sort-Object { [datetime]::ParseExact($_.date, 'yyyy-MM-dd', $null) } -Descending

# “最新动态”保留最近两天，避免单日条目过少
$latestDate = $merged[0].date
$latestCutoff = ([datetime]::ParseExact($latestDate, 'yyyy-MM-dd', $null)).AddDays(-1).ToString('yyyy-MM-dd')
$current = @($merged | Where-Object { $_.date -ge $latestCutoff } | Select-Object -First 15)
$archive = @($merged | Where-Object { $_.date -lt $latestCutoff } | Select-Object -First 300)

$result = [PSCustomObject]@{
    updated = (Get-Date -Format 'yyyy-MM-dd')
    current = $current
    archive = $archive
}

$jsonText = $result | ConvertTo-Json -Depth 5
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($jsonPath, $jsonText, $utf8NoBom)
Write-Log "ai-news.json 更新完成: current=$($current.Count) archive=$($archive.Count)"

# ---------- 提交并推送 ----------
Push-Location $siteDir
try {
    & $git add ai-news.json
    $status = & $git status --porcelain -- ai-news.json
    if ($status) {
        $msgFile = Join-Path $env:TEMP ("ai-news-msg-" + (Get-Date -Format 'yyyyMMddHHmmss') + ".txt")
        [System.IO.File]::WriteAllText($msgFile, "news: AI新闻更新 $(Get-Date -Format 'yyyy-MM-dd')", (New-Object System.Text.UTF8Encoding($false)))
        & $git -c user.name="Qi-Xiang Sun" -c user.email="qixiangsun@126.com" commit -F $msgFile
        Remove-Item -LiteralPath $msgFile -ErrorAction SilentlyContinue
        $pushOut = & $git push origin main 2>&1 | Out-String
        Write-Log "push 结果: $pushOut"
    } else {
        Write-Log "无变更，跳过提交"
    }
} catch {
    Write-Log "ERROR git: $($_.Exception.Message)"
} finally {
    Pop-Location
}

Write-Log "===== 更新结束 ====="
