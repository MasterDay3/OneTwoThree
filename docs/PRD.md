# Product Requirements Document — Meetings App (OneTwoThree fork)

This PRD documents feature requirements for the `MasterDay3/OneTwoThree` fork of the course
repository `dobosevych/OneTwoThree` (product name in the course materials: "Spry"). Sections are
numbered and added incrementally; each section states scope, requirements, and acceptance criteria
before any implementation starts.

**Repository facts assumed by every section below** (see `README.md`, `Makefile`, `compose.yaml`,
`infra/*.yaml`, `.github/workflows/code-style.yml`):
- Monorepo: `back/` (FastAPI + SQLAlchemy 2 + Alembic, Python 3.12, `uv`), `front/` (React 19 + Vite +
  TypeScript + Tailwind 4 + shadcn/ui, Node 24), `compose.yaml` (services `db`, `backend`, `frontend`),
  `infra/` (CloudFormation: `cognito.yaml`, `backend-ecr.yaml`, `backend.yaml`, `frontend.yaml`).
- AWS deploy target: backend on **Lambda (container image) + Aurora Serverless v2**, behind a public
  **Lambda function URL** (no ALB/ECS); frontend on **S3 + CloudFront** (flat-rate Free plan + WAF).
  Region is fixed to `us-east-1`.
- `Makefile`'s `aws-check` target itself already reads AWS credentials from the standard SDK
  credential chain (it just runs `aws sts get-caller-identity`), but `Makefile` also does
  `-include .env` plus `export $(shell sed ... .env)`, and ships a `.env:` rule that GNU make runs
  as an implicit prerequisite of **any** target — including `make help` or a bare `make` in a fresh
  CI checkout — which copies `.env.example` into `.env`. `.env.example` currently sets
  `AWS_ACCESS_KEY_ID=`/`AWS_SECRET_ACCESS_KEY=` (empty but present), so `-include .env` +
  `export` then blank out any `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` already present in the
  process environment (e.g. ones written by `aws-actions/configure-aws-credentials` in CI), while
  leaving an orphaned `AWS_SESSION_TOKEN` — this is the actual credential-clobbering bug this
  feature must fix (see FR-19). `FRONTEND_DOMAIN` currently defaults to the lecturer's
  `onetwothree.dobosevych.com`.
- CI today is `.github/workflows/code-style.yml`: lint-only (ruff, eslint/prettier/tsc, cfn-lint), on
  push to `main` and on PRs. There is no deploy workflow yet.

**HARD CONSTRAINT (applies to every section):** all work stays inside the fork
`MasterDay3/OneTwoThree`. Nothing in this PRD may modify, push to, or depend on write access to the
upstream teacher repository `dobosevych/OneTwoThree`.

---

## 1. Lab 2 — Documented Repository Contract, Custom-Domain HTTPS Deploy, and OIDC-Based CI/CD

### 1.1 Feature description

Lab 2 closes the gaps between the current repo state and the course's Lab 2 assignment: (a) a
written specification of the monorepo's structure and contracts (`PROJECT.md`), (b) HTTPS access to
both frontend and backend on a student-owned custom domain instead of the lecturer's domain or raw
AWS service URLs — the backend via a new, dedicated `infra/backend-domain.yaml` API Gateway HTTP API
front door, decided at architecture review — (c) a `make deploy-frontend` / `make deploy-backend`
Makefile contract, restricted to code/asset rollout only (never `aws cloudformation deploy`), that CI
can call verbatim, (d) keyless CI/CD authentication to AWS via GitHub OIDC scoped to this fork's
`main` branch with a least-privilege, itemized IAM policy, (e) a fix to the `Makefile`'s `.env`
auto-inclusion behavior that today silently blanks environment-supplied (OIDC) AWS credentials, (f) a
GitHub Actions workflow that runs lint/tests then deploys on push to `main`, and (g) a written
discussion document (`docs/lab2-discussion.md`), written last against the as-built system, answering
the course's class-discussion questions against this repo's actual files. No application feature,
endpoint, or database schema changes. Backend infrastructure stays on Lambda + Aurora Serverless v2
(not ECS/ALB) — this is a deliberate, recorded decision, not a gap to close.

**Architecture review disposition:** an earlier version of this section failed architecture review.
The findings (a real `.env`-driven credential-clobbering bug distinct from `aws-check`; an unsafe
`deploy-backend`/`deploy-frontend` design that would have run CloudFormation stack updates, including
an unwanted Cognito Google-IdP-disabling update, from CI; an incorrect rollback mechanism given
mutable ECR tags; underspecified IAM; and an unspecified backend custom-domain design) are
incorporated throughout this section; see the specific FRs called out below for each fix.

### 1.2 User stories

- **As the student (repo owner/deployer)**, I want a single documented, idempotent `make` command
  per side (`make deploy-backend`, `make deploy-frontend`) so that I can redeploy manually or let CI
  do it, without maintaining a second "CI-only" deploy path.
- **As the student**, I want my own domain (not the lecturer's `onetwothree.dobosevych.com`) serving
  both `app.<domain>` and `api.<domain>` over HTTPS, so the submission reflects my own infrastructure.
- **As a lecturer/reviewer**, I want `PROJECT.md` to describe the repository exactly as it exists
  (directories, compose services, ports, health checks, the `GET/POST /api/meetings` contract, pinned
  versions) so I can verify the student understands the system rather than having invented one.
- **As a lecturer/reviewer**, I want to see a red CI pipeline on a lint failure and a green one after
  the fix, proving the pipeline actually gates deploys rather than always deploying.
- **As CI (GitHub Actions)**, I want to assume a short-lived AWS role via OIDC — scoped to this fork's
  `main` branch only — so no long-lived AWS access keys are stored as GitHub secrets.

### 1.3 Functional requirements

**PROJECT.md (repo root)**
- FR-1. `PROJECT.md` MUST document, for the repository as it exists today (not an idealized or
  future version): every top-level folder (`back/`, `front/`, `infra/`, `.github/`, `docs/`) and its
  purpose; the purpose of each subfolder called out in `README.md`'s Layout section
  (`back/app/routers`, `back/app/services`, `back/alembic`, `front/src/pages`,
  `front/src/components`, `front/src/lib`, etc.).
- FR-2. `PROJECT.md` MUST document each `compose.yaml` service (`db`, `backend`, `frontend`): image
  or build context, published port, `depends_on` target and condition (`service_healthy`), and what
  its own healthcheck tests (`pg_isready` for `db`; `GET /api/health` via `urllib.request` for
  `backend`; `frontend` has no healthcheck of its own but depends on `backend`'s).
- FR-3. `PROJECT.md` MUST state a concrete API contract for `GET /api/meetings` and
  `POST /api/meetings`: request/response field names and types, the ISO 8601-with-timezone format of
  `starts_at`/`ends_at`, required fields (title, `starts_at`, `ends_at` with end after start, at least
  one of `call_link`/`place`), the `Authorization: Bearer <Cognito ID token>` header, and status codes
  (200 for GET, 201 for POST success, 401 for a missing/invalid token, 422 for validation failure —
  values MUST be verified against `back/app/routers/` and `back/app/schemas.py` before being written
  down, not assumed).
- FR-4. `PROJECT.md` MUST list pinned versions read from the repo's own files: Postgres image
  (`postgres:16-alpine`), Python (`>=3.12`, CI uses `3.12`), Node (CI uses `24`), and key libraries
  from `back/pyproject.toml` (fastapi, sqlalchemy, alembic, mangum, pyjwt, psycopg) and
  `front/package.json` (react, vite, typescript, tailwindcss, @tanstack/react-query) — copy the exact
  version constraints, do not invent numbers.
- FR-5. `PROJECT.md` MUST record the monorepo decision in writing: atomic cross-cutting changes
  (e.g. a schema field touching `back/app/models.py`, `back/app/schemas.py`, and a `front/src/lib`
  type in one commit) and the "the repository is the context window" argument (a reviewer or an LLM
  agent can see backend and frontend contracts in one checkout); the trade-off is stated explicitly:
  a repo-per-service boundary buys deploy/ownership independence at the cost of that shared context.

**Custom-domain HTTPS**
- FR-6. The frontend MUST be served over HTTPS at `app.<domain>` using the existing
  `infra/frontend.yaml` custom-domain support (`DomainName`/`CertificateArn`/`HostedZoneId`
  parameters, `cert.sh`), with `<domain>` supplied by the student, never defaulting to
  `onetwothree.dobosevych.com`.
- FR-7. The backend MUST be reachable over HTTPS at a custom domain `api.<domain>`. Since a Lambda
  function URL cannot take a custom domain or a regional ACM certificate directly, a front door is
  required in front of the existing `BackendFunction`. **Decided design** (post architecture review,
  not a candidate): a new, separate CloudFormation stack `infra/backend-domain.yaml`
  (`$(PROJECT)-backend-domain`), additive to `infra/backend.yaml` and never merged into it, containing:
  - An `AWS::ApiGatewayV2::Api` HTTP API with `DisableExecuteApiEndpoint: true` (the API's own
    auto-generated `*.execute-api.*.amazonaws.com` URL is not a supported entry point once the custom
    domain exists) and an `AWS_PROXY` integration to `BackendFunction` at `PayloadFormatVersion: "2.0"`
    with `TimeoutInMillis: 30000` (API Gateway hard-caps integration timeout at 30 s regardless of the
    Lambda's own `Timeout`; the discussion doc, FR-27, must call this out as a real constraint on the
    Aurora-resume cold path).
  - A single `$default` route to that integration and a `$default` stage with `AutoDeploy: true` and a
    throttling configuration (burst/rate limits) to bound cost/abuse.
  - An `AWS::Lambda::Permission` for `apigateway.amazonaws.com` whose `SourceArn` is scoped to this
    specific API's execution ARN (not a wildcard across all API Gateways/functions).
  - An `AWS::ApiGatewayV2::DomainName` for `api.<domain>` with `EndpointType: REGIONAL` and
    `SecurityPolicy: TLS_1_2`, using a **regional** ACM certificate for `api.<domain>` requested via the
    existing, already-generic `infra/scripts/cert.sh` (no backend-specific fork of the script), DNS
    validated with a Route 53 CNAME, followed by an `AWS::ApiGatewayV2::ApiMapping` with an empty
    `ApiMappingKey` (so paths are served at the domain root, not under a stage prefix).
  - A Route 53 alias `A` record for `api.<domain>` to the API Gateway domain's regional domain name,
    conditional on a `HostedZoneId` parameter being supplied (mirrors `infra/frontend.yaml`'s
    `CreateDnsRecords` condition pattern).
  - The stack's `ApiUrl` output MUST be `https://api.<domain>/` (**with a trailing slash**, matching
    the shape of `infra/backend.yaml`'s existing `ApiUrl` output). This is not cosmetic: the
    `Makefile`'s `aws-backend-health` target concatenates `$(API_URL)api/health` and the `ApiDocsUrl`
    output concatenates `${BackendFunctionUrl.FunctionUrl}api/docs` directly onto the base URL with no
    separator, and `front/src/lib/api.ts`'s API base-URL handling strips trailing slashes before
    joining paths — both call sites depend on the base URL consistently ending in `/`, so a
    `backend-domain` output missing the trailing slash would silently produce a malformed URL like
    `https://api.<domain>api/health`.
  - `infra/backend-domain.yaml` MUST take the backend Lambda's function name as an ordinary
    `Parameters:` input (passed by the `Makefile` the same way it already passes `ProjectName` /
    `ImageUri` to other stacks), not via CloudFormation `Export`/`Fn::ImportValue` cross-stack
    references — this keeps `backend-domain` independently deployable/deletable from `backend` without
    export-in-use delete-ordering constraints.
  - The HTTP API MUST NOT declare a `CorsConfiguration`: `back/app/main.py`'s `CORSMiddleware` already
    handles CORS, including preflight `OPTIONS` requests, end-to-end; a second CORS layer at the
    gateway would risk conflicting or duplicate headers.
  - The existing `BackendFunctionUrl` (`infra/backend.yaml`) MUST remain deployed and reachable as a
    documented **second, fallback entry point** (useful for debugging without the custom domain in the
    loop) — it is not replaced or removed by this feature.
  - New `Makefile` targets: `aws-backend-cert` (request/reuse the regional ACM cert for
    `BACKEND_DOMAIN` via `cert.sh`), `aws-backend-https` (wait for issuance, deploy
    `infra/backend-domain.yaml`, then **re-run `aws-frontend-publish`**, since `VITE_API_URL` is
    baked in at build time and must be rebuilt against the new domain), and
    `aws-backend-https-check` (curl the domain), mirroring the existing `aws-frontend-cert` /
    `aws-frontend-https` / `aws-frontend-https-check` pattern. All three MUST no-op with a clear error
    if `BACKEND_DOMAIN`/`DOMAIN` is empty (see FR-8).
  - `make aws-destroy` MUST delete the `backend-domain` stack **before** the `backend` stack (the
    API Gateway integration and Lambda permission reference `BackendFunction`, so it must go first).
- FR-8. A single `DOMAIN` Makefile/env variable (default **empty**) MUST drive both subdomains, using
  Make's conditional-expansion form so an empty `DOMAIN` yields a truly empty value (not a dangling
  `app.`/`api.` prefix): `FRONTEND_DOMAIN ?= $(if $(DOMAIN),app.$(DOMAIN))` and
  `BACKEND_DOMAIN ?= $(if $(DOMAIN),api.$(DOMAIN))` — a plain `FRONTEND_DOMAIN ?= app.$(DOMAIN)` is
  explicitly WRONG here because with `DOMAIN` empty it would still expand to the literal string `app.`.
  Each subdomain variable remains individually overridable, and each only takes effect when `DOMAIN`
  (or the individual override) is non-empty. The current `FRONTEND_DOMAIN ?= onetwothree.dobosevych.com`
  default MUST be removed — with `DOMAIN` unset, the site serves on its `*.cloudfront.net` domain only
  (existing no-custom-domain behavior), never the lecturer's domain. `.env.example` MUST document
  `DOMAIN=` (empty, commented or blank) in place of any lecturer-domain default. The DNS zone for
  `<domain>` is assumed to already exist as a Route 53 public hosted zone in the same AWS account (per
  user decision) — the same "zone found automatically" behavior `cert.sh` already implements for the
  frontend domain MUST extend, generically (not backend-specific), to the backend domain's certificate
  validation and alias record (FR-7). Every cert/HTTPS Makefile target (`aws-frontend-cert`,
  `aws-frontend-https`, `aws-backend-cert`, `aws-backend-https`) MUST fail fast with a clear message if
  its domain variable is empty, rather than silently deploying with an empty `DomainName` parameter.
  `aws-frontend-stack` MUST NOT detach an already-attached custom domain when `DOMAIN`/`FRONTEND_DOMAIN`
  is empty on a later run (e.g. because `.env` was reset) — it MUST use CloudFormation
  `UsePreviousValue` semantics for the domain-related parameters when no override is supplied, so a
  plain re-deploy never silently un-does a previously attached domain. Because the normal path never
  detaches a domain, deliberately removing a previously attached custom domain MUST be a documented
  manual escape hatch, not something a routine `make` invocation can do by accident — e.g. an explicit
  `DETACH_DOMAIN=1` flag recognized by `aws-frontend-stack`/`aws-backend-https` that forces empty
  `DomainName`/`CertificateArn`/`HostedZoneId` values instead of `UsePreviousValue`, or running
  `aws cloudformation deploy` by hand with those parameters explicitly set to empty strings.
- FR-9. The backend's `CorsOrigins` parameter (`infra/backend.yaml`) MUST include
  `https://app.<domain>` once deployed, and the Cognito stack's `CallbackUrls`/`LogoutUrls`
  (`infra/cognito.yaml`, wired via `COGNITO_CALLBACK_URLS`/`COGNITO_LOGOUT_URLS` in the `Makefile`)
  MUST include `https://app.<domain>/auth/callback` and `https://app.<domain>/` respectively — mirroring
  the existing `aws-frontend-cors` target's behavior for the CloudFront domain. This origin list is
  derived from the frontend stack's `SiteOrigins` output exactly as `aws-frontend-cors` already does
  today; this feature does not change that derivation.
- FR-10. The frontend production build MUST set `VITE_API_URL` from the **backend stack outputs**:
  both `infra/backend-domain.yaml` and the existing `infra/backend.yaml` expose an output named
  `ApiUrl` (note: `infra/backend.yaml`'s `ApiUrl` output already exists today and is the Lambda
  function URL — it is not the `BackendFunctionUrl` *resource* name, which is a different, internal
  identifier), and the `Makefile` resolves
  `$(or $(call backend_domain_output,ApiUrl),$(call backend_output,ApiUrl))` — i.e. it prefers the
  `backend-domain` stack's `ApiUrl` when that stack is deployed, and falls back to `backend.yaml`'s
  `ApiUrl` (the function URL) when it is not. `VITE_API_URL` is never computed client-side from
  `BACKEND_DOMAIN`. This means `deploy-frontend`/`aws-frontend-publish` and CI never need to know
  `DOMAIN`/`BACKEND_DOMAIN` at all; they only need the already-existing `stack_output` lookup mechanism
  the `Makefile` uses today.

**Makefile deploy contract**

CI never runs any `*-stack` target (those apply CloudFormation changes — new resources, parameter
changes, IAM capability changes — and must be reviewed/applied manually from a laptop, still gated by
`cfn-lint` in CI per the existing `infra` job). `deploy-backend`/`deploy-frontend` are strictly
code/asset rollout onto **already-existing** infrastructure.

- FR-11. `make deploy-frontend` MUST perform, in one invocation: `aws-check`, then
  `aws-frontend-publish` (build the SPA with the correct `VITE_API_URL`, from stack outputs per FR-10,
  and Cognito build args; upload; invalidate CloudFront) — and nothing else. It MUST NOT depend on or
  invoke `aws-frontend-stack` or `aws-frontend-cors` (those run `aws cloudformation deploy`, which in
  CI, with an empty `GoogleClientId`, would silently redeploy the Cognito stack and could disable/delete
  the Google identity provider — see R2). `aws-frontend-publish`'s upload MUST use a safe four-step
  order to avoid a window where the SPA references chunks that don't exist yet, or where old chunks are
  deleted while the still-live old `index.html` still references them: (1) `aws s3 sync` the new
  content-hashed asset files (everything except `index.html`) with long-lived cache headers, **without**
  `--delete`, so old chunks stay available while the old `index.html` is still being served; (2) upload
  the new `index.html` with `no-cache`, flipping traffic to the new build; (3) only now run a second
  `aws s3 sync --delete` (excluding `index.html`) to remove chunks that are no longer referenced by the
  new build; (4) invalidate CloudFront. Today's single `aws s3 sync --delete` pass is exactly the bug
  this replaces: it removes old chunks while the old `index.html` (cached at the edge until invalidated)
  is still live, breaking any client mid-load. `make deploy-frontend` MUST exist as a named target and
  MUST be the only target CI invokes for the frontend.
- FR-12. `make deploy-backend` MUST perform, in one invocation: `aws-check`, `aws-backend-push`
  (build and push the image to ECR tagged `$(TAG)`, per FR-13), a new `aws-backend-update-code` target
  (`aws lambda update-function-code --function-name $(BACKEND_FUNCTION) --image-uri
  $(ECR_URI):$(TAG)`, followed by `aws lambda wait function-updated-v2` before proceeding), then
  `aws-backend-migrate`. It MUST NOT depend on or invoke `aws-backend-stack` (that runs
  `aws cloudformation deploy` against the backend stack's VPC/Aurora/Lambda configuration, which is a
  manual/laptop operation per this section's contract). `make deploy-backend` MUST exist as a named
  target and MUST be the only target CI invokes for the backend.
- FR-13. The image tag pushed to ECR MUST be the git commit SHA of the commit being deployed, never
  `latest`. The `Makefile`'s `TAG` default MUST be: on a clean working tree, the **full**
  `git rev-parse HEAD` (40 hex chars); on a dirty working tree,
  `<full-sha>-dirty-<unix-or-datetime-timestamp>` — the timestamp suffix is required (not just
  `-dirty`) so that two manual `make aws-backend-deploy` runs from the same dirty tree still produce
  distinct, unique tags, which is what forces CloudFormation/Lambda to see a changed `ImageUri` and
  actually pick up the new push (an unchanged tag string on a `MUTABLE` ECR repo can otherwise leave a
  stack update believing nothing changed). This replaces today's
  `git describe --always --dirty=-dirty-$$(date +...)` default only insofar as the **clean** case must
  resolve to the full SHA rather than `git describe`'s abbreviated/tag-relative form. CI MUST pass
  `TAG=${{ github.sha }}` explicitly (always a clean-tree full SHA, since CI always deploys a committed
  ref) so a locally-reproduced deploy and CI's deploy of the same commit always produce byte-identical
  tags.
- FR-14. Rollback is a **distinct, explicit** operation, not a re-run of `deploy-backend` with an old
  `TAG` (ECR tags in `infra/backend-ecr.yaml` are `MUTABLE`, so re-running the full build under an old
  tag would push the *current* checked-out code under that old tag, silently corrupting it — the
  original PRD's `deploy-backend TAG=<old-sha>` rollback description was wrong and is retracted). A new
  target `make aws-backend-rollback TAG=<sha>` MUST: (1) verify the image tag already exists in ECR via
  `aws ecr describe-images` and fail loudly if it does not, (2) run
  `aws lambda update-function-code --image-uri $(ECR_URI):$(TAG)` + `aws lambda wait
  function-updated-v2`, and (3) explicitly **not** run any migration and **not** rebuild/push anything.
  Because `infra/backend-ecr.yaml`'s lifecycle policy keeps only the last 5 images, the practical
  rollback window is approximately the last 5 deploys — this MUST be documented in
  `docs/lab2-discussion.md` (FR-27) and in the Makefile target's help text. Rolling back the Lambda's
  code does **not** revert any database schema change made by a migration that shipped with the code
  being rolled back — this MUST also be documented; schema rollback is out of scope for this target.
- FR-15. `deploy-frontend`, `deploy-backend`, and `aws-backend-rollback` MUST NOT require any secret or
  credential recipe beyond what CI's OIDC-assumed role already provides in the environment (no separate
  "CI secrets" path different from local developer credentials besides how the credentials are
  obtained).
- FR-31 (extends this group). When `aws-backend-stack` is run with `KEEP_IMAGE=1` (its existing use for
  CORS-only/Cognito-only updates via `aws-frontend-cors`, and for domain-related stack updates), it MUST
  first read the function's **currently running** image via
  `aws lambda get-function --function-name $(BACKEND_FUNCTION) --query Code.ImageUri` and pass that
  value as the `ImageUri` parameter to `aws cloudformation deploy`, instead of omitting the parameter
  (which would let CloudFormation fall back to whatever `ImageUri` default/last-used-value it has
  internally and risk rolling the function back to a stale image on an unrelated parameter-only
  update).

**GitHub OIDC and IAM**
- FR-16. A new CloudFormation template `infra/github-oidc.yaml`, deployed by a new Makefile target
  `make aws-oidc-deploy` (a manual/laptop target, run **after** the frontend and backend stacks already
  exist, since its IAM policy needs to know their bucket/distribution/function names — see FR-18), MUST
  create the GitHub OIDC identity provider (`token.actions.githubusercontent.com`, audience
  `sts.amazonaws.com`) and a deploy IAM role assumable by that provider. Because creating an
  `AWS::IAM::Role` with an inline/managed policy requires `CAPABILITY_NAMED_IAM` (a fixed `RoleName` is
  used, per below), `aws-oidc-deploy` MUST pass `--capabilities CAPABILITY_NAMED_IAM` to
  `aws cloudformation deploy`. Because an AWS account can only have one OIDC provider per URL, the
  template MUST accept an `ExistingOidcProviderArn` parameter (default empty), which the `Makefile`
  MUST auto-fill by querying `aws iam list-open-id-connect-providers` for an existing
  `token.actions.githubusercontent.com` provider before invoking the deploy, and conditionally create
  the `AWS::IAM::OIDCProvider` only when none was found (referencing the discovered ARN otherwise), so
  a re-run against an account that already has the GitHub provider (e.g. from another project) does not
  fail. The provider resource MUST have `DeletionPolicy: Retain` (deleting the stack must not remove a
  provider potentially shared by other roles/projects). The deploy role MUST have a fixed, predictable
  `RoleName: $(PROJECT)-github-deploy` (not an auto-generated name), so the role ARN is stable across
  stack recreations for the Makefile/README/CI configuration to reference. The template MUST take the
  frontend bucket name and CloudFront distribution ID as explicit `Parameters:` (resolved by the
  `Makefile` from the frontend stack's outputs, the same way other stacks already consume
  `stack_output`), not `Fn::ImportValue`, so the OIDC/role stack has no export-based coupling to the
  frontend stack's lifecycle. Because the policy is scoped to these concrete resource identifiers,
  `aws-oidc-deploy` MUST be re-run whenever the frontend stack (bucket/distribution) or the backend
  stack (function) is deleted and recreated with new physical IDs — this MUST be documented in
  `docs/lab2-discussion.md` or the target's own help text.
- FR-17. The deploy role's trust policy MUST restrict the `token.actions.githubusercontent.com:sub`
  claim with `StringEquals` (not `StringLike`/wildcard) to exactly
  `repo:MasterDay3/OneTwoThree:ref:refs/heads/main` (owner and repo name compared case-sensitively —
  the fork's actual casing MUST be used verbatim), and restrict
  `token.actions.githubusercontent.com:aud` with `StringEquals` to `sts.amazonaws.com`. No other
  branch, PR ref, or fork MAY assume the role. The deploy job in the GitHub Actions workflow MUST NOT
  declare a job-level `environment:` (a GitHub Environment changes the token's `sub` claim to
  `repo:...:environment:<name>`, which would no longer match this trust condition), and the repository
  MUST NOT configure a custom OIDC subject claim template that would alter the default `sub` shape
  assumed here.
- FR-18. The deploy role's permission policy MUST be least-privilege for exactly what
  `deploy-backend`/`deploy-frontend`/`aws-backend-rollback` do, itemized per service (no broader
  `service:*` grants except where noted):
  - ECR, scoped to `repository/$(PROJECT)-backend`: `BatchCheckLayerAvailability`,
    `InitiateLayerUpload`, `UploadLayerPart`, `CompleteLayerUpload`, `PutImage`, `BatchGetImage`,
    `GetDownloadUrlForLayer`, `DescribeImages`. Plus `ecr:GetAuthorizationToken` on `Resource: "*"` —
    this action does not support resource-level scoping in IAM, so it is a **documented, explicit
    exception** to NFR-2, not an oversight.
  - Lambda, scoped to `function:$(PROJECT)-backend`: exactly `UpdateFunctionCode`, `GetFunction`,
    `GetFunctionConfiguration`, and `InvokeFunction` (required by `aws-backend-migrate`'s
    `aws lambda invoke --payload '{"action":"migrate"}'` call). `lambda:InvokeFunctionUrl` is
    explicitly EXCLUDED — no deploy-contract target calls the function URL directly, so this role has
    no need for it.
  - S3, scoped to the frontend bucket: `s3:ListBucket` on the bucket ARN, `s3:PutObject` and
    `s3:DeleteObject` on `<bucket-arn>/*`. The bucket name is not hardcoded in the policy; it is
    resolved from the frontend stack's `BucketName` output the same way the `Makefile` already does,
    and passed as a template parameter.
  - `cloudfront:CreateInvalidation` scoped to the single distribution's ARN.
  - `cloudformation:DescribeStacks` (read-only, for `stack_output` lookups), scoped to
    `stack/$(PROJECT)-backend-ecr/*`, `stack/$(PROJECT)-backend/*`, `stack/$(PROJECT)-backend-domain/*`,
    `stack/$(PROJECT)-frontend/*`, and `stack/$(PROJECT)-cognito/*`.
  - The role MUST NOT be able to create, update, or delete any CloudFormation stack
    (no `cloudformation:CreateStack`/`UpdateStack`/`DeleteStack`/`ExecuteChangeSet`/etc.), and MUST
    have **no** IAM, Cognito, ACM, or Route 53 permissions of any kind — those are exercised only by
    the manual/laptop `*-stack`, `*-cert`, `*-https` targets, never by CI.
- FR-19. **The Makefile's `.env` auto-inclusion is the actual credential-clobbering defect and MUST be
  fixed as follows** (see the repository-facts note above for the root cause):
  (a) `.env.example` MUST have its `AWS_ACCESS_KEY_ID=`/`AWS_SECRET_ACCESS_KEY=` lines commented out
  (e.g. `# AWS_ACCESS_KEY_ID=` / `# AWS_SECRET_ACCESS_KEY=`) so a freshly copied `.env` does not define
  them as empty strings at all.
  (b) Independent of comment state, both the `make`-level `-include .env` / `export $(shell ...)`
  mechanism and the shell-level `LOAD_ENV` (used inside `$(shell ...)` calls) MUST NOT override
  `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`, or `AWS_PROFILE` when any of them
  is already non-empty in the invoking process's environment — i.e. environment-supplied OIDC
  credentials always win over whatever `.env` contains, empty or not.
  (c) `.env` MUST NOT be implicitly created as a side effect of `-include .env` / a bare
  `make`/`make help` run. Two things must both hold, since GNU make will remake **any** included file
  that has a matching rule — even under `-include` — the moment that file doesn't exist yet, regardless
  of which target was actually requested: first, the `.env:` rule (or equivalent) MUST only fire as an
  explicit prerequisite of targets that actually need it for local Docker Compose use — `start` and
  `test-back` (`up` already depends on `start`, so it is covered transitively, not as a separate case) —
  never as a global implicit dependency reachable from every target; second, the include directive
  itself MUST be guarded so make does not attempt to remake `.env` just because `-include .env` names
  it (e.g. `-include $(wildcard .env)`, which expands to nothing — and so triggers no implicit rule
  search — when `.env` does not exist, instead of `-include .env`, which still lets make match `.env`
  against the `.env:` rule and build it). Both fixes are required together: guarding the include alone
  does not stop `start`/`test-back` from being able to create `.env` on purpose, and restricting the
  `.env:` rule's prerequisite-reachability alone does not stop `-include .env` itself from independently
  triggering a rebuild.
  (d) The GitHub Actions deploy workflow MUST assert `.env` does not exist immediately before the
  deploy steps (e.g. `test ! -f .env` as a workflow step) and fail the job if it does, as a defense-in-
  depth check that a fresh checkout never silently picks up stray local credentials.
  Beyond this fix, `aws-check` and every `aws-*`/`deploy-*` target already work with credentials from
  the standard AWS SDK credential chain (env vars set by `aws-actions/configure-aws-credentials@v4`, or
  a local AWS profile) once (a)-(c) stop `.env` from interfering; local developer use of static keys in
  `.env` MAY remain supported as a fallback for manual/local deploys.
- FR-20. No long-lived AWS access keys may be stored in GitHub Actions secrets or committed anywhere
  in the repository as part of this feature. The deploy role's ARN is supplied to the workflow via the
  repository variable `vars.AWS_DEPLOY_ROLE_ARN` (a plain, non-secret configuration value — it is not
  sensitive, since only the account's trust policy, not the ARN's secrecy, gates who can assume it).

**CI/CD workflow**
- FR-21. `.github/workflows/code-style.yml` MUST be changed so its lint/format/typecheck jobs are
  reusable: add a `workflow_call:` trigger and **drop** its current `push: branches: [main]` trigger
  (keeping `pull_request:` as-is, so PRs are still linted directly). A new
  `.github/workflows/deploy.yml` MUST trigger on `push` to `main` only, and its first job MUST be
  `uses: ./.github/workflows/code-style.yml` (a `workflow_call` invocation), not a duplicated copy of
  the lint steps. This avoids the original design's flaw: if `code-style.yml` kept its own
  `push: branches: [main]` trigger, its independent `concurrency: group: code-style-${{ github.ref }}`
  would race and can cancel-in-progress against the deploy workflow's own run on the same ref.
- FR-22. Before any deploy step, `deploy.yml` MUST run tests without going through `make test` (which
  would trigger the `.env` auto-creation described in FR-19's pre-fix behavior and is unnecessary
  overhead in CI): backend tests run against a `postgres:16-alpine` GitHub Actions service container,
  with `TEST_DATABASE_URL` pointed at it and `uv run pytest` invoked directly in `back/`; frontend tests
  run via `npm ci && npm test` directly in `front/`. The reused `code-style.yml` job (FR-21) plus these
  test jobs MUST all pass before any deploy job starts.
- FR-23. The deploy job MUST configure AWS credentials using `aws-actions/configure-aws-credentials@v4`
  with `role-to-assume` set to `vars.AWS_DEPLOY_ROLE_ARN` (FR-20). Workflow-level `permissions:` MUST be
  `contents: read` only; `id-token: write` MUST be declared **only** on the deploy job, not at the
  workflow's top level, so the lint/test jobs never receive an OIDC token. The deploy job MUST NOT
  declare a job-level `environment:` (FR-17). The workflow MUST NOT declare a `workflow_dispatch`
  trigger (push-to-main is the only path that can deploy). All third-party actions used anywhere in
  `deploy.yml`, including `aws-actions/configure-aws-credentials` (its `v4` release), MUST be pinned by
  full commit SHA rather than a mutable tag reference (e.g. `uses: aws-actions/configure-aws-credentials@<40-char-sha>
  # v4.x.x`, not `@v4`), and the `checkout` step MUST set `persist-credentials: false`.
- FR-24. After assuming credentials, the workflow MUST run `make deploy-backend` then
  `make deploy-frontend` (backend before frontend, since the frontend build needs the backend's
  URL/domain from stack outputs per FR-10, matching the existing `aws-deploy` ordering). It MUST also
  assert `.env` is absent immediately before these steps (FR-19d).
- FR-25. `deploy.yml` MUST declare its `concurrency` group at the **workflow level** (not per-job):
  `group: deploy-${{ github.ref }}`, `cancel-in-progress: false`, so two deploys triggered in quick
  succession queue rather than run concurrently against the same Lambda/bucket, and neither cancels an
  in-progress deploy mid-way (which could leave the Lambda code and DB migration state inconsistent).
  A second push while a deploy is running is expected to show as a **queued** run that GitHub reports
  as `cancelled` if a third push supersedes it before it starts (GitHub keeps only one pending run per
  concurrency group) — this is expected behavior for `cancel-in-progress: false`, not a defect.
- FR-26. It MUST be possible to demonstrate a red pipeline: a deliberately introduced lint violation
  on a push to `main` MUST fail the reused `code-style.yml` job and block the deploy jobs from running.
- FR-32 (extends this group). The backend image build/push step in `deploy-backend` MUST produce a
  `linux/arm64` image (matching `infra/backend.yaml`'s default `Architecture: arm64`/Graviton), using
  either a native ARM64 GitHub-hosted runner (`ubuntu-24.04-arm`, available because this fork is a
  public repository) or `docker buildx` with QEMU emulation if a native ARM64 runner is not used. CI
  MUST NOT silently build and push an `x86_64` image while the Lambda function is configured for
  `arm64`.

**Discussion document**
- FR-27. `docs/lab2-discussion.md` MUST answer, grounded in this repo's real files (cite the actual
  file/line, e.g. `compose.yaml`, `back/Dockerfile.lambda`, `back/app/main.py`), at minimum:
  compose keys (`image` vs `build`, published `ports` vs `expose`, `environment`, `volumes`,
  `command`, dev-only `develop.watch`, and the exposed `DB_PORT` on `db`); readiness (the `db`
  healthcheck + `backend`'s `depends_on: service_healthy`; what happens if the DB disappears after
  startup — connection pool behavior, retries, and what `/api/health` reports); Dockerfile vs compose
  responsibilities and which survives a hypothetical move to ECS/Cloud Run; base image choices (this
  repo's `postgres:16-alpine`, and the backend/frontend Dockerfile base images — read from
  `back/Dockerfile.lambda`/`back/Dockerfile` and `front/Dockerfile` before writing conclusions);
  backend layering (`routers`/`services`/`models`/`schemas`); SQLAlchemy trade-offs; Alembic vs
  `create_all` and when migrations actually run (`alembic upgrade head` in the compose `command`
  locally; the `{"action": "migrate"}` Lambda invoke via `make aws-backend-migrate` on AWS — migrations
  do NOT run on Lambda cold start); CloudFront vs S3 responsibilities; ECR vs ECS (and that this repo
  uses ECR + Lambda, not ECS); what a health check checks (contrast the lab's canonical ALB target
  group health check against this repo's `/api/health` route and Lambda's own invocation-level
  health signal); CNAME-for-validation vs CNAME-for-routing (ACM DNS validation records vs a routing
  CNAME); a leaked static AWS key vs OIDC's short-lived, `sub`-scoped credentials; and what breaks
  first if this architecture had to serve ~1000 organizations (call out the Aurora Serverless v2
  single-cluster/no-sharding limit, Lambda concurrency limits, the CloudFront Free plan's fixed
  rule/usage ceiling, and the API Gateway HTTP API's hard 30-second integration timeout on the
  Aurora-resume cold path explicitly).
- FR-28. `docs/lab2-discussion.md` MUST explicitly and honestly state, at least once, that this
  repository uses Lambda + Aurora Serverless v2 rather than the lab's canonical ECS/ALB pattern, and
  must not imply otherwise anywhere else in the document.
- FR-29. `docs/lab2-discussion.md` MUST list the submission artefacts the student produces manually
  and out of CI/automation scope: a screenshot of the deployed frontend listing meetings, the live
  HTTPS URLs of the frontend and backend on the student's own domain, and the fork's repo link. It
  MUST note these are manual steps, not automated by this feature.
- FR-30. `docs/lab2-discussion.md` MUST include a cost/teardown note referencing `make aws-destroy`
  (extended to also tear down the `backend-domain` stack added by this feature, deleting
  `backend-domain` before `backend` per FR-7) as the way to stop incurring Aurora/CloudFront/API
  Gateway charges after grading. `aws-destroy` MUST leave the OIDC/deploy-role stack (`infra/github-oidc.yaml`)
  alone by default — it is cheap to keep (no per-hour charge) and deleting it would break CI's ability
  to deploy again without a manual `aws-oidc-deploy` re-run; a new `DESTROY_OIDC=1` flag on `aws-destroy`
  MUST additionally run a new `aws-oidc-destroy` target, **last**, after the other stacks are gone. Since
  the OIDC provider resource has `DeletionPolicy: Retain` (FR-16), even `aws-oidc-destroy` does not
  remove the underlying `token.actions.githubusercontent.com` identity provider from the account — only
  the deploy role and its policy. This MUST be documented, and MUST document the ECR-lifecycle-bounded
  rollback window and the "code rollback does not revert schema" caveat from FR-14.
- FR-33 (extends this group). `docs/lab2-discussion.md` MUST be written **last**, after the
  infrastructure, Makefile, and workflow changes in this section are actually implemented, and its
  answers MUST describe what was actually built (e.g. the real OIDC role name, the real
  `backend-domain` stack design) rather than the design as originally proposed, if the two diverge
  during implementation.
- FR-34 (documentation consistency, extends this group). Alongside `docs/lab2-discussion.md`:
  `PROJECT.md` (FR-1–FR-5) is the canonical, authoritative structure spec for the repo; `README.md`'s
  existing `## Layout` section MUST be replaced with a short pointer/link to `PROJECT.md` rather than
  duplicating (and risking drift from) the structure listing. `README.md` MUST otherwise be updated to
  match this feature: remove the static-AWS-keys framing for CI/deploy use (local-only static keys may
  still be mentioned as a fallback per FR-19), remove the `onetwothree.dobosevych.com` default-domain
  reference, update the Mermaid architecture diagram to show the new `backend-domain` API Gateway front
  door alongside the existing Lambda function URL, and state explicitly that the function URL remains
  as a documented fallback entry point (FR-7). `back/app/lambda_handler.py`'s module docstring, which
  today describes only the function-URL entry point, MUST be updated to also mention the API Gateway
  custom-domain entry point added by this feature.

### 1.4 Non-functional requirements

- NFR-1 (Security). No long-lived AWS credentials in GitHub secrets, `.env.example`, or any tracked
  file. The OIDC trust policy's `sub` condition MUST use exact-match `StringEquals` against
  `repo:MasterDay3/OneTwoThree:ref:refs/heads/main` — a wildcard or `StringLike` match is a hard
  failure of this requirement (see FR-17).
- NFR-2 (Security). Every new IAM permission MUST be scoped by ARN to the single resource it targets
  (see FR-18); no `Resource: "*"` in the deploy role's policy for services capable of ARN scoping. The
  sole documented exception is `ecr:GetAuthorizationToken`, which AWS does not support scoping by
  resource ARN at all (FR-18) — this is the only permitted `Resource: "*"` in the role.
- NFR-3 (Cost). New infrastructure added for the backend front door (API Gateway HTTP API + regional
  ACM certificate, `infra/backend-domain.yaml`) MUST stay within services that have an always-free or
  negligible-cost tier at this traffic level (HTTP APIs and ACM certificates are effectively free at
  low volume); the PRD's parent feature does not introduce a NAT gateway, ALB, or other always-on paid
  resource. `make aws-destroy` MUST tear down `backend-domain` by default (deleted before `backend`,
  FR-7), since it carries the same cost profile as the other application stacks. The OIDC/deploy-role
  stack is different: it has no meaningful per-hour cost (an IAM role and an OIDC provider reference are
  free), so `aws-destroy` MUST leave it in place by default and only remove it when `DESTROY_OIDC=1` is
  passed (FR-30) — cost is not the reason to tear it down routinely, and the OIDC provider itself is
  retained regardless (`DeletionPolicy: Retain`, FR-16).
- NFR-4 (Idempotency). Re-running `make deploy-backend`, `make deploy-frontend`, or the CI workflow
  against an already-deployed function/bucket with no code changes MUST succeed with no errors and no
  duplicate resources (`update-function-code`/`s3 sync`/`cloudfront create-invalidation` are all
  naturally idempotent no-ops on unchanged input); this no longer relies on
  `--no-fail-on-empty-changeset`, since `deploy-backend`/`deploy-frontend` do not run
  `aws cloudformation deploy` at all (FR-11, FR-12). The manual `*-stack` targets keep relying on that
  existing CloudFormation pattern.
- NFR-5 (Availability/UX). The first request to the backend after an Aurora auto-pause (documented
  today as ~15s) is an existing, accepted behavior and is out of scope to change in this feature; the
  new API Gateway front door's fixed 30-second integration timeout (FR-7) MUST NOT be silently reduced
  below the existing Lambda `Timeout` budget for that cold path — if the Lambda `Timeout` parameter is
  ever raised above 30s, requests through the custom domain would still be cut off at 30s by API
  Gateway, and this constraint MUST be documented (FR-27), not "fixed" by this feature.
- NFR-6 (Portability). Nothing in this feature may require write access to, or a change in,
  `dobosevych/OneTwoThree` (hard constraint, see top of document).
- NFR-7 (Documentation accuracy). `PROJECT.md` and `docs/lab2-discussion.md` MUST describe the repo
  as it is at time of writing (verified against source files), not an aspirational or invented state;
  any figure that cannot be verified against a file in the repo MUST be omitted or flagged rather than
  guessed. `docs/lab2-discussion.md` specifically MUST reflect the as-built system (FR-33), not the
  as-proposed design.

### 1.5 Acceptance criteria

1. `PROJECT.md` exists at repo root and covers, verifiably against the current repo tree: every
   top-level and named subfolder, every `compose.yaml` service with port/dependency/healthcheck, the
   full `GET/POST /api/meetings` field-level contract with status codes, pinned versions matching
   `back/pyproject.toml`/`front/package.json`/`compose.yaml`, and a written monorepo trade-off
   discussion (FR-1–FR-5).
2. Visiting `https://app.<student-domain>/` loads the SPA over valid HTTPS (not the lecturer's
   domain), and it successfully calls the backend at `https://api.<student-domain>` over valid HTTPS
   (FR-6–FR-10).
3. `curl -fsS https://api.<student-domain>/api/health` returns 200 (FR-7).
4. `make deploy-backend` and `make deploy-frontend` each complete successfully as standalone targets
   when run with only OIDC-derived environment credentials (no `.env` present at all), each idempotent
   on a second run, and neither invokes `aws cloudformation deploy` (verified by inspecting the
   targets' prerequisite chains: no dependency on `aws-backend-stack`, `aws-frontend-stack`, or
   `aws-frontend-cors`) (FR-11–FR-13, FR-15, NFR-4).
5. Inspecting the pushed ECR image tags after a deploy shows the **full git commit SHA** as the tag
   (matching `git rev-parse HEAD` / `${{ github.sha }}`); `latest` is not relied upon for identifying
   or rolling back a deploy (FR-13).
6. `make aws-backend-rollback TAG=<sha-of-a-previous-successful-deploy>` updates the Lambda to that
   exact previously-pushed image (verified via `aws lambda get-function --query Code.ImageUri`
   matching that tag's digest) without rebuilding and without running any migration; attempting
   `aws-backend-rollback` with a `TAG` that was never pushed (or has aged out of the ECR lifecycle
   policy's last-5 window) fails loudly instead of silently building/pushing new code under that tag
   (FR-14).
7. `aws sts assume-role-with-web-identity` (or the equivalent GitHub Actions OIDC exchange) from a
   workflow run on a branch other than `main`, from a pull request, or from any other repository
   (including a fork-of-the-fork), is denied by the trust policy (FR-17); a deploy job that adds a
   job-level `environment:` fails to assume the role, confirming the `sub`-shape sensitivity (FR-17);
   `aws iam simulate-principal-policy` or manual review confirms the only unscoped (`Resource: "*"`)
   statement in the deploy role's policy is `ecr:GetAuthorizationToken`, and the role has no
   `cloudformation:*Stack*`, IAM, Cognito, ACM, or Route 53 permissions (FR-18).
8. `git grep` for `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` literal values across the repo, plus a
   review of the repository's GitHub secrets/variables configuration (since secrets aren't grep-able),
   turns up no committed long-lived keys and confirms `AWS_DEPLOY_ROLE_ARN` is a repository **variable**
   (`vars.*`), not a secret (FR-20, NFR-1).
9. A push to `main` containing a deliberate lint error (e.g. an unused import) shows the reused
   `code-style.yml` job failing red inside `deploy.yml`'s run and the deploy jobs not running; a
   follow-up push fixing it shows a full green pipeline ending in a successful deploy (FR-21, FR-22,
   FR-26). A separate PR against `main` still triggers `code-style.yml` directly via its retained
   `pull_request:` trigger (FR-21).
10. Two pushes to `main` in quick succession show one deploy run executing and the other queued under
    the `deploy-${{ github.ref }}` concurrency group at the workflow level, never two deploy runs
    "in progress" simultaneously; a third push while both are pending/running results in the older
    queued (not the running) run being reported `cancelled`, per `cancel-in-progress: false` semantics
    (FR-25).
11. Building and pushing the backend image in CI produces a `linux/arm64` image (verified via
    `docker inspect`/ECR image manifest architecture, or by confirming the workflow's runner/buildx
    configuration), matching the Lambda's configured `Architecture: arm64` (FR-32).
12. A CI run against a fresh checkout with no local `.env` file present shows the pre-deploy `.env`-
    absence assertion step passing; manually placing a `.env` file with stale/empty
    `AWS_ACCESS_KEY_ID=` before that step causes the assertion to fail the job rather than silently
    proceeding with blanked credentials (FR-19).
13. With `DOMAIN` unset, `make aws-frontend-cert`/`aws-backend-cert`/`aws-frontend-https`/
    `aws-backend-https` each fail with a clear error instead of deploying an empty `DomainName`; with a
    custom domain already attached, a subsequent `aws-frontend-stack` run with `DOMAIN` unset (e.g.
    after a fresh `.env`) leaves the existing custom domain attached rather than detaching it (FR-8).
14. `docs/lab2-discussion.md` exists and answers every topic listed in FR-27 (including the API
    Gateway 30-second timeout point), cites real files for each claim, and contains the explicit
    Lambda-vs-ECS/ALB acknowledgement (FR-28), the manual submission artefacts + cost/teardown notes
    (FR-29–FR-30), and reflects the as-built system rather than the as-proposed design (FR-33).
15. `README.md`'s `## Layout` section is a pointer to `PROJECT.md` rather than a duplicated listing,
    contains no reference to `onetwothree.dobosevych.com` or to static AWS keys as the CI/deploy
    credential method, and its architecture diagram shows both the API Gateway custom domain and the
    Lambda function URL fallback; `back/app/lambda_handler.py`'s docstring mentions both entry points
    (FR-34).
16. On a fresh checkout, `make help` (and a bare `make`) does not create `.env` (confirmed by its
    absence on disk afterward). Separately, with a `.env` file present whose `AWS_ACCESS_KEY_ID=` line
    is empty, running `AWS_ACCESS_KEY_ID=x AWS_SECRET_ACCESS_KEY=y AWS_SESSION_TOKEN=z make aws-check`
    reports the identity resolved from `x`/`y`/`z` (verified via `aws sts get-caller-identity` reflecting
    those credentials, e.g. against a mock/test AWS endpoint or by checking which credential source the
    AWS CLI logs as used) rather than failing or silently using empty/absent credentials (FR-19).
17. After running `make deploy-backend` (or `make aws-backend-rollback`), running
    `make aws-frontend-cors` (which internally runs `aws-backend-stack KEEP_IMAGE=1`) leaves
    `aws lambda get-function --query Code.ImageUri` unchanged before and after — i.e. a CORS/Cognito-only
    stack update never reverts the Lambda to a different image than the one `deploy-backend`/
    `aws-backend-rollback` just set (FR-31).

### 1.6 Affected endpoints

- No application API endpoints are added, removed, or changed. `back/app/routers/` (`/api/meetings`,
  `/api/participants`, `/api/health`, `/api/me`) are unaffected in behavior.
- New **infrastructure** endpoint: `https://api.<domain>` (API Gateway HTTP API custom domain, proxy
  to the existing Lambda) as the new externally addressed backend origin. The existing Lambda function
  URL MUST remain deployed and reachable in parallel as a fallback (matching FR-7's "second, fallback
  entry point" requirement — not merely permitted, but required).
- New **infrastructure** endpoint: `https://app.<domain>` (CloudFront custom domain) as the primary
  frontend origin, using `infra/frontend.yaml`'s existing custom-domain capability.

### 1.7 Schema changes

None. No changes to `back/app/models.py`, `back/alembic/` migrations, or any database table/column.

### 1.8 UI changes

None in application UI/pages/components. The only front-end-adjacent change is a **build
configuration** change: the production build's `VITE_API_URL` value moves from the raw Lambda
function URL to `https://api.<domain>` once the backend custom domain is live (FR-10). No React
component, page, or route changes.

### 1.9 Out of scope

- Migrating the backend from Lambda + Aurora Serverless v2 to ECS/Fargate + ALB. This PRD explicitly
  keeps Lambda + Aurora Serverless per the recorded decision; `docs/lab2-discussion.md` (FR-27–FR-28)
  documents the ECS/ALB pattern only as a point of comparison, not as an implementation target.
- Any new application feature, endpoint, or schema change (calendar, meetings, participants, auth
  behavior are unaffected).
- Any modification to, or dependency on write access to, `dobosevych/OneTwoThree` (hard constraint).
- Capturing the manual submission artefacts (screenshot, live URLs, repo link) — these are the
  student's manual deliverables per FR-29 and are not produced by any automation in this feature.
- Multi-account, multi-region, or multi-tenant hardening implied by the "1000 orgs" discussion
  question in FR-27 — that question is answered analytically in `docs/lab2-discussion.md`, not
  implemented.
