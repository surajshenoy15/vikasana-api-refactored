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

check_endpoint () {
  METHOD=$1
  PATH=$2
  EXPECTED=$3

  URL="$BASE_URL$PATH"

  echo "Testing $METHOD $PATH"

  CODE=$(curl -s -o /tmp/aws-smoke-response.txt -w "%{http_code}" -X "$METHOD" "$URL")

  echo "HTTP_STATUS=$CODE"

  if [ "$CODE" = "$EXPECTED" ]; then
    echo "PASS"
  else
    echo "WARN: Expected $EXPECTED but got $CODE"
    echo "Response preview:"
    head -c 300 /tmp/aws-smoke-response.txt || true
    echo ""
  fi

  echo "----------------------------------"
}

echo "Starting safe public/read-only smoke tests..."
echo "----------------------------------"

check_endpoint GET "/health" "200"
check_endpoint GET "/" "200"
check_endpoint GET "/docs" "200"
check_endpoint GET "/openapi.json" "200"
check_endpoint GET "/api/college-access/status" "200"

echo ""
echo "Smoke test completed."
echo "If /api/college-access/status gives 404 or 401, it may require configuration/auth and can be ignored."
