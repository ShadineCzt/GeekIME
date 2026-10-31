@echo off
taskkill /f /im python.exe /fi "WINDOWTITLE eq GeekIME*" >nul 2>&1
taskkill /f /im WeaselServer.exe >nul 2>&1
echo WeaselServer 已停止
pause
