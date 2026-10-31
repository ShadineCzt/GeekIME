@echo off
title GeekIME Uninstall
schtasks /delete /tn GeekIME_server /f >nul 2>&1
schtasks /delete /tn GeekIME_restart /f >nul 2>&1
echo Auto-start removed.
pause
