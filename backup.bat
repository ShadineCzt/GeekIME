@echo off
chcp 65001 >nul
title GeekIME 一键备份

echo ========================================
echo   GeekIME 配置一键备份
echo   备份内容：双拼方案/词库/Lua插件/用户词典
echo ========================================

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ts = Get-Date -Format 'yyyyMMdd_HHmmss';" ^
  "$dst = Join-Path 'F:\GeekIME_备份' ('backup_' + $ts);" ^
  "New-Item -ItemType Directory -Force -Path $dst | Out-Null;" ^
  "Copy-Item 'F:\7.输入法\*.yaml' $dst;" ^
  "Copy-Item 'F:\7.输入法\custom_phrase.txt','F:\7.输入法\rime.lua','F:\7.输入法\dedup_sogou.py' $dst;" ^
  "Copy-Item 'F:\7.输入法\geek_*.bat' $dst;" ^
  "Copy-Item 'F:\7.输入法\lua' $dst -Recurse;" ^
  "Get-ChildItem 'F:\7.输入法' -Directory -Filter '*.userdb' | ForEach-Object { Copy-Item $_.FullName $dst -Recurse -ErrorAction SilentlyContinue };" ^
  "Write-Host ('备份完成: ' + $dst)"

echo.
pause
