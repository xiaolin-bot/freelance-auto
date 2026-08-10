# ============================================================
#  freelance-auto one-click launcher (PowerShell)
#  Steps: check python -> pip install -e . -> run once -> done
# ============================================================
$ErrorActionPreference = "Stop"

Write-Host "================================================" -ForegroundColor Cyan
Write-Host "  freelance-auto launcher" -ForegroundColor Cyan
Write-Host "================================================" -ForegroundColor Cyan

# 1. Check python
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) {
    Write-Host "[ERROR] Python not found. Install Python 3.11+ and check 'Add to PATH'." -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}
Write-Host ("Python: " + (python --version))

# 2. Install dependencies (idempotent, fast if already installed)
Write-Host "Installing dependencies (pip install -e .) ..." -ForegroundColor Yellow
python -m pip install -e .
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] pip install failed. Check your network connection." -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

# 3. Run the full pipeline once
Write-Host "Running: freelance-auto once" -ForegroundColor Yellow
$cmd = Get-Command freelance-auto -ErrorAction SilentlyContinue
if ($cmd) {
    & freelance-auto once
} else {
    Write-Host "freelance-auto command not found, fallback: python -m freelance_auto.cli once" -ForegroundColor Yellow
    python -m freelance_auto.cli once
}
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] Run failed, exit code $LASTEXITCODE" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit $LASTEXITCODE
}

# 4. Done
Write-Host ""
Write-Host "============ DONE ============" -ForegroundColor Green
Write-Host "Log file:  data\app.log"
Write-Host "Database:  data\freelance.db"
Read-Host "Press Enter to exit"
exit 0
