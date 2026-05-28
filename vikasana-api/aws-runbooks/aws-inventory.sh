#!/bin/bash
set -e

source "$(dirname "$0")/aws-common.env"

echo "LoRa Connect AWS Resource Inventory"
echo "Generated at: $(date)"
echo "------------------------------------"

echo ""
echo "1) ECR repository/images"
aws ecr describe-images \
  --repository-name lora-connect-api \
  --region $AWS_REGION \
  --query 'imageDetails[*].{Tags:imageTags,Digest:imageDigest,Pushed:imagePushedAt,Size:imageSizeInBytes}' \
  --output table || true

echo ""
echo "2) ECS cluster"
aws ecs describe-clusters \
  --clusters $ECS_CLUSTER_NAME \
  --region $AWS_REGION \
  --query 'clusters[*].{Cluster:clusterName,Status:status,Running:runningTasksCount,Pending:pendingTasksCount,Services:activeServicesCount}' \
  --output table || true

echo ""
echo "3) ECS task definitions"
aws ecs list-task-definitions \
  --family-prefix lora-connect-api-test \
  --region $AWS_REGION \
  --sort DESC \
  --max-items 5 \
  --output table || true

echo ""
echo "4) ECS service"
aws ecs describe-services \
  --cluster $ECS_CLUSTER_NAME \
  --services $SERVICE_NAME \
  --region $AWS_REGION \
  --query 'services[0].{Status:status,Desired:desiredCount,Running:runningCount,Pending:pendingCount}' \
  --output table || true

echo ""
echo "5) Running ECS tasks"
aws ecs list-tasks \
  --cluster $ECS_CLUSTER_NAME \
  --region $AWS_REGION \
  --desired-status RUNNING \
  --output table || true

echo ""
echo "6) Aurora cluster"
aws rds describe-db-clusters \
  --db-cluster-identifier $DB_CLUSTER_ID \
  --region $AWS_REGION \
  --query 'DBClusters[0].{Cluster:DBClusterIdentifier,Status:Status,Engine:Engine,Endpoint:Endpoint,Port:Port}' \
  --output table || true

echo ""
echo "7) S3 buckets"
aws s3api list-buckets \
  --query 'Buckets[?contains(Name, `vikasana`) || contains(Name, `activity-uploads`) || contains(Name, `face-verification`)].Name' \
  --output table || true

echo ""
echo "8) ALB check"
aws elbv2 describe-load-balancers \
  --names $ALB_NAME \
  --region $AWS_REGION \
  --query 'LoadBalancers[0].{Name:LoadBalancerName,DNS:DNSName,State:State.Code}' \
  --output table 2>/dev/null || echo "ALB not found. Event Mode is OFF."

echo ""
echo "Inventory completed."
