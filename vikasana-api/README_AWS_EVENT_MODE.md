# LoRa Connect AWS Event Mode Runbook

This runbook explains how to safely start and stop AWS Event Mode for LoRa Connect.

## Production Safety

The live Hostinger VPS production app remains unchanged.

Live VPS currently uses:

- Docker PostgreSQL
- MinIO
- Redis
- Celery worker
- FastAPI backend
- Live .env with S3_PROVIDER=minio
- Live .env database pointing to local Docker PostgreSQL

Do not change the live .env unless intentionally migrating production.

## AWS Event Mode Architecture

AWS Event Mode uses:

- Amazon ECR for Docker image
- Amazon ECS Fargate for FastAPI backend
- Application Load Balancer for temporary public API URL
- Aurora PostgreSQL for database
- AWS S3 for object storage
- CloudWatch Logs for container logs
- AWS Systems Manager Parameter Store for environment variables

Verified path:

ALB -> ECS Fargate -> FastAPI -> Aurora PostgreSQL + AWS S3

## Verified Working Image

Stable ECR image tag:

652197206453.dkr.ecr.ap-south-1.amazonaws.com/lora-connect-api:fargate-test-db-s3-ssl-ok

This image has been tested for:

- FastAPI startup
- /health endpoint
- Aurora PostgreSQL connection
- Aurora SSL verification
- pgvector extension
- AWS S3 bucket access
- ALB target group health

## Start Event Mode

Run from project root:

./aws-runbooks/aws-event-up.sh

Wait 2 to 3 minutes.

Then check status:

./aws-runbooks/aws-event-status.sh

Expected:

- ECS service ACTIVE
- Desired: 1
- Running: 1
- Target health: healthy
- /health returns {"status":"healthy"}

## Check Event Mode Status

./aws-runbooks/aws-event-status.sh

Use this during event testing to confirm:

- ECS task is running
- ALB exists
- Target group is healthy
- /health endpoint works

## Stop Event Mode

After testing or after an event, run:

./aws-runbooks/aws-event-down.sh

Then verify:

./aws-runbooks/aws-event-status.sh

Expected:

- No running ECS tasks
- ALB not found
- ECS service DRAINING or INACTIVE

DRAINING is okay if:

- Desired = 0
- Running = 0
- ALB not found

AWS will mark it INACTIVE after some time.

## Cost Safety

To avoid unnecessary AWS cost, always run this after testing:

./aws-runbooks/aws-event-down.sh

ALB costs money while it exists.

Fargate costs money while tasks are running.

Aurora Serverless v2 may still have minimum ACU cost.

## Do Not Do These Unless Intentionally Migrating Production

Do not run:

docker compose down
docker compose up

Do not edit live .env.

Do not switch live VPS app from MinIO/PostgreSQL to AWS without a planned migration window.

Do not point the mobile app to AWS ALB URL unless you intentionally want users to test AWS.

## Useful Manual Checks

Check running Fargate tasks:

aws ecs list-tasks \
  --cluster lora-connect-test-cluster \
  --region ap-south-1 \
  --desired-status RUNNING \
  --output table

Check service status:

aws ecs describe-services \
  --cluster lora-connect-test-cluster \
  --services lora-connect-api-test-service \
  --region ap-south-1 \
  --query 'services[0].{Status:status,Desired:desiredCount,Running:runningCount,Pending:pendingCount}' \
  --output table

Check latest logs:

aws logs describe-log-streams \
  --log-group-name /ecs/lora-connect-api-test \
  --region ap-south-1 \
  --order-by LastEventTime \
  --descending \
  --max-items 5 \
  --query 'logStreams[*].logStreamName' \
  --output table

## Current Safe Checkpoint

Completed and verified:

- MinIO data copied to AWS S3
- PostgreSQL data restored to Aurora
- Table counts matched
- pgvector enabled
- ECR image pushed
- Stable ECR tag created
- Fargate task tested
- Fargate to Aurora tested
- Aurora SSL verification fixed
- Fargate to S3 tested
- ALB to Fargate tested
- Event up/down/status scripts created
- Live VPS production untouched
