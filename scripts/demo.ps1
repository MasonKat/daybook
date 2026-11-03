# Windows twin of `make demo`: venv, schema, synthetic seed, publish, then every check (scripts\check.ps1).
# Run from anywhere:  powershell -ExecutionPolicy Bypass -File scripts\demo.ps1
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)

function Invoke-Step([string]$Label, [scriptblock]$Block) {
    Write-Host "== $Label"
    & $Block
    if ($LASTEXITCODE -ne 0) { Write-Host "FAILED: $Label" -ForegroundColor Red; exit $LASTEXITCODE }
}

if (-not (Test-Path '.venv\Scripts\daybook.exe')) {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        Invoke-Step 'create .venv (py -3)' { py -3 -m venv .venv }
    } else {
        Invoke-Step 'create .venv (python)' { python -m venv .venv }
    }
    Invoke-Step 'upgrade pip' { .\.venv\Scripts\python.exe -m pip install -q --upgrade pip }
    Invoke-Step 'install daybook' { .\.venv\Scripts\python.exe -m pip install -q -e '.[test]' }
}

$env:DAYBOOK_DB = 'demo.sqlite'
$env:DAYBOOK_CONFIG = 'demo\daybook.demo.json'
try {
    if (Test-Path 'demo.sqlite') { Remove-Item 'demo.sqlite' }
    Invoke-Step 'daybook init' { .\.venv\Scripts\daybook.exe init }
    Invoke-Step 'daybook demo' { .\.venv\Scripts\daybook.exe demo }
} finally {
    Remove-Item Env:DAYBOOK_DB, Env:DAYBOOK_CONFIG -ErrorAction SilentlyContinue
}
& (Join-Path $PSScriptRoot 'check.ps1')
exit $LASTEXITCODE
