@echo off
chcp 65001 >nul
title LinkedIn Auto - Status
cd /d %~dp0
python -X utf8 check_status.py
echo.
pause
