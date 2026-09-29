# Use Cases: Lab 2 — Documented Repository Contract, Custom-Domain HTTPS Deploy, and OIDC-Based CI/CD

> Based on [PRD](../PRD.md), section 1 ("Lab 2 — Documented Repository Contract, Custom-Domain HTTPS
> Deploy, and OIDC-Based CI/CD").
>
> Grounded in `README.md`, `Makefile`, `compose.yaml`, `infra/*.yaml`, `infra/scripts/cert.sh`, and
> `.github/workflows/code-style.yml` as they exist in the `MasterDay3/OneTwoThree` fork today. All new
> infrastructure and deploy behavior described here is additive to that baseline; no application
> endpoint, schema, or UI behavior changes.

---

## Actors

- **Student/deployer** — the repo owner, running `make` targets locally with SSO/temporary AWS
  credentials (never long-lived static keys for the new OIDC-gated resources).
- **CI workflow** — GitHub Actions running as `MasterDay3/OneTwoThree`, `main` branch only, assuming
  the deploy role via OIDC.
- **Lecturer/reviewer** — reads `PROJECT.md`, `docs/lab2-discussion.md`, and the Actions run history
  to grade the submission; never has write access and never triggers deploys themselves.
- **Attacker/other repo** — any workflow run that is not `repo:MasterDay3/OneTwoThree:ref:refs/heads/main`
  (a PR run, a branch other than `main`, a fork of the fork, or an entirely different repository)
  attempting to assume the deploy role.
- **End user of the app** — a browser visiting `https://app.<domain>` and calling
  `https://api.<domain>` to see/add meetings.

---

## UC-1: New developer brings up the local stack

**Actor**: Student/deployer (new clone of the fork)
**Preconditions**: Docker and Docker Compose are installed; the repo is freshly cloned; no `.env`
file exists yet.
**Trigger**: Developer runs `make up` (or `make start`) for the first time.

### Primary Flow (Happy Path)
1. `make up` depends on `start`, which depends on the `.env` target: since `.env` does not exist, it
   is created by `cp .env.example .env` and a message is printed telling the developer to review
   ports and AWS credentials.
2. `start` runs `docker compose up -d --build --wait`, which builds and starts `db`, `backend`,
   `frontend` in dependency order.
3. `db` (image `postgres:16-alpine`) starts; its healthcheck (`pg_isready -U $POSTGRES_USER -d
   $POSTGRES_DB`, 5s interval, up to 10 retries) must pass before dependents proceed.
4. `backend` (`build: ./back`) starts only once `db` is `service_healthy` (`depends_on: db:
   condition: service_healthy`). Its container command runs `alembic upgrade head`, then
   `python -m app.seed`, then starts `uvicorn app.main:app` on port 8000. Its own healthcheck (`GET
   /api/health` via `urllib.request`, 10s interval, up to 5 retries) must pass.
5. `frontend` starts only once `backend` is `service_healthy` (`depends_on: backend: condition:
   service_healthy`); it has no healthcheck of its own.
6. `docker compose up --wait` returns once all services report healthy (or the wait times out).
7. `make up` then runs `docker compose watch --no-up`, rebuilding `backend`/`frontend` images on file
   changes under `./back` / `./front` (frontend ignores `src/test/`) until the developer presses
   Ctrl+C; containers keep running after that.
8. Developer opens `http://localhost:3000` (or the `FRONTEND_PORT` override) and sees the sign-in
   page; API docs are reachable at `http://localhost:3000/api/docs`.

**Postconditions**: All three containers are running and healthy; the `meetings` database has the
current Alembic head schema applied; with `COGNITO_USER_POOL_ID` empty, auth is disabled and every
request acts as the single local user `dev@localhost`; if `SEED=true`, 5 sample participants exist.

### Alternative Flows
- **UC-1-A1: `make start` instead of `make up`** — developer wants the stack up without file-watching
  (e.g. for scripted/CI-like local runs). Steps 1-6 are identical; step 7-8 are skipped (no `docker
  compose watch`); `make start` returns as soon as `--wait` reports healthy.
- **UC-1-A2: `.env` already exists** — the `.env` file target is a Makefile file-based prerequisite;
  if `.env` already exists, `cp .env.example .env` is skipped entirely and the existing file's values
  (including any custom `DB_PORT`/`BACKEND_PORT`/`FRONTEND_PORT` or `SEED=true`) are used as-is.
- **UC-1-A3: `SEED=true` on first start** — `python -m app.seed` inserts 5 sample participants; this
  is asserted idempotent by the app itself (the use case does not require Lab 2 to add idempotency,
  only to document the existing behavior in `PROJECT.md`).

### Error Flows
- **UC-1-E1: host port already taken** — `docker compose up` fails to bind e.g. `3000:80` for
  `frontend` because another process holds it. Developer must set `FRONTEND_PORT` (or `DB_PORT`/
  `BACKEND_PORT`) in `.env` and re-run `make up`. Documented in `README.md` and `PROJECT.md`; no code
  change required.
- **UC-1-E2: `db` never becomes healthy** — e.g. corrupted `pgdata` volume. `backend` never starts
  (its `depends_on: service_healthy` condition blocks it); `docker compose up --wait` eventually times
  out and `make up`/`make start` exits non-zero. Recovery: `make clean` (removes the `pgdata` volume)
  then `make up`.
- **UC-1-E3: `backend` healthcheck never passes** — e.g. a migration fails on startup (bad
  `DATABASE_URL`, or a broken Alembic revision). The container's healthcheck (`GET /api/health`) never
  succeeds, `frontend` never starts (blocked by `depends_on: service_healthy`), and `docker compose up
  --wait` times out. Recovery: `make logs SERVICE=backend` to see the Alembic/uvicorn error.

### Edge Cases
- **UC-1-X1**: Running `make up` twice in a row (stack already healthy) — `docker compose up -d
  --build --wait` is idempotent: no duplicate containers, only rebuilds images if source changed.
- **UC-1-X2**: `docker compose down -v` (or `make clean`) wipes the `pgdata` volume; the next `make
  up` starts from a fresh empty database and reapplies all Alembic migrations from scratch.

### Data Requirements
- **Input**: `.env` (copied from `.env.example`), source in `back/` and `front/`.
- **Output**: three running containers; `meetings` Postgres database at the current Alembic head.
- **Side Effects**: creates/reuses the `pgdata` Docker volume; no AWS calls.

---

## UC-2: Add a meeting locally and confirm it persists across reload

**Actor**: Student/deployer (or any local user, since auth is disabled without Cognito config)
**Preconditions**: Stack from UC-1 is up and healthy; browser open at `http://localhost:3000/home`.
**Trigger**: User fills the meeting form (title, `starts_at`/`ends_at`, and at least one of
`call_link`/`place`) and submits.

### Primary Flow (Happy Path)
1. Frontend sends `POST /api/meetings` with the form body (relative path, same-origin via the
   frontend's nginx `/api` proxy locally).
2. Backend validates the payload (title required; `ends_at` after `starts_at`; at least one of
   `call_link`/`place`), persists it owned by the current user (`dev@localhost` when auth is off), and
   responds `201` with the created meeting.
3. The calendar view (TanStack Query) refetches `GET /api/meetings` and renders the new meeting in the
   week/day view.
4. User reloads the page (`F5`/hard refresh).
5. `GET /api/meetings` is called again on mount; the previously created meeting is returned from
   Postgres and rendered identically — confirming the meeting was durably persisted, not just held in
   client state.

**Postconditions**: A row exists in the `meetings` table (and any `meeting_participants` rows for
selected participants) that survives a full page reload and a container restart (as long as the
`pgdata` volume is not removed).

### Alternative Flows
- **UC-2-A1: meeting with `call_link` only, or `place` only** — both are individually sufficient;
  either combination passes validation.

### Error Flows
- **UC-2-E1: missing both `call_link` and `place`** — `POST /api/meetings` returns `422`; the form
  shows a validation error and no meeting is created.
- **UC-2-E2: `ends_at` not after `starts_at`** — `422`; no meeting is created.
- **UC-2-E3: backend restarts mid-session (e.g. `make restart`)** — the meeting created before restart
  is still present after reload once the backend is healthy again, because it was persisted to the
  `pgdata` volume, not held in backend memory.

### Edge Cases
- **UC-2-X1**: reload immediately after submit, before the create request's optimistic UI update
  would have mattered — the reload forces a fresh `GET`, so it is a strict test of server-side
  persistence, not client cache.

### Data Requirements
- **Input**: meeting form fields.
- **Output**: `201` with the created meeting; subsequent `GET /api/meetings` includes it.
- **Side Effects**: new row in `meetings` (and join rows in `meeting_participants` if participants are
  attached).

---

## UC-3: Reader/agent uses `PROJECT.md` to understand the repository contract

**Actor**: Lecturer/reviewer, or an LLM coding agent operating on the repo with no other context.
**Preconditions**: `PROJECT.md` exists at repo root (FR-1–FR-5).
**Trigger**: Reader opens `PROJECT.md` to answer "what does `GET/POST /api/meetings` require and
return, and how is the repo laid out" without reading application source first.

### Primary Flow (Happy Path)
1. Reader finds, for every top-level folder (`back/`, `front/`, `infra/`, `.github/`, `docs/`) and the
   README-called-out subfolders (`back/app/routers`, `back/app/services`, `back/alembic`,
   `front/src/pages`, `front/src/components`, `front/src/lib`), a stated purpose matching the actual
   tree.
2. Reader finds each `compose.yaml` service documented: image/build context, published port,
   `depends_on` target + condition, and what its healthcheck actually tests (matching UC-1 step 3-5
   exactly: `pg_isready` for `db`; `GET /api/health` via `urllib.request` for `backend`; no healthcheck
   of its own for `frontend`).
3. Reader finds the full field-level contract for `GET /api/meetings` and `POST /api/meetings`:
   request/response field names and types, ISO 8601-with-timezone format for `starts_at`/`ends_at`,
   required fields (title, `starts_at`, `ends_at` with end after start, at least one of
   `call_link`/`place`), the `Authorization: Bearer <Cognito ID token>` header, and status codes (200
   GET, 201 POST success, 401 missing/invalid token, 422 validation failure) — each value cross-checked
   against `back/app/routers/` and `back/app/schemas.py`, not assumed from the README table alone.
4. Reader finds pinned versions copied verbatim from `back/pyproject.toml`, `front/package.json`, and
   `compose.yaml` (Postgres `postgres:16-alpine`, Python `>=3.12`/CI `3.12`, Node CI `24`, and the
   listed library version constraints for fastapi/sqlalchemy/alembic/mangum/pyjwt/psycopg and
   react/vite/typescript/tailwindcss/@tanstack/react-query).
5. Reader finds the written monorepo trade-off discussion: atomic cross-cutting changes in one commit,
   the "repository is the context window" argument, and the explicit cost (losing independent
   deploy/ownership boundaries a repo-per-service split would give).
6. Using only `PROJECT.md`, the reader/agent can correctly predict that an unauthenticated `POST
   /api/meetings` returns `401`, and that a `POST` missing both `call_link` and `place` returns `422`,
   without opening `back/app/routers/meetings.py`.

**Postconditions**: The reader forms an accurate mental model of the repo's structure and API contract
sourced entirely from `PROJECT.md`.

### Alternative Flows
- **UC-3-A1: reviewer cross-checks a specific claim** — reviewer opens `back/app/schemas.py` to spot
  check one field type documented in `PROJECT.md` and finds it matches exactly (this is the acceptance
  mechanism for FR-3/NFR-7, not a separate document).

### Error Flows
- **UC-3-E1: `PROJECT.md` states a status code or field name that does not match
  `back/app/routers/`/`back/app/schemas.py`** — this is a documentation defect (violates NFR-7); the
  fix is to correct `PROJECT.md`, not the code. A reviewer catching this treats it as a failed
  acceptance criterion (#1 in PRD 1.5).
- **UC-3-E2: `PROJECT.md` documents an invented/aspirational version number not present in
  `back/pyproject.toml` or `front/package.json`** — violates NFR-7 ("any figure that cannot be
  verified ... MUST be omitted or flagged rather than guessed"); flagged as a documentation defect.

### Edge Cases
- **UC-3-X1**: a library listed in `back/pyproject.toml` with a version range (e.g. `>=x,<y`) is
  copied as the exact constraint string, not collapsed to a single invented pinned number.

### Data Requirements
- **Input**: `back/pyproject.toml`, `front/package.json`, `compose.yaml`, `back/app/routers/`,
  `back/app/schemas.py`.
- **Output**: `PROJECT.md` content only (no runtime effect).
- **Side Effects**: none (documentation only).

---

## UC-4: One-time AWS bootstrap of the OIDC identity provider and deploy role

**Actor**: Student/deployer, running locally with sufficiently privileged AWS credentials (IAM,
CloudFormation) — a one-time, manual, pre-CI step.
**Preconditions**: An AWS account is available in `us-east-1`; the student has credentials able to
create IAM resources and CloudFormation stacks.
**Trigger**: Student runs the new bootstrap target (`make aws-oidc-deploy`, backed by
`infra/github-oidc.yaml`) before the first CI-driven deploy can succeed. Preconditions include that the
frontend stack (UC-5) already exists, since the deploy role's policy is scoped to that stack's bucket
name and distribution ID as parameters.

### Primary Flow (Happy Path)
1. The template creates an `AWS::IAM::OIDCProvider` for `token.actions.githubusercontent.com` with
   audience `sts.amazonaws.com` (see UC-4-A1 for the already-exists case).
2. The template creates a deploy IAM role whose trust policy restricts
   `token.actions.githubusercontent.com:sub` via `StringEquals` to exactly
   `repo:MasterDay3/OneTwoThree:ref:refs/heads/main`, and `token.actions.githubusercontent.com:aud` via
   `StringEquals` to `sts.amazonaws.com`.
3. The template attaches the least-privilege permission policy itemized in PRD FR-18 (scoped by ARN to
   the single backend ECR repository, the single backend Lambda function, the single frontend S3
   bucket, the single CloudFront distribution, and the project's CloudFormation stacks; no `iam:*`, no
   unscoped `*` resource for ARN-scopable actions) — see FR-18 for the authoritative action list rather
   than restating it here.
4. `aws cloudformation deploy` is run with `--capabilities CAPABILITY_NAMED_IAM` (the template names the
   IAM role). The stack deploy succeeds; the role's ARN is captured as an output.
5. `make aws-oidc-deploy` stores the role's ARN as the **repository variable**
   `vars.AWS_DEPLOY_ROLE_ARN` (via `gh variable set` or the student setting it manually in the repo's
   Settings → Secrets and variables → Actions → Variables) — **not** a GitHub Actions secret, since the
   role ARN is not sensitive (only the trust policy's `sub`/`aud` conditions gate who can use it). The
   `deploy.yml` workflow's `role-to-assume` reads `${{ vars.AWS_DEPLOY_ROLE_ARN }}`. Because the deploy
   role's policy is parameterized on the frontend stack's live bucket name and distribution ID,
   `aws-oidc-deploy` must be re-run any time the frontend stack is deleted and recreated (a new bucket/
   distribution gets new physical IDs the existing role policy would no longer match).

**Postconditions**: A deploy role exists that only `repo:MasterDay3/OneTwoThree:ref:refs/heads/main`
can assume; its ARN is available to CI as `vars.AWS_DEPLOY_ROLE_ARN`; no long-lived AWS keys are stored
anywhere for this path.

### Alternative Flows
- **UC-4-A1: OIDC provider already exists in the account** (e.g. from a previous lab or another
  project using GitHub Actions with the same account). The decision is: `make aws-oidc-deploy` first
  runs `aws iam list-open-id-connect-providers`, and if a provider for
  `token.actions.githubusercontent.com` is already present, its ARN is auto-filled into the template's
  `ExistingOidcProviderArn` parameter and the template skips creating a new `AWS::IAM::OIDCProvider`
  resource for that invocation. This is a plain lookup-and-parameterize, not a CloudFormation import and
  not a catch of an "already exists" API error — the template never attempts to create a duplicate
  provider in the first place when `ExistingOidcProviderArn` is non-empty. Re-running the stack deploy
  after the provider already exists succeeds with no duplicate provider and no error (NFR-4
  idempotency).
- **UC-4-A2: re-running `aws-oidc-deploy` with no changes** — `--no-fail-on-empty-changeset` semantics
  (consistent with every other `aws-*-stack` target) mean a second run against an unchanged template
  succeeds with an empty changeset, not an error.

### Error Flows
- **UC-4-E1: insufficient IAM privileges** — the bootstrapping credentials cannot create
  `AWS::IAM::Role`/`AWS::IAM::OIDCProvider`; `aws cloudformation deploy` fails with an
  `AccessDenied`/`InsufficientCapabilitiesException` (missing `--capabilities CAPABILITY_IAM` or
  `CAPABILITY_NAMED_IAM`). Recovery: use an account/role with `iam:CreateRole`,
  `iam:CreateOpenIDConnectProvider`, `iam:PutRolePolicy` permissions, and pass the required
  `--capabilities` flag.
- **UC-4-E2: stack left in `ROLLBACK_COMPLETE`** from a first failed create (e.g. a malformed trust
  policy JSON) — mirrors the existing `clear_failed_stack` pattern in the `Makefile`: the failed stack
  is deleted before the next deploy attempt, exactly like `aws-backend-ecr`/`aws-backend-stack`/
  `aws-frontend-stack`/`aws-cognito-stack` already do.

### Edge Cases
- **UC-4-X1**: two students in the same course each bootstrapping their own fork into their own
  separate AWS accounts — no collision, since the OIDC provider and role live per-account and the
  trust policy's `sub` condition is scoped to each student's own `MasterDay3/OneTwoThree` fork slug
  (this is this student's fork; a different student's fork name would need its own trust condition,
  out of scope here).

### Data Requirements
- **Input**: `infra/github-oidc.yaml`, bootstrapping AWS credentials (one-time, human-supplied,
  never CI).
- **Output**: deploy role ARN.
- **Side Effects**: creates (or reuses) an IAM OIDC provider and creates an IAM role + inline/managed
  policy in the account.

---

## UC-5: One-time AWS bootstrap of ECR, backend stack, frontend stack, and certificates

**Actor**: Student/deployer, local credentials (may still be the student's own SSO/temporary
credentials, not necessarily the OIDC role, since this happens before or alongside UC-4).
**Preconditions**: AWS account ready in `us-east-1`; Cognito not yet required for ECR bootstrap but
required before the backend stack can complete (`aws-backend-stack` checks `COGNITO_POOL_ID` is set).
**Trigger**: Student runs `make aws-cognito-deploy`, then `make aws-backend-deploy`, then
`make aws-frontend-deploy` (or the aggregate `make aws-deploy`) for the very first time on a brand-new
account, following existing README ordering (Cognito → backend → frontend).

### Primary Flow (Happy Path)
1. `make aws-cognito-deploy` creates the Cognito user pool, app client, Hosted UI domain; prints
   `COGNITO_*` lines for `.env`.
2. `make aws-backend-deploy` runs `aws-check` (credentials present), `aws-backend-ecr` (ECR repo
   stack), `aws-backend-push` (build+push a Lambda image tagged with the current `TAG`),
   `aws-backend-stack` (VPC, Aurora Serverless v2, Lambda + function URL — requires
   `COGNITO_POOL_ID`/`COGNITO_CLIENT` already present), `aws-backend-migrate` (invokes
   `{"action": "migrate"}` to run Alembic + seeding). Prints the function URL and API docs URL.
3. Student requests certificates for `api.<domain>` and `app.<domain>` (see UC-6) before or after this
   step; either order is acceptable since the frontend/backend stacks accept an empty
   `CertificateArn`/`DomainName` until the cert is issued.
4. `make aws-frontend-deploy` runs `aws-check`, `aws-frontend-stack` (S3 + CloudFront + WAF on the
   chosen `CLOUDFRONT_PLAN`, custom domain args if the cert is already issued), `aws-frontend-publish`
   (build the SPA with `VITE_API_URL` and Cognito args, upload to S3, invalidate CloudFront),
   `aws-frontend-cors` (re-runs `aws-backend-stack KEEP_IMAGE=1` and `aws-cognito-stack` so the site's
   origin is allowed in CORS and as a Cognito redirect URL). Prints the site URL.
5. The whole first-time bootstrap takes roughly 15 minutes, dominated by Aurora and CloudFront
   provisioning (per README).

**Postconditions**: ECR repository, VPC + Aurora Serverless v2 cluster + Lambda function + function
URL, S3 bucket + CloudFront distribution, and Cognito user pool all exist and are wired together; the
app is reachable on its CloudFront/function-URL domains (custom domains layered in by UC-6/UC-7).

### Alternative Flows
- **UC-5-A1: `make aws-deploy` (aggregate target)** — runs `aws-cognito-deploy`, `aws-backend-deploy`,
  `aws-frontend-deploy` in one invocation; functionally identical to running them one at a time in
  order.
- **UC-5-A2: re-running any `aws-*-stack` deploy with no template/parameter changes** — succeeds with
  an empty changeset (`--no-fail-on-empty-changeset`), matching NFR-4.

### Error Flows
- **UC-5-E1: `aws-backend-stack` run before Cognito exists** — the target's own guard
  (`test -n "$(COGNITO_POOL_ID)"`) fails fast with "Cognito not deployed: run `make
  aws-cognito-deploy` first" and exits non-zero before touching CloudFormation.
- **UC-5-E2: `aws-frontend-publish` run before the backend stack exists** — the target's guard
  (`test -n "$$api"`) fails fast with "Backend not deployed: run `make aws-backend-deploy` first".
- **UC-5-E3: a stack's first create fails and is left in `ROLLBACK_COMPLETE`** — the next deploy
  attempt at any of `aws-backend-ecr`/`aws-backend-stack`/`aws-frontend-stack`/`aws-cognito-stack`
  detects the status via `clear_failed_stack` and deletes it before recreating.
- **UC-5-E4: account not eligible for the CloudFront Free plan** (already has 3 free plans, or is on
  the AWS Free Tier) — `aws-frontend-stack` with default `CLOUDFRONT_PLAN=FREE` fails at the pricing
  plan subscription step; student re-runs with `CLOUDFRONT_PLAN=PAY_AS_YOU_GO`.

### Edge Cases
- **UC-5-X1**: `ARCH=amd64` override builds an x86_64 Lambda image instead of the default
  Graviton/`arm64`; both are valid, functionally equivalent images tagged and pushed the same way.
- **UC-5-X2**: re-running `aws-backend-stack` with `KEEP_IMAGE=1` (as `aws-frontend-cors` does)
  updates only parameters (e.g. `CorsOrigins`); it resolves the `ImageUri` parameter by reading the
  Lambda function's live, currently-deployed image URI via `aws lambda get-function` (not a
  Makefile-computed `$(ECR_URI):$(TAG)` value), so a stack update never re-points the function at a
  stale or default image URI baked into the template/parameters — it always re-applies whatever image
  is actually running, including one placed there by `aws-backend-rollback` (UC-11) or `deploy-backend`
  (UC-8/UC-9) outside of any stack update.
- **UC-5-X3**: running `aws-frontend-stack` (whether via `aws-frontend-deploy`, `aws-frontend-cors`, or
  directly) with `DOMAIN`/`FRONTEND_DOMAIN` unset **must not detach an already-attached custom
  domain**. If a student re-runs a stack-touching target later (e.g. `aws-frontend-cors` just to
  refresh CORS after a Cognito change) without passing `DOMAIN` again, the target must not fall back to
  empty `CertificateArn=`/`DomainName=`/`HostedZoneId=` overrides that would strip the domain the
  frontend stack already has attached from a previous run — an empty `DOMAIN`/`FRONTEND_DOMAIN` on a
  given invocation must leave the stack's existing custom-domain parameters exactly as they were, not
  cleared to empty by default.

### Data Requirements
- **Input**: `.env` AWS credentials (student's own, for this one-time bootstrap), `infra/*.yaml`
  templates, `back/Dockerfile.lambda`, `front/` build output.
- **Output**: stack outputs (`ApiUrl`, `ApiDocsUrl`, `SiteUrl`, `BucketName`, `DistributionId`,
  Cognito pool/client/domain IDs).
- **Side Effects**: creates billable AWS resources (Aurora, CloudFront, Lambda, S3, ECR, Cognito,
  Secrets Manager secret for the DB password).

---

## UC-6: Request and validate the ACM certificate for `api.<domain>` and `app.<domain>`

**Actor**: Student/deployer.
**Preconditions**: Route 53 public hosted zone for `<domain>` already exists in the same AWS account
(per the PRD's stated decision); AWS credentials configured; `us-east-1` region (ACM certs for both
CloudFront and the regional API Gateway custom domain must be requested from `us-east-1` per this
repo's existing convention, mirroring `cert.sh`'s hard-coded `AWS_REGION=us-east-1`).
**Trigger**: Student sets `DOMAIN=<domain>` (empty by default) and runs `make aws-frontend-cert`
(existing target; `FRONTEND_DOMAIN` is derived as `app.$(DOMAIN)`) and the new backend equivalent,
e.g. `make aws-backend-cert` (`BACKEND_DOMAIN` derived as `api.$(DOMAIN)`; extending `cert.sh`'s
existing `request`/`status`/`wait`/`arn`/`zone-id` subcommands to the backend domain). `FRONTEND_DOMAIN`/
`BACKEND_DOMAIN` MAY still be overridden individually, but the single `DOMAIN` variable is the normal
entry point so both subdomains stay in sync.

### Primary Flow (Happy Path)
1. `cert.sh request <domain>` checks for an existing `ISSUED`/`PENDING_VALIDATION` certificate for
   that exact domain name; none exists, so it calls `aws acm request-certificate --validation-method
   DNS` with an idempotency token derived from the domain name, tagged `PROJECT_NAME=<project>`.
2. The script polls (`describe-certificate`, up to 20 tries at 3s each) until ACM publishes the DNS
   validation `CNAME` record (name + value).
3. `zone_id()` walks up the domain labels (`api.example.com` → `example.com`) via
   `route53 list-hosted-zones-by-name`, finds the public zone for `<domain>` in the same account, and
   returns its zone ID.
4. Since a zone was found, the script UPSERTs the validation CNAME into that Route 53 zone directly (no
   manual DNS step for the student) and prints "Validation record added to Route 53 zone <id>; ACM
   usually validates within minutes."
5. Student runs the `wait` subcommand (or the composite `aws-backend-https`/`aws-frontend-https`
   target), which blocks on `aws acm wait certificate-validated` until ACM transitions the certificate
   from `PENDING_VALIDATION` to `ISSUED` (typically within minutes of the CNAME propagating, per DNS
   TTL of 300s set on the UPSERT).
6. Once issued, the certificate ARN is available via `cert.sh arn <domain>` for use as the
   `CertificateArn` parameter of `infra/frontend.yaml` (for `app.<domain>`) or the new backend
   custom-domain template/extension (for `api.<domain>`).

**Postconditions**: An `ISSUED` ACM certificate exists in `us-east-1` for each of `api.<domain>` and
`app.<domain>`, each tagged `PROJECT_NAME=<project>`, ready to attach to API Gateway/CloudFront
respectively.

### Alternative Flows
- **UC-6-A1: certificate already exists (reuse)** — `cert_arn ISSUED PENDING_VALIDATION` finds a
  prior certificate for the same exact domain name; the script skips `request-certificate` and instead
  re-tags the existing certificate (`add-tags-to-certificate`) and reports "Reusing certificate for
  <domain>". If already `ISSUED`, the script exits immediately with `Status: ISSUED` and performs no
  further DNS action — this is the re-run/idempotent path for both first-time and subsequent deploys
  (NFR-4).
- **UC-6-A2: propagation delay** — the validation CNAME is added to Route 53, but ACM has not yet
  observed it (public DNS/resolver caching). `cert.sh wait` continues blocking (student may Ctrl+C
  safely per the script's own message and re-run later); status stays `PENDING_VALIDATION` until ACM's
  own polling picks it up — this is expected, not an error, and the script explicitly says "Ctrl+C is
  safe" because the certificate request itself is not lost.

### Error Flows
- **UC-6-E1: DNS zone not found in Route 53 for `<domain>`** — `zone_id()` returns empty after walking
  all label levels. `cert.sh request` falls back to printing the manual validation record
  ("No Route 53 zone for <domain> in this account" + the CNAME name/value to add at an external DNS
  provider) instead of auto-inserting it. Since the PRD's stated decision is that the zone always
  exists in the same account, this flow documents the existing `cert.sh` fallback behavior for
  completeness but is not the expected path for this student's setup.
- **UC-6-E2: `validation_record` times out** (ACM has not published the DNS record after 20×3s = 60s)
  — the script prints "Timed out waiting for ACM to publish the validation record" to stderr and exits
  non-zero; student re-runs `cert.sh status <domain>` a bit later.
- **UC-6-E3: requesting a certificate for a domain already covered by a certificate in a different,
  non-`ISSUED`/`PENDING_VALIDATION` state** (e.g. `EXPIRED`/`REVOKED`) — `cert_arn` does not match
  those statuses, so a brand-new certificate request is made rather than reusing the dead one; the
  student ends up with two certificates for the same domain in ACM, only the new one usable — a known,
  acceptable quirk of the existing script's status filter, not a new defect introduced by this
  feature.

### Edge Cases
- **UC-6-X1**: `api.<domain>` and `app.<domain>` are sibling third-level labels under the same
  registered domain (e.g. both under `example.com`) — `zone_id()` finds the same `example.com` zone
  for both, so both validation CNAMEs land in the same Route 53 hosted zone without conflict (distinct
  record names).
- **UC-6-X2**: re-running `aws-backend-cert`/`aws-frontend-cert` after the certificate is already
  `ISSUED` is a no-op status check, not a re-request — safe to run on every deploy (as
  `aws-frontend-https` already does by depending on `aws-frontend-cert`).

### Data Requirements
- **Input**: `<domain>` (student-supplied, e.g. via `DOMAIN`/`BACKEND_DOMAIN`/`FRONTEND_DOMAIN` make
  variables), Route 53 hosted zone for that domain.
- **Output**: ACM certificate ARN, `ISSUED` status.
- **Side Effects**: creates an ACM certificate resource in `us-east-1`; UPSERTs a Route 53 CNAME
  record in the student's zone (auto path) or requires a manual DNS change (fallback path).

---

## UC-7: Routing — `api.<domain>` to API Gateway, `app.<domain>` to CloudFront, HTTPS-only behavior

**Actor**: Student/deployer (sets up routing); end user of the app (consumes it).
**Preconditions**: Certificates from UC-6 are `ISSUED`; backend and frontend stacks from UC-5 exist.
**Trigger**: Student runs the composite target that attaches the domain once its cert is issued (e.g.
`make aws-backend-https BACKEND_DOMAIN=api.<domain>` mirroring the existing
`aws-frontend-https FRONTEND_DOMAIN=app.<domain>`).

### Primary Flow (Happy Path)
1. **Frontend**: `aws-frontend-stack` is re-run with `CertificateArn`/`DomainName`/`HostedZoneId` now
   populated (`FRONTEND_DOMAIN_ARGS`); `infra/frontend.yaml`'s `UseCustomDomain`/`CreateDnsRecords`
   conditions attach the certificate to the CloudFront distribution and create Route 53 alias `A`/`AAAA`
   records for `app.<domain>` pointing at the distribution's fixed CloudFront hosted zone
   (`Z2FDTNDATAQYW2`).
2. **Backend**: a new, separate CloudFormation stack (`infra/backend-domain.yaml`, deployed manually
   from a laptop — never by CI, see UC-9) creates an API Gateway HTTP API with
   `DisableExecuteApiEndpoint: true` (so the API is reachable only through the custom domain, never the
   default `https://<api-id>.execute-api...` URL) and a Lambda proxy integration to the existing
   `BackendFunction` wired through a single `$default` stage/route (no per-method route configuration
   needed, matching FastAPI's own internal routing). The stack also creates a `REGIONAL`-endpoint-type
   API Gateway custom domain enforcing a minimum TLS version of `TLS_1_2`, using the `api.<domain>`
   regional ACM certificate from UC-6, and a Route 53 alias `A` record `api.<domain>` → the API Gateway
   custom domain's regional domain name (analogous "zone found automatically" behavior to the
   frontend's, per FR-8).
3. `curl -fsS https://api.<domain>/api/health` returns `200` (acceptance criterion #3).
4. Visiting `https://app.<domain>/` loads the SPA over valid HTTPS; the SPA (built with
   `VITE_API_URL=https://api.<domain>`) calls the backend at `https://api.<domain>` over valid HTTPS
   and successfully lists meetings for a signed-in user (acceptance criterion #2).
5. The existing Lambda function URL remains reachable in parallel as a fallback/debug entry point
   (unchanged, not replaced).

**Postconditions**: Both `app.<domain>` and `api.<domain>` resolve via Route 53 aliases, serve valid
HTTPS certificates, and route to the correct backing service (CloudFront→S3 SPA;
API Gateway→Lambda→FastAPI→Aurora).

### Alternative Flows
- **UC-7-A1: plain HTTP request to either domain** — CloudFront redirects/denies HTTP per its default
  viewer protocol policy (redirect-to-HTTPS or HTTPS-only, consistent with the existing
  `infra/frontend.yaml` distribution config); the API Gateway custom domain, being HTTPS-only by
  construction (no HTTP listener/stage configured), simply has no plaintext endpoint to connect to —
  `curl http://api.<domain>/api/health` fails to connect or is redirected, never returns a plaintext
  `200`.
- **UC-7-A2: fallback via the raw Lambda function URL** — `curl` against
  `https://<id>.lambda-url.us-east-1.on.aws/api/health` continues to work independently of the custom
  domain, useful for debugging if the API Gateway front door has an issue.

### Error Flows
- **UC-7-E1: Route 53 alias record not yet propagated** — `app.<domain>`/`api.<domain>` briefly
  resolve via stale/cached DNS (or NXDOMAIN) right after the stack update; `make
  aws-frontend-https-check` (and its backend analog) retries/reports the current `dig +short` result
  and HTTP status so the student can tell propagation is still in progress versus a real failure.
- **UC-7-E2: API Gateway custom domain mapped to the wrong/unissued certificate** — the domain fails
  TLS handshake (certificate mismatch) even though DNS resolves; student re-checks the certificate ARN
  passed to the API Gateway custom domain resource matches the `ISSUED` ARN from UC-6.
- **UC-7-E3: CORS not yet updated for the new backend origin** — `app.<domain>` calls
  `https://api.<domain>/api/meetings` and the browser blocks the response because
  `infra/backend.yaml`'s `CorsOrigins` parameter does not yet include `https://app.<domain>`; see UC-13
  for the dedicated CORS use case. Resolved by re-running the CORS-refresh step (`aws-backend-stack
  KEEP_IMAGE=1` with the updated `CorsOrigins` value) before or as part of `aws-frontend-cors`.

### Edge Cases
- **UC-7-X1**: `api.<domain>` and `app.<domain>` are deployed in different orders (backend custom
  domain before frontend, or vice versa) — each is independent infrastructure; neither blocks the
  other's DNS/cert setup, though the frontend build needs `api.<domain>` to already resolve with a
  valid cert for `VITE_API_URL` to be meaningful at build time (see UC-9 ordering).
- **UC-7-X2**: the API Gateway HTTP API integration timeout is a fixed platform maximum of 30 seconds
  that cannot be raised — it is a hard ceiling, not merely "as large as the Lambda's `Timeout`". The
  Lambda function's own `Timeout` (default `30`s in `infra/backend.yaml`) already matches that ceiling,
  which is why the Aurora cold-start path (UC-14) fits today. If a student were ever to raise the
  Lambda's `Timeout` parameter above `30`s (e.g. to tolerate a slower-than-usual Aurora resume), API
  Gateway would still cut the request off at 30 seconds regardless — the Lambda-side timeout increase
  would have no effect on requests routed through `api.<domain>` (only on requests via the raw Lambda
  function URL, which has no such 30s cap).

### Data Requirements
- **Input**: issued certificates (UC-6), existing backend/frontend stack outputs.
- **Output**: `app.<domain>`/`api.<domain>` DNS records and live HTTPS endpoints.
- **Side Effects**: creates the `infra/backend-domain.yaml` stack (API Gateway HTTP API with
  `DisableExecuteApiEndpoint: true`, `$default` stage, `REGIONAL` custom domain at `TLS_1_2`, Route 53
  alias `A` record); updates the CloudFront distribution's alternate domain names and certificate via
  `infra/frontend.yaml`.

---

## UC-8: Local manual `make deploy-backend` / `make deploy-frontend` with SSO/temporary credentials

**Actor**: Student/deployer, running locally with AWS SSO or otherwise temporary/short-lived
credentials (no static `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` in `.env` for this path).
**Preconditions**: `aws sso login` (or equivalent) has produced a valid, non-expired session in the
current shell/profile; the repo is on a clean, committed working tree; the OIDC-role's underlying
stacks already exist (UC-4/UC-5).
**Trigger**: Student runs `make deploy-backend` and/or `make deploy-frontend` directly from a terminal
to ship a new commit's code without touching any CloudFormation stack. `deploy-backend` is its own
target chain — `aws-check` → `aws-backend-push` (build + push the Lambda image tagged with the commit
SHA) → the new `aws-backend-update-code` (`aws lambda update-function-code --image-uri
<ECR_URI>:<TAG>`, then `aws lambda wait function-updated-v2`) → `aws-backend-migrate`. `deploy-frontend`
is `aws-check` → `aws-frontend-publish` (build the SPA with `VITE_API_URL` read from the backend
stack's live outputs, upload the built non-`index.html` assets, upload `index.html` last, `aws s3
sync --delete` the bucket, then invalidate CloudFront). Neither target aliases
`aws-backend-deploy`/`aws-frontend-deploy` — those chains still exist for the one-time/occasional
infra apply (UC-5), which only ever runs manually from a laptop; CI never invokes
`aws-backend-stack`/`aws-frontend-stack`/any `*-stack` target, only `deploy-backend`/`deploy-frontend`
(see UC-9).

### Primary Flow (Happy Path)
1. The AWS SDK credential chain resolves credentials from the SSO/temporary session (environment
   variables or a named profile) — `aws-check`'s `aws sts get-caller-identity` succeeds without any
   `.env` static keys present (FR-19).
2. `TAG` defaults to `git rev-parse HEAD` behavior for a clean tree matching the currently-checked-out
   commit (FR-13's contract: CI-triggered deploys pin `TAG` to the exact commit SHA; the same value is
   used for a manual deploy of that same commit so ECR image tags are unambiguous by commit).
3. `make deploy-backend` builds the Lambda image tagged with that SHA, pushes it to ECR
   (`aws-backend-push`), calls `aws lambda update-function-code --image-uri <ECR_URI>:<TAG>` and waits
   for `function-updated-v2` (`aws-backend-update-code` — a direct Lambda API call, not a
   CloudFormation stack update), then runs Alembic migrations (`aws-backend-migrate`) — identical steps
   and identical command to what CI runs (FR-15: no separate CI-only path). No CloudFormation stack is
   touched by this target.
4. `make deploy-frontend` builds the SPA with `VITE_API_URL` read from the frontend/backend stacks'
   live outputs (not a hardcoded value) and the current Cognito build args, uploads the built
   non-`index.html` assets, then uploads `index.html` last, `aws s3 sync --delete`s the bucket, and
   invalidates CloudFront — identical command to what CI runs. No CloudFormation stack is touched by
   this target either.
5. Both targets complete successfully using only the SSO-derived environment credentials, no `.env`
   static keys read or required.

**Postconditions**: The Lambda function runs the newly pushed image tagged with the exact commit SHA;
the frontend serves the newly built SPA; both operations are re-runnable with the same result
(idempotent, NFR-4).

### Alternative Flows
- **UC-8-A1: local deploy still using legacy static keys in `.env`** — explicitly allowed to remain as
  a fallback for manual/local deploys per FR-19; `aws-check` succeeds either way, since it only checks
  that *some* valid credential source resolves, not which kind.

### Error Flows
- **UC-8-E1: dirty working tree** — uncommitted changes are present when `make deploy-backend` runs.
  The Makefile's default `TAG` derivation is: on a clean tree, the full `git rev-parse HEAD` (the
  complete 40-character commit SHA, matching FR-13); on a dirty tree, `<full-sha>-dirty-<timestamp>`
  (the full SHA plus a `-dirty-` suffix and a timestamp) — not `git describe --always
  --dirty=...`. An uncommitted build therefore still gets a unique, clearly dirty-marked tag derived
  from the current `HEAD` SHA — the deploy succeeds but the pushed image tag makes the dirty state
  visible in ECR, distinguishing it from a clean commit-SHA deploy. Student is expected to commit
  before a deploy that should be attributable to a specific SHA (as CI's deploys always will be, since
  CI only runs against a committed `github.sha`).
- **UC-8-E2: missing git SHA (not a git repository, or `.git` missing/corrupted)** — `git rev-parse
  HEAD` fails; the Makefile's `TAG` fallback further degrades to a bare timestamp
  (`date +%Y%m%d%H%M%S`) per the existing `ifndef TAG` block. The deploy still runs (the target does
  not hard-fail), but the resulting tag carries no commit provenance — student must supply `TAG=`
  explicitly if traceability matters, or fix the git checkout.
- **UC-8-E3: missing `DOMAIN`/`FRONTEND_DOMAIN`/`BACKEND_DOMAIN`** — `deploy-frontend`'s
  `aws-frontend-publish` step reads `VITE_API_URL` from live stack outputs (the `infra/backend-domain.yaml`
  stack's custom-domain output if that stack exists and is attached, otherwise `infra/backend.yaml`'s
  raw function-URL output), which is independent of whether `BACKEND_DOMAIN`/`DOMAIN` happens to be set
  on this particular invocation — the deploy does not hard-fail on a missing domain variable, but the
  resulting build will point at whichever backend URL is currently live, which the student must verify
  matches the intended custom domain.
- **UC-8-E4: expired SSO session** — `aws sts get-caller-identity` in `aws-check` fails with an
  expired-token error; the target's existing failure message ("AWS credentials missing/invalid...")
  fires (message text may be extended to mention SSO), and the student must run `aws sso login` again
  before retrying.
- **UC-8-E5: a stale local `.env` blanks already-exported AWS credentials (verified bug)** —
  `.env.example`/`.env` ship with empty `AWS_ACCESS_KEY_ID=`/`AWS_SECRET_ACCESS_KEY=` lines for
  students who use SSO instead of static keys. The Makefile's `-include .env` plus
  `export $(shell sed ... .env)` re-exports every variable *named* in `.env`, including ones set to an
  empty string — this overwrites an already-exported, non-empty `AWS_ACCESS_KEY_ID`/
  `AWS_SECRET_ACCESS_KEY`/`AWS_SESSION_TOKEN`/`AWS_PROFILE` that the student's shell picked up from
  `aws sso login`, silently breaking `aws-check` and every subsequent AWS call with "no
  credentials"/"credentials expired"-style errors that are confusing because the student did log in.
  Required fix: `.env`-sourced values MUST NOT override an already non-empty `AWS_ACCESS_KEY_ID`/
  `AWS_SECRET_ACCESS_KEY`/`AWS_SESSION_TOKEN`/`AWS_PROFILE` already present in the calling shell's
  environment (only export the `.env`-read value when the corresponding variable is currently
  unset/empty); and `.env` MUST NOT be implicitly created as a side effect of `-include .env` or of
  `deploy-backend`/`deploy-frontend` running. The only targets that may create `.env` (via
  `cp .env.example .env`) are `start` and `test-back` (`up` depends on `start`, so it inherits this
  behavior rather than creating `.env` itself) — `deploy-backend`, `deploy-frontend`, `help`, and every
  other target must never trigger that creation.

### Edge Cases
- **UC-8-X1**: running `make deploy-backend` twice back-to-back with no code changes and the same
  `TAG` — the second run rebuilds and re-pushes the image under the same tag; the resulting image
  digest is **not guaranteed to be identical** to the first build's digest (`back/Dockerfile.lambda`
  installs dependencies without a fully pinned/locked resolution and layers may pick up newer
  transitive packages between builds — see UC-11-E1's unlocked-dependency/`uv:latest` note), so ECR may
  record a new digest under the reused tag. `aws-backend-update-code` still succeeds either way (Lambda
  is pointed at whatever image the tag currently resolves to), and migrations still no-op against an
  already-migrated database — the operation is idempotent in *effect* (same end state, no errors, no
  duplicate resources, NFR-4) even though the underlying image bytes are not guaranteed to be
  byte-for-byte reproducible.
- **UC-8-X2**: on a fresh checkout with no `.env` present, running `make help` MUST NOT create `.env`
  (only `start`/`test-back` may, per UC-8-E5) — `make help` only lists targets and touches no file.
  Separately, if a `.env` file does exist and contains an empty `AWS_ACCESS_KEY_ID=` line (the
  `.env.example` default for SSO users), and the calling shell already has a non-empty
  `AWS_ACCESS_KEY_ID` (and/or `AWS_SECRET_ACCESS_KEY`/`AWS_SESSION_TOKEN`/`AWS_PROFILE`) exported from
  `aws sso login`, the environment-provided credentials MUST still win — `aws-check` and every
  subsequent AWS call authenticate as the SSO session, not as "no credentials", per the fix required in
  UC-8-E5.

### Data Requirements
- **Input**: local git commit SHA (or dirty-tree tag), SSO/temporary AWS credentials, `.env`
  non-secret settings (ports, `FRONTEND_DOMAIN`/`BACKEND_DOMAIN`/`DOMAIN`).
- **Output**: pushed ECR image, updated Lambda function code, migrated database, published S3
  build, invalidated CloudFront cache.
- **Side Effects**: ECR image push, `lambda:UpdateFunctionCode` (direct API call, not a stack update),
  Alembic migration run, S3 sync, CloudFront invalidation.

---

## UC-9: CI push to `main` — lint/back-test/front-test → OIDC → deploy-backend → deploy-frontend

**Actor**: CI workflow (`.github/workflows/deploy.yml`), triggered by a push authored by the
student/deployer.
**Preconditions**: `main` branch protection (if any) allows the push; the OIDC role from UC-4 exists
and its ARN is configured in the workflow; UC-5's/UC-7's stacks already exist (first-ever CI deploy
assumes the one-time bootstrap already ran) — CI itself never creates or updates any CloudFormation
stack.
**Trigger**: `git push origin main` (direct push or a merged PR) lands a new commit on `main`.

### Primary Flow (Happy Path)
1. `deploy.yml` triggers on `push: branches: [main]` only (not on PRs, and it has no
   `workflow_dispatch` or other manually-triggerable trigger). `code-style.yml` no longer triggers on
   push to `main` itself; instead `deploy.yml`'s `lint` job calls it via `workflow_call`
   (`uses: ./.github/workflows/code-style.yml`), so the same backend/frontend/infra lint checks run as
   a job inside the deploy workflow. `code-style.yml` keeps its own `pull_request` trigger for PR-time
   linting.
2. Two more jobs run alongside `lint`: `back-test` (starts a `postgres:16-alpine` GitHub Actions
   service container, sets `TEST_DATABASE_URL` to point at it, and runs the backend's pytest suite
   directly — not via `make test`, which assumes a full Docker Compose stack) and `front-test`
   (`npm test`). The `deploy` job's `needs:` lists `lint`, `back-test`, and `front-test` — it does not
   start unless all three succeed (FR-22).
3. `deploy` is the only job with `permissions: id-token: write` (and `contents: read`) — `lint`,
   `back-test`, and `front-test` do not have `id-token: write`, so only `deploy` can ever request an
   OIDC token. `deploy` declares no GitHub `environment:` (see UC-10-E6 for why this matters to the
   trust policy's `sub` claim), and runs on `ubuntu-24.04-arm` (a native arm64 GitHub-hosted runner) so
   the Lambda image it builds for the default `ARCH=arm64` target needs no cross-architecture
   emulation.
4. `deploy` uses `aws-actions/configure-aws-credentials` — the `v4` release, pinned by commit SHA (not
   a mutable tag) in the workflow file — with `role-to-assume` set to the OIDC deploy role's ARN.
   GitHub issues an OIDC token whose `sub` claim is exactly
   `repo:MasterDay3/OneTwoThree:ref:refs/heads/main`; the deploy role's trust policy matches it via
   `StringEquals` and STS returns temporary credentials.
5. With those credentials in the environment, the workflow runs `make deploy-backend` with
   `TAG=${{ github.sha }}` (FR-13): pushes the image to ECR tagged with the exact commit SHA, calls
   `aws lambda update-function-code` and waits for `function-updated-v2`, then runs Alembic migrations.
   No `*-stack` target runs — the backend/frontend/backend-domain CloudFormation stacks are applied
   only manually from a laptop (UC-5/UC-7).
6. The workflow then runs `make deploy-frontend`, building the SPA with `VITE_API_URL` read from the
   live stack outputs and the current Cognito build args, syncing to S3, invalidating CloudFront — run
   strictly after `deploy-backend` (FR-24 ordering, since the frontend build needs the backend's live
   domain/URL).
7. The workflow run shows green end-to-end; the live site at `https://app.<domain>` reflects the new
   commit.

**Postconditions**: The commit just pushed to `main` is live in both the Lambda function (by SHA tag)
and the S3/CloudFront-served SPA; no long-lived credentials were used at any point.

### Alternative Flows
- **UC-9-A1: push is a merge of an approved PR** — behaves identically to a direct push once the merge
  commit lands on `main`; the workflow does not distinguish direct pushes from PR merges, only the
  target ref (`refs/heads/main`).

### Error Flows
- **UC-9-E1: lint failure on purpose (red pipeline, no deploy)** — a commit introduces a deliberate
  lint violation (e.g. an unused import in `back/` or a Prettier violation in `front/`). The
  corresponding lint job (`ruff check` / `eslint`+`prettier --check`+`tsc` / `cfn-lint`) fails and is
  reported red in the Actions UI; because the deploy job depends on (`needs:`) all lint/test jobs
  succeeding, it never starts — `make deploy-backend`/`make deploy-frontend` are not invoked, and the
  previously deployed Lambda/S3/CloudFront content is untouched (FR-22, FR-26, acceptance criterion
  #9). A follow-up commit fixing the lint violation produces a fully green pipeline ending in a
  successful deploy.
- **UC-9-E2: migration failure after the Lambda image was already updated** — `aws-backend-update-code`
  succeeds (`aws lambda update-function-code` + `function-updated-v2` complete; Lambda now points at
  the new image), but `aws-backend-migrate`'s Lambda invoke with `{"action": "migrate"}` returns a
  `FunctionError` (e.g. a bad Alembic revision). The target's own guard (`[ "$$err" = None ] ||
  { echo "Migration failed..."; exit 1; }`) makes `make deploy-backend` exit non-zero, failing the CI
  job and the whole workflow run red — even though the Lambda code is already updated, the workflow
  does not proceed to `deploy-frontend`, so the frontend is not rebuilt against a backend whose
  migration is in an unknown state. The running Lambda now serves the new code against a database that
  may be mid-migration or at the old schema version — recovery is a follow-up fixed migration deployed
  the same way, or a rollback (`make aws-backend-rollback TAG=<previous-sha>`, UC-11) to the last
  known-good image tag via another direct `update-function-code` call (which does not by itself revert
  a partially-applied migration — see UC-11's error flows).
- **UC-9-E3: image push failure** (e.g. ECR authentication expired mid-job, network failure, or the
  image exceeds a size/rate limit) — `docker buildx build ... --push` fails; `make deploy-backend`
  exits non-zero before reaching `aws-backend-update-code`/`aws-backend-migrate`; the previously
  deployed Lambda image and function configuration are untouched, and no migration is attempted
  against the (unchanged) database. The workflow run is red; `deploy-frontend` never runs.
- **UC-9-E4: CloudFront invalidation failure** (e.g. `cloudfront:CreateInvalidation` denied by an
  over-narrow IAM policy, or a transient API error) — the S3 sync in `aws-frontend-publish` has already
  completed (new build objects are in the bucket), but the `aws cloudfront create-invalidation` command
  fails and `make deploy-frontend` exits non-zero, failing the workflow red. Until a subsequent
  successful invalidation, CloudFront's edge caches may continue serving the previous `index.html`
  (cached `no-cache` control means index.html itself should still revalidate promptly, but hashed
  asset filenames served from cache remain consistent with whichever `index.html` version a given edge
  location currently has) — recovery is re-running `make deploy-frontend` (idempotent) once IAM/API
  issues are fixed.
- **UC-9-E5: a stray `.env` in the CI runner blanks OIDC-issued credentials (verified-bug case, CI
  side)** — `.env` is gitignored and should never exist in a fresh CI checkout, but if one were ever
  present (e.g. left behind by a cache restore, a prior step, or a self-hosted runner reusing a
  workspace), the same Makefile behavior as UC-8-E5 would re-export its (likely empty)
  `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` and silently blank the credentials
  `aws-actions/configure-aws-credentials@v4` just placed in the job's environment — `aws-check` and
  every subsequent AWS call would then fail (or worse, silently resolve some *other* ambient
  credential). Required mitigation: the `deploy` job asserts `.env` is absent (e.g. `test ! -f .env ||
  { echo "unexpected .env in CI checkout"; exit 1; }`) before running `deploy-backend`/`deploy-frontend`,
  failing fast and loudly instead of deploying with corrupted credentials.

### Edge Cases
- **UC-9-X1: two pushes to `main` in quick succession (concurrency guard)** — `deploy.yml` declares a
  single, workflow-level `concurrency` group keyed `group: deploy-${{ github.ref }}` with
  `cancel-in-progress: false` (unlike `code-style.yml`'s `cancel-in-progress: true` for its own,
  separate `pull_request` trigger — cancelling an in-flight deploy mid-`deploy-backend` could leave the
  Lambda/DB in an inconsistent state). The second push's workflow run queues behind the first rather
  than starting a concurrent `make deploy-backend`/`deploy-frontend` against the same Lambda function/
  S3 bucket/CloudFront distribution. Actions run history shows the second run as queued, then running
  only after the first completes — never two "in progress" deploy runs at once (FR-25, acceptance
  criterion #10).
- **UC-9-X1a: three pushes to `main` in quick succession** — with a single non-cancelling concurrency
  group, GitHub Actions keeps at most one run queued behind the currently running one; a third push
  arriving while a second is already queued causes the *middle* (second) queued run to show as
  `cancelled` in the Actions run history, while the first (already in progress) and third (now queued)
  runs proceed normally — this is GitHub's own documented behavior for `cancel-in-progress: false` (a
  newly queued run in the same group still supersedes an older *queued-but-not-yet-started* run; it
  simply never cancels a run that has already started) and is the expected, correct outcome here, not
  a defect: the eventual deploy always reflects the latest (third) pushed commit, and no two deploys
  ever run concurrently.
- **UC-9-X2: a push to `main` that only touches `docs/` or `infra/*.yaml` with no `back/`/`front/`
  code change** — the full pipeline still runs (lint/test/deploy are not path-filtered per FR-21/FR-22
  as specified); `cfn-lint` is exercised even for a docs-only push if it's part of the same commit
  range, and the deploy steps still run and are idempotent (NFR-4) even though nothing meaningfully
  changed in the built artifacts.

### Data Requirements
- **Input**: pushed commit on `main`, `github.sha`, OIDC deploy role ARN (configured in the workflow),
  the four `deploy.yml` job definitions (`lint` via `workflow_call`, `back-test`, `front-test`,
  `deploy`).
- **Output**: green/red Actions run; on green, updated Lambda image + migrated DB + updated
  S3/CloudFront content.
- **Side Effects**: ECR push, `lambda:UpdateFunctionCode` (direct API call, no stack update), DB
  migration, S3 sync, CloudFront invalidation — the same side effects as UC-8, triggered automatically
  instead of manually.

---

## UC-10: Trust boundary — non-`main`/non-fork workflow runs are denied OIDC role assumption

**Actor**: Attacker/other repo (a PR run from a contributor, a workflow on a non-`main` branch, a fork
of `MasterDay3/OneTwoThree`, or a workflow in an unrelated repository) attempting to reach the same
AWS account/role.
**Preconditions**: The deploy role's trust policy is deployed as specified in UC-4 (`StringEquals` on
`sub` = `repo:MasterDay3/OneTwoThree:ref:refs/heads/main`, `StringEquals` on `aud` =
`sts.amazonaws.com`); `deploy.yml` has exactly one trigger (`push: branches: [main]`, UC-9) and no
`workflow_dispatch` or other manually-invocable trigger; the `deploy` job declares no GitHub
`environment:` key.
**Trigger**: Any workflow run — whether from a PR against this repo, a push to a branch other than
`main`, a fork of this fork, or an entirely different GitHub repository — attempts
`AssumeRoleWithWebIdentity` against the deploy role's ARN using its own OIDC token.

### Primary Flow — treated as Error Flows (there is no "happy path" for an unauthorized caller)

### Error Flows
- **UC-10-E1: workflow run on a non-`main` branch of this same repo** — a push to, say, `feature/x` in
  `MasterDay3/OneTwoThree` produces an OIDC token whose `sub` is
  `repo:MasterDay3/OneTwoThree:ref:refs/heads/feature/x`. This does not `StringEquals`-match
  `repo:MasterDay3/OneTwoThree:ref:refs/heads/main`; STS denies `AssumeRoleWithWebIdentity` with an
  access-denied error. No deploy step can run even if such a workflow were mistakenly configured to
  attempt one.
- **UC-10-E2: a pull request run against this repo** — GitHub's OIDC token for a `pull_request` event
  carries a `sub` of the form `repo:MasterDay3/OneTwoThree:pull_request` (or ref-based equivalent),
  never `ref:refs/heads/main` — denied for the same reason as UC-10-E1. This is also structurally
  prevented since `deploy.yml` itself only triggers `on: push: branches: [main]`, never `on:
  pull_request` (FR-21) — so no such workflow run even attempts the assumption in normal operation;
  this error flow covers the belt-and-suspenders case of a misconfigured or manually-dispatched
  workflow.
- **UC-10-E3: a fork of `MasterDay3/OneTwoThree`** (e.g. someone forks the student's fork) pushes to
  their own copy's `main` branch. Their OIDC token's `sub` is
  `repo:<their-org>/OneTwoThree:ref:refs/heads/main` — the repository-owner segment does not match
  `MasterDay3/OneTwoThree`, so `StringEquals` fails and STS denies the assumption. This is the primary
  scenario the exact-match `StringEquals` (vs. a `StringLike` wildcard) is defending against (FR-17,
  acceptance criterion #7).
- **UC-10-E4: an entirely unrelated GitHub repository** possessing (by misconfiguration or leak) the
  deploy role's ARN attempts `AssumeRoleWithWebIdentity` — its OIDC token's `sub` never matches
  `repo:MasterDay3/OneTwoThree:ref:refs/heads/main` regardless of that repo's own branch/ref, so the
  assumption is denied identically to UC-10-E3.
- **UC-10-E5: audience mismatch** — a token whose `aud` claim is not exactly `sts.amazonaws.com` (e.g.
  a token requested for a different audience) fails the trust policy's `aud` `StringEquals` condition
  independently of whether `sub` would have matched — denied.
- **UC-10-E6: a workflow job declares a GitHub Actions `environment:`** — if the `deploy` job in
  `deploy.yml` were ever configured with `environment: production` (or any named environment),
  GitHub's OIDC token `sub` claim changes shape to include the environment segment, e.g.
  `repo:MasterDay3/OneTwoThree:environment:production` (replacing the plain
  `ref:refs/heads/main` form used today). The deploy role's trust policy's `StringEquals` condition on
  `repo:MasterDay3/OneTwoThree:ref:refs/heads/main` no longer matches that shape, so
  `AssumeRoleWithWebIdentity` is denied — even for a legitimate push to `main` by the legitimate repo.
  This is exactly why UC-9's `deploy` job is specified to declare no `environment:` key at all; adding
  one later (whether by an attacker who somehow gained write access, or by a well-meaning teammate
  tightening the workflow) breaks the trust policy unless the policy's `sub` condition is deliberately
  updated to match the new claim shape first.

### Edge Cases
- **UC-10-X1**: a workflow run triggered by `workflow_dispatch` or a scheduled trigger on `main`
  (if such a trigger were ever added later, despite the current workflow having none — see
  Preconditions) would still carry `sub = repo:MasterDay3/OneTwoThree:ref:refs/heads/main` and would be
  permitted by the trust policy as written — the trust policy authorizes based on ref (and, per
  UC-10-E6, on whether an `environment:` is set), not the triggering event type; UC-9's `on: push`
  restriction is what actually limits triggering events today, not the IAM trust policy. This is noted
  so a reviewer does not mistake the IAM condition for controlling *when* deploys happen versus *who*
  can assume the role.
- **UC-10-X2**: manual review / `aws iam simulate-principal-policy` against the deploy role's
  permission policy confirms no `Resource: "*"` remains for any ARN-scopable action (ECR, Lambda, S3,
  CloudFront, CloudFormation) — a distinct check from the trust-policy denial scenarios above, covering
  acceptance criterion #7's second half (no unscoped wildcards) even for the legitimate `main`-branch
  caller.

### Data Requirements
- **Input**: an OIDC token from an unauthorized source (wrong ref, wrong repo, wrong event, wrong
  audience).
- **Output**: `AssumeRoleWithWebIdentity` denial (`AccessDenied` from STS); no credentials issued.
- **Side Effects**: none — the boundary is precisely that no AWS action of any kind becomes possible
  for a denied caller.

---

## UC-11: Rollback — `make aws-backend-rollback TAG=<sha>` points Lambda at a previously-pushed image

**Actor**: Student/deployer (a local, manual operation — not a CI or automated workflow trigger).
**Preconditions**: A previous deploy (UC-8 or UC-9) already pushed an image to ECR tagged with an
older commit SHA that is still present in ECR (see UC-11-E1 for the retention window); the current
deployed image (tagged with a newer SHA) is suspected bad (e.g. UC-9-E2's post-migration-failure
state, or a functional regression found after deploy).
**Trigger**: Student runs `make aws-backend-rollback TAG=<previous-sha>`.

### Primary Flow (Happy Path)
1. Student identifies the last known-good commit SHA (e.g. from `git log` or `aws ecr
   describe-images`).
2. `aws-backend-rollback` first calls `aws ecr describe-images --image-ids
   imageTag=<previous-sha>` to verify that tag still exists in the ECR repository; if found, it
   proceeds (see Error Flows for the not-found case).
3. It calls `aws lambda update-function-code --image-uri <ECR_URI>:<previous-sha>` — the same direct
   Lambda API call `aws-backend-update-code` uses, not a CloudFormation stack update — and waits for
   `aws lambda wait function-updated-v2`.
4. No rebuild, no image push, and no migration run: rollback is scoped strictly to pointing the
   Lambda's code back at a previously-pushed image. Restoring a previous *database schema* is
   explicitly out of scope (see UC-11-E2).
5. The Lambda function now serves the previous, known-good code, with no new Docker build and no
   CloudFormation stack touched.

**Postconditions**: The Lambda function runs the image tagged with `<previous-sha>`; the ECR
repository is unmodified (nothing deleted or re-pushed by the rollback itself, though its lifecycle
policy — UC-11-E1 — continues to prune independently); the rollback completed without a new Docker
build or a migration run.

### Alternative Flows
- **UC-11-A1: rollback re-run via the CI workflow itself** (e.g. by reverting the bad commit and
  pushing that revert to `main`) — this is the "CI-native" way to roll back: `deploy.yml` runs
  normally against the revert commit's own new SHA, rebuilding and pushing a *new* tag equal to the
  revert commit's SHA (not literally re-deploying the old tag) — functionally restores the old
  behavior while still respecting FR-13's "never `latest`, always the SHA of the commit being
  deployed" rule. This is distinct from UC-11's primary flow, which explicitly reuses a previously
  pushed tag without rebuilding.

### Error Flows
- **UC-11-E1: the requested tag is not found in ECR** — `aws-backend-rollback`'s `describe-images`
  check for `imageTag=<previous-sha>` returns empty/`ImageNotFoundException`; the target fails fast
  with an explicit error before attempting any `update-function-code` call (no partial rollback). This
  is expected to happen for anything but recent history: **the backend ECR repository carries a
  lifecycle policy that keeps only the last 5 pushed images, regardless of tag** — so the practical
  rollback window is approximately the last 5 deploys (UC-8/UC-9 pushes), not "any prior commit ever
  deployed". Recovery for an out-of-window SHA is to rebuild from that old commit via `git checkout
  <sha>` + `make deploy-backend` — but this is **not guaranteed to reproduce the original image
  byte-for-byte**: `back/Dockerfile.lambda` installs dependencies without a fully locked/pinned
  resolution and pulls `uv:latest` as a build stage, so a rebuild from old source can silently pick up
  newer transitive dependency versions or a newer `uv` release than the original build used. A true,
  guaranteed-identical rollback is only possible while the original image tag is still within the
  5-image retention window.
- **UC-11-E2: the previous SHA's schema is incompatible with the current (newer) database schema**
  — rolling back the Lambda's *code* does not revert the database; a migration applied by the bad
  deploy stays applied (e.g. a non-nullable column the rolled-back code doesn't populate), and the old
  code may error against the newer schema. `aws-backend-rollback` never runs a migration in either
  direction — reverting schema state requires a separate, deliberate down-migration, out of scope of
  this rollback mechanism.

### Edge Cases
- **UC-11-X1**: rolling back and then re-deploying the originally-bad SHA again (e.g. after fixing the
  underlying issue in a new commit rather than truly reusing the bad tag) — since FR-13 requires the
  tag pushed by `deploy-backend` to equal the commit SHA being deployed, "re-deploying the bad SHA"
  only makes sense if the fix amends that same commit (rare) or, more commonly, a new commit with its
  own new SHA is deployed instead — the rollback mechanism is not intended to loop back to a
  still-broken tag.

### Data Requirements
- **Input**: `TAG=<previous-sha>` (must currently exist in ECR, within the 5-image lifecycle-retained
  window).
- **Output**: Lambda function's live `ImageUri` (as reported by `aws lambda get-function`) updated to
  the previous tag's image.
- **Side Effects**: one `lambda:UpdateFunctionCode` call; no ECR push; no CloudFormation stack update;
  no migration invoked; ECR's lifecycle policy continues to prune down to the last 5 images
  independently of rollback activity.

---

## UC-12: Teardown with `make aws-destroy` (extended for OIDC/API Gateway/certificate resources)

**Actor**: Student/deployer, after grading is complete and further AWS charges (Aurora, CloudFront,
API Gateway, Secrets Manager) are no longer wanted.
**Preconditions**: All stacks from UC-4/UC-5/UC-6/UC-7 exist; student has credentials able to delete
them (may be the same SSO credentials as UC-8, not the CI-only OIDC role, since the OIDC role's
permission policy per FR-18 is scoped to deploy actions, not delete/`DeleteStack`).
**Trigger**: Student runs `make aws-destroy` (existing aggregate target, extended to also cover the
new OIDC/API Gateway/certificate stacks per NFR-3).

### Primary Flow (Happy Path)
1. `aws-destroy` runs `aws-frontend-destroy` (prompts "Delete stack $(FRONTEND_STACK)... [y/N]",
   empties the S3 bucket, deletes the frontend CloudFormation stack and waits for completion).
2. Then a new `aws-backend-domain-destroy` step deletes the `infra/backend-domain.yaml` stack (API
   Gateway custom domain mapping, the HTTP API, and the `api.<domain>` Route 53 alias record). This
   MUST run **before** `aws-backend-destroy`: `infra/backend-domain.yaml` takes the backend Lambda
   function's *name* as a plain Makefile-passed parameter (not a CloudFormation `Fn::ImportValue`
   cross-stack reference), so CloudFormation itself would not block deleting the backend stack first.
   The actual ordering constraint is the backend-domain stack's own `AWS::Lambda::Permission` (granting
   API Gateway permission to invoke the function) and its API Gateway integration, both of which
   reference the function by ARN — deleting the backend stack (and therefore the function) first would
   leave those resources pointing at a function that no longer exists, causing the backend-domain
   stack's own later deletion to partially fail (see UC-12-E4).
3. Then `aws-backend-destroy` (prompts, deletes `$(BACKEND_STACK)` and `$(BACKEND_ECR_STACK)`, waits
   for each; a final Aurora snapshot is kept per the existing behavior; prints a reminder about the
   kept snapshot) — now safe to run since the backend-domain stack no longer references it.
4. Then `aws-cognito-destroy` (prompts, warns that ALL user accounts are deleted with it, deletes the
   Cognito stack).
5. Extended for this feature: the OIDC provider + deploy role stack from UC-4 is left standing by
   default (since deleting it would break future CI deploys). The stack's `AWS::IAM::OIDCProvider`
   resource itself carries `DeletionPolicy: Retain`, precisely because the same OIDC provider is a
   per-account singleton that may be shared by other projects/labs already using GitHub Actions OIDC in
   the same account — deleting the CloudFormation stack that created it removes only the deploy role
   (and its policy); the underlying IAM OIDC provider resource is retained regardless, even if a
   student explicitly deletes the OIDC stack. A student wanting to fully remove the provider itself must
   do so manually (`aws iam delete-open-id-connect-provider`) and only after confirming nothing else in
   the account depends on it.
6. Extended for this feature: the ACM certificates from UC-6 are not automatically deleted by
   `aws-destroy` (ACM certificates are free to hold as long as nothing references them, but the
   `CertificateArn` becomes unusable once the CloudFront/API Gateway resource referencing it is
   deleted) — a note is added that certificates may optionally be deleted separately
   (`aws acm delete-certificate`) once no distribution/domain references them.

**Postconditions**: All billable per-request/per-hour resources (Aurora cluster, CloudFront
distribution, API Gateway custom domain + HTTP API, Lambda function, Secrets Manager secret) are gone;
a final Aurora snapshot remains (small, storage-only cost) until manually deleted; the OIDC provider
resource (retained by design) and issued ACM certificates optionally remain at negligible/zero cost.

### Alternative Flows
- **UC-12-A1: student declines a prompt (`N` or Enter)** — that stack's deletion is skipped entirely;
  `aws-destroy` continues to the next stack in its chain rather than aborting the whole teardown (each
  `aws-*-destroy` target's confirmation is independent).

### Error Flows
- **UC-12-E1: S3 bucket not fully empty due to versioning or additional prefixes** — `aws s3 rm
  s3://$$bucket --recursive --quiet` may not remove all object versions if versioning was ever enabled
  out-of-band; the subsequent `aws cloudformation delete-stack` for the frontend stack fails
  ("bucket not empty"). Recovery: manually empty all versions, then re-run `aws-frontend-destroy`.
- **UC-12-E2: Aurora final snapshot creation fails or the stack delete times out waiting on RDS** —
  `aws cloudformation wait stack-delete-complete` for `$(BACKEND_STACK)` hangs/fails; student checks
  the RDS/CloudFormation console for the specific error (e.g. a dependent security group or subnet
  still referenced elsewhere) and retries deletion after resolving it.
- **UC-12-E3: API Gateway custom domain deletion fails because the Route 53 alias record still
  references it** — the new teardown step must delete the alias record (or the whole HTTP API/domain
  as a CloudFormation-managed unit) in dependency order; if done out of CloudFormation's control (e.g.
  a manually created alias), the stack delete fails until the record is removed first.
- **UC-12-E4: teardown run in the wrong order (backend stack destroyed before backend-domain stack)**
  — `infra/backend-domain.yaml` takes the backend Lambda function's name as a plain Makefile-passed
  parameter, not a `Fn::ImportValue` cross-stack reference, so CloudFormation does not itself block
  deleting the backend stack first. The actual ordering constraint is the backend-domain stack's own
  `AWS::Lambda::Permission` and API Gateway integration resources, which reference the function by ARN:
  if `aws-backend-destroy` runs first, the backend stack (and its function) is gone, and the
  backend-domain stack is left with a permission/integration pointing at a nonexistent function — its
  own later deletion may then partially fail (e.g. a Lambda permission delete call erroring against an
  ARN that no longer exists). Recovery: always run `aws-backend-domain-destroy` before
  `aws-backend-destroy` (the corrected ordering documented in the Primary Flow above); `make
  aws-destroy` itself is updated to encode this order so a plain `make aws-destroy` run never hits this
  case.

### Edge Cases
- **UC-12-X1**: running `make aws-destroy` a second time after a successful teardown — each
  `aws-*-destroy` target's stack-existence check (`stack_output`/`describe-stacks` returning empty) or
  CloudFormation itself reports "does not exist" for an already-deleted stack; the aggregate target
  should not hard-fail on an idempotent re-run of a full teardown (existing `aws-*-destroy` targets do
  not currently guard for "already deleted" explicitly beyond the delete/wait calls tolerating a
  missing stack gracefully via AWS API errors being reported, not the Makefile crashing on them).

### Data Requirements
- **Input**: student confirmation (`y`) at each stack's prompt.
- **Output**: none (destructive operation).
- **Side Effects**: deletes S3 bucket contents and the bucket, CloudFront distribution, the
  `infra/backend-domain.yaml` stack (API Gateway custom domain/HTTP API and the `api.<domain>` Route 53
  alias record — deleted before the backend stack), Aurora cluster (final snapshot kept), Lambda
  function, ECR repository, Cognito user pool (and all its users), and the `app.<domain>` Route 53
  alias records; the IAM OIDC provider resource is retained (`DeletionPolicy: Retain`) even if the
  OIDC/deploy-role stack itself is deleted.

---

## UC-13: End user's browser at `app.<domain>` calls the API at `api.<domain>` (CORS + Cognito redirects)

**Actor**: End user of the app (a signed-in or signing-in browser user).
**Preconditions**: `app.<domain>` and `api.<domain>` are both live (UC-7); `infra/backend.yaml`'s
`CorsOrigins` parameter includes `https://app.<domain>`; the Cognito stack's `CallbackUrls`/
`LogoutUrls` include `https://app.<domain>/auth/callback` and `https://app.<domain>/` respectively.
**Trigger**: A browser loads `https://app.<domain>/home` and the SPA issues `fetch` calls to
`https://api.<domain>/api/meetings` with `Authorization: Bearer <Cognito ID token>`.

### Primary Flow (Happy Path)
1. Because the SPA's fetch wrapper (`front/src/lib/api.ts`) always attaches `Content-Type:
   application/json` and, for authenticated routes, `Authorization: Bearer <token>` — both
   non-"simple" headers under the CORS spec — the browser always sends a preflight `OPTIONS` request to
   `https://api.<domain>/api/meetings` first, carrying `Origin: https://app.<domain>` and the
   `Access-Control-Request-Method`/`Access-Control-Request-Headers` the actual call will use. Only
   after a successful preflight does the browser send the actual cross-origin `GET`/`POST` request,
   itself also carrying `Origin: https://app.<domain>`. There is no "simple request" path for this app
   — every call, including every `GET`, is preflighted.
2. The `OPTIONS` preflight reaches the backend through the API Gateway HTTP API's `$default`
   route/stage exactly like any other method — the API Gateway resource itself has no
   `CorsConfiguration` of its own (`infra/backend-domain.yaml` does not configure API Gateway-level
   CORS), so the preflight is proxied straight through to the Lambda. CORS is handled **only** by
   FastAPI's `CORSMiddleware` inside the Lambda (configured from the `CorsOrigins` environment
   variable, which now includes `https://app.<domain>` per FR-9): it answers the `OPTIONS` preflight
   with `Access-Control-Allow-Origin: https://app.<domain>` (and the other required CORS headers), and
   the browser then allows the actual request's response through to the page's JavaScript.
3. Separately, a user completing Google sign-in via the Cognito Hosted UI is redirected back to
   `https://app.<domain>/auth/callback` (in `CallbackUrls`) after authenticating, and to
   `https://app.<domain>/` (in `LogoutUrls`) after signing out — both allowed because the Cognito app
   client's redirect URL lists include the custom domain (FR-9).
4. The end user sees their meetings list load successfully with no browser console CORS errors.

**Postconditions**: Cross-origin requests from the production custom domain succeed exactly as they
did locally (`http://localhost:3000`) or on the CloudFront domain before the custom domain was added.

### Alternative Flows
- **UC-13-A1: user still on the bare CloudFront domain** (before or without the custom domain
  configured) — `CorsOrigins`/`CallbackUrls`/`LogoutUrls` already include the CloudFront domain from
  the existing `aws-frontend-cors` behavior; both the CloudFront domain and the eventual custom domain
  can be allowed simultaneously (the `CorsOrigins` value is a comma-separated list, not a single
  value), so switching to the custom domain does not require removing the CloudFront-domain entry.

### Error Flows
- **UC-13-E1: `CorsOrigins` not yet refreshed after the custom domain went live** — see UC-7-E3. Since
  every call from `app.<domain>` is preflighted (Primary Flow step 1 — there is no "simple GET" case in
  this app), a missing `https://app.<domain>` entry in `CorsOrigins` causes FastAPI's `CORSMiddleware`
  to answer the `OPTIONS` preflight itself without a matching `Access-Control-Allow-Origin`/
  `Access-Control-Allow-Headers`; the browser fails the preflight and **never sends the actual
  `GET`/`POST` request at all** — unlike a simple-request CORS failure, the backend's route handler is
  never invoked for the blocked call, so there is no partial server-side processing to reason about.
  The API Gateway HTTP API itself has no `CorsConfiguration` to misconfigure separately — CORS behavior
  is entirely determined by the Lambda's own `CorsOrigins` environment variable. Resolved by re-running
  the CORS-refresh step (`aws-backend-stack KEEP_IMAGE=1` with the updated origins list, and
  `aws-cognito-stack` for the redirect URLs).
- **UC-13-E2: Cognito redirect URL not updated** — Google sign-in completes at the Hosted UI, but the
  Hosted UI rejects the redirect (`redirect_uri` mismatch error) because `https://app.<domain>/auth/callback`
  is not yet in the app client's `CallbackUrls`. The user sees Cognito's own error page instead of
  returning to the app. Resolved by re-running `aws-cognito-stack` with the updated
  `COGNITO_CALLBACK_URLS`/`COGNITO_LOGOUT_URLS`.

### Edge Cases
- **UC-13-X1**: a request from an origin not in `CorsOrigins` at all (e.g. a copy-pasted API URL
  opened directly in a script from an unrelated site) — correctly blocked by CORS; this is the
  intended, unchanged behavior of the existing CORS middleware, not a regression introduced by adding
  the custom domain.

### Data Requirements
- **Input**: `CorsOrigins` (backend stack parameter), `CallbackUrls`/`LogoutUrls` (Cognito stack
  parameters), both including `https://app.<domain>`-based values.
- **Output**: successful cross-origin API responses and successful Cognito Hosted UI redirects.
- **Side Effects**: none beyond the stack parameter updates already covered in UC-7/UC-9.

---

## UC-14: Aurora Serverless v2 cold start observed through the API Gateway custom domain

**Actor**: End user of the app (the first request after a period of inactivity); Student/deployer
verifying the behavior post-deploy.
**Preconditions**: Aurora Serverless v2 cluster has `DbMinCapacity=0` and has auto-paused after 5 idle
minutes (existing, documented, unchanged behavior); the new API Gateway front door (UC-7) sits in
front of the Lambda function; the Lambda function's own `Timeout` (default `30`s per
`infra/backend.yaml`) is unchanged by this feature (NFR-5).
**Trigger**: A request arrives at `https://api.<domain>/api/meetings` (or any DB-touching route) after
the Aurora cluster has been paused.

### Primary Flow (Happy Path)
1. API Gateway receives the HTTPS request on the custom domain and proxies it to the Lambda function
   via the Lambda proxy integration.
2. The Lambda cold- or warm-starts, opens a connection to Aurora, and Aurora resumes from its paused
   state — documented as taking roughly 15 seconds.
3. The API Gateway integration's `TimeoutInMillis` is fixed at `30000` (30 seconds) in
   `infra/backend-domain.yaml` — a decided value, matching (not reducing below) the Lambda function's
   own `Timeout` of `30`s per NFR-5 ("the new API Gateway front door MUST NOT reduce the existing
   Lambda Timeout budget for that cold path"). The ~15s resume time fits comfortably within both the
   Lambda timeout and this fixed 30-second integration timeout.
4. The request completes successfully once Aurora resumes; the response is returned to the client
   through API Gateway with no gateway-level timeout error.
5. Subsequent requests within the idle-timeout window hit the now-resumed Aurora cluster with normal
   latency.

**Postconditions**: The end user experiences a single slow (~15s) request after a period of app
inactivity, then normal latency — an existing, accepted, out-of-scope-to-change UX characteristic
(NFR-5) that must simply not be broken by inserting API Gateway in front of the Lambda.

### Alternative Flows
- **UC-14-A1: request via the raw Lambda function URL instead of the custom domain** — the same cold
  start behavior is observed with no API Gateway integration timeout in the path at all (function URLs
  have their own timeout characteristics tied directly to the Lambda's `Timeout`), serving as the
  baseline this feature must not regress.

### Error Flows
- **UC-14-E1: API Gateway integration timeout is misconfigured lower than the decided `30000`ms**
  (a regression this feature must avoid per NFR-5) — a cold-start request taking close to or over a
  shorter timeout results in a gateway 5xx timeout response from API Gateway even though the Lambda
  itself would have eventually succeeded within its own 30s budget. This is the specific failure mode
  NFR-5 exists to prevent; `infra/backend-domain.yaml`'s `TimeoutInMillis` is fixed at `30000` precisely
  so this misconfiguration cannot occur through normal template usage.
- **UC-14-E2: Aurora resume takes longer than the documented ~15s** (e.g. under unusual load or a
  transient AWS-side delay) and exceeds the 30s combined budget — the request fails with a timeout at
  either the Lambda or API Gateway level; this is an existing, accepted risk of the Lambda + Aurora
  Serverless v2 architecture (documented in `docs/lab2-discussion.md`'s architecture trade-off
  discussion), not something this feature introduces or is required to fix.

### Edge Cases
- **UC-14-X1**: two concurrent requests both arrive during Aurora's resume window — both wait on the
  same underlying cluster resume (Aurora Serverless v2 resume is a cluster-level, not
  per-connection-level, operation); both should complete once the cluster is available, still within
  the ~15s + normal-latency budget, assuming Lambda's own concurrency limits are not separately
  exhausted.

### Data Requirements
- **Input**: any DB-touching HTTP request to `api.<domain>` after an idle period.
- **Output**: a successful (if slow) response, or a gateway 5xx timeout error if NFR-5 is violated.
- **Side Effects**: Aurora cluster transitions from paused to active capacity.

---

## UC-15: Writing and reviewing `docs/lab2-discussion.md`

**Actor**: Student/deployer (writer, though per this feature's process the BA/other pipeline roles do
not write this file — the student or a designated writer step produces it per FR-27–FR-30);
Lecturer/reviewer (reader, checking answers are grounded in the actual repo).
**Preconditions**: `PROJECT.md` (UC-3), the deploy contract (UC-8/UC-9), and the custom-domain/OIDC
infrastructure (UC-4 through UC-7) exist so the discussion document can accurately describe the
as-built system, not a speculative one.
**Trigger**: The discussion document is authored (and later read by the reviewer) as part of the Lab 2
submission.

### Primary Flow (Happy Path)
1. Writer answers each required topic in FR-27 by citing the actual repo file/line for each claim,
   e.g.: compose keys (`image` vs `build`, `ports` vs `expose`, `environment`, `volumes`, `command`,
   `develop.watch`, `db`'s exposed `DB_PORT`) — verified against `compose.yaml` (as read in this
   analysis); readiness (the `db` healthcheck + `backend`'s `depends_on: service_healthy`, and DB
   connection-pool/retry/`/api/health` behavior if the DB disappears after startup); Dockerfile vs
   compose responsibilities and which survives a hypothetical ECS/Cloud Run move; base images (this
   repo's `postgres:16-alpine`, and whatever `back/Dockerfile.lambda`/`back/Dockerfile`/
   `front/Dockerfile` actually specify — verified, not assumed); backend layering
   (`routers`/`services`/`models`/`schemas`); SQLAlchemy trade-offs; Alembic vs `create_all` and
   exactly when migrations run (compose `command` locally; `make aws-backend-migrate`'s
   `{"action": "migrate"}` Lambda invoke on AWS; explicitly not on cold start); CloudFront vs S3
   responsibilities; ECR vs ECS (this repo uses ECR + Lambda, not ECS — ties to FR-28); what a health
   check checks (ALB target-group health check vs this repo's `/api/health` route vs Lambda's own
   invocation-level health signal); CNAME-for-validation (ACM DNS validation, UC-6) vs
   CNAME-for-routing (a routing alias, UC-7); a leaked static AWS key vs OIDC's short-lived,
   `sub`-scoped credentials (ties directly to UC-10); and what breaks first at ~1000 organizations'
   scale (Aurora Serverless v2's single-cluster/no-sharding limit, Lambda concurrency limits,
   CloudFront Free plan's fixed rule/usage ceiling — named explicitly, not vaguely).
2. The document explicitly and unambiguously states, at least once, that this repository uses Lambda +
   Aurora Serverless v2 rather than the lab's canonical ECS/ALB pattern (FR-28) and does not imply
   otherwise anywhere else.
3. The document lists the manual, out-of-automation submission artefacts: a screenshot of the deployed
   frontend listing meetings, the live HTTPS URLs of `app.<domain>`/`api.<domain>`, and the fork's repo
   link — explicitly noted as manual, not produced by CI (FR-29).
4. The document includes a cost/teardown note referencing `make aws-destroy` (extended per UC-12) as
   the way to stop Aurora/CloudFront/API-Gateway charges after grading (FR-30).
5. Reviewer reads the document and, spot-checking a handful of citations against the actual repo files
   named, confirms each cited fact matches reality.

**Postconditions**: `docs/lab2-discussion.md` exists, answers every FR-27 topic with grounded
citations, and satisfies acceptance criterion #14.

### Alternative Flows
- **UC-15-A1: reviewer requests a citation be re-verified** — writer re-opens the named file/line and
  either confirms it or corrects the discussion document; this is the intended check-and-correct loop,
  not a failure of the process.

### Error Flows
- **UC-15-E1: a claim in the document cannot be verified against any repo file** (e.g. a guessed
  number of ACUs, a guessed base image tag not actually pinned in the Dockerfile) — per NFR-7, the
  figure MUST be omitted or explicitly flagged as unverified rather than stated as fact; a reviewer
  finding an unverifiable, unflagged claim treats it as a documentation defect.
- **UC-15-E2: the document omits or contradicts the required Lambda-vs-ECS/ALB acknowledgement**
  (FR-28) — e.g. implying the app runs on ECS somewhere else in the document — this is an explicit
  acceptance-criterion failure (#14) and must be corrected before submission.
- **UC-15-E3: the document fails to note that the submission artefacts (screenshot, live URLs, repo
  link) are manual steps** — violates FR-29; a reviewer reading the document should not conclude those
  artefacts are produced automatically by the CI pipeline.

### Edge Cases
- **UC-15-X1**: the discussion document is written before the custom-domain/OIDC infrastructure is
  fully live (e.g. drafted against the current Lambda-function-URL/CloudFront-domain-only state) — any
  claims about `api.<domain>`/`app.<domain>` specifically must be updated once UC-6/UC-7 are actually
  deployed, since NFR-7 requires describing the repo "as it is at time of writing," not aspirationally.

### Data Requirements
- **Input**: `compose.yaml`, `back/Dockerfile.lambda`, `back/Dockerfile` (if present),
  `front/Dockerfile` (if present), `back/app/main.py`, `Makefile`, `infra/*.yaml`, the live deployed
  state from UC-6/UC-7/UC-9.
- **Output**: `docs/lab2-discussion.md`.
- **Side Effects**: none (documentation only).
