param(
    [Parameter(Mandatory = $true)]
    [string]$TableName,

    [int]$Limit = 20
)

aws dynamodb scan --table-name $TableName --max-items $Limit --output json
