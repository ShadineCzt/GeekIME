@echo off
chcp 65001 >nul
title GeekIME Web 管理台

echo ========================================
echo   GeekIME Web 管理台
echo   地址: http://127.0.0.1:8765
echo   关闭此窗口即停止管理台
echo ========================================

python "F:\7.输入法\geek_web\app.py"

pause
