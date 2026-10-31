@echo off
chcp 65001 >nul
title GeekIME

echo ========================================
echo   GeekIME 一键更新并启动
echo ========================================

echo [1/4] 同步最新配置...
copy /Y "F:\7.输入法\rime.lua" "F:\12.Rime\weasel-0.17.4\data\rime.lua" >nul 2>&1
if not exist "F:\12.Rime\weasel-0.17.4\data\lua" mkdir "F:\12.Rime\weasel-0.17.4\data\lua"
copy /Y "F:\7.输入法\lua\v_mode.lua" "F:\12.Rime\weasel-0.17.4\data\lua\v_mode.lua" >nul 2>&1
copy /Y "F:\7.输入法\lua\geek.lua" "F:\12.Rime\weasel-0.17.4\data\lua\geek.lua" >nul 2>&1

echo [2/4] 重启 WeaselServer（加载最新插件）...
taskkill /f /im WeaselServer.exe >nul 2>&1
ping -n 3 127.0.0.1 >nul

echo [3/4] 重新部署 Rime...
start "" /b "F:\12.Rime\weasel-0.17.4\WeaselDeployer.exe" /deploy
ping -n 4 127.0.0.1 >nul

echo [4/4] 完成。输入 gt 开启极客模式
echo.
pause