param(
    [int]$Port = 8000,
    [switch]$UsePostgres,
    [ValidateSet('serve', 'auth-status', 'auth-update')][string]$Command = 'serve',
    [int]$ExpectedGeneration = 0
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$projectPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $projectPython)) {
    throw 'Create .venv and install requirements.txt first (see README.md).'
}
$previousDatabaseUrl = $env:DATABASE_URL
try {
    if (-not $UsePostgres) {
        New-Item -ItemType Directory -Force -Path (Join-Path $projectRoot '.local') | Out-Null
        $env:DATABASE_URL = 'sqlite+aiosqlite:///' + (Join-Path $projectRoot '.local\deadline.db')
    }
    if ($Command -eq 'serve') {
        & $projectPython -m uvicorn src.server:app --host 127.0.0.1 --port $Port
    } elseif ($Command -eq 'auth-update') {
        if ($ExpectedGeneration -lt 1) {
            throw 'Use -ExpectedGeneration with the current version from auth-status.'
        }
        & $projectPython main.py auth-update --expected-generation $ExpectedGeneration
    } else {
        & $projectPython main.py auth-status
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Application exited with code $LASTEXITCODE"
    }
} finally {
    $env:DATABASE_URL = $previousDatabaseUrl
}
