# Meetings App

Monorepo with a FastAPI backend (`back/`), a React + shadcn/ui frontend (`front/`) and PostgreSQL,
all started with Docker Compose. See meetings in a week or day calendar, add them (title, time,
description, participants, call link, place), edit and remove them.

## Run everything

```bash
make up          # = cp .env.example .env (first time) + docker compose up --build --wait, then
                 #   docker compose watch: rebuilds backend/frontend when their files change
make start       # the same without watching (returns once the stack is healthy)
make help        # all targets: logs, test, lint, format, clean, aws-*
```

- App: http://localhost:3000 (sign in / sign up; the calendar is at `/home`). Until Cognito is set
  up (see [Sign-in](#sign-in-cognito)), nothing is checked: any valid form opens the calendar and
  everyone is one local user.
- API docs: http://localhost:3000/api/docs (or http://localhost:8000/api/docs directly)

If a host port is already taken, change `DB_PORT`, `BACKEND_PORT` or `FRONTEND_PORT` in `.env`.
`SEED=true` inserts 5 sample participants on first start. `docker compose down -v` wipes the database.

## Layout

The repository structure, the purpose of every folder, the compose startup order, the API contract and
the pinned versions are specified in [PROJECT.md](PROJECT.md).

## API

| Method | Path | Description |
| --- | --- | --- |
| GET | `/api/health` | Liveness + DB check (no sign-in needed) |
| GET | `/api/me` | The signed-in user (created on first request) |
| PATCH | `/api/me` | Update the user's `name` |
| GET | `/api/meetings` | List meetings with participants, by start time |
| GET | `/api/meetings/{id}` | One meeting |
| POST | `/api/meetings` | Create a meeting |
| PUT | `/api/meetings/{id}` | Replace a meeting (same body as POST) |
| DELETE | `/api/meetings/{id}` | Delete a meeting |
| GET | `/api/participants?q=` | List/search participants |
| POST | `/api/participants` | Create a participant (409 on duplicate email) |
| DELETE | `/api/participants/{id}` | Delete a participant |

Everything except `/api/health` needs `Authorization: Bearer <Cognito ID token>`. Meetings are
personal: each belongs to the user who created it (`owner_id`), and other users get 404 for it. The
participant directory is shared. All IDs are UUIDs. A meeting needs a title, `starts_at` and `ends_at` (ISO 8601 with a timezone, end after start)
and at least one of `call_link` or `place`.

## Sign-in (Cognito)

The browser signs in with a Cognito user pool directly: email + password (sign-up sends a 6-digit
code to confirm the email) and, once configured, Google through the Cognito Hosted UI. The API
verifies the ID token's signature, issuer, audience and expiry against the pool's public keys and
stores the user in the `users` table (keyed by the token's `sub`), where extra profile data lives.

```bash
make aws-cognito-deploy   # user pool + app client + Hosted UI domain; prints the lines for .env
make aws-cognito-env      # print them again
```

Paste the printed `COGNITO_*` lines into `.env` and run `make up`. The local backend and frontend
then use the real user pool. With `COGNITO_USER_POOL_ID` empty, auth is off: no token is checked and
every request acts as one local user (`dev@localhost`).

**Google sign-in** (off until configured): create an OAuth client ID (type "Web application") in
Google Cloud with the redirect URI `https://<COGNITO_DOMAIN>/oauth2/idpresponse`, set
`GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` in `.env`, then run `make aws-cognito-deploy` again and
copy `COGNITO_GOOGLE_ENABLED=true` into `.env`. On AWS, run `make aws-frontend-publish` to rebuild the
frontend with it. Until then the Google button shows as "coming soon".

On AWS the Lambda has no internet access, so `aws-backend-stack` downloads the pool's JWKS and passes
it in as `COGNITO_JWKS`. Cognito does not rotate user pool signing keys. Meetings created before
accounts existed have no owner, so nobody sees them.

## Local development

Backend (needs a Postgres, e.g. `docker compose up -d db`):

```bash
cd back
uv sync
DATABASE_URL=postgresql+psycopg://meetings:meetings@localhost:5432/meetings uv run alembic upgrade head
DATABASE_URL=postgresql+psycopg://meetings:meetings@localhost:5432/meetings uv run uvicorn app.main:app --reload
```

Frontend (Vite proxies `/api` to `http://localhost:8000`, override with `VITE_API_PROXY`):

```bash
cd front
npm install
npm run dev        # http://localhost:5173
```

## Tests

```bash
# backend: uses a separate database (created once)
docker compose exec db psql -U meetings -c "CREATE DATABASE meetings_test"
cd back && TEST_DATABASE_URL=postgresql+psycopg://meetings:meetings@localhost:5432/meetings_test uv run pytest

# frontend
cd front && npm test
```

## Code style

CI (`.github/workflows/code-style.yml`) runs on pull requests and as the first stage of every deploy (push to `main`); it also runs the `infra/tests` suite (`make test-infra`):

| Part | Tools | Run locally | Auto-fix |
| --- | --- | --- | --- |
| `back/` | Ruff (lint + format) | `uv run ruff check . && uv run ruff format --check .` | `uv run ruff check --fix . && uv run ruff format .` |
| `front/` | ESLint, Prettier, `tsc` | `npm run lint && npm run format:check && npm run typecheck` | `npm run format` |

Config lives in `back/pyproject.toml` (`[tool.ruff]`), `front/eslint.config.js` and `front/.prettierrc.json`.

## Deploy to AWS (backend on Lambda + Aurora Serverless, frontend on CloudFront)

Everything is deployed to **us-east-1**. CloudFront accepts custom-domain certificates only from that region. Infrastructure is CloudFormation in `infra/`:

- `infra/cognito.yaml`: Cognito user pool (email + password, self sign-up with email code), public app client, Hosted UI domain, and Google as an identity provider when `GOOGLE_CLIENT_ID` is set.
- `infra/backend-ecr.yaml`: ECR repository for the backend's Lambda container image (`back/Dockerfile.lambda`).
- `infra/backend.yaml`: VPC with private subnets, Aurora Serverless v2 PostgreSQL (scales to 0 ACU when idle), and a Lambda function with a public **function URL** (the backend URL until a custom domain is set up, then a fallback).
- `infra/backend-domain.yaml`: API Gateway HTTP API on `api.<domain>` (regional ACM certificate, TLS 1.2, throttled) in front of the same Lambda.
- `infra/github-oidc.yaml`: GitHub OIDC provider and the deploy role CI assumes (see [CI/CD](#cicd-github-actions--oidc)).
- `infra/frontend.yaml`: private S3 bucket and CloudFront distribution for the SPA on the **flat-rate Free plan** ($0/month, with the WAF web ACL the plan requires), with an optional custom domain.

Every resource carries the tag `PROJECT_NAME=<project>`. It is set in the templates and as a stack tag, and `cert.sh` puts it on the ACM certificate. Some resource types can't be tagged in AWS at all: function URLs, Lambda permissions, the bucket policy, the CloudFront origin access control, Route 53 records and the pricing plan subscription.

```mermaid
flowchart LR
    B[Browser] -->|HTTPS| CF[CloudFront + WAF<br/>Free plan, optional custom domain]
    CF --> S3[(S3<br/>built SPA)]
    B -->|HTTPS api.&lt;domain&gt;, CORS| APIGW[API Gateway HTTP API<br/>custom domain]
    APIGW --> L[Lambda<br/>FastAPI via Mangum<br/>private subnets]
    URL[Lambda function URL<br/>fallback] -.-> L
    L -->|:5432| DB[(Aurora Serverless v2<br/>PostgreSQL, private subnets)]
    L -. image .-> ECR[ECR]
```

1. Sign in with an IAM user or role allowed to use CloudFormation, EC2/VPC, Lambda, ECR, RDS, Secrets Manager, S3, CloudFront, WAF, Pricing Plan Manager, ACM, Route 53, IAM and CloudWatch Logs. Prefer short-lived credentials: `aws sso login` (or `export AWS_PROFILE=...`). Credentials in the environment always win over `.env`; static keys in `.env` (`AWS_ACCESS_KEY_ID=...`, `AWS_SECRET_ACCESS_KEY=...`) still work for local use only and must never be committed. `make aws-check` shows who you are signed in as.

2. Optionally copy `infra/backend.params.example.env` to `infra/backend.params.env` to override stack parameters (memory, Aurora capacity, seeding, …).
3. Deploy. The first run takes about 15 minutes, mostly waiting for Aurora and CloudFront:

   ```bash
   make aws-deploy   # = aws-cognito-deploy, aws-backend-deploy, then aws-frontend-deploy
   ```

   The steps run in this order:

   1. **Cognito** (`make aws-cognito-deploy`): user pool, app client and Hosted UI domain. Redirect URLs cover localhost and the site's origins. It prints the `.env` lines for local use.
   2. **Backend** (`make aws-backend-deploy`): ECR stack → build and push the Lambda image → backend stack → `aws-backend-migrate` invokes the function with `{"action": "migrate"}` to run Alembic and seeding. It prints the function URL (`https://<id>.lambda-url.us-east-1.on.aws/`).
   3. **Frontend** (`make aws-frontend-deploy`): frontend stack → `npm run build` with `VITE_API_URL=<function URL>` and the `VITE_COGNITO_*` IDs → upload to S3 and invalidate CloudFront → allow the site's origin in the backend's `CORS_ORIGINS` and as a Cognito redirect URL. It prints the site URL.

Other targets: `make aws-backend-outputs`, `aws-backend-status`, `aws-backend-logs`, `aws-backend-health`, `aws-backend-migrate`, `aws-frontend-outputs`, `aws-frontend-publish` (rebuild and upload the frontend only), `aws-destroy`. Use `ARCH=amd64` to build an x86 Lambda instead of Graviton (`arm64`, the default). Use `CLOUDFRONT_PLAN=PAY_AS_YOU_GO` if the account can't subscribe to the Free plan (accounts on the AWS Free Tier are not eligible, and each account gets at most 3 free plans).

### Custom domains: `app.<domain>` and `api.<domain>` (optional)

By default the site is served on its `*.cloudfront.net` domain and the API on its function URL. Set
`DOMAIN=example.com` in `.env` (or on the command line): it gives `FRONTEND_DOMAIN=app.example.com` and
`BACKEND_DOMAIN=api.example.com` (each can be overridden). With `DOMAIN` empty the domain targets stop
before calling AWS. After the first `make aws-deploy`:

```bash
make aws-frontend-https       # certificate (us-east-1) → wait → attach to CloudFront → CORS/Cognito → DNS
make aws-frontend-https-check # curl https://app.example.com/
make aws-backend-https        # certificate → wait → API Gateway custom domain + DNS → rebuild the SPA against it
make aws-backend-https-check  # curl https://api.example.com/api/health
```

- **Domain's zone in Route 53 (same account):** the zone is found automatically. The validation `CNAME` records and the alias records (`app.` → CloudFront, `api.` → API Gateway) are created for you.
- **Any other DNS provider:** the `*-cert` step prints the validation `CNAME` to add there, and the `*-https` step prints the routing `CNAME`.

A certificate is issued only after its validation record resolves, so DNS propagation can take a few
minutes; `make aws-frontend-cert-status` / `aws-backend-cert-status` show where it is. Once attached,
later deploys keep the domains; `make aws-frontend-stack DETACH_DOMAIN=1` removes the frontend domain
on purpose, and `make aws-backend-domain-destroy` removes the API domain (the function URL keeps
working). API Gateway cuts requests at 30 s, which is also the Lambda timeout.

## CI/CD (GitHub Actions + OIDC)

The `Makefile` is the deploy contract, and CI runs the same targets you can run yourself:

- `make deploy-backend`: build the Lambda image, push it to ECR tagged with the **commit SHA** (never `latest`), point the Lambda at it and wait, run migrations.
- `make deploy-frontend`: build the SPA with `VITE_API_URL` = `https://api.<domain>/` (or the function URL), upload new assets, then `index.html`, then delete old files, and invalidate CloudFront.

Neither touches CloudFormation: infrastructure changes (`*-stack`, `*-https`) stay deliberate laptop
steps. `.github/workflows/deploy.yml` runs on every push to `main`: `code-style.yml` (lint + infra
tests) and the backend and frontend tests, then `deploy-backend` and `deploy-frontend` on an arm64
runner. Deploys run one at a time, in push order.

GitHub gets AWS access through **OIDC**, not stored keys. Each run receives a signed token naming the
repository and branch; AWS exchanges it for temporary credentials of the role in
`infra/github-oidc.yaml`, whose trust policy accepts only `repo:MasterDay3/OneTwoThree:ref:refs/heads/main`
and whose permissions cover only this project's ECR repository, Lambda function, S3 bucket,
CloudFront distribution and stack outputs. One-time setup, after the frontend stack exists:

```bash
make aws-oidc-deploy   # creates the role (reuses the account's GitHub OIDC provider if there is one)
gh variable set AWS_DEPLOY_ROLE_ARN --repo MasterDay3/OneTwoThree --body <printed role ARN>   # a variable, not a secret
```

Re-run `make aws-oidc-deploy` if the frontend stack is ever recreated (the bucket name changes).
Actions must be enabled on the fork. **Rollback:** `make aws-backend-rollback TAG=<earlier commit sha>`
points the Lambda at an image already in ECR (the repository keeps the last 5), without rebuilding or
migrating. Database migrations are not reverted, so roll back only across compatible schema changes.

**Cost.** There is no load balancer, NAT gateway or public IPv4 address. Lambda and function URLs fit in the always-free tier for a small app. CloudFront runs on the flat-rate Free plan, which costs $0 with no overage charges and also covers its WAF web ACL (a per-IP rate limit). Requests that WAF blocks don't count toward the plan's allowance. Aurora Serverless v2 has no free tier. With `DbMinCapacity=0` it pauses after 5 idle minutes, and then you pay only for storage (about $0.10/GB-month). While active it costs about $0.12 per ACU-hour. The first request after a pause waits about 15 seconds while Aurora resumes. The DB credentials secret costs $0.40/month. API Gateway HTTP APIs cost $1.00 per million requests; custom domains and ACM certificates are free. Run `make aws-destroy` when you are done: it deletes the frontend, the API domain, the backend and Cognito (each asks first) and keeps a final Aurora snapshot. The CI role is kept unless you add `DESTROY_OIDC=1`; the account's GitHub OIDC provider itself is always retained.

On AWS the backend reads `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER` and `DB_PASSWORD` instead of `DATABASE_URL`. The password is generated in Secrets Manager and resolved into the function's environment at deploy time, so the VPC needs no internet access. `DB_NULL_POOL=true` closes connections after each request, because idle connections from warm Lambdas would stop Aurora from pausing. Migrations do not run on cold start. `make aws-backend-migrate` runs them, and every `aws-backend-deploy` calls it.
