# AWS Cloud Lab

Portfolio-ready AWS project that demonstrates how to run a containerized Python web app on AWS Fargate with AWS Copilot, store files in S3, define infrastructure with CloudFormation, apply IAM least privilege, and monitor the workload with CloudWatch.

## Skills demonstrated

- Deploying a containerized web app on AWS Fargate through AWS Copilot.
- Creating an S3 bucket with CloudFormation.
- Defining a VPC, subnets, and security groups with CloudFormation.
- Applying IAM least privilege for S3 access from the application.
- Monitoring logs and CPU metrics with CloudWatch.
- Managing AWS resources from AWS CLI scripts.
- Structuring a clean GitHub repository for a cloud portfolio.

## Repository structure

```text
aws-cloud-lab/
├── .env.example
├── Dockerfile
├── app.py
├── requirements.txt
├── infrastructure/
│   ├── cloudwatch-alarms.yml
│   ├── iam-least-privilege.json
│   ├── iam-s3-full-access-example.json
│   ├── network.yml
│   ├── s3-bucket.yml
│   └── task-role-trust-policy.json
├── cli-scripts/
│   ├── ec2-status.ps1
│   ├── ec2-status.sh
│   ├── upload-to-s3.ps1
│   └── upload-to-s3.sh
├── copilot/
│   ├── environments/production/manifest.yml
│   └── frontend/manifest.yml
├── docs/
│   ├── architecture.md
│   └── screenshots.md
└── README.md
```

## Application behavior

The Flask app exposes four endpoints:

- `GET /` returns service metadata.
- `GET /health` is used by the load balancer health check.
- `GET /s3/check` validates that the task role can reach the configured bucket.
- `POST /s3/upload-demo` uploads a small text file to the `demo/` prefix in S3.
- `GET /stress?seconds=20` generates CPU load for CloudWatch alarm demonstrations.

## Prerequisites

- AWS account with permissions to create ECS, ECR, IAM, CloudWatch, S3, VPC, and CloudFormation resources.
- AWS CLI v2 configured locally.
- Docker Desktop or Docker Engine.
- AWS Copilot CLI installed.

## Local build and test

Build the image locally:

```bash
docker build -t aws-cloud-lab .
```

Run the container locally:

```bash
docker run --rm -p 8080:8080 -e AWS_REGION=eu-west-1 -e S3_BUCKET=my-demo-bucket aws-cloud-lab
```

Then test:

```bash
curl http://localhost:8080/
curl http://localhost:8080/health
curl http://localhost:8080/s3/check
curl -X POST http://localhost:8080/s3/upload-demo
```

## Step 1: Create the S3 bucket with CloudFormation

```bash
aws cloudformation deploy \
  --stack-name aws-cloud-lab-storage \
  --template-file infrastructure/s3-bucket.yml \
  --parameter-overrides ProjectName=aws-cloud-lab EnvironmentName=production
```

After deployment, get the generated bucket name:

```bash
aws cloudformation describe-stacks \
  --stack-name aws-cloud-lab-storage \
  --query "Stacks[0].Outputs[?OutputKey=='BucketName'].OutputValue" \
  --output text
```

## Step 2: Create the baseline VPC

This step is included to demonstrate VPC, subnet, and security group knowledge in CloudFormation.

```bash
aws cloudformation deploy \
  --stack-name aws-cloud-lab-network \
  --template-file infrastructure/network.yml
```

If you want the fastest deployment path, you can still let AWS Copilot create its own environment VPC. Keep the CloudFormation network stack in the repository as the explicit IaC example.

## Step 3: IAM roles and policies

Before applying the example policies, replace `ACCOUNT-ID-REGION` with the real suffix used by your bucket ARN.

### Recommended: least privilege

The file `infrastructure/iam-least-privilege.json` grants only these permissions:

- `s3:ListBucket` on the project bucket.
- `s3:GetObject`, `s3:PutObject`, and `s3:DeleteObject` on the `demo/` prefix.

Create a task role and attach the policy:

```bash
aws iam create-role \
  --role-name aws-cloud-lab-task-role \
  --assume-role-policy-document file://infrastructure/task-role-trust-policy.json

aws iam put-role-policy \
  --role-name aws-cloud-lab-task-role \
  --policy-name aws-cloud-lab-s3-least-privilege \
  --policy-document file://infrastructure/iam-least-privilege.json
```

### Temporary lab shortcut: full access example

The file `infrastructure/iam-s3-full-access-example.json` intentionally grants `s3:*` on `*`.

Use it only for short-lived experiments and never as the final production policy. Keeping both examples in the repo is useful in interviews because it shows that you understand the tradeoff between convenience and least privilege.

## Step 4: Initialize and deploy with AWS Copilot

Initialize the application:

```bash
copilot app init aws-cloud-lab
```

Create the environment:

```bash
copilot env init --name production --profile default --region eu-west-1
```

Create the load-balanced web service:

```bash
copilot svc init \
  --name frontend \
  --svc-type "Load Balanced Web Service" \
  --dockerfile ./Dockerfile \
  --port 8080
```

Deploy the service:

```bash
copilot svc deploy --name frontend --env production
```

Useful Copilot commands:

```bash
copilot svc status --name frontend --env production
copilot svc logs --name frontend --env production --follow
copilot svc show --name frontend
copilot env show --name production
```

Before deployment, update the `S3_BUCKET` value in `copilot/frontend/manifest.yml` with the real bucket name created by the storage stack.

## Step 5: CloudWatch logs, metrics, and CPU alarm

Logs are produced automatically because the app writes to stdout and stderr.

Tail logs with AWS CLI:

```bash
aws logs tail /copilot/aws-cloud-lab-production-frontend --follow
```

Deploy the CPU > 70 percent alarm:

```bash
aws cloudformation deploy \
  --stack-name aws-cloud-lab-alarms \
  --template-file infrastructure/cloudwatch-alarms.yml \
  --parameter-overrides ClusterName=<ecs-cluster-name> ServiceName=<ecs-service-name>
```

To generate CPU load, call the stress endpoint a few times:

```bash
for i in 1 2 3; do curl "http://<service-url>/stress?seconds=20"; done
```

Validate the alarm from AWS CLI:

```bash
aws cloudwatch describe-alarms --alarm-names <alarm-name>
```

## AWS CLI examples

Upload a file to S3:

```bash
./cli-scripts/upload-to-s3.sh <bucket-name> ./README.md portfolio/README.md
```

Windows PowerShell variant:

```powershell
.\cli-scripts\upload-to-s3.ps1 -BucketName <bucket-name> -FilePath .\README.md -Key portfolio/README.md
```

Inspect EC2 instances in a region:

```bash
./cli-scripts/ec2-status.sh eu-west-1
```

Windows PowerShell variant:

```powershell
.\cli-scripts\ec2-status.ps1 -Region eu-west-1
```

## Security notes

- The S3 bucket blocks all public access and enables encryption at rest.
- The recommended IAM policy limits access to the project bucket and the `demo/` prefix only.
- The application security group accepts traffic only from the load balancer security group.
- The `/stress` endpoint exists only for monitoring demonstrations and should be removed or protected in a real production system.

## Manual verification checklist

1. Build and run the container locally.
2. Deploy the storage and network stacks.
3. Update the Copilot manifest with the real S3 bucket name.
4. Deploy the application with AWS Copilot.
5. Open the load balancer URL and hit `/`, `/health`, and `/s3/check`.
6. Upload a demo file with `/s3/upload-demo` or the CLI script.
7. Tail CloudWatch logs and verify request entries.
8. Trigger the CPU alarm and confirm it enters `ALARM` state.

## Cleanup

Remove resources when you finish the demo to avoid ongoing cost:

```bash
copilot svc delete --name frontend --env production
copilot env delete --name production
aws cloudformation delete-stack --stack-name aws-cloud-lab-alarms
aws cloudformation delete-stack --stack-name aws-cloud-lab-storage
aws cloudformation delete-stack --stack-name aws-cloud-lab-network
```

