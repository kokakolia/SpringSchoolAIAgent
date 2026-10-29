param([int]$Port = 8000, [switch]$UsePostgres)
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
    & $projectPython -m uvicorn src.server:app --host 127.0.0.1 --port $Port
    if ($LASTEXITCODE -ne 0) {
        throw "Application exited with code $LASTEXITCODE"
    }
} finally {
    $env:DATABASE_URL = $previousDatabaseUrl
}
