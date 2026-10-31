rem GeekIME Auto-Start Setup
@echo off
echo Config synced OK.
schtasks /create /tn GeekIME_restart /tr "cmd /c taskkill /f /im WeaselServer.exe" /sc onlogon /rl highest /f >nul 2>&1
echo Auto-start registered. Done.
echo Uninstall: F:\7.输入法\geek_uninstall.bat
pause