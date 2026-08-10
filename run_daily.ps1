# freelance-auto 全自动日常任务入口（每小时由计划任务调用）
# 实际逻辑在 daily_runner.py（Python 处理日志编码，避免 PowerShell 重定向乱码）

$ErrorActionPreference = "Continue"
python C:\freelance-auto\daily_runner.py
exit $LASTEXITCODE
