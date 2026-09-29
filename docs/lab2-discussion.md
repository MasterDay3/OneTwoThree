# Lab 2: answers to the discussion questions

Answers are grounded in this repository's files. One deliberate difference from the lab text: the
backend runs on **Lambda + Aurora Serverless v2, not ECS/ALB**. Where a question is about ECS, ECR or
the load balancer's health check, the answer covers both what the lab describes and what this repo
does instead, and why.

## Part 1: the repository and the running app

### The compose file (`compose.yaml`)

- **`image` vs `build`.** `db` uses `image: postgres:16-alpine`: a ready-made image pulled from a
  registry. `backend` (`build: ./back`) and `frontend` (`build.context: ./front`) are built from
  our own `Dockerfile`s. Use `image` for software you run as-is; use `build` for code you write.
- **`ports` vs `expose`.** `ports: ["${BACKEND_PORT:-8000}:8000"]` publishes a container port on the
  host (`host:container`). `expose` only documents a port for other containers. On the compose
  network every service already reaches the others by name (`db:5432`), so `expose` is rarely needed.
  We use no `expose`.
- **`environment`.** Passes configuration in: `DATABASE_URL` (built from `POSTGRES_*` in `.env`),
  `CORS_ORIGINS`, `SEED`, `COGNITO_*`. The frontend's settings are `build.args` instead, because Vite
  bakes `VITE_*` into the bundle at build time.
- **`volumes`.** `pgdata:/var/lib/postgresql/data` is a named volume, so the database survives
  `docker compose down` and is wiped only by `down -v` (`make clean`).
- **`command`.** Overrides the image's default:
  `alembic upgrade head && python -m app.seed && uvicorn …`, so migrations run before the API serves.
- **Development-only lines.**
  - `develop.watch` (rebuild on file change).
  - The published DB port `${DB_PORT:-5432}:5432`: in production the database must not be reachable
    from outside.
  - `SEED`.
  - Default credentials (`meetings/meetings` in `.env.example`).
  - `CORS_ORIGINS` pointing at localhost.
  - In production, secrets come from a secret store (here `DbSecret` in `infra/backend.yaml`), not a
    `.env` file.

### Waiting for a dependency

- `depends_on` alone orders **start**, not **readiness**: Postgres needs a few seconds to initialise
  after its container starts.
- Here readiness is explicit:
  - `db` has a `healthcheck` (`pg_isready -U … -d …`, every 5 s, 10 retries).
  - `backend` declares `depends_on: db: { condition: service_healthy }`.
  - `backend` has its own healthcheck (a Python `urllib` call to `/api/health`), and
    `frontend` waits for `backend: service_healthy`.
  - So the order is db healthy → backend (migrate, seed, serve) healthy → frontend.
  - `make start` uses `docker compose up --wait`, which returns only when everything is healthy.
- **If the database disappears later**, compose cannot help: healthchecks only gate startup. The
  backend copes in code:
  - `back/app/db.py` creates the engine with `pool_pre_ping=True`, so a dead pooled connection is
    detected and replaced on the next checkout instead of failing a request.
  - `back/app/routers/health.py` returns **503** `{"status": "unavailable"}` when `SELECT 1` fails,
    so an orchestrator (compose's healthcheck, a load balancer) sees the service as unhealthy.
  - Requests that hit the database while it is down fail and succeed again once it returns. There
    is no retry loop, which is a reasonable default for a stateless API.

### Dockerfile vs compose

- A **Dockerfile** answers *what is inside one image and how it starts*:
  - base image and dependencies;
  - `EXPOSE 8000` and `CMD` (`back/Dockerfile`);
  - for the frontend, a Node build stage plus an nginx serve stage (`front/Dockerfile`).
- **Compose** answers *how several containers run together on one machine*: networking between
  services, ports, env, volumes, startup order and healthchecks.
- One compose file describes the whole system; one Dockerfile per service describes each part.
- Moving to ECS or Cloud Run:
  - The **Dockerfiles survive**: the image is the unit those platforms run.
  - **Compose is replaced** by the platform's own description: an ECS task definition and service,
    or in this repo the CloudFormation stacks in `infra/`.
  - This repo shows that split: `back/Dockerfile` for compose, and `back/Dockerfile.lambda` (base
    `public.ecr.aws/lambda/python:3.12`) for Lambda.

### Base images

- A base image is the file system and runtime a Dockerfile starts from (`FROM`). You inherit its
  contents, its size and its security patches.
- **`python:3.12-slim`** (`back/Dockerfile`) is Debian with CPython and the minimum to run it.
- **`python:3.12`**, the full image, adds compilers, headers and many system libraries. It is
  useful when building C extensions, but it is several times larger, and has more packages that
  need patching.
- **Alpine** (`postgres:16-alpine`, `node:24-alpine`, `nginx:1.27-alpine` here) is smaller still. It
  uses musl instead of glibc:
  - many Python wheels are built for glibc (manylinux), so on Alpine they compile from source,
    which is slower and needs build tools;
  - musl can behave differently (DNS, locale, performance).
  - It fits well for Postgres, nginx and a Node build stage, and is worse for Python with native
    dependencies.
- The choice changes:
  - **build time**: pull size, and whether wheels compile;
  - **image size**: storage, pull time and cold starts;
  - **which CVEs you inherit and how quickly they are patched**: Debian vs Alpine security
    trackers.
  - Rebuilding regularly picks up base-image patches.

### The backend structure (`back/app/`)

- **HTTP layer.** `routers/` (`meetings.py`, `participants.py`, `me.py`, `health.py`) parses requests
  and returns responses. `main.py` builds the app, CORS and error handlers.
- **Contracts.** `schemas.py`: Pydantic request/response models, which are the API contract in
  `PROJECT.md`.
- **Business logic.** `services/` (`meetings.py`, `participants.py`, `users.py`, `errors.py`):
  validation, ownership rules and queries.
- **Persistence.** `models.py` (SQLAlchemy tables), `db.py` (engine, session), `alembic/` (schema
  history).
- **Cross-cutting.** `auth.py` (Cognito token verification), `config.py` (settings from env).
- Why not one file:
  - Each layer changes for a different reason, and can be tested and read on its own.
  - The service layer is also what `lambda_handler.py` and the tests reuse without HTTP.
  - For an agent, small files with clear names mean it reads the right 100 lines instead of 1 000.

### SQLAlchemy

- It maps rows to Python objects (`Meeting`, `Participant`, `User` in `models.py`) and builds SQL
  from Python expressions.
- **Gained:**
  - no hand-written SQL for create/read/update;
  - parameters bound safely, so no string-built SQL injection;
  - relationships loaded for you (meeting → participants through `meeting_participants`);
  - one model definition that Alembic compares the database against;
  - portability of simple queries.
- **Lost when a query gets interesting:**
  - window functions, CTEs, bulk updates and database-specific features are harder to express
    than in SQL;
  - the generated SQL is hidden, so N+1 queries and missing indexes go unnoticed;
  - you think in two languages.
  - The fix is to drop to SQLAlchemy Core or `text()` for such queries (as
    `routers/health.py` does for `SELECT 1`) and to read the SQL it logs.

### Alembic vs `Base.metadata.create_all()`

- `create_all` creates missing tables and does nothing else. It cannot add a column to an existing
  table, rename one, change a type or move data, and it keeps no history.
- A running product's database already holds rows, so schema changes must be:
  - **versioned**: `back/alembic/versions/0001_initial.py` → `0002_meeting_times.py` →
    `0003_users.py`;
  - **reviewable** in a PR;
  - applied in order;
  - **reversible** (`downgrade`).
- `create_all` appears only in `back/tests/conftest.py`, for a throwaway test database.
- When migrations run:
  - **locally: at container start**, from the compose `command` (`alembic upgrade head` before
    `uvicorn`), not at build time, because the database doesn't exist while the image is built;
  - **on AWS: by an explicit step** after new code is live. `make aws-backend-migrate` (part of
    `make deploy-backend`) invokes the Lambda with `{"action": "migrate"}`
    (`back/app/lambda_handler.py`).
- Consequence: new code serves traffic for a few seconds before the migration finishes, so
  migrations should be backward-compatible (expand, then contract).

## Part 2: deploying to AWS

### Why CloudFront if S3 can already serve a file over HTTP?

- In `infra/frontend.yaml` the bucket is **private**: S3 website hosting has no HTTPS on a custom
  domain, and a public bucket exposes everything in it.
- **CloudFront** is the only reader, through an Origin Access Control and a bucket policy. It:
  - terminates **HTTPS** with our ACM certificate on `app.<domain>`;
  - caches at **edge locations** near users;
  - rewrites SPA routes to `index.html`;
  - hosts the **WAF** rate limit.
- Because edges cache, every deploy ends with an **invalidation** (`aws-frontend-publish`).
- Hashed assets are cached for a year (`immutable`), and `index.html` is `no-cache`, so users get
  the new bundle immediately.

### ECR vs ECS, and why they are separate

- **ECR** is a *registry*: it stores and versions images. Here that is `infra/backend-ecr.yaml`,
  with a lifecycle rule that keeps the last 5.
- **ECS** is an *orchestrator*: it runs containers from some registry, keeps N copies alive and
  wires them to a load balancer.
- They are separate because storing artefacts and running them are different jobs with different
  consumers:
  - the same ECR image can be run by ECS, EKS, Lambda (as here), App Runner or a laptop;
  - ECS can pull from Docker Hub or GHCR.
- **In this repo** Lambda plays ECS's role: `make deploy-backend` pushes `…/meetings-backend:<sha>` to
  ECR and points the function at that image (`aws lambda update-function-code`).

### What does the health check check, and what happens on failure?

- **Lab setup (ALB + ECS).** The target group calls a path (for example `/api/health`) on each task
  every N seconds and expects a 200.
  - After a number of consecutive failures the target is marked unhealthy and the ALB **stops
    sending it traffic**.
  - ECS then **replaces the task**, and a new task gets traffic only after it passes.
  - It checks "does this process answer HTTP and can it reach its dependencies", which depends on
    what the endpoint does.
- **This repo.**
  - `/api/health` runs `SELECT 1` and returns 503 on failure, so it checks the database too.
  - In compose it gates startup order.
  - On Lambda there is no long-lived instance to replace: each request gets a working execution
    environment or fails, and Lambda retires crashed environments on its own.
  - `make aws-backend-health` and `make aws-backend-https-check` are manual probes.
  - The first request after Aurora pauses takes about 15 s (resume). This fits inside API
    Gateway's 30 s cap (`TimeoutInMillis: 30000` in `infra/backend-domain.yaml`), which equals the
    Lambda `Timeout` default of 30 in `infra/backend.yaml`.

### Why a CNAME both proves ownership and routes traffic

- A CNAME is just "this name is an alias for that name". Anyone who can create records under a
  domain controls it, so a record you publish is proof of control.
- **Validation.** ACM asks for `_<token>.api.example.com CNAME _<token>.acm-validations.aws`. Only
  the domain's owner can publish it, and ACM issues the certificate once it resolves.
  `infra/scripts/cert.sh` creates it automatically when the zone is in Route 53, or prints it.
- **Routing.** `api.example.com` → the API Gateway regional domain, and `app.example.com` → the
  CloudFront distribution. The mechanism is the same; the target is a service endpoint instead of a
  token.
- In Route 53 we use **alias A records** for routing (`infra/frontend.yaml`,
  `infra/backend-domain.yaml`):
  - they work at a zone apex, where a CNAME cannot;
  - they cost nothing per query.
  - With another DNS provider, the Makefile prints plain CNAMEs.

### A leaked access key vs an OIDC role

- A leaked **access key** gives an attacker whatever the IAM user can do:
  - from any machine, at any time, until someone notices and deletes it;
  - often with broad permissions, because humans use the same key for everything;
  - public repos are scanned for keys within minutes.
- **OIDC** (`infra/github-oidc.yaml`) has no long-lived secret to leak:
  - each workflow run gets a GitHub-signed token naming the repository and ref;
  - AWS exchanges it for credentials that expire with the job.
  - Our trust policy uses **`StringEquals`** on `token.actions.githubusercontent.com:sub` =
    `repo:MasterDay3/OneTwoThree:ref:refs/heads/main` and on `aud` = `sts.amazonaws.com`. Only
    workflows running on `main` of this fork can assume it; other branches, PRs, forks, other repos
    and jobs with an `environment:` cannot.
  - The role can only push to one ECR repository, update and invoke one Lambda, write to one bucket,
    invalidate one distribution and read five stacks' outputs. It cannot change IAM, stacks or DNS.
- What an attacker would need now: the ability to push to `main` of this repository, which is a
  GitHub-account compromise and is visible in history.
- The broken configuration to avoid: `StringLike` with `repo:*` would **grant the account to every
  repository on GitHub**.

### What we would keep at 1 000 organisations, and what breaks first

**Keep:**
- the monorepo and the Makefile-as-contract;
- images tagged by commit SHA, with rollback by tag;
- OIDC deploys with scoped roles;
- CloudFront for the static SPA, which scales without change;
- Alembic migrations run as a deploy step;
- stateless API containers or functions.

**What breaks first, and why:**
- **The database connection budget.** Every concurrent Lambda environment opens its own
  connection. At many organisations × users, Aurora's max connections (tied to ACU) runs out before
  CPU does.
  - Fix: RDS Proxy (or PgBouncer on ECS), and a minimum capacity above 0 so there is no 15 s resume
    on the hot path.
  - If requests become steady and heavy, ECS Fargate behind an ALB (the lab's design) with a fixed
    connection pool becomes cheaper and more predictable than Lambda.
- **Tenant isolation.** Meetings are scoped per user (`owner_id`), and the participant directory is
  shared by everyone. Organisations need an `org_id` on every row, authorisation per organisation,
  and indexes on it.
- **Operational limits that are fine now:**
  - API Gateway's 30 s cap and our 50 req/s stage throttle (`ThrottleRateLimit`);
  - one region;
  - a CloudFront Free plan with 3 plans per account;
  - an ECR lifecycle of 5 images (a short rollback window);
  - deploys that go straight to production with no staging stage;
  - no request metrics or alarms beyond CloudWatch logs.

## Manual submission artefacts (not automated)

These are produced by hand after the first deploy:
- the screenshot of the frontend listing meetings;
- the live `https://app.<domain>` and `https://api.<domain>` URLs;
- the repository link (the fork, `MasterDay3/OneTwoThree`).

**Cost and teardown.** API Gateway, ACM, the OIDC role and Lambda cost close to nothing at this
volume; Aurora bills while active, and $0.40/month for the credentials secret. When done:
- `make aws-destroy` removes everything; add `DESTROY_OIDC=1` to also remove the CI role.
- The GitHub OIDC provider is retained (it is account-wide), as is a final Aurora snapshot.
- The ECR lifecycle keeps only 5 images, so a rollback reaches at most 5 deploys back. Database
  migrations are never rolled back by `aws-backend-rollback`.
