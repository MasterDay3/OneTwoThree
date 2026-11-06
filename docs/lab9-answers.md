# Lab 9: one project, two clouds — costs and answers

The service choices are in [`adr/0008-google-cloud.md`](adr/0008-google-cloud.md). This file has the
Part 3 cost table and the Part 4 questions.

## Part 3 — what Spry's core costs on each cloud

On-demand list prices in USD, checked on 5 November 2026, without tax, 730 hours a month: AWS in
us-east-1, Google Cloud in us-central1. Both columns are what this repository deploys: AWS from
Lab 4 (Aurora Serverless v2 0–1 ACU that pauses after 5 idle minutes, Lambda 512 MB arm64, API
Gateway HTTP API on `api.<domain>`, CloudFront on the Free plan), and Google Cloud from `gcp/`
(Cloud SQL `db-f1-micro` with 10 GB SSD, Cloud Run 1 vCPU / 512 MiB with request-based billing and
min 0 instances, Firebase Hosting).

### Assumptions

| Profile | Database awake | Traffic in a month |
| --- | --- | --- |
| Nobody | 0 h | none |
| Class demos (10 × 2 h) | 20 h (+5 min each time before Aurora pauses) | ~2,000 API requests |
| Office hours (8 h × 22 days) | 176 h (+5 min a day) | ~50,000 API requests |
| Busy around the clock | 730 h | 1 request a second: 2.63 M API requests, 100 ms each; ~2,000 page loads a day |

- Aurora at 0.5 ACU while lightly used ($0.06 an hour) and at its 1 ACU maximum when busy ($0.12),
  1 GB of storage ($0.10). The Lambda free tier (1 M requests, 400,000 GB-seconds) and CloudFront's
  Free plan ($0, no overage) are always-free, so they stay after the first year.
- Cloud SQL bills by the hour while it runs, whatever the traffic. The Cloud Run free tier
  (2 M requests, 180,000 vCPU-seconds, 360,000 GiB-seconds) and Firebase Hosting's 10 GB stored /
  360 MB a day transferred are monthly free tiers. Requests rewritten to Cloud Run count toward the
  Hosting transfer. Artifact Registry (0.5 GB), Secret Manager (6 versions, 10,000 accesses) and
  Cloud Logging (50 GiB) stay within their free tiers.

### The table

| Piece | Nobody | Class demos | Office hours | Busy 24 × 7 |
| --- | --- | --- | --- | --- |
| **AWS:** CloudFront Free plan + WAF | $0 | $0 | $0 | $0 |
| Lambda | $0 | $0 | $0 | $0.33 (1.63 M requests over the free 1 M) |
| API Gateway HTTP API ($1.00 per million) | $0 | $0 | $0.05 | $2.63 |
| Aurora compute | $0 (paused) | $1.25 (20.8 h × 0.5 ACU) | $10.67 (178 h × 0.5 ACU) | $87.60 (730 h × 1 ACU) |
| Aurora storage and I/O | $0.10 | $0.10 | $0.10 | ~$0.60 |
| ECR, logs | ~$0.20 | ~$0.20 | ~$0.20 | ~$0.45 |
| **AWS total** | **≈ $0.30** | **≈ $1.55** | **≈ $11.00** | **≈ $91.60** |
| **Google Cloud:** Firebase Hosting | $0 | $0 | $0 | ~$1.40 (~315 MB a day over the free 360 MB, $0.15/GB) |
| Cloud Run | $0 | $0 | $0 | ~$2.25 (0.63 M requests, ~83,000 vCPU-s over the free tier) |
| Cloud SQL `db-f1-micro`, running all month | $7.67 | $7.67 | $7.67 | $7.67 |
| Cloud SQL storage, 10 GB SSD | $1.70 | $1.70 | $1.70 | $1.70 |
| Artifact Registry, Secret Manager, logs | $0 | $0 | $0 | $0 |
| **Google Cloud total, instance always on** | **≈ $9.40** | **≈ $9.40** | **≈ $9.40** | **≈ $13.00** |
| Google Cloud, instance stopped when unused (`make gcp-db-stop`) | ≈ $1.70 | ≈ $1.90 (20 h) | ≈ $3.55 (176 h) | — |

### Which cloud is cheaper, and which line decides

| Profile | Cheaper | Why |
| --- | --- | --- |
| Nobody | AWS ($0.30 against $9.40, or $1.70 with the instance stopped) | Aurora pauses to $0; Cloud SQL bills every hour it runs. |
| Class demos | AWS ($1.55 against $9.40); about even if you stop Cloud SQL after every demo ($1.90) | Aurora is awake 21 hours, Cloud SQL 730. |
| Office hours | Google Cloud ($9.40, or $3.55 stopped every evening, against $11.00) | 178 Aurora hours at $0.06 already cost more than 730 Cloud SQL hours at $0.0105. |
| Busy around the clock | Google Cloud (≈ $13 against ≈ $92) | An awake 1-ACU Aurora costs 11 times an f1-micro hour. The fair comparison is a bigger instance: `db-g1-small` (1.7 GB) at $0.035 is $25.55 a month, still far below Aurora. |

The deciding line is always **database compute**. Everything else (the CDN, the API, the registry,
the secrets, the logs) costs less than a dollar a month on both clouds until the traffic is constant.
Aurora is the cheapest database when it is mostly asleep and the most expensive one when it is awake;
Cloud SQL costs the same small amount every hour and only gets cheaper if someone stops it.

### Where the GCP side has the same trap

On AWS, Lab 4 showed that anything that keeps Aurora awake (a health check every few minutes, a warm
connection pool) turns $0 into $87 a month. On Google Cloud the database has no pause to lose: the
trap is a Cloud SQL instance that is simply **left running**, or forgotten when the project ends, at
$9.40 a month for nothing; a stopped one still bills $1.70 for its disk. The other trap is on the API
side: Cloud Run with **minimum instances ≥ 1 or instance-based billing** ("CPU always allocated")
bills an instance for every second of the month, about $40–50 for 1 vCPU and 512 MiB, instead of $0
when idle. `gcp/` keeps min 0 instances and request-based billing (`cpu_idle = true`) for that
reason, and `disk_autoresize = false`, because an auto-grown Cloud SQL disk can never shrink back.

## Part 4 — questions

**When is a specialised service like Document AI the better tool than a general model, and when is
it not?** Document AI wins when the input is a document image or a scanned PDF and the output is
fixed: text with its layout, form key–value pairs, tables, receipts and invoices. It is
deterministic, gives confidence and bounding boxes for every field, needs no prompt, and costs $1.50
per 1,000 OCR pages (the first 1,000 a month free), far less than an LLM call per page. It does not
fit Lab 6's job: an agenda arrives as free text in two languages, and the task is understanding it
(which line is a topic, who owns it, "45 хв" is a duration, "Пн" is next Monday). There is no fixed
form to extract, so a general model with a schema (tool call + validation) is the right tool. The two
combine well: OCR a photographed agenda with Document AI or Vision, then give the text to the LLM.

**Lab 8 on Google Cloud.** The raw payment files would still land in object storage (Cloud Storage
instead of S3). Athena and the Glue Data Catalog become **BigQuery**: either external tables over the
bucket or, better, native tables loaded once, queried with standard SQL and paid per byte scanned.
Glue ETL jobs (cleaning, joining, converting to Parquet) become **Dataflow** pipelines (Apache Beam,
the same code for batch and streaming), or plain SQL in BigQuery. QuickSight becomes **Looker
Studio**, free over BigQuery. What we would no longer manage: Glue crawlers and their schedules, the
catalog's schemas and partitions (partition projection), the Athena query-results bucket and
workgroups, and the conversion to columnar files, because BigQuery stores data in its own columnar
format and partitions it on a column.
