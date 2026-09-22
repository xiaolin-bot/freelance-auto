@echo off
chcp 65001 >nul
title LinkedIn Login
cd /d %~dp0
python -X utf8 login_linkedin.py
echo.
pause
