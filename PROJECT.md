# OneTwoThree — Meetings App: repository contract

A small meetings calendar: a FastAPI + PostgreSQL backend (`back/`), a React SPA (`front/`),
Docker Compose for local runs, CloudFormation templates for AWS (`infra/`) and a GitHub Actions
code-style workflow (`.github/`). A signed-in user sees their own meetings in a week or day view and
can create, edit and delete them; participants come from a shared directory.

This file is the specification of the repository structure; `README.md` is the how-to. It
describes the repository **as it is today**, and every value below was copied from the file named
next to it. `infra/tests/test_docs_project.py` re-checks the folders, Compose services, API fields,
status codes, versions and the monorepo section against the source, so this document fails CI-style
checks the moment it drifts.

## Monorepo decision

**Decision: one repository holds the backend, the frontend, the database configuration, the
infrastructure templates and CI.** We will defend it as follows.

- **Atomic changes.** A contract change touches several places at once: a new meeting field goes
  into `back/app/models.py`, a migration in `back/alembic/versions/`, `back/app/schemas.py`, the
  TypeScript type in `front/src/types.ts` and the form in `front/src/components/`. In one repository
  that is one commit and one review; API and client cannot drift apart, because there is no moment
  where one is merged and the other is not. Across repositories the same change is a coordinated
  multi-repo release that someone will eventually get wrong.
- **The repository is the context window.** Most of our code is written or reviewed together with an
  LLM agent. In one checkout the agent reads the endpoint, the Pydantic model, the migration and the
  React component in one pass and sees the real contract. With split repositories it sees a third of
  the system and guesses the rest; the wrong guess is found at integration time, which is the most
  expensive moment to find it. The same holds for a human reviewer.
- **One place for "how it runs".** `compose.yaml`, the `Makefile`, the Dockerfiles and the
  CloudFormation templates live next to the code they build, so a change to how the backend starts
  and the code that needs it land together.

**Trade-off.** A repository boundary buys independence: separate owners, separate release cadence,
separate permissions and CI that only builds what changed. It costs shared context. For a
four-person team building a product that does not exist yet, context is worth more than
independence: we have no separate teams to isolate and the contract changes weekly. The price we
accept is that CI and permissions are repository-wide (the code-style workflow lints all parts on
every run) and that a careless commit can touch both halves.

**What would make us split later:** separate teams owning the frontend and the backend with
different release cadences; a second client (e.g. a mobile app) consuming the API, which would turn
the API into a versioned public contract; or CI time growing to the point where building everything
on every change hurts. Until one of those happens we stay in one repository.

## Folders

Every tracked folder and why it exists. Nothing else is tracked (dependencies, build output, virtual
environments and caches are in `.gitignore`).

| Folder | Purpose |
| --- | --- |
| `.github/` | GitHub configuration; holds only CI workflows. |
| `.github/workflows/` | `code-style.yml`: Ruff on `back/`, ESLint + Prettier + `tsc` on `front/`, cfn-lint on `infra/*.yaml`. Runs on pushes to `main` and on pull requests. It runs no test suites and deploys nothing. |
| `back/` | Backend: FastAPI app, Alembic migrations, tests, `pyproject.toml` + `uv.lock`, `Dockerfile` (Compose) and `Dockerfile.lambda` (AWS Lambda image). |
| `back/app/` | The Python package `app`: `main.py` (FastAPI app, CORS, 404/409 error handlers), `config.py` (settings from env), `db.py` (engine, session), `models.py` (SQLAlchemy tables), `schemas.py` (Pydantic request/response models = the API contract), `auth.py` (Cognito ID-token check), `seed.py` (sample participants), `lambda_handler.py` (Mangum entry point + `migrate` action). |
| `back/app/routers/` | HTTP layer only: one module per resource (`health`, `me`, `meetings`, `participants`), mounted under `/api` in `main.py`. |
| `back/app/services/` | Business logic and queries used by the routers (`meetings`, `participants`, `users`) and the domain errors `NotFoundError` / `ConflictError`. |
| `back/alembic/` | Alembic environment (`env.py`, `script.py.mako`). Alembic owns the database schema; the app never creates tables itself. |
| `back/alembic/versions/` | Ordered migrations: `0001_initial`, `0002_meeting_times`, `0003_users`. |
| `back/tests/` | Backend pytest suite (`test_api.py`, `test_auth.py`) against a real PostgreSQL database named by `TEST_DATABASE_URL`. Excluded from the Docker build context by `back/.dockerignore`. |
| `docs/` | Course/lab documents: `PRD.md` (requirements) and `lab2-implementation-plan.md`. |
| `docs/qa/` | QA test cases derived from the use cases. |
| `docs/use-cases/` | Use-case scenarios for the lab features. |
| `front/` | Frontend: Vite + React + TypeScript + Tailwind + shadcn/ui; `package.json` + `package-lock.json`, lint/format/TS config, `Dockerfile` (build with Node, serve with nginx) and `nginx.conf`. |
| `front/src/` | App entry (`main.tsx`, `App.tsx` with the routes), `types.ts` (TypeScript mirror of the API schemas), global CSS. |
| `front/src/pages/` | One component per route: `LoginPage` (`/`), `SignUpPage` (`/signup`), `ConfirmPage` (`/confirm`), `AuthCallbackPage` (`/auth/callback`), `HomePage` (`/home`, the calendar; needs sign-in). |
| `front/src/components/` | App components: `MeetingsCalendar`, `MeetingFormDialog`, `DeleteMeetingDialog`, `ParticipantsMultiSelect`, header/footer, `BackToTop`. |
| `front/src/components/auth/` | Sign-in building blocks: `AuthLayout`, `GoogleButton`, `PasswordInput`, `RequireAuth` (route guard). |
| `front/src/components/ui/` | Generated shadcn/ui primitives (buttons, dialogs, inputs…); regenerated with the shadcn CLI rather than hand-edited. |
| `front/src/hooks/` | TanStack Query hooks: `useMeetings`, `useParticipants`, `useMe`. |
| `front/src/lib/` | Non-UI modules: `api.ts` (typed fetch wrapper, adds the Bearer token), `auth.ts` (Cognito sign-in/up, session, Google via Hosted UI + PKCE), `calendar.ts` (date helpers, overlap layout), `participants.ts`, `utils.ts`. |
| `front/src/test/` | Vitest + Testing Library tests and their setup. |
| `infra/` | CloudFormation templates, one stack each: `cognito.yaml`, `backend-ecr.yaml`, `backend.yaml` (VPC, Aurora Serverless v2, Lambda + function URL), `frontend.yaml` (S3 + CloudFront + WAF); `backend.params.example.env` (optional parameter overrides). |
| `infra/scripts/` | `cert.sh`: ACM certificate and DNS helper for the frontend's custom domain, called by the `Makefile`. |
| `infra/tests/` | Python checks for the repository's non-application parts (this document, the `Makefile`, the templates). |

**Root files.** `compose.yaml` (local stack), `Makefile` (every local and AWS workflow; `make help`
lists them), `.env.example` (template for the git-ignored `.env`: Compose settings, host ports,
Cognito IDs, AWS credentials), `.gitignore`, `README.md` (how-to) and this `PROJECT.md` (contract).
`main.py` and `pyproject.toml` at the root are **leftovers from the initial commit**: a PyCharm
sample script and an empty project named `onetwothree` with `requires-python = ">=3.14"`. Nothing
(Compose, Dockerfiles, `Makefile`, CI) uses them; the backend's real project file is
`back/pyproject.toml`.

## Compose services

`compose.yaml` defines three services and one named volume, `pgdata`. Values come from `.env`
(created from `.env.example` by `make start`/`make up`).

### `db`

- **Image:** `postgres:16-alpine`.
- **Port:** `${DB_PORT:-5432}:5432` (host port overridable with `DB_PORT`). Publishing the database
  port is a development convenience (backend tests and local `uv run` connect through it); the
  other containers reach it as `db:5432` on the Compose network.
- **Environment:** `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` from `.env`.
- **Volume:** `pgdata` → `/var/lib/postgresql/data` (survives `make down`; `make clean` deletes it).
- **Readiness:** healthcheck `pg_isready -U $POSTGRES_USER -d $POSTGRES_DB` every 5 s, up to 10
  retries.
- **depends_on:** nothing; it starts first.

### `backend`

- **Build:** `./back` (`back/Dockerfile`, base `python:3.12-slim`, installs the dependencies from
  `back/pyproject.toml` with `uv pip install`).
- **Port:** `${BACKEND_PORT:-8000}:8000` (override with `BACKEND_PORT`).
- **depends_on:** `db` with condition `service_healthy`.
- **Command:** `sh -c "alembic upgrade head && python -m app.seed && uvicorn app.main:app --host 0.0.0.0 --port 8000"`
  — migrations first, then seeding (only when `SEED=true` and the participants table is empty),
  then the server. Alembic takes a PostgreSQL advisory lock, so parallel starts do not race.
- **Environment:** `DATABASE_URL` (built from the `POSTGRES_*` values, host `db`), `CORS_ORIGINS`,
  `SEED`, `COGNITO_REGION`, `COGNITO_USER_POOL_ID`, `COGNITO_CLIENT_ID`. An empty
  `COGNITO_USER_POOL_ID` disables auth.
- **Readiness:** healthcheck `python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')"`
  every 10 s, up to 5 retries. `/api/health` runs `SELECT 1`, so "healthy" means migrated, serving
  and connected to the database (it answers 503 when the query fails).
- **Dev only:** `develop.watch` rebuilds the image when `./back` changes (`make up` runs
  `docker compose watch`).

### `frontend`

- **Build:** `./front` (`front/Dockerfile`: build stage `node:24-alpine` runs `npm ci` and
  `npm run build`; runtime stage `nginx:1.27-alpine` serves `dist/`). Build args `VITE_COGNITO_*`
  bake the Cognito IDs into the bundle (empty = auth off).
- **Port:** `${FRONTEND_PORT:-3000}:80` (override with `FRONTEND_PORT`).
- **depends_on:** `backend` with condition `service_healthy`.
- **Readiness:** no healthcheck of its own. It is only started once the backend is healthy, and
  `docker compose up --wait` treats it as ready once its container is running.
- **Dev only:** `develop.watch` rebuilds on changes in `./front` (ignoring `src/test/`).

### Startup order

Nothing is assumed to be up; every step waits on a checked condition:

1. `db` starts; Compose waits until `pg_isready` succeeds (`service_healthy`).
2. `backend` starts, runs `alembic upgrade head`, the seed step, then `uvicorn`; Compose waits until
   `GET /api/health` succeeds (`service_healthy`), which implies the migrations finished.
3. `frontend` starts (nginx), proxying `/api/` to the healthy backend.

`make start` runs `docker compose up -d --build --wait`, which returns when this chain is up.

## Contracts between the parts

- **Frontend ↔ backend.** The SPA calls `${VITE_API_URL}/api/...`. Locally `VITE_API_URL` is
  empty, so requests go to the same origin: nginx (`front/nginx.conf`) proxies `location /api/` to
  `http://backend:8000`, and the Vite dev server proxies `/api` to `http://localhost:8000`
  (`VITE_API_PROXY` overrides it). On AWS the bundle is built with `VITE_API_URL` = the Lambda
  function URL, and the backend allows the site's origin through `CORS_ORIGINS`.
- **Backend ↔ database.** Compose passes one `DATABASE_URL` (`postgresql+psycopg://…@db:5432/…`).
  On AWS the Lambda gets `DB_HOST`/`DB_PORT`/`DB_NAME`/`DB_USER`/`DB_PASSWORD` instead
  (`back/app/config.py` accepts either). Alembic owns the schema: containers migrate on start,
  AWS migrates through `make aws-backend-migrate` (never on cold start).
- **Auth.** Every endpoint except `GET /api/health` needs `Authorization: Bearer <Cognito ID token>`.
  The backend verifies signature (RS256 against the pool's JWKS), issuer, audience (= app client
  ID), expiry and `token_use == "id"` with an email claim, then finds or creates the matching row in
  `users`. When `COGNITO_USER_POOL_ID` is empty, auth is **off**: no token is checked and every
  request acts as one local user (`dev@localhost`) — this is the default local setup.

## API contract: `GET /api/meetings` and `POST /api/meetings`

Source of truth: `back/app/routers/meetings.py`, `back/app/schemas.py`, `back/app/auth.py`,
`back/app/services/meetings.py`. The full live schema is at `/api/docs` (OpenAPI at
`/api/openapi.json`). The other endpoints (`/api/meetings/{id}`, `/api/participants`, `/api/me`,
`/api/health`) are listed in `README.md`.

Conventions:

- Content type `application/json` both ways. IDs are UUID strings.
- Dates are ISO 8601 date-times. **Input must carry a timezone** (`Z` or `±HH:MM`); a naive value
  such as `2026-09-28T10:00:00` is rejected with 422. They are stored as `timestamptz`, so the
  response returns the same instant with an offset (the tests compare instants, not the offset's
  spelling).
- Errors use FastAPI's shape `{"detail": ...}`: a string for 401/404, a list of
  `{loc, msg, type}` items for 422.

### `GET /api/meetings`

No parameters. Returns a JSON array of `MeetingRead` objects: **only the caller's own meetings**
(`owner_id` = the signed-in user), sorted by `starts_at`, then `id`. Each meeting includes its
participants, sorted by name. An empty list is `[]` with 200.

### `POST /api/meetings`

Body: `MeetingCreate`. Creates a meeting owned by the caller and returns it as `MeetingRead` with
**201**.

### Request body — `MeetingCreate`

String fields have surrounding whitespace stripped; an empty or blank `description`, `call_link` or
`place` counts as `null`.

| Field | Type (schemas.py) | Rules |
| --- | --- | --- |
| `title` | `str` | Required; 1–200 characters. |
| `description` | `str \| None` | Optional; up to 5000 characters. |
| `call_link` | `HttpUrl \| None` | Optional; an `http`/`https` URL. Stored in normalised form (e.g. a bare host gains a trailing `/`). |
| `place` | `str \| None` | Optional; up to 255 characters. |
| `starts_at` | `AwareDatetime` | Required; ISO 8601 date-time with a timezone. |
| `ends_at` | `AwareDatetime` | Required; ISO 8601 date-time with a timezone; must be strictly after `starts_at`. |
| `participant_ids` | `list[UUID]` | Optional, default `[]`; IDs from `/api/participants`. Duplicates are ignored; an unknown ID → 404. |

Cross-field rules (model validators): at least one of `call_link` / `place` must be given
("Provide a call link, a place, or both"), and `ends_at` > `starts_at` ("The meeting must end after
it starts"). Both fail with 422.

### Response item — `MeetingRead`

| Field | Type (schemas.py) | Notes |
| --- | --- | --- |
| `id` | `UUID` | Generated by the server. |
| `title` | `str` | |
| `description` | `str \| None` | |
| `call_link` | `str \| None` | The normalised URL as a string. |
| `place` | `str \| None` | |
| `starts_at` | `datetime` | ISO 8601 with offset. |
| `ends_at` | `datetime` | ISO 8601 with offset. |
| `owner_id` | `UUID \| None` | The creator's user ID. `null` only for meetings created before accounts existed; nobody sees those. |
| `participants` | `list[ParticipantRead]` | Sorted by name. |
| `created_at` | `datetime` | Set by the database. |

### Nested — `ParticipantRead`

| Field | Type (schemas.py) | Notes |
| --- | --- | --- |
| `id` | `UUID` | |
| `name` | `str` | |
| `email` | `str` | Stored lower-case. |

### Status codes

Verified in the source: success codes in the route decorators of `back/app/routers/meetings.py`
(POST sets `status_code=status.HTTP_201_CREATED`, GET uses FastAPI's default 200), 401 in
`back/app/auth.py`, 404 via `NotFoundError` → `back/app/main.py`, 422 from FastAPI's request
validation of `MeetingCreate`.

| Endpoint | Code | When |
| --- | --- | --- |
| `GET /api/meetings` | `200` | Success (possibly `[]`). |
| `GET /api/meetings` | `401` | Auth on and the `Authorization: Bearer` header is missing, or the token is invalid/expired/not an ID token. Response carries `WWW-Authenticate: Bearer`. |
| `POST /api/meetings` | `201` | Created; body is the new `MeetingRead`. |
| `POST /api/meetings` | `401` | Same as for GET. |
| `POST /api/meetings` | `404` | A `participant_ids` entry does not exist (`{"detail": "Participants not found: <ids>"}`). |
| `POST /api/meetings` | `422` | Body fails validation: missing/empty `title`, naive or malformed dates, `ends_at` not after `starts_at`, neither `call_link` nor `place`, bad URL, bad UUID, over-long strings. |

With auth off, 401 never occurs. Codes not listed (e.g. a 500 when the database is unreachable)
are not part of the contract.

### Examples

```http
POST /api/meetings
Authorization: Bearer <Cognito ID token>
Content-Type: application/json

{
  "title": "Sprint planning",
  "description": null,
  "call_link": "https://meet.example.com/abc",
  "place": null,
  "starts_at": "2026-09-28T10:00:00+03:00",
  "ends_at": "2026-09-28T11:00:00+03:00",
  "participant_ids": ["3f0c8a52-6f7e-4a4e-9a51-2b1d9a0c1e11"]
}
```

```http
HTTP/1.1 201 Created
Content-Type: application/json

{
  "id": "9b2f4c1e-0d7a-4b8e-8f63-5a1c2d3e4f50",
  "title": "Sprint planning",
  "description": null,
  "call_link": "https://meet.example.com/abc",
  "place": null,
  "starts_at": "2026-09-28T07:00:00Z",
  "ends_at": "2026-09-28T08:00:00Z",
  "owner_id": "5d6e7f80-1a2b-4c3d-9e8f-0a1b2c3d4e5f",
  "participants": [
    {"id": "3f0c8a52-6f7e-4a4e-9a51-2b1d9a0c1e11", "name": "Anna Kovalenko", "email": "anna@example.com"}
  ],
  "created_at": "2026-09-27T12:34:56.789012Z"
}
```

`GET /api/meetings` returns `[ <MeetingRead>, ... ]` with 200. A POST without `call_link` and
`place` returns 422 with a `detail` item whose `msg` contains "Provide a call link, a place, or
both".

### The course's minimal meeting vs. this model

The course brief's minimal meeting is `id`, `title`, `starts_at`, `ends_at` and an attendee count.
The first four exist with those names. There is **no attendee-count field**: attendees are the
`participants` list (entries of the shared participant directory, not user accounts), so the count
is the length of that list, computed by the client. The model is richer than the brief:
`description`, `call_link`, `place`, `owner_id`, `created_at`, and per-user ownership.

## Pinned versions

Copied verbatim from the named files. Read the constraint column literally: most library entries
are **ranges, not exact pins** — `>=` in `back/pyproject.toml` has no upper bound, `^x.y.z` allows
any later version with the same major, `~6.0` allows `6.0.x` only. Exact resolutions live in the
lock files `back/uv.lock` (used by `uv sync` locally and in CI) and `front/package-lock.json` (used
by `npm ci` in `front/Dockerfile`, CI and `make aws-frontend-publish`). Note that `back/Dockerfile`
and `back/Dockerfile.lambda` install from `back/pyproject.toml`, **not** from `uv.lock`, so the
backend images resolve the ranges at build time.

**Runtimes, images and CI**

| Item | Version | Source | Note |
| --- | --- | --- | --- |
| `postgres` | `postgres:16-alpine` | `compose.yaml` | Local database. |
| `backend base image` | `python:3.12-slim` | `back/Dockerfile` | Compose backend. |
| `lambda base image` | `public.ecr.aws/lambda/python:3.12` | `back/Dockerfile.lambda` | AWS backend. |
| `uv` | `ghcr.io/astral-sh/uv:latest` | `back/Dockerfile` | **Not pinned** (`latest` tag); same in `Dockerfile.lambda`. |
| `frontend build image` | `node:24-alpine` | `front/Dockerfile` | Build stage. |
| `frontend runtime image` | `nginx:1.27-alpine` | `front/Dockerfile` | Serves the SPA locally. |
| `requires-python` | `>=3.12` | `back/pyproject.toml` | A range. Ruff targets `py312`. |
| `CI Python` | `3.12` | `.github/workflows/code-style.yml` | `uv sync --python 3.12`. |
| `CI Node` | `24` | `.github/workflows/code-style.yml` | `actions/setup-node` `node-version`. |
| `Aurora PostgreSQL` | `16.14` | `infra/backend.yaml` | Default of the `DbEngineVersion` parameter. |

**Backend libraries** (`[project].dependencies` and the `dev` group)

| Library | Constraint | Source |
| --- | --- | --- |
| `fastapi` | `>=0.115` | `back/pyproject.toml` |
| `uvicorn[standard]` | `>=0.30` | `back/pyproject.toml` |
| `sqlalchemy` | `>=2.0` | `back/pyproject.toml` |
| `alembic` | `>=1.13` | `back/pyproject.toml` |
| `psycopg[binary]` | `>=3.2` | `back/pyproject.toml` |
| `pydantic[email]` | `>=2.7` | `back/pyproject.toml` |
| `pydantic-settings` | `>=2.3` | `back/pyproject.toml` |
| `mangum` | `>=0.19` | `back/pyproject.toml` |
| `pyjwt[crypto]` | `>=2.9` | `back/pyproject.toml` |
| `pytest` | `>=8` | `back/pyproject.toml` |
| `httpx` | `>=0.27` | `back/pyproject.toml` |
| `ruff` | `>=0.6` | `back/pyproject.toml` |

**Frontend libraries** (key entries of `dependencies` / `devDependencies`)

| Library | Constraint | Source |
| --- | --- | --- |
| `react` | `^19.3.0` | `front/package.json` |
| `react-dom` | `^19.3.0` | `front/package.json` |
| `react-router` | `^7.18.4` | `front/package.json` |
| `@tanstack/react-query` | `^5.103.1` | `front/package.json` |
| `zod` | `^4.6.5` | `front/package.json` |
| `vite` | `^8.3.0` | `front/package.json` |
| `typescript` | `~6.0` | `front/package.json` |
| `tailwindcss` | `^4.3.3` | `front/package.json` |
| `vitest` | `^4.1.11` | `front/package.json` |

## Deliberately absent

No cache (Redis), no message queue or background workers, no second database, no reverse proxy
other than the frontend's own nginx, no service mesh, no Kubernetes. Locally that is three
containers; on AWS it is Lambda + Aurora Serverless v2 + S3/CloudFront + Cognito. Anything added
must earn its place in this file.

## AWS in one paragraph

Everything deploys to `us-east-1` with CloudFormation through the `Makefile` (`make aws-deploy` =
Cognito, then backend, then frontend). The backend runs as a container image on Lambda behind a
public function URL, with Aurora Serverless v2 PostgreSQL in private subnets; the frontend is a
private S3 bucket behind CloudFront with a WAF web ACL. Details, costs and the optional custom
domain are in `README.md`; this section will be refreshed when the deployment pipeline changes.
