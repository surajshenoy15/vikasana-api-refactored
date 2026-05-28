#!/bin/bash
set -e

source "$(dirname "$0")/aws-common.env"

ALB_ARN=$(aws elbv2 describe-load-balancers \
  --names $ALB_NAME \
  --region $AWS_REGION \
  --query 'LoadBalancers[0].LoadBalancerArn' \
  --output text 2>/dev/null || true)

if [ -z "$ALB_ARN" ] || [ "$ALB_ARN" = "None" ]; then
  echo "ALB not found. Run ./aws-runbooks/aws-event-up.sh first."
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

read -p "Admin email: " ADMIN_EMAIL
read -s -p "Admin password: " ADMIN_PASSWORD
echo ""

echo "Logging in admin..."
LOGIN_RESPONSE=$(curl -s -X POST "$BASE_URL/api/auth/login" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$ADMIN_EMAIL\",\"password\":\"$ADMIN_PASSWORD\"}")

echo "$LOGIN_RESPONSE" > /tmp/lora-auth-response.json

TOKEN=$(python3 - <<'PY'
import json
data=json.load(open("/tmp/lora-auth-response.json"))
for key in ["access_token", "token", "accessToken"]:
    if key in data:
        print(data[key])
        break
PY
)

if [ -z "$TOKEN" ]; then
  echo "Login failed or token key not found."
  echo "Response:"
  cat /tmp/lora-auth-response.json
  exit 1
fi

echo "Login OK. Token received."

echo ""
echo "Testing admin dashboard stats..."
curl -s -i "$BASE_URL/api/admin/dashboard/stats" \
  -H "Authorization: Bearer $TOKEN"

echo ""
echo ""
echo "Testing admin students..."
curl -s -i "$BASE_URL/api/admin/students" \
  -H "Authorization: Bearer $TOKEN"

echo ""
echo ""
echo "Auth smoke test completed."
