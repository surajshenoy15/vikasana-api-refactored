# LoRa Connect AWS Real-User Test Checklist

This checklist is for controlled testing of AWS Event Mode before using it in a real event.

## Important Safety Notes

- Do not change live Hostinger `.env`.
- Do not restart live Docker Compose stack.
- Do not switch production mobile app users to AWS yet.
- Use only trusted test users.
- After testing, always run `aws-event-down.sh` and `aws-cost-check.sh`.

---

## Pre-Test Checklist

Run:

./aws-runbooks/aws-cost-check.sh

Expected safe state before starting:

- No running ECS tasks
- ECS desired/running/pending = 0
- ALB not found
- Aurora available
- ECR stable image exists

---

## Start AWS Event Mode

Run:

./aws-runbooks/aws-event-up.sh

Wait 2 to 3 minutes.

Then run:

./aws-runbooks/aws-event-status.sh

Expected:

- ECS service ACTIVE
- Desired = 1
- Running = 1
- Target health = healthy
- /health returns {"status":"healthy"}

---

## Smoke Test

Run:

./aws-runbooks/aws-api-smoke-test.sh

Expected:

- /health returns 200
- /docs returns 200
- /openapi.json returns 200

---

## Manual API/User Flow Test

Test with 1 trusted user first.

### Test 1: Login

Check:

- User can login
- Token/session works
- No server error
- CloudWatch logs are clean

### Test 2: Event List

Check:

- Events load correctly
- Event data matches Aurora data
- No missing images or thumbnails

### Test 3: Upload Flow

Check:

- Image upload works
- File appears in correct AWS S3 bucket
- DB record is created in Aurora
- No MinIO dependency is used in AWS Event Mode

### Test 4: Certificate/Existing Data

Check:

- Existing certificates can be accessed
- Existing S3 migrated files are reachable
- No broken URLs

### Test 5: Admin/Faculty Flow

Check:

- Admin/faculty login works
- Event/submission views load
- Approval or read-only test works as expected

---

## CloudWatch Log Check

List latest logs:

aws logs describe-log-streams \
  --log-group-name /ecs/lora-connect-api-test \
  --region ap-south-1 \
  --order-by LastEventTime \
  --descending \
  --max-items 5 \
  --query 'logStreams[*].logStreamName' \
  --output table

Read latest log stream by replacing LOG_STREAM_NAME:

aws logs get-log-events \
  --log-group-name /ecs/lora-connect-api-test \
  --log-stream-name "LOG_STREAM_NAME" \
  --region ap-south-1 \
  --limit 100 \
  --query 'events[*].message' \
  --output text

Check for:

- ERROR
- Traceback
- database connection failures
- S3 access failures
- 500 responses

---

## Stop AWS Event Mode

After testing, always run:

./aws-runbooks/aws-event-down.sh

Then verify:

./aws-runbooks/aws-event-status.sh

Expected:

- No running ECS tasks
- ECS service DRAINING or INACTIVE
- Desired = 0
- Running = 0
- ALB not found

Then run:

./aws-runbooks/aws-cost-check.sh

Expected:

- No running ECS tasks
- ALB not found

---

## Test Result Template

Date:
Tester:
AWS ALB URL:
Test user:
App version/build:
Backend image tag:

Results:

- Health:
- Login:
- Event list:
- Upload:
- S3 verification:
- Aurora DB verification:
- Admin/faculty:
- Logs clean:
- Event mode stopped:
- Cost check completed:

Issues found:

1.
2.
3.

Final decision:

- PASS / FAIL
- Ready for next larger test: YES / NO
