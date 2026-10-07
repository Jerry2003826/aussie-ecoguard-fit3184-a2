param([ValidateSet('api','web')][string]$Target = 'api')
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path $PSScriptRoot -Parent
Push-Location $repoRoot
try {
    if ($Target -eq 'api') {
        $env:ECO_MODE = 'local'
        $env:PYTHONPATH = (Join-Path $repoRoot 'packages\domain') + ';' + (Join-Path $repoRoot 'services\aws')
        & '.venv\Scripts\python.exe' -m ecoguard_aws.local
    } else {
        Push-Location apps/web
        try { & npm run dev -- --host 127.0.0.1 }
        finally { Pop-Location }
    }
    if ($LASTEXITCODE -ne 0) { throw 'Development process stopped with an error.' }
} finally { Pop-Location }
