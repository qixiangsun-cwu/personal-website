# =============================================================================
# serve.ps1 —— 零依赖静态服务器（给学生自带电脑用）
#
# 为什么不用 python -m http.server / npx serve？
#   学生电脑不一定装了 Python 或 Node。这个脚本只用 Windows 自带的 PowerShell。
# 为什么不用 HttpListener？
#   HttpListener 绑定非 localhost 地址需要管理员 + netsh urlacl。这里用裸 TcpListener，
#   普通用户权限就能跑。
#
# 用法：
#   powershell -ExecutionPolicy Bypass -File serve.ps1
#   powershell -ExecutionPolicy Bypass -File serve.ps1 -Root "D:\我的VR作品" -Port 8080
#
# 它会打印两个地址：
#   ① http://127.0.0.1:8080/  → 配合 USB 转发（adb reverse）给头显用，安全上下文 ✅
#   ② http://<本机IP>:8080/   → 头显连同一 Wi-Fi 时用（需要在头显浏览器里加白名单）
# =============================================================================
param(
  [string]$Root = '',
  [int]$Port = 8080
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

if ([string]::IsNullOrWhiteSpace($Root)) { $Root = (Get-Location).Path }
if (-not (Test-Path -LiteralPath $Root)) { Write-Host "[错误] 目录不存在：$Root" -ForegroundColor Red; exit 1 }
$Root = (Resolve-Path -LiteralPath $Root).Path
if (-not (Test-Path -LiteralPath (Join-Path $Root 'index.html')) -and
    -not (Test-Path -LiteralPath (Join-Path $Root 'index.htm'))) {
  Write-Host "[提示] 这个目录里没有 index.html。直接用 http://<地址>:端口/你的文件名.html 打开也可以。" -ForegroundColor Yellow
}

# ---------------------------------------------------------------- 找本机局域网 IP
function Get-LanIp {
  try {
    $u = New-Object System.Net.Sockets.UdpClient
    $u.Connect('8.8.8.8', 80)          # 只用来问系统"出口网卡是哪个"，不真的发包
    $ip = $u.Client.LocalEndPoint.Address.ToString()
    $u.Close()
    if ($ip -and $ip -notmatch '^127\.') { return $ip }
  } catch { }
  try {
    $c = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop |
         Where-Object { $_.IPAddress -notmatch '^127\.' } |
         Sort-Object InterfaceMetric | Select-Object -First 1
    if ($c) { return $c.IPAddress }
  } catch { }
  return '127.0.0.1'
}
$LanIp = Get-LanIp

# ------------------------------------------------------------------ MIME 类型表
$Mime = @{
  '.html'='text/html; charset=utf-8'; '.htm'='text/html; charset=utf-8'
  '.js'='text/javascript; charset=utf-8'; '.mjs'='text/javascript; charset=utf-8'
  '.css'='text/css; charset=utf-8'; '.json'='application/json; charset=utf-8'
  '.webmanifest'='application/manifest+json; charset=utf-8'
  '.txt'='text/plain; charset=utf-8'; '.md'='text/plain; charset=utf-8'
  '.svg'='image/svg+xml'; '.png'='image/png'; '.jpg'='image/jpeg'; '.jpeg'='image/jpeg'
  '.webp'='image/webp'; '.gif'='image/gif'; '.ico'='image/x-icon'
  '.mp3'='audio/mpeg'; '.mp4'='video/mp4'; '.wasm'='application/wasm'
  '.glb'='model/gltf-binary'; '.gltf'='model/gltf+json'
  '.ttf'='font/ttf'; '.woff'='font/woff'; '.woff2'='font/woff2'
}

# -------------------------------------------------------------------- 启动监听
$listener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Any, $Port)
try { $listener.Start() }
catch { Write-Host "[错误] 端口 $Port 被占用。换一个，例如： -Port 8081" -ForegroundColor Red; exit 1 }

Write-Host ''
Write-Host '============================================================' -ForegroundColor DarkCyan
Write-Host '  VR 作品 · 本机服务器已启动' -ForegroundColor Cyan
Write-Host '------------------------------------------------------------'
Write-Host ("  根目录 : {0}" -f $Root)
Write-Host ("  端口   : {0}" -f $Port)
Write-Host ''
Write-Host '  头显里打开（USB 转发 / 安全上下文）:' -ForegroundColor Green
Write-Host ("      http://127.0.0.1:{0}/" -f $Port)
Write-Host '    ↑ 先把头显用数据线连到这台电脑，然后另开一个窗口执行：' -ForegroundColor DarkGray
Write-Host ("       adb reverse tcp:{0} tcp:{0}" -f $Port) -ForegroundColor DarkGray
Write-Host ''
Write-Host '  头显里打开（同一 Wi-Fi，需先在头显浏览器加白名单）:' -ForegroundColor Yellow
Write-Host ("      http://{0}:{1}/" -f $LanIp, $Port)
Write-Host ("    白名单填： http://{0}:{1}" -f $LanIp, $Port)
Write-Host '    入口：chrome://flags/#unsafely-treat-insecure-origin-as-secure → Enabled → 重启浏览器' -ForegroundColor DarkGray
Write-Host ''
Write-Host '  本机浏览器自测:' -ForegroundColor DarkGray
Write-Host ("      http://127.0.0.1:{0}/" -f $Port) -ForegroundColor DarkGray
Write-Host '------------------------------------------------------------'
Write-Host '  这个窗口不要关。按 Ctrl+C 停止。' -ForegroundColor Yellow
Write-Host '============================================================' -ForegroundColor DarkCyan
Write-Host ''

# -------------------------------------------------------------------- 请求循环
$count = 0
while ($true) {
  $client = $null
  try {
    $client = $listener.AcceptTcpClient()
    $stream = $client.GetStream()
    $stream.ReadTimeout = 5000
    $reader = New-Object System.IO.StreamReader($stream, [System.Text.Encoding]::ASCII, $false, 1024, $true)

    $requestLine = $reader.ReadLine()
    if ([string]::IsNullOrWhiteSpace($requestLine)) { $client.Close(); continue }
    $parts = $requestLine.Split(' ')
    $method = $parts[0]; $rawPath = if ($parts.Length -gt 1) { $parts[1] } else { '/' }
    $isHead = ($method -eq 'HEAD')

    # 把请求头读干净（本服务器不需要它们，但不读会污染下一个请求）
    while ($true) { $line = $reader.ReadLine(); if ($null -eq $line -or $line -eq '') { break } }

    $rel = [System.Uri]::UnescapeDataString(($rawPath -split '\?')[0])
    if ($rel -eq '/' -or $rel -eq '') { $rel = '/index.html' }
    $rel = $rel.TrimStart('/').Replace('/', [System.IO.Path]::DirectorySeparatorChar)
    $full = [System.IO.Path]::GetFullPath((Join-Path $Root $rel))

    # 目录穿越防护
    if (-not $full.StartsWith($Root, [System.StringComparison]::OrdinalIgnoreCase)) {
      $body = [System.Text.Encoding]::UTF8.GetBytes('403 Forbidden')
      $head = "HTTP/1.1 403 Forbidden`r`nContent-Type: text/plain; charset=utf-8`r`nContent-Length: $($body.Length)`r`nConnection: close`r`n`r`n"
      $hb = [System.Text.Encoding]::ASCII.GetBytes($head)
      $stream.Write($hb, 0, $hb.Length)
      if (-not $isHead) { $stream.Write($body, 0, $body.Length) }
      $stream.Flush(); $client.Close(); continue
    }

    if (Test-Path -LiteralPath $full -PathType Container) {
      $full = Join-Path $full 'index.html'
    }

    if (Test-Path -LiteralPath $full -PathType Leaf) {
      $bytes = [System.IO.File]::ReadAllBytes($full)
      $ext = [System.IO.Path]::GetExtension($full).ToLower()
      $ct = if ($Mime.ContainsKey($ext)) { $Mime[$ext] } else { 'application/octet-stream' }
      $head = "HTTP/1.1 200 OK`r`nContent-Type: $ct`r`nContent-Length: $($bytes.Length)`r`nCache-Control: no-cache`r`nConnection: close`r`n`r`n"
      $hb = [System.Text.Encoding]::ASCII.GetBytes($head)
      $stream.Write($hb, 0, $hb.Length)
      if (-not $isHead) { $stream.Write($bytes, 0, $bytes.Length) }
      $stream.Flush()
      $count++
      Write-Host ("  [{0}] 200  {1}  ({2:N0} B)" -f $count, $rawPath, $bytes.Length) -ForegroundColor DarkGray
    } else {
      $body = [System.Text.Encoding]::UTF8.GetBytes("404 Not Found: $rawPath")
      $head = "HTTP/1.1 404 Not Found`r`nContent-Type: text/plain; charset=utf-8`r`nContent-Length: $($body.Length)`r`nConnection: close`r`n`r`n"
      $hb = [System.Text.Encoding]::ASCII.GetBytes($head)
      $stream.Write($hb, 0, $hb.Length); if (-not $isHead) { $stream.Write($body, 0, $body.Length) }
      $stream.Flush()
      Write-Host ("  [--] 404  {0}" -f $rawPath) -ForegroundColor DarkYellow
    }
    $client.Close()
  } catch {
    if ($client) { try { $client.Close() } catch { } }
  }
}
