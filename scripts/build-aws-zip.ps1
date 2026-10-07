$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path $PSScriptRoot -Parent
Push-Location $repoRoot
try {
    $packagePath = Join-Path $repoRoot 'build\lambda-package'
    if (Test-Path -LiteralPath $packagePath) {
        # Resolve and check before recursive deletion on Windows.
        $resolvedPackage = (Resolve-Path -LiteralPath $packagePath).Path
        $buildRoot = [IO.Path]::GetFullPath((Join-Path $repoRoot 'build')) + [IO.Path]::DirectorySeparatorChar
        if (-not $resolvedPackage.StartsWith($buildRoot, [StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe build directory.' }
        Remove-Item -LiteralPath $resolvedPackage -Recurse -Force
    }
    New-Item -ItemType Directory -Path $packagePath -Force | Out-Null
    # Python 3.12 Lambda runs AL2023 (glibc 2.34), compatible with these wheels.
    & uv pip install --python '.venv\Scripts\python.exe' --target $packagePath --python-platform x86_64-manylinux_2_28 --python-version 3.12 --only-binary ':all:' --require-hashes -r services/aws/requirements.lock.txt
    if ($LASTEXITCODE -ne 0) { throw 'Linux dependency packaging failed.' }
    Copy-Item -LiteralPath 'packages\domain\ecoguard_domain' -Destination $packagePath -Recurse
    Copy-Item -LiteralPath 'services\aws\ecoguard_aws' -Destination $packagePath -Recurse
    $configPath = Join-Path $packagePath 'config'
    New-Item -ItemType Directory -Path $configPath -Force | Out-Null
    if (Test-Path -LiteralPath 'config\google-wif.json') {
        Copy-Item -LiteralPath 'config\google-wif.json' -Destination $configPath
    }
    & '.venv\Scripts\python.exe' scripts/package_lambda.py $packagePath build/aws-lambda.zip
    if ($LASTEXITCODE -ne 0) { throw 'Lambda archive creation failed.' }
    Get-FileHash -LiteralPath build/aws-lambda.zip -Algorithm SHA256
} finally { Pop-Location }
