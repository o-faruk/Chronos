# Deploying Chronos

Not deployed by Claude Code -- this needs your AWS account, your credentials,
and creates real billed resources. Steps below are for you to run.

## Prerequisites

- [AWS SAM CLI](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html)
- Docker (needed for `--use-container` below -- numpy/scipy ship compiled
  binaries that must match Lambda's Amazon Linux runtime, not whatever OS
  you're building on; building without a container risks bundling
  incompatible `.so` files that fail at cold start, not at build time)
- An AWS account with credentials configured (`aws configure`)
- A Space-Track account (see the main README/docs/decisions.md)

## 1. Store Space-Track credentials in SSM Parameter Store

Never in the template, never in the repo:

```bash
aws ssm put-parameter --name /chronos/spacetrack/username \
  --type String --value "you@example.com"
aws ssm put-parameter --name /chronos/spacetrack/password \
  --type SecureString --value "your-password"
```

## 2. Build

```bash
sam build --use-container
```

## 3. Deploy

```bash
sam deploy --guided
```

First run will prompt for a stack name, region, and confirm IAM role
creation (the template creates least-privilege roles per function via SAM's
policy templates -- `DynamoDBCrudPolicy`, `S3CrudPolicy`, `S3ReadPolicy`,
`DynamoDBReadPolicy`, plus an explicit `ssm:GetParameter` statement scoped to
just the two Chronos parameter paths).

## 4. First run

The scheduled screening Lambda won't fire until the first EventBridge
schedule tick (default every 2 hours -- see `ScreeningScheduleExpression` in
`template.yaml`). To populate data immediately instead of waiting:

```bash
aws lambda invoke --function-name <stack-name>-ScheduledScreeningFunction-XXXX out.json
```

Then `GET /conjunctions` on the API URL from the stack outputs should return
real data.

## Cost note

Pay-per-request DynamoDB + S3 + Lambda, no idle compute. At the default 2h
schedule (12 runs/day, ~3.2 min compute each -- see docs/decisions.md) this
is a low-single-digit-dollars/month workload, not a fixed-cost server. The
on-demand `/screen` endpoint only runs (and only costs) when called.
