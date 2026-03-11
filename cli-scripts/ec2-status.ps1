param(
    [string]$Region = "eu-west-1"
)

aws ec2 describe-instances `
    --region $Region `
    --query "Reservations[].Instances[].{InstanceId:InstanceId,State:State.Name,Type:InstanceType,PrivateIp:PrivateIpAddress,PublicIp:PublicIpAddress,Name:Tags[?Key=='Name']|[0].Value}" `
    --output table
