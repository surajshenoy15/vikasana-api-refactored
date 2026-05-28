#!/bin/bash
set -e

source "$(dirname "$0")/aws-common.env"

echo "Starting LoRa Connect AWS Event Mode..."

echo "Fetching VPC/subnet/security group details..."
DB_SUBNET_GROUP=$(aws rds describe-db-clusters \
  --db-cluster-identifier $DB_CLUSTER_ID \
  --region $AWS_REGION \
  --query 'DBClusters[0].DBSubnetGroup' \
  --output text)

VPC_ID=$(aws rds describe-db-subnet-groups \
  --db-subnet-group-name $DB_SUBNET_GROUP \
  --region $AWS_REGION \
  --query 'DBSubnetGroups[0].VpcId' \
  --output text)

SUBNETS=$(aws rds describe-db-subnet-groups \
  --db-subnet-group-name $DB_SUBNET_GROUP \
  --region $AWS_REGION \
  --query 'DBSubnetGroups[0].Subnets[0:2].SubnetIdentifier' \
  --output text)

FARGATE_SG_ID=$(aws ec2 describe-security-groups \
  --filters "Name=group-name,Values=lora-connect-fargate-test-sg" "Name=vpc-id,Values=$VPC_ID" \
  --region $AWS_REGION \
  --query 'SecurityGroups[0].GroupId' \
  --output text)

echo "VPC_ID=$VPC_ID"
echo "SUBNETS=$SUBNETS"
echo "FARGATE_SG_ID=$FARGATE_SG_ID"

echo "Creating/getting ALB security group..."
ALB_SG_ID=$(aws ec2 create-security-group \
  --group-name lora-connect-alb-test-sg \
  --description "LoRa Connect temporary ALB test SG" \
  --vpc-id $VPC_ID \
  --region $AWS_REGION \
  --query 'GroupId' \
  --output text 2>/dev/null || aws ec2 describe-security-groups \
  --filters "Name=group-name,Values=lora-connect-alb-test-sg" "Name=vpc-id,Values=$VPC_ID" \
  --region $AWS_REGION \
  --query 'SecurityGroups[0].GroupId' \
  --output text)

VPS_PUBLIC_IP=$(curl -s https://checkip.amazonaws.com | tr -d '\n')

aws ec2 authorize-security-group-ingress \
  --group-id $ALB_SG_ID \
  --protocol tcp \
  --port 80 \
  --cidr $VPS_PUBLIC_IP/32 \
  --region $AWS_REGION 2>/dev/null || true

aws ec2 authorize-security-group-ingress \
  --group-id $FARGATE_SG_ID \
  --protocol tcp \
  --port 8000 \
  --source-group $ALB_SG_ID \
  --region $AWS_REGION 2>/dev/null || true

echo "Creating/getting target group..."
TG_ARN=$(aws elbv2 create-target-group \
  --name $TG_NAME \
  --protocol HTTP \
  --port 8000 \
  --vpc-id $VPC_ID \
  --target-type ip \
  --health-check-protocol HTTP \
  --health-check-path /health \
  --health-check-port traffic-port \
  --region $AWS_REGION \
  --query 'TargetGroups[0].TargetGroupArn' \
  --output text 2>/dev/null || aws elbv2 describe-target-groups \
  --names $TG_NAME \
  --region $AWS_REGION \
  --query 'TargetGroups[0].TargetGroupArn' \
  --output text)

echo "Creating/getting ALB..."
ALB_ARN=$(aws elbv2 create-load-balancer \
  --name $ALB_NAME \
  --subnets $SUBNETS \
  --security-groups $ALB_SG_ID \
  --scheme internet-facing \
  --type application \
  --ip-address-type ipv4 \
  --region $AWS_REGION \
  --query 'LoadBalancers[0].LoadBalancerArn' \
  --output text 2>/dev/null || aws elbv2 describe-load-balancers \
  --names $ALB_NAME \
  --region $AWS_REGION \
  --query 'LoadBalancers[0].LoadBalancerArn' \
  --output text)

ALB_DNS=$(aws elbv2 describe-load-balancers \
  --load-balancer-arns $ALB_ARN \
  --region $AWS_REGION \
  --query 'LoadBalancers[0].DNSName' \
  --output text)

echo "Creating/getting listener..."
LISTENER_ARN=$(aws elbv2 create-listener \
  --load-balancer-arn $ALB_ARN \
  --protocol HTTP \
  --port 80 \
  --default-actions Type=forward,TargetGroupArn=$TG_ARN \
  --region $AWS_REGION \
  --query 'Listeners[0].ListenerArn' \
  --output text 2>/dev/null || aws elbv2 describe-listeners \
  --load-balancer-arn $ALB_ARN \
  --region $AWS_REGION \
  --query 'Listeners[0].ListenerArn' \
  --output text)

echo "Creating ECS service if missing..."
SERVICE_STATUS=$(aws ecs describe-services \
  --cluster $ECS_CLUSTER_NAME \
  --services $SERVICE_NAME \
  --region $AWS_REGION \
  --query 'services[0].status' \
  --output text 2>/dev/null || true)

if [ "$SERVICE_STATUS" = "ACTIVE" ] || [ "$SERVICE_STATUS" = "DRAINING" ]; then
  echo "Service exists. Updating desired count to 1..."
  aws ecs update-service \
    --cluster $ECS_CLUSTER_NAME \
    --service $SERVICE_NAME \
    --desired-count 1 \
    --force-new-deployment \
    --region $AWS_REGION >/dev/null
else
  echo "Service not active. Creating service..."
  aws ecs create-service \
    --cluster $ECS_CLUSTER_NAME \
    --service-name $SERVICE_NAME \
    --task-definition lora-connect-api-test \
    --desired-count 1 \
    --launch-type FARGATE \
    --network-configuration "awsvpcConfiguration={subnets=[$(echo $SUBNETS | sed 's/ /,/g')],securityGroups=[$FARGATE_SG_ID],assignPublicIp=ENABLED}" \
    --load-balancers "targetGroupArn=$TG_ARN,containerName=lora-connect-api,containerPort=8000" \
    --health-check-grace-period-seconds 120 \
    --region $AWS_REGION >/dev/null
fi

echo ""
echo "AWS Event Mode starting..."
echo "ALB_DNS=$ALB_DNS"
echo "Health URL: http://$ALB_DNS/health"
echo ""
echo "Wait 2-3 minutes, then run:"
echo "./aws-runbooks/aws-event-status.sh"
