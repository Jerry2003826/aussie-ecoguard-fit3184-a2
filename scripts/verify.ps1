$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path $PSScriptRoot -Parent
Push-Location $repoRoot
try {
    $env:PYTHONPATH = (Join-Path $repoRoot 'packages\domain') + ';' + (Join-Path $repoRoot 'services\aws')
    & '.venv\Scripts\python.exe' -m unittest discover -s tests/backend -v
    if ($LASTEXITCODE -ne 0) { throw 'Backend tests failed.' }
    Push-Location apps/web
    try {
        & npm run build
        if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
        & npm test -- --run
        if ($LASTEXITCODE -ne 0) { throw 'Frontend tests failed.' }
    } finally { Pop-Location }
    $terraformCmd = (Get-Command terraform -ErrorAction SilentlyContinue).Source
    if (-not $terraformCmd) { $terraformCmd = Join-Path $env:LOCALAPPDATA 'Programs\Terraform\terraform.exe' }
    foreach ($cloud in @('aws','gcp')) {
        Push-Location "infra/$cloud"
        try { & $terraformCmd validate '-no-color'; if ($LASTEXITCODE -ne 0) { throw "$cloud Terraform validation failed." } }
        finally { Pop-Location }
    }
} finally { Pop-Location }
