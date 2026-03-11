# Screenshot Checklist

Capture these screenshots after a successful deployment so the repository looks complete on GitHub.

## Recommended screenshots

1. AWS Copilot service list showing the `frontend` service in the `production` environment.
2. ECS service detail page with running tasks and desired count.
3. Application Load Balancer target group showing healthy targets.
4. S3 bucket overview with versioning enabled and public access blocked.
5. CloudFormation stacks for `aws-cloud-lab-storage`, `aws-cloud-lab-network`, and `aws-cloud-lab-alarms`.
6. IAM policy JSON or attached policy screen for the least-privilege task role.
7. CloudWatch Logs stream with requests hitting `/`, `/s3/check`, or `/stress`.
8. CloudWatch alarm history showing the CPU threshold at 70 percent.

## Screenshot naming suggestion

- `01-copilot-service.png`
- `02-ecs-service.png`
- `03-alb-targets.png`
- `04-s3-bucket.png`
- `05-cloudformation-stacks.png`
- `06-iam-policy.png`
- `07-cloudwatch-logs.png`
- `08-cloudwatch-alarm.png`

## Tips

- Blur account IDs if you plan to share screenshots publicly.
- Keep the AWS region visible in at least one screenshot.
- If you use the temporary full-access policy for experiments, do not showcase it as the final security setup.
