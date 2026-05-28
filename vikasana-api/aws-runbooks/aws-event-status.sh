#!/bin/bash
set -e

source "$(dirname "$0")/aws-common.env"

echo "Checking ECS tasks..."
aws ecs list-tasks \
  --cluster $ECS_CLUSTER_NAME \
  --region $AWS_REGION \
  --desired-status RUNNING \
  --output table

echo ""
echo "Checking ECS service..."
aws ecs describe-services \
  --cluster $ECS_CLUSTER_NAME \
  --services $SERVICE_NAME \
  --region $AWS_REGION \
  --query 'services[0].{Status:status,Desired:desiredCount,Running:runningCount,Pending:pendingCount}' \
  --output table || true

echo ""
echo "Checking ALB..."
ALB_ARN=$(aws elbv2 describe-load-balancers \
  --names $ALB_NAME \
  --region $AWS_REGION \
  --query 'LoadBalancers[0].LoadBalancerArn' \
  --output text 2>/dev/null || true)

if [ -z "$ALB_ARN" ] || [ "$ALB_ARN" = "None" ]; then
  echo "ALB not found. Good if event mode is OFF."
  exit 0
fi

ALB_DNS=$(aws elbv2 describe-load-balancers \
  --load-balancer-arns $ALB_ARN \
  --region $AWS_REGION \
  --query 'LoadBalancers[0].DNSName' \
  --output text)

echo "ALB_DNS=$ALB_DNS"

TG_ARN=$(aws elbv2 describe-target-groups \
  --names $TG_NAME \
  --region $AWS_REGION \
  --query 'TargetGroups[0].TargetGroupArn' \
  --output text 2>/dev/null || true)

if [ -n "$TG_ARN" ] && [ "$TG_ARN" != "None" ]; then
  echo ""
  echo "Checking target health..."
  aws elbv2 describe-target-health \
    --target-group-arn $TG_ARN \
    --region $AWS_REGION \
    --query 'TargetHealthDescriptions[*].{Target:Target.Id,Port:Target.Port,State:TargetHealth.State,Reason:TargetHealth.Reason}' \
    --output table
fi

echo ""
echo "Testing /health..."
curl -s "http://$ALB_DNS/health" || true
echo ""
