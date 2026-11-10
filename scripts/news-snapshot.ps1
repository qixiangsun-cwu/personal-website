# ============================================================
# 个人网站 · AI 资讯每日快照（可视化底座 · Phase 0）
# 读 ai-news.json（current + archive），按 URL 去重、只增不删，
# 增量追加到 news-snapshots/all-items.jsonl（JSONL，UTF8 无 BOM）。
# 用于后续可视化统计页的历史快照：站上 ai-news.json 会滚动缩短，
# 这里保存的条目不会被删除，词云/榜单/时间线都有据可查。
#
# 用法：
#   - 由 update-ai-news.ps1（每日抓取后）与 approve-ai-news.ps1（发布后）调用
#   - 也可手动运行：powershell -File scripts\news-snapshot.ps1 [-Trigger approve|daily|manual]
# 全流程 try/catch，任何异常只记日志并以 0 退出，绝不影响每日主流程。
# ============================================================
param([string]$Trigger = 'daily')

$ErrorActionPreference = 'Stop'

$siteDir  = Split-Path -Parent $PSScriptRoot
$jsonPath = Join-Path $siteDir 'ai-news.json'
$snapDir  = Join-Path $siteDir 'news-snapshots'
$snapPath = Join-Path $snapDir 'all-items.jsonl'
$logPath  = Join-Path $siteDir '.news-update.log'

function Write-SnapLog([string]$msg) {
    $line = "[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg
    try { Add-Content -Path $logPath -Value $line -Encoding UTF8 } catch {}
}

try {
    if (-not (Test-Path $jsonPath)) {
        Write-SnapLog "快照跳过（触发=$Trigger）：ai-news.json 不存在"
        exit 0
    }

    # ---------- 1. 读取已发布条目（current 在前，按 URL 去重） ----------
    $raw = Get-Content -Raw -Encoding UTF8 $jsonPath | ConvertFrom-Json
    $items = @()
    if ($raw.current)  { $items += @($raw.current) }
    if ($raw.archive)  { $items += @($raw.archive) }
    if ($items.Count -eq 0) {
        Write-SnapLog "快照跳过（触发=$Trigger）：ai-news.json 无条目"
        exit 0
    }

    $byUrl = [ordered]@{}
    foreach ($it in $items) {
        $u = [string]$it.url
        if ($u -and -not $byUrl.Contains($u)) { $byUrl[$u] = $it }
    }

    # ---------- 2. 读取已有快照（累计库，只增不删） ----------
    $existingUrls = @{}
    $existingCount = 0
    if (Test-Path $snapPath) {
        foreach ($line in (Get-Content -Encoding UTF8 $snapPath)) {
            if ([string]::IsNullOrWhiteSpace($line)) { continue }
            $existingCount++
            try {
                $o = $line | ConvertFrom-Json
                $u = [string]$o.url
                if ($u) { $existingUrls[$u] = $true }
            } catch {}
        }
    }

    # ---------- 3. 计算增量并追加 ----------
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    $sb = New-Object System.Text.StringBuilder
    $added = 0
    foreach ($it in $byUrl.Values) {
        $u = [string]$it.url
        if ($existingUrls.ContainsKey($u)) { continue }

        $rec = [ordered]@{
            url     = $u
            title   = [string]$it.title
            summary = [string]$it.summary
            date    = [string]$it.date
            source  = [string]$it.source
            addedAt = (Get-Date -Format 'yyyy-MM-dd')
        }
        # 原英文标题（翻译前）如存在则保留，供词云统计英文实体
        if ($it.PSObject.Properties['title_en'] -and $it.title_en) {
            $rec['title_en'] = [string]$it.title_en
        }
        [void]$sb.Append(($rec | ConvertTo-Json -Compress) + "`n")
        $added++
    }

    if ($added -gt 0) {
        [System.IO.Directory]::CreateDirectory($snapDir) | Out-Null
        [System.IO.File]::AppendAllText($snapPath, $sb.ToString(), $utf8NoBom)
    }

    $total = $existingCount + $added
    Write-SnapLog ("快照完成（触发={0}） 新增={1} 全量={2} 累计={3}" -f $Trigger, $added, $byUrl.Count, $total)
    exit 0
} catch {
    # 可视化底座出问题不能拖垮每日抓取/发布，只记日志
    Write-SnapLog ("快照失败（触发={0}，已忽略）：{1}" -f $Trigger, $_.Exception.Message)
    exit 0
}
