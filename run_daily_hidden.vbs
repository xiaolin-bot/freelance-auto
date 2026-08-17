Set ws = CreateObject("WScript.Shell")
ws.Run "powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File C:\freelance-auto\run_daily.ps1", 0, False
