@echo off
chcp 65001 >nul
echo ================================================
echo   freelance-auto 计划任务注册（每小时自动跑）
echo ================================================

rem 注册任务：每天 7:00-23:00 每小时跑一次日常任务
schtasks /Create /TN "freelance-auto-daily" /TR "powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\freelance-auto\run_daily.ps1" /SC HOURLY /MO 1 /ST 07:00 /F

if errorlevel 1 (
    echo [失败] 注册失败，可能需要管理员权限。
    echo 请右键此文件 → 以管理员身份运行。
    pause
    exit /b 1
)

echo [成功] 已注册每小时任务。可通过"任务计划程序"查看/管理。
echo        也可以随时手动运行：powershell -File C:\freelance-auto\run_daily.ps1
pause
