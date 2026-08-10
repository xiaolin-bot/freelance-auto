@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
REM ============================================================
REM  freelance-auto one-click launcher (Windows cmd / batch)
REM  Steps: check python -> pip install -e . -> run once -> done
REM ============================================================

echo ================================================
echo   freelance-auto launcher
echo ================================================

REM 1. Check python
where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python not found. Install Python 3.11+ and check "Add to PATH".
    pause
    exit /b 1
)
echo Python version:
python --version

REM 2. Install dependencies (idempotent, fast if already installed)
echo Installing dependencies ^(pip install -e .^) ...
python -m pip install -e .
if errorlevel 1 (
    echo [ERROR] pip install failed. Check your network connection.
    pause
    exit /b 1
)

REM 3. Run the full pipeline once
echo Running: freelance-auto once
where freelance-auto >nul 2>nul
if errorlevel 1 (
    echo freelance-auto command not found, fallback: python -m freelance_auto.cli once
    python -m freelance_auto.cli once
    set "code=!errorlevel!"
) else (
    freelance-auto once
    set "code=!errorlevel!"
)
if not "!code!"=="0" (
    echo [ERROR] Run failed, exit code !code!
    pause
    exit /b !code!
)

REM 4. Done
echo.
echo ============ DONE ============
echo Log file:  data\app.log
echo Database:  data\freelance.db
pause
exit /b 0
