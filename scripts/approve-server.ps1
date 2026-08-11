# ============================================================
# 个人网站 · AI 动态审批服务器（本地）
#
# 作用：
#   1) 把站点根目录托管到 http://localhost:8866
#      （解决 file:// 直接打开页面时 fetch 被 CORS 拦截的问题）
#   2) 提供审批台 /approve.html，可查看待审稿并一键「批准发布」
#   3) GET  /api/status    查询待审稿 / 已发布状态
#   4) POST /api/approve   批准发布（待审稿 -> ai-news.json，git 提交并推送）
#   5) POST /api/shutdown  停止本服务器
#
# 用法：
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\approve-server.ps1
#   或双击 scripts\start-approve.bat
# 参数：
#   -Port 8866   监听端口
#   -NoOpen      启动后不自动打开浏览器
#   -Lan         绑定 0.0.0.0（局域网设备可访问；需管理员先执行
#                netsh http add urlacl url=http://+:8866/ user=Everyone）
# ============================================================
param(
    [int]$Port = 8866,
    [switch]$NoOpen,
    [switch]$Lan
)

$ErrorActionPreference = 'Stop'
$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$siteDir = Split-Path -Parent $PSScriptRoot
$tokenFile = Join-Path $siteDir '.approve-token'
$pendingPath = Join-Path $siteDir 'ai-news.pending.json'
$jsonPath = Join-Path $siteDir 'ai-news.json'
$logPath = Join-Path $siteDir '.news-update.log'

function Write-Log([string]$msg) {
    $line = "[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg
    try { Add-Content -Path $logPath -Value $line -Encoding UTF8 } catch {}
    Write-Host $line
}

# ---------- 审批 token（首次启动生成，保存到项目根目录 .approve-token） ----------
$token = ''
try {
    if (Test-Path $tokenFile) {
        $token = ([System.IO.File]::ReadAllText($tokenFile)).Trim()
    } else {
        $chars = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'.ToCharArray()
        $token = -join (1..32 | ForEach-Object { $chars | Get-Random })
        [System.IO.File]::WriteAllText($tokenFile, $token, (New-Object System.Text.UTF8Encoding($false)))
    }
} catch {
    Write-Log "审批 token 初始化失败: $($_.Exception.Message)"
}

# ---------- 单实例检查 ----------
$existing = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($existing) {
    Write-Log "审批服务器已在运行（端口 $Port），如需重启请先停止旧进程。"
    if (-not $NoOpen) { try { Start-Process "http://localhost:$Port/approve" } catch {} }
    exit 0
}

$listener = [System.Net.HttpListener]::new()
if ($Lan) {
    $listener.Prefixes.Add("http://*:$Port/")
} else {
    $listener.Prefixes.Add("http://localhost:$Port/")
}
try {
    $listener.Start()
} catch {
    Write-Log "审批服务器启动失败：$($_.Exception.Message)"
    Write-Log "若使用了 -Lan，请先以管理员运行：netsh http add urlacl url=http://+:8866/ user=Everyone"
    exit 1
}
Write-Log "审批服务器已启动: http://localhost:$Port/approve"
if (-not $NoOpen) { try { Start-Process "http://localhost:$Port/approve" } catch {} }

$mime = @{
    '.html' = 'text/html; charset=utf-8'
    '.htm'  = 'text/html; charset=utf-8'
    '.css'  = 'text/css; charset=utf-8'
    '.js'   = 'application/javascript; charset=utf-8'
    '.json' = 'application/json; charset=utf-8'
    '.svg'  = 'image/svg+xml'
    '.png'  = 'image/png'
    '.jpg'  = 'image/jpeg'
    '.jpeg' = 'image/jpeg'
    '.gif'  = 'image/gif'
    '.webp' = 'image/webp'
    '.ico'  = 'image/x-icon'
    '.txt'  = 'text/plain; charset=utf-8'
    '.pdf'  = 'application/pdf'
    '.woff' = 'font/woff'
    '.woff2'= 'font/woff2'
    '.mp4'  = 'video/mp4'
    '.webm' = 'video/webm'
}

function Send-Raw([System.Net.HttpListenerResponse]$res, [byte[]]$bytes, [string]$contentType, [int]$status = 200) {
    $res.StatusCode = $status
    $res.ContentType = $contentType
    $res.ContentLength64 = $bytes.Length
    $res.OutputStream.Write($bytes, 0, $bytes.Length)
    $res.OutputStream.Close()
}

function Send-Text($res, [string]$text, [string]$contentType = 'text/plain; charset=utf-8', [int]$status = 200) {
    Send-Raw $res ([System.Text.Encoding]::UTF8.GetBytes($text)) $contentType $status
}

function Send-Json($res, $obj, [int]$status = 200) {
    $json = $obj | ConvertTo-Json -Depth 8 -Compress
    Send-Text $res $json 'application/json; charset=utf-8' $status
}

function Read-Body($req) {
    if ($req.ContentLength64 -le 0) { return '' }
    $reader = New-Object System.IO.StreamReader($req.InputStream, [System.Text.Encoding]::UTF8)
    try { return $reader.ReadToEnd() } finally { $reader.Close() }
}

function Get-Status {
    $published = $null
    $pending = $null
    try { $published = Get-Content -Raw -Encoding UTF8 $jsonPath | ConvertFrom-Json } catch {}
    try { $pending = Get-Content -Raw -Encoding UTF8 $pendingPath | ConvertFrom-Json } catch {}

    $pubTitles = @{}
    if ($published) {
        foreach ($it in @($published.current) + @($published.archive)) {
            if ($it.title) { $pubTitles[[string]$it.title] = $true }
        }
    }
    $newTitles = @()
    if ($pending) {
        foreach ($it in @($pending.current)) {
            $t = [string]$it.title
            if ($t -and -not $pubTitles.ContainsKey($t)) {
                $newTitles += [PSCustomObject]@{
                    title  = $t
                    date   = [string]$it.date
                    source = [string]$it.source
                }
            }
        }
    }
    $pendingObj = [PSCustomObject]@{ exists = $false; generated = ''; updated = ''; currentCount = 0; archiveCount = 0; newCount = 0; newTitles = @() }
    if ($pending) {
        $pendingObj = [PSCustomObject]@{
            exists       = $true
            generated    = [string]$pending.generated
            updated      = [string]$pending.updated
            currentCount = @($pending.current).Count
            archiveCount = @($pending.archive).Count
            newCount     = $newTitles.Count
            newTitles    = $newTitles
        }
    }
    $publishedObj = [PSCustomObject]@{ updated = ''; currentCount = 0; archiveCount = 0 }
    if ($published) {
        $publishedObj = [PSCustomObject]@{
            updated      = [string]$published.updated
            currentCount = @($published.current).Count
            archiveCount = @($published.archive).Count
        }
    }
    return [PSCustomObject]@{
        server    = [PSCustomObject]@{ port = $Port; running = $true }
        pending   = $pendingObj
        published = $publishedObj
    }
}

$script:stopRequested = $false
try {
    while ($listener.IsListening -and -not $script:stopRequested) {
        $ctx = $null
        try {
            $ctx = $listener.GetContext()
        } catch {
            if ($listener.IsListening) { Write-Log "请求处理异常: $($_.Exception.Message)" }
            continue
        }
        $req = $ctx.Request
        $res = $ctx.Response
        $rawPath = $req.Url.AbsolutePath
        $path = [System.Uri]::UnescapeDataString($rawPath)
        if ($path -eq '/' -or $path -eq '') { $path = '/index.html' }

        if ($path -eq '/api/status') {
            Send-Json $res (Get-Status)
            continue
        }

        if ($path -eq '/api/approve' -or $path -eq '/api/shutdown') {
            $body = Read-Body $req
            $payload = $null
            try { $payload = $body | ConvertFrom-Json } catch {}
            $got = ''
            if ($payload -and $payload.token) { $got = [string]$payload.token }
            if ($got -ne $token) {
                Send-Json $res @{ ok = $false; message = 'token 不正确，已拒绝操作' } 403
                continue
            }
            if ($path -eq '/api/shutdown') {
                Send-Json $res @{ ok = $true; message = '正在停止审批服务器' }
                $script:stopRequested = $true
                continue
            }
            # 批准发布：调用审批脚本（headless），把输出原样返回给页面
            try {
                $ps = Join-Path $PSScriptRoot 'approve-ai-news.ps1'
                $oldEap = $ErrorActionPreference
                $ErrorActionPreference = 'Continue'
                try {
                    $out = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $ps -Headless 2>&1 | Out-String
                    $exit = $LASTEXITCODE
                } finally {
                    $ErrorActionPreference = $oldEap
                }
                $m = [regex]::Match([string]$out, 'APPROVE_RESULT\s+(\{.*\})')
                if ($m.Success) {
                    try {
                        $r = $m.Groups[1].Value | ConvertFrom-Json
                        Send-Json $res @{ ok = [bool]$r.ok; exitCode = $exit; message = [string]$r.message }
                        continue
                    } catch {}
                }
                Send-Json $res @{ ok = ($exit -eq 0); exitCode = $exit; message = ([string]$out).Trim() } 200
            } catch {
                Send-Json $res @{ ok = $false; message = "批准发布出错：$($_.Exception.Message)" } 500
            }
            continue
        }

        # ---------- 静态文件 ----------
        $rel = $path.TrimStart('/')
        $full = [System.IO.Path]::GetFullPath((Join-Path $siteDir $rel))
        $cmp = $siteDir.TrimEnd('\') + '\'
        if (-not $full.StartsWith($cmp, [System.StringComparison]::OrdinalIgnoreCase)) {
            Send-Text $res 'forbidden' 'text/plain' 403
            continue
        }
        if (Test-Path -LiteralPath $full -PathType Leaf) {
            $ext = [System.IO.Path]::GetExtension($full).ToLowerInvariant()
            $ct = 'application/octet-stream'
            if ($mime.ContainsKey($ext)) { $ct = $mime[$ext] }
            $bytes = [System.IO.File]::ReadAllBytes($full)
            if ($rel -eq 'approve.html') {
                $text = [System.Text.Encoding]::UTF8.GetString($bytes)
                $text = $text.Replace('__APPROVE_TOKEN__', $token)
                $bytes = [System.Text.Encoding]::UTF8.GetBytes($text)
            }
            Send-Raw $res $bytes $ct
        } else {
            Send-Text $res '404 not found' 'text/plain' 404
        }
    }
} finally {
    try { $listener.Stop(); $listener.Close() } catch {}
    Write-Log "审批服务器已停止"
}
