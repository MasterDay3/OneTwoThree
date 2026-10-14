# Lab 5 — weekly meetings report: answers

What was built (`infra/reports.yaml`, `back/app/reports/`): EventBridge Scheduler (`cron(0 7 ? * MON *)`,
Europe/Kyiv) and `make report-now WEEK=…` send `{"week": …, "source": …}` to the `meetings-report-requests`
SQS queue (visibility timeout 360 s = 6 × the builder's 60 s, DLQ after 3 receives). `meetings-report-builder`
(the backend image, handler `app.reports.lambda_handler.handler`) builds `reports/<week>.csv` and saves it to
`meetings-reports-<account>`. The `ObjectCreated` event (prefix `reports/`, suffix `.csv`) starts
`meetings-report-mailer`, which emails the file through SES from `reports@mykollazab.pp.ua`.

**One difference from the lab text.** This deployment has no VPC. On the AWS Free plan the only Aurora
available is the express configuration, which has no VPC and is reached through its internet access gateway
with IAM tokens (see `infra/backend.yaml`). So the builder is not in a VPC, has no reason for an S3 gateway
endpoint, and could reach SES itself. The two functions are still split. The builder is the only one with
database access, the mailer is the only one with SES access, and they are joined only by the S3 event.

## Step 7 checks

- **Same week twice:** two `make report-now WEEK=2026-W40` messages produced **one file**
  (`reports/2026-W40.csv`, overwritten) and **two emails**. Every `PutObject` is a new `ObjectCreated` event,
  even when the key and the bytes are the same. So the builder is idempotent (same week, same key) but the
  mailer is not: sending an email is a side effect that cannot be overwritten. Answer 1 shows how to fix it.
- **Dead-letter queue:** with `REPORT_FAIL_WEEK=2026-W30` (`make aws-reports-stack REPORT_FAIL_WEEK=2026-W30`),
  `make report-now WEEK=2026-W30` failed at 18:19, 18:25 and 18:31 UTC. The 6-minute gaps are the visibility
  timeout. After the third receive the message was in `meetings-report-requests-dlq`. After redeploying
  without the failure, the DLQ redrive moved it back, and the builder saved `reports/2026-W30.csv` and
  the mailer emailed it.

## Answers

### 1. The mailer got the same `ObjectCreated` event twice. Where do you store "already sent", and what is the key?

In a DynamoDB table (for example `report-emails`), written with a conditional put
(`attribute_not_exists(pk)`) before `send_email`. If the put fails, the email was already sent and the
mailer stops. DynamoDB fits because the mailer has no database access by design and runs outside any VPC,
and a conditional write is atomic, so two concurrent deliveries of the same event cannot both win. A TTL of
about 90 days, the same as the reports, keeps the table small.

The key is the object version, not just its name: `pk = "<bucket>/<key>#<eTag>"` (or `#<versionId>` with
versioning on), plus the recipient if each person is tracked separately. A duplicate delivery carries the same
key and the same ETag, so it is dropped. A deliberate rebuild with changed data has a new ETag and is
emailed again. Keying on `reports/2026-W40.csv` alone would also suppress a corrected report. The record
should go `sending` → `sent`, so that a crash between the put and the send can be retried, not lost.

### 2. The builder in the VPC cannot reach SES. Three fixes and their monthly cost

Prices are us-east-1, with a Lambda subnet in each of 2 AZs:

| Option | Fixed cost per month | Per use |
|---|---|---|
| NAT gateway (one, in one AZ) | $0.045/h × 730 h ≈ **$33** (≈ $66 with one per AZ for availability) | $0.045 per GB processed |
| VPC interface endpoint for SES (`email-smtp`/`email`) | $0.01/h × 730 h × 2 AZ ≈ **$15** | $0.01 per GB |
| The split: builder → S3 (gateway endpoint, free) → event → mailer outside the VPC | **$0** | one extra Lambda invocation and one S3 GET per report, inside the free tier |

- **Ten more emails a week:** the split. It costs nothing at this volume, and adding a second listener
  later (Slack) needs no network change.
- **A million emails a week:** SES itself costs $0.10 per 1,000, so about $430 a month. All three network
  options are rounding errors next to that, and NAT data processing for ~5 GB of mail is about $0.25. The
  choice becomes about operations, not price. I would use the SES interface endpoint: sending stays
  private, one function sends in bulk with no S3 round trip per email, and there is no NAT that every
  other outbound request in the VPC would start using without anyone noticing. The NAT gateway is the
  worst of the three in both cases: most expensive fixed cost, and it opens the whole internet to the VPC.

### 3. A link that works for a week instead of an attachment

A presigned S3 URL is only valid while the credentials that signed it are. A Lambda role's credentials are
temporary, so the link dies with them, usually within hours, whatever `ExpiresIn` says. Options that work
for seven days:

- **CloudFront signed URL (my choice).** Put CloudFront with Origin Access Control in front of the reports
  bucket and sign the URL with a CloudFront key pair. The private key is in Secrets Manager or SSM
  SecureString, and the mailer reads it. The expiry is just a timestamp in the signed policy, so 7 days
  means 7 days, independent of IAM sessions. The bucket stays private.
- **A link to the app instead of the file.** For example `https://app.mykollazab.pp.ua/reports/2026-W40`.
  The API checks the Cognito sign-in and responds with a fresh 5-minute presigned URL. The link never
  expires, and access is checked when it is clicked, not when the email is sent.
- Signing with a long-lived IAM user's access key would also allow 7 days (the SigV4 maximum). It means
  keeping a static key, which this project avoids everywhere else (CI uses OIDC).
