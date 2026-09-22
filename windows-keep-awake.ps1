# ============================================================
# 本机保活设置（让 Windows 计划任务不漏跑）
# 作用：
#   1) 计划任务允许电池供电时运行（默认禁止 → 拔电就整轮跳过）
#   2) 任务不会被"拔电"中断
#   3) 睡眠期间错过的轮次，开机后自动补跑
#   4) 允许唤醒定时器（睡眠中也能被计划任务唤醒执行）
# 运行：右键「以管理员身份运行」PowerShell 执行本脚本
#   powershell -ExecutionPolicy Bypass -File deploy\windows-keep-awake.ps1
# 可选：合盖不睡眠（插电时）
#   powershell -ExecutionPolicy Bypass -File deploy\windows-keep-awake.ps1 -SetLidNoSleep
# ============================================================
param(
    [switch]$SetLidNoSleep
)

$ErrorActionPreference = "Stop"
$TASK = "linkedin-auto"

Write-Host "== 1) 计划任务设置 ==" -ForegroundColor Cyan
$t = Get-ScheduledTask -TaskName $TASK -ErrorAction Stop
$t.Settings.WakeToRun = $true
$t.Settings.StartWhenAvailable = $true
$t.Settings.DisallowStartIfOnBatteries = $false
$t.Settings.StopIfGoingOnBatteries = $false
Set-ScheduledTask -TaskName $TASK -Settings $t.Settings | Out-Null
Write-Host "   已设置: 电池可运行 + 不因拔电中断 + 错过补跑 + 唤醒执行"

Write-Host "== 2) 启用唤醒定时器（交流电） ==" -ForegroundColor Cyan
# RTCWAKE GUID: BD3B718A-0680-4D9D-8AB2-E1D2B4AC806D，值 1 = 启用
powercfg /setacvalueindex SCHEME_CURRENT SUB_SLEEP BD3B718A-0680-4D9D-8AB2-E1D2B4AC806D 1 | Out-Null
powercfg /setactive SCHEME_CURRENT | Out-Null
Write-Host "   已启用"

if ($SetLidNoSleep) {
    Write-Host "== 3) 插电时合盖不睡眠 ==" -ForegroundColor Cyan
    # 部分 OEM 电源方案默认隐藏该设置 → 先取消隐藏（需要管理员权限）
    powercfg -attributes SUB_BUTTONS 5ca83367-6e45-459f-a27b-476b1d01c936 -ATTRIB_HIDE 2>$null
    # LIDACTION: 0 = 不采取任何操作（仅交流电生效；电池上仍正常睡眠，便于携带）
    powercfg /setacvalueindex SCHEME_CURRENT SUB_BUTTONS 5ca83367-6e45-459f-a27b-476b1d01c936 0 | Out-Null
    powercfg /setactive SCHEME_CURRENT | Out-Null
    Write-Host "   设置结果（第一行应为 0x00000000 = 插电合盖不休眠；第二行电池保持原样）:"
    (powercfg /q SCHEME_CURRENT SUB_BUTTONS 5ca83367-6e45-459f-a27b-476b1d01c936 |
        Select-String "0x" | Select-Object -Last 2) | ForEach-Object { Write-Host "     $($_.Line.Trim())" }
}

Write-Host "== 4) 验证 ==" -ForegroundColor Cyan
(Get-ScheduledTask -TaskName $TASK).Settings |
    Select-Object WakeToRun, StartWhenAvailable, DisallowStartIfOnBatteries, StopIfGoingOnBatteries |
    Format-List
Write-Host ""
Write-Host "完成。说明：锁屏不影响任务运行；睡眠中若任务到点且唤醒定时器生效，会自动唤醒执行。" -ForegroundColor Green
Write-Host "注意：笔记本关机时仍然不会运行（要 7×24 请部署云服务器，见 deploy/README.md）"
