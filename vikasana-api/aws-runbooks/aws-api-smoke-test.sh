#!/bin/bash
set -e

source "$(dirname "$0")/aws-common.env"

echo "Finding ALB DNS..."

ALB_ARN=$(aws elbv2 describe-load-balancers \
  --names $ALB_NAME \
  --region $AWS_REGION \
  --query 'LoadBalancers[0].LoadBalancerArn' \
  --output text 2>/dev/null || true)

if [ -z "$ALB_ARN" ] || [ "$ALB_ARN" = "None" ]; then
  echo "ALB not found. Event Mode is OFF."
  echo "Run ./aws-runbooks/aws-event-up.sh first."
  exit 1
fi

ALB_DNS=$(aws elbv2 describe-load-balancers \
  --load-balancer-arns $ALB_ARN \
  --region $AWS_REGION \
  --query 'LoadBalancers[0].DNSName' \
  --output text)

BASE_URL="http://$ALB_DNS"

echo "BASE_URL=$BASE_URL"
echo ""

echo "1) Testing /health..."
curl -s -i "$BASE_URL/health"
echo ""
echo ""

echo "2) Testing /docs..."
curl -s -o /dev/null -w "HTTP_STATUS=%{http_code}\n" "$BASE_URL/docs"
echo ""

echo "3) Testing /openapi.json..."
curl -s -o /dev/null -w "HTTP_STATUS=%{http_code}\n" "$BASE_URL/openapi.json"
echo ""

echo "Smoke test completed."
