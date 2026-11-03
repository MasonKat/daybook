# Windows twin of `make check`: pytest, the Worker node tests, the Playwright end-to-end check (needs `daybook demo` first).
# Run from anywhere:  powershell -ExecutionPolicy Bypass -File scripts\check.ps1 [-Screenshots]
# -Screenshots refreshes the committed demo\screenshots\ (on purpose only; a normal run never writes tracked files).
param([switch]$Screenshots)
# Windows PowerShell 5.1 turns a native command's stderr into a terminating error under 'Stop' (H5):
# keep 'Continue' and check $LASTEXITCODE after every native command instead.
$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

function Invoke-Step([string]$Label, [scriptblock]$Block) {
    Write-Host "== $Label"
    & $Block
    if ($LASTEXITCODE -ne 0) { Write-Host "FAILED: $Label" -ForegroundColor Red; exit $LASTEXITCODE }
}

Invoke-Step 'pytest' { .\.venv\Scripts\pytest.exe -q }

Push-Location worker
try { Invoke-Step 'worker node tests' { node --experimental-sqlite --no-warnings --test test/worker.test.mjs test/checkin.test.mjs test/moments.test.mjs test/budget.test.mjs } } finally { Pop-Location }

# Playwright: use an installed copy if Node can resolve it, else install it locally (needs network once).
cmd /c "node -e ""require('playwright')"" >nul 2>nul"
if ($LASTEXITCODE -ne 0) {
    Invoke-Step 'install playwright' { npm install --no-save --no-audit --no-fund playwright@1 }
    Invoke-Step 'install chromium' { npx playwright install chromium }
}

$env:DAYBOOK_DB = 'demo.sqlite'
$env:DAYBOOK_CONFIG = 'demo\daybook.demo.json'
$env:DAYBOOK_BIN = (Resolve-Path '.venv\Scripts\daybook.exe').Path
try {
    if ($Screenshots) { Invoke-Step 'e2e (Playwright) + screenshots' { node tests\e2e\check.cjs --screenshots } }
    else { Invoke-Step 'e2e (Playwright)' { node tests\e2e\check.cjs } }
} finally {
    Remove-Item Env:DAYBOOK_DB, Env:DAYBOOK_CONFIG, Env:DAYBOOK_BIN -ErrorAction SilentlyContinue
}
Write-Host 'all checks passed' -ForegroundColor Green
