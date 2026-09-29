@echo off
setlocal

rem =============================================================
rem  VR work - zero-dependency local server (Windows)
rem
rem  Usage:
rem     Double-click this file, OR
rem     serve.bat "D:\my-vr-work" 8080
rem
rem  It serves the given folder (default: this folder) on 8080
rem  using nothing but Windows PowerShell. No Python, no Node.
rem
rem  ASCII-only on purpose: cmd.exe reads .bat with the system ANSI
rem  codepage (GBK on Chinese Windows), so Chinese text saved as
rem  UTF-8 would break the script. Chinese notes live in index.html.
rem =============================================================

set "ROOT=%~dp0."
set "PORT=8080"

if not "%~1"=="" set "ROOT=%~1"
if not "%~2"=="" set "PORT=%~2"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0serve.ps1" -Root "%ROOT%" -Port %PORT%

echo.
echo Server stopped.
pause
