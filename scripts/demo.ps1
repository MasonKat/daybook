# Windows twin of `make demo`: venv, schema, synthetic seed, publish, then every check (scripts\check.ps1).
# Run from anywhere:  powershell -ExecutionPolicy Bypass -File scripts\demo.ps1
# Windows PowerShell 5.1 turns a native command's stderr into a terminating error under 'Stop' (H5):
# keep 'Continue' and check $LASTEXITCODE after every native command instead.
$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

function Invoke-Step([string]$Label, [scriptblock]$Block) {
    Write-Host "== $Label"
    & $Block
    if ($LASTEXITCODE -ne 0) { Write-Host "FAILED: $Label" -ForegroundColor Red; exit $LASTEXITCODE }
}

# Never call pip's generated launcher (.venv\Scripts\<name>.exe): antivirus blocks it on the teacher's laptop.
# Everything runs as `python.exe -m console` / `python.exe -m pytest` instead.
$installed = $false
if (Test-Path '.venv\Scripts\python.exe') {
    cmd /c ".venv\Scripts\python.exe -c ""import console"" >nul 2>nul"
    $installed = ($LASTEXITCODE -eq 0)
}
if (-not $installed) {
    if (Test-Path '.venv\Scripts\python.exe') { }
    elseif (Get-Command py -ErrorAction SilentlyContinue) {
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
    Invoke-Step 'daybook init' { .\.venv\Scripts\python.exe -m console init }
    Invoke-Step 'daybook demo' { .\.venv\Scripts\python.exe -m console demo }
} finally {
    Remove-Item Env:DAYBOOK_DB, Env:DAYBOOK_CONFIG -ErrorAction SilentlyContinue
}
& (Join-Path $PSScriptRoot 'check.ps1')
exit $LASTEXITCODE
