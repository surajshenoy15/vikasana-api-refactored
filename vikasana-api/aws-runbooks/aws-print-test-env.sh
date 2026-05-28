#!/bin/bash
set -e

source "$(dirname "$0")/aws-common.env"

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

echo ""
echo "AWS Event Mode API URL:"
echo "$BASE_URL"
echo ""
echo "For React Native / Expo test env:"
echo "EXPO_PUBLIC_API_URL=$BASE_URL"
echo "EXPO_PUBLIC_ENV=aws-test"
echo ""
echo "For React + Vite admin test env:"
echo "VITE_API_URL=$BASE_URL"
echo "VITE_ENV=aws-test"
echo ""
echo "Backend health check:"
echo "$BASE_URL/health"
echo ""
