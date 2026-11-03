# Windows twin of `make deploy-pages`: publish real data (stops if the no-PII lint fails), then deploy dist\ to Pages.
# Run from anywhere:  powershell -ExecutionPolicy Bypass -File scripts\deploy-pages.ps1 [-Project daybook]
param([string]$Project = 'daybook')
# Windows PowerShell 5.1 turns a native command's stderr into a terminating error under 'Stop' (H5):
# keep 'Continue' and check $LASTEXITCODE after every native command instead.
$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

.\.venv\Scripts\daybook.exe publish
if ($LASTEXITCODE -ne 0) { Write-Host 'publish stopped: nothing was deployed' -ForegroundColor Red; exit $LASTEXITCODE }
Push-Location worker
try {
    npx wrangler pages deploy ..\dist --project-name $Project --branch main
    exit $LASTEXITCODE
} finally { Pop-Location }
