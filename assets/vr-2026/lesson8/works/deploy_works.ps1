# deploy_works.ps1 —— 把第 8 次课作品墙（全部提交）部署到课程网站
#
# 用法（在任意目录）：
#   powershell -ExecutionPolicy Bypass -File "D:\各种工作\个人网站\assets\vr-2026\lesson8\works\deploy_works.ps1"
#
# 前提：已先运行 build_works.py 生成 works-data.js 与截图。
# 本脚本只提交作品墙相关文件：assets/vr-2026/lesson8/works/ 与 vr-2026-lesson8-works.html
# 推送需经本机代理（git 已配置 127.0.0.1:7890）；若代理未开，先在 FlClash 打开系统代理。
# 本文件保存为 UTF-8 with BOM（PowerShell 5.1 对无 BOM 中文脚本会按 ANSI 解析报错）。

$ErrorActionPreference = 'Stop'
$env:GIT_TERMINAL_PROMPT = '0'

$WorksDir = Split-Path -Parent $MyInvocation.MyCommand.Path       # ...\assets\vr-2026\lesson8\works
$Repo = Resolve-Path (Join-Path $WorksDir '..\..\..\..') | Select-Object -ExpandProperty Path
$WorkPage = 'vr-2026-lesson8-works.html'

Write-Host "站点仓库：$Repo"
if (-not (Test-Path (Join-Path $Repo '.git'))) { Write-Host "[X] 不是 git 仓库：$Repo"; exit 1 }

# 0. 确认数据不是空的
$dataFile = Join-Path $WorksDir 'works-data.js'
if (-not (Test-Path $dataFile)) { Write-Host "[X] 缺 works-data.js，请先运行 build_works.py"; exit 1 }
$dataText = [System.IO.File]::ReadAllText($dataFile, [System.Text.Encoding]::UTF8)
if ($dataText -notmatch "id\s*:") { Write-Host "[!] works-data.js 里没有作品（数组为空），仍会部署空作品墙。" }

# 1. 在本地 main 上提交一次（只加作品墙相关文件）
Push-Location $Repo
git add -- "assets/vr-2026/lesson8/works" $WorkPage 2>&1 | Out-Null
$status = git status --porcelain
if (-not $status) {
    Write-Host "[信息] 没有新改动，跳过提交；直接尝试推送。"
} else {
    git commit -m "feat(VR第8次课): 作品墙更新（全部提交作品）" 2>&1 | Select-Object -Last 3
    if ($LASTEXITCODE -ne 0) { Write-Host "[X] 提交失败"; Pop-Location; exit 1 }
}

# 2. 用「以 origin/main 为基」的临时分支推送，避开本地主线历史分叉
$ok = $false
for ($i = 1; $i -le 8; $i++) {
    Write-Host "=== 部署尝试 $i ==="
    git -c http.sslBackend=openssl fetch origin main 2>&1 | Select-Object -Last 1
    if ($LASTEXITCODE -ne 0) { Start-Sleep -Seconds 5; continue }

    git checkout -B works-deploy origin/main 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { Write-Host "[X] 切分支失败"; break }

    git checkout main -- "assets/vr-2026/lesson8/works" $WorkPage 2>&1 | Out-Null
    git add -- "assets/vr-2026/lesson8/works" $WorkPage 2>&1 | Out-Null
    git commit -m "feat(VR第8次课): 作品墙更新（全部提交作品）" 2>&1 | Select-Object -Last 2
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[信息] 相对 origin/main 无差异，可能已经部署过。"
        git checkout main 2>&1 | Out-Null
        Pop-Location
        exit 0
    }

    git -c http.sslBackend=openssl push origin HEAD:main 2>&1 | Select-Object -Last 3
    if ($LASTEXITCODE -eq 0) { $ok = $true; break }

    git checkout main 2>&1 | Out-Null
    Start-Sleep -Seconds 6
}

git checkout main 2>&1 | Out-Null
Pop-Location

if ($ok) {
    Write-Host ""
    Write-Host "[OK] 已推送，Cloudflare Pages 约 1 分钟生效："
    Write-Host "     https://www.qixiangsun.com.cn/vr-2026-lesson8-works"
    exit 0
} else {
    Write-Host "[!] 推送未成功（多为代理/网络或远端又更新）。可稍后重试，或让 DSH 助手代为部署。"
    exit 1
}
