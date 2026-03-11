param(
    [Parameter(Mandatory = $true)]
    [string]$BucketName,

    [Parameter(Mandatory = $true)]
    [string]$FilePath,

    [string]$Key
)

if (-not $Key) {
    $Key = Split-Path -Path $FilePath -Leaf
}

aws s3 cp $FilePath "s3://$BucketName/$Key"
Write-Host "Uploaded $FilePath to s3://$BucketName/$Key"
