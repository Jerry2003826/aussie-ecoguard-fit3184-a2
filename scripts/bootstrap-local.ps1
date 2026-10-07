$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path $PSScriptRoot -Parent
Push-Location $repoRoot
try {
    & uv python install 3.12
    if ($LASTEXITCODE -ne 0) { throw 'Python installation failed.' }
    if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
        & uv venv --python 3.12 .venv
        if ($LASTEXITCODE -ne 0) { throw 'Virtual environment creation failed.' }
    }
    & uv pip install --python '.venv\Scripts\python.exe' -r services/aws/requirements.lock.txt
    if ($LASTEXITCODE -ne 0) { throw 'Backend dependency installation failed.' }
    Push-Location apps/web
    try { & npm ci; if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' } }
    finally { Pop-Location }
} finally { Pop-Location }
