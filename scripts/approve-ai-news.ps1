# ============================================================
# 个人网站 · AI 资讯待审稿发布
# 把 ai-news.pending.json 发布为 ai-news.json，提交并推送到 GitHub
# 用法：审核完预览页后双击 scripts\approve-ai-news.bat
# ============================================================
$ErrorActionPreference = 'Stop'
$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$siteDir = Split-Path -Parent $PSScriptRoot
$pendingPath = Join-Path $siteDir 'ai-news.pending.json'
$jsonPath = Join-Path $siteDir 'ai-news.json'
$previewPath = Join-Path $siteDir 'ai-news-preview.html'
$logPath = Join-Path $siteDir '.news-update.log'

function Write-Log([string]$msg) {
    $line = "[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg
    Add-Content -Path $logPath -Value $line -Encoding UTF8
}

Write-Log "===== 开始发布待审稿 ====="

if (-not (Test-Path $pendingPath)) {
    Write-Host "没有找到待审稿（ai-news.pending.json），无需发布。" -ForegroundColor Yellow
    pause
    exit 0
}

try {
    $pending = Get-Content -Raw -Encoding UTF8 $pendingPath | ConvertFrom-Json
    $curCount = @($pending.current).Count
    $arcCount = @($pending.archive).Count
    if ($curCount -eq 0 -and $arcCount -eq 0) { throw "待审稿内容为空" }
} catch {
    Write-Host "待审稿无效：$($_.Exception.Message)" -ForegroundColor Red
    pause
    exit 1
}

# 待审稿 -> 正式数据
[System.IO.File]::Copy($pendingPath, $jsonPath, $true)
Write-Host ("已更新 ai-news.json（最新 {0} 条 / 归档 {1} 条）" -f $curCount, $arcCount)

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
    Write-Host "未找到 git：本地文件已更新，但未能推送。" -ForegroundColor Yellow
    pause
    exit 1
}

$dateStr = [string]$pending.updated
$msgFile = Join-Path $env:TEMP ("ai-news-msg-" + (Get-Date -Format 'yyyyMMddHHmmss') + ".txt")
[System.IO.File]::WriteAllText($msgFile, "news: AI新闻更新 $dateStr", (New-Object System.Text.UTF8Encoding($false)))

Push-Location $siteDir
$oldEap = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
try {
    & $git add ai-news.json
    & $git -c user.name="Qi-Xiang Sun" -c user.email="qixiangsun@126.com" commit -F $msgFile
    if ($LASTEXITCODE -ne 0) { throw "git commit 失败" }

    $pushed = $false
    for ($i = 1; $i -le 4; $i++) {
        $null = & $git push origin main 2>&1 | Out-String
        if ($LASTEXITCODE -eq 0) { $pushed = $true; break }
        Start-Sleep -Seconds 5
    }

    if ($pushed) {
        Write-Log "发布成功并已推送"
        Write-Host "已发布并推送到 GitHub ✓" -ForegroundColor Green
    } else {
        Write-Log "已提交但推送失败（网络问题）"
        Write-Host "已提交，但推送失败（网络问题）。可稍后重跑本脚本，或手动推送。" -ForegroundColor Yellow
    }
} catch {
    Write-Log "发布出错: $($_.Exception.Message)"
    Write-Host "发布出错：$($_.Exception.Message)" -ForegroundColor Red
} finally {
    $ErrorActionPreference = $oldEap
    Pop-Location
}

Remove-Item -LiteralPath $msgFile -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $pendingPath -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $previewPath -ErrorAction SilentlyContinue
Write-Log "===== 发布结束 ====="
pause
