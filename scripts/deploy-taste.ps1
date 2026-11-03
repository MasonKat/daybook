# F1 taste build: a fresh demo seed (demo.sqlite only, never the real database), only the demo student's pages
# into dist-taste\, then deploy to the separate Pages project "daybook-taste". Needs `npx wrangler login` once.
# Run from anywhere:  powershell -ExecutionPolicy Bypass -File scripts\deploy-taste.ps1 [-Project daybook-taste]
param([string]$Project = 'daybook-taste')
# Windows PowerShell 5.1 turns a native command's stderr into a terminating error under 'Stop' (H5):
# keep 'Continue' and check $LASTEXITCODE after every native command instead.
$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

$env:DAYBOOK_DB = 'demo.sqlite'
$env:DAYBOOK_CONFIG = 'demo\daybook.demo.json'
try {
    .\.venv\Scripts\python.exe -m console init
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    .\.venv\Scripts\python.exe -m console demo --force --no-publish
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    .\.venv\Scripts\python.exe -m console publish --taste
    if ($LASTEXITCODE -ne 0) { Write-Host 'taste publish stopped: nothing was deployed' -ForegroundColor Red; exit $LASTEXITCODE }
} finally {
    Remove-Item Env:DAYBOOK_DB, Env:DAYBOOK_CONFIG -ErrorAction SilentlyContinue
}
Push-Location worker
try {
    npx wrangler pages deploy ..\dist-taste --project-name $Project --branch main
    exit $LASTEXITCODE
} finally { Pop-Location }
