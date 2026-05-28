#!/bin/bash
set -e

source "$(dirname "$0")/aws-common.env"

echo "LoRa Connect Aurora DB Counts"
echo "-----------------------------"

echo "Fetching network values..."

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

echo "DB_SUBNET_GROUP=$DB_SUBNET_GROUP"
echo "VPC_ID=$VPC_ID"
echo "SUBNETS=$SUBNETS"
echo "FARGATE_SG_ID=$FARGATE_SG_ID"

TASK_ARN=$(aws ecs run-task \
  --cluster $ECS_CLUSTER_NAME \
  --launch-type FARGATE \
  --task-definition lora-connect-api-test \
  --count 1 \
  --network-configuration "awsvpcConfiguration={subnets=[$(echo $SUBNETS | sed 's/ /,/g')],securityGroups=[$FARGATE_SG_ID],assignPublicIp=ENABLED}" \
  --overrides '{
    "containerOverrides": [
      {
        "name": "lora-connect-api",
        "command": [
          "python",
          "-c",
          "import os, asyncio, asyncpg, ssl\nfrom urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode\nasync def main():\n    raw=os.environ[\"DATABASE_URL\"].replace(\"postgresql+asyncpg://\",\"postgresql://\")\n    p=urlsplit(raw)\n    q=dict(parse_qsl(p.query))\n    for k in list(q.keys()):\n        if k.lower() in (\"ssl\",\"sslmode\"):\n            q.pop(k)\n    clean=urlunsplit((p.scheme,p.netloc,p.path,urlencode(q),p.fragment))\n    ctx=ssl.create_default_context(cafile=\"/app/certs/global-bundle.pem\")\n    conn=await asyncpg.connect(clean, ssl=ctx)\n    tables=[\"students\",\"admins\",\"events\",\"event_submissions\",\"activity_sessions\",\"activity_photos\",\"certificates\"]\n    print(\"DB_COUNTS_START\")\n    for t in tables:\n        try:\n            c=await conn.fetchval(f\"select count(*) from {t}\")\n            print(f\"{t}: {c}\")\n        except Exception as e:\n            print(f\"{t}: ERROR {e}\")\n    await conn.close()\n    print(\"DB_COUNTS_SUCCESS\")\nasyncio.run(main())"
        ]
      }
    ]
  }' \
  --region $AWS_REGION \
  --query 'tasks[0].taskArn' \
  --output text)

echo "TASK_ARN=$TASK_ARN"
sleep 60

TASK_ID=$(basename $TASK_ARN)

LOG_STREAM=$(aws logs describe-log-streams \
  --log-group-name /ecs/lora-connect-api-test \
  --region $AWS_REGION \
  --log-stream-name-prefix "api/lora-connect-api/$TASK_ID" \
  --query 'logStreams[0].logStreamName' \
  --output text | awk '{print $1}')

aws logs get-log-events \
  --log-group-name /ecs/lora-connect-api-test \
  --log-stream-name "$LOG_STREAM" \
  --region $AWS_REGION \
  --limit 100 \
  --query 'events[*].message' \
  --output text

echo ""
echo "DB count check completed."
