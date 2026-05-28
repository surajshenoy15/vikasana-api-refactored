#!/bin/bash
set -e

source "$(dirname "$0")/aws-common.env"

echo "Stopping LoRa Connect AWS Event Mode..."

echo "Scaling ECS service to 0 if active..."
SERVICE_STATUS=$(aws ecs describe-services \
  --cluster $ECS_CLUSTER_NAME \
  --services $SERVICE_NAME \
  --region $AWS_REGION \
  --query 'services[0].status' \
  --output text 2>/dev/null || true)

if [ "$SERVICE_STATUS" = "ACTIVE" ]; then
  aws ecs update-service \
    --cluster $ECS_CLUSTER_NAME \
    --service $SERVICE_NAME \
    --desired-count 0 \
    --region $AWS_REGION >/dev/null

  echo "Waiting for running tasks to stop..."
  sleep 60

  aws ecs delete-service \
    --cluster $ECS_CLUSTER_NAME \
    --service $SERVICE_NAME \
    --region $AWS_REGION >/dev/null || true

  echo "ECS service delete requested."
else
  echo "ECS service is not ACTIVE. Current status: $SERVICE_STATUS"
fi

echo "Getting ALB resources..."
ALB_ARN=$(aws elbv2 describe-load-balancers \
  --names $ALB_NAME \
  --region $AWS_REGION \
  --query 'LoadBalancers[0].LoadBalancerArn' \
  --output text 2>/dev/null || true)

TG_ARN=$(aws elbv2 describe-target-groups \
  --names $TG_NAME \
  --region $AWS_REGION \
  --query 'TargetGroups[0].TargetGroupArn' \
  --output text 2>/dev/null || true)

if [ -n "$ALB_ARN" ] && [ "$ALB_ARN" != "None" ]; then
  LISTENER_ARN=$(aws elbv2 describe-listeners \
    --load-balancer-arn $ALB_ARN \
    --region $AWS_REGION \
    --query 'Listeners[0].ListenerArn' \
    --output text 2>/dev/null || true)

  if [ -n "$LISTENER_ARN" ] && [ "$LISTENER_ARN" != "None" ]; then
    echo "Deleting ALB listener..."
    aws elbv2 delete-listener \
      --listener-arn $LISTENER_ARN \
      --region $AWS_REGION || true
  fi

  echo "Deleting ALB..."
  aws elbv2 delete-load-balancer \
    --load-balancer-arn $ALB_ARN \
    --region $AWS_REGION || true

  echo "Waiting for ALB deletion..."
  sleep 120
else
  echo "ALB not found."
fi

if [ -n "$TG_ARN" ] && [ "$TG_ARN" != "None" ]; then
  echo "Deleting target group..."
  aws elbv2 delete-target-group \
    --target-group-arn $TG_ARN \
    --region $AWS_REGION || echo "Target group may still be in use. Retry after 1-2 minutes."
else
  echo "Target group not found."
fi

echo ""
echo "AWS Event Mode OFF cleanup requested."
echo "Run ./aws-runbooks/aws-event-status.sh to verify."
