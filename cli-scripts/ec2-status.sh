#!/usr/bin/env bash
set -euo pipefail

AWS_REGION="${1:-eu-west-1}"

aws ec2 describe-instances \
  --region "$AWS_REGION" \
  --query 'Reservations[].Instances[].{InstanceId:InstanceId,State:State.Name,Type:InstanceType,PrivateIp:PrivateIpAddress,PublicIp:PublicIpAddress,Name:Tags[?Key==`Name`]|[0].Value}' \
  --output table
