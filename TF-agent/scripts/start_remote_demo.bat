@echo off
REM 双击即可一键启动远程演示（会打开 3 个 PowerShell 窗口）
cd /d "%~dp0.."
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_remote_demo_all.ps1"
pause
