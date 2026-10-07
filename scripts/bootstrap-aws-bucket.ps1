param([string]$Region = 'us-east-1', [string]$Prefix = 'ecoguard3184', [string]$Environment = 'dev')
$ErrorActionPreference = 'Stop'
$awsCmd = (Get-Command aws -ErrorAction SilentlyContinue).Source
if (-not $awsCmd) { $awsCmd = Join-Path $env:LOCALAPPDATA 'Programs\Amazon\AWSCLIV2\aws.exe' }
$identityJson = & $awsCmd sts get-caller-identity --output json --region $Region
if ($LASTEXITCODE -ne 0) { throw 'AWS session is not valid. Refresh Learner Lab credentials.' }
$accountId = ($identityJson | ConvertFrom-Json).Account
$bucketName = "$Prefix-$Environment-$accountId-media"
& $awsCmd s3api head-bucket --bucket $bucketName --region $Region 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    # On create failure stop; never try deleting or taking over another bucket.
    $createArgs = @('s3api','create-bucket','--bucket',$bucketName,'--region',$Region)
    if ($Region -ne 'us-east-1') { $createArgs += @('--create-bucket-configuration',"LocationConstraint=$Region") }
    & $awsCmd @createArgs | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Cannot create owned bucket $bucketName." }
    & $awsCmd s3api head-bucket --bucket $bucketName --region $Region | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Created bucket cannot be verified.' }
}
Write-Output $bucketName
