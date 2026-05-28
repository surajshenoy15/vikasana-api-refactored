#!/bin/bash
set -e

source "$(dirname "$0")/aws-common.env"

echo "LoRa Connect AWS Cost Safety Check"
echo "----------------------------------"

echo ""
echo "1) Checking running ECS/Fargate tasks..."
aws ecs list-tasks \
  --cluster $ECS_CLUSTER_NAME \
  --region $AWS_REGION \
  --desired-status RUNNING \
  --output table

echo ""
echo "2) Checking ECS service..."
aws ecs describe-services \
  --cluster $ECS_CLUSTER_NAME \
  --services $SERVICE_NAME \
  --region $AWS_REGION \
  --query 'services[0].{Status:status,Desired:desiredCount,Running:runningCount,Pending:pendingCount}' \
  --output table || true

echo ""
echo "3) Checking ALB..."
ALB_EXISTS=$(aws elbv2 describe-load-balancers \
  --names $ALB_NAME \
  --region $AWS_REGION \
  --query 'LoadBalancers[0].LoadBalancerName' \
  --output text 2>/dev/null || true)

if [ -z "$ALB_EXISTS" ] || [ "$ALB_EXISTS" = "None" ]; then
  echo "ALB not found. Good: no ALB hourly cost for event mode."
else
  echo "WARNING: ALB exists: $ALB_EXISTS"
  echo "If event mode is not needed, run: ./aws-runbooks/aws-event-down.sh"
fi

echo ""
echo "4) Checking Aurora cluster status..."
aws rds describe-db-clusters \
  --db-cluster-identifier $DB_CLUSTER_ID \
  --region $AWS_REGION \
  --query 'DBClusters[0].{Cluster:DBClusterIdentifier,Status:Status,Engine:Engine,Endpoint:Endpoint}' \
  --output table

echo ""
echo "5) Checking ECR stable image tag..."
aws ecr describe-images \
  --repository-name lora-connect-api \
  --region $AWS_REGION \
  --image-ids imageTag=$STABLE_TAG \
  --query 'imageDetails[0].{ImageDigest:imageDigest,ImageTags:imageTags,Size:imageSizeInBytes}' \
  --output table

echo ""
echo "Cost check completed."
echo "Expected safe event-OFF state:"
echo "- No running ECS tasks"
echo "- ECS desired/running/pending = 0"
echo "- ALB not found"
