Set-Location $PSScriptRoot
Write-Host "Starting ContentProtector at http://localhost:5000 ..." -ForegroundColor Cyan
Write-Host "Press Ctrl+C to stop." -ForegroundColor Gray
$python = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $python) { $python = "py" }
& $python app.py
