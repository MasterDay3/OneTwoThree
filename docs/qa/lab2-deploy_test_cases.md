# Test Cases: Lab 2 — Documented Repository Contract, Custom-Domain HTTPS Deploy, and OIDC-Based CI/CD

> Based on [PRD](../PRD.md) (section 1, FR-1..FR-34, NFR-1..NFR-7, AC-1..AC-17) and
> [Use Cases](../use-cases/lab2-deploy_use_cases.md) (UC-1..UC-15).

---

## Legend — Execution Types & Harnesses

| Code | Meaning |
|---|---|
| **AUTO-LOCAL** | Automatable today, in-repo, no AWS account needed. |
| **AUTO-CI** | Only observable by watching a real GitHub Actions run (red/green pipeline, concurrency/queueing behavior). |
| **MANUAL-AWS** | Requires the student's real AWS account/domain (cert issuance, DNS, live curl, AssumeRole denial, live rollback, teardown). |
| **MANUAL-LOCAL** | Requires a human at a browser/terminal against `docker compose` (no AWS). |

AUTO-LOCAL harnesses (concrete, to be built by `test-writer`/`e2e-runner`):

- **H-CFN** — `infra/tests/test_cfn_*.py`, run via `uvx --with pyyaml --with cfn-lint pytest infra/tests`. Parses `infra/*.yaml` with a custom PyYAML loader that registers CFN intrinsic tags (`!Ref`, `!Sub`, `!GetAtt`, `!Condition`, `!If`, etc.) as tagged scalars/sequences so the file loads without error, then asserts on the resulting dict structure.
- **H-MAKE** — `infra/tests/test_makefile_*.py`. Copies (or symlinks) the repo's `Makefile`/`infra/scripts/*` into a temp dir, prepends a temp `bin/` to `PATH` containing stub executables named `aws`, `docker`, `git`, `curl`, `dig` that log every invocation (args + stdin) to a file and return canned exit codes/stdout, then runs `make -C <tmp> <target> VAR=...` via `subprocess.run` and asserts on the stub call log, exit code, and stdout/stderr. `make -n <target>` (dry-run) is used wherever only the *shape* of the recipe (never actually running a real command) needs checking.
- **H-WORKFLOW** — `infra/tests/test_workflows.py`. Parses `.github/workflows/*.yml` with PyYAML and asserts on triggers/permissions/`needs`/`runs-on`/step content; runs `actionlint` via `uvx actionlint` if available on `PATH` (skip with a clear skip-reason if not installed, do not silently pass).
- **H-CFNLINT** — `cfn-lint infra/*.yaml`, already wired as the existing `infra` job in `.github/workflows/code-style.yml`; extended automatically once `infra/backend-domain.yaml` and `infra/github-oidc.yaml` exist in that glob.
- **H-DOCS** — `infra/tests/test_docs_*.py`. Plain text/regex assertions against `PROJECT.md`, `docs/lab2-discussion.md`, `README.md`, `back/app/lambda_handler.py`'s docstring, cross-checked against `compose.yaml`, `back/pyproject.toml`, `front/package.json`, `back/app/routers/`, `back/app/schemas.py`.

Every TC below states its type as `AUTO-LOCAL (H-xxx)`, `AUTO-CI`, `MANUAL-AWS`, or `MANUAL-LOCAL`. Where a scenario needs both a structural check and a live demonstration, two TCs are listed.

---

## 1. Local development stack (`make up`/`make start`)

### 1.1 Bring-up, watch mode, and env bootstrap

| TC | Title | Maps to | Type | Preconditions | Steps | Expected Result |
|---|---|---|---|---|---|---|
| TC-1.1 | Fresh clone `make up` brings all services healthy | UC-1 (primary) | MANUAL-LOCAL | Docker/Compose installed; fresh clone; no `.env` | 1. `make up` 2. Watch container health 3. Open `http://localhost:3000` | `.env` auto-created from `.env.example`; `db`→`backend`→`frontend` start in order, all report healthy; sign-in page loads; `/api/docs` reachable |
| TC-1.2 | `make start` (no watch) | UC-1-A1 | MANUAL-LOCAL | Same as TC-1.1 | 1. `make start` | Identical to TC-1.1 through health-wait; returns immediately, no `docker compose watch` process |
| TC-1.3 | Existing `.env` is not overwritten | UC-1-A2 | AUTO-LOCAL (H-MAKE) | Temp dir with `.env.example` + a `.env` containing a custom `DB_PORT=15432` | 1. Run `make start` (docker stub) | `.env` file content unchanged (`cp` never invoked on it) |
| TC-1.4 | `SEED=true` first start is idempotent | UC-1-A3 | MANUAL-LOCAL | Fresh stack, `.env` has `SEED=true` | 1. `make up` 2. `make restart` | Exactly 5 sample participants exist after both runs, no duplicates |
| TC-1.5 | Host port already taken | UC-1-E1 | MANUAL-LOCAL | Another process bound to `3000` | 1. `make up` | `docker compose up` fails to bind; setting `FRONTEND_PORT` in `.env` and re-running succeeds |
| TC-1.6 | `db` never becomes healthy | UC-1-E2 | MANUAL-LOCAL | Corrupted `pgdata` volume | 1. `make up` | `backend` never starts; `--wait` times out; `make up` exits non-zero; `make clean && make up` recovers |
| TC-1.7 | `backend` healthcheck never passes | UC-1-E3 | MANUAL-LOCAL | Bad `DATABASE_URL` or broken Alembic revision | 1. `make up` 2. `make logs SERVICE=backend` | `frontend` never starts; `--wait` times out; logs show the Alembic/uvicorn error |
| TC-1.8 | Re-running `make up` is idempotent | UC-1-X1 | MANUAL-LOCAL | Stack already healthy | 1. `make up` twice | No duplicate containers; only rebuilds on source change |
| TC-1.9 | `make clean` wipes volume, fresh migrations reapply | UC-1-X2 | MANUAL-LOCAL | Stack running | 1. `make clean` 2. `make up` | Empty DB on next start; all Alembic revisions reapply from scratch |

---

## 2. Add a meeting locally and confirm persistence

| TC | Title | Maps to | Type | Preconditions | Steps | Expected Result |
|---|---|---|---|---|---|---|
| TC-2.1 | Meeting persists across full reload | UC-2 (primary) | MANUAL-LOCAL | Stack up (TC-1.1) | 1. Fill form, submit 2. Observe calendar update 3. Hard reload (`F5`) | `POST /api/meetings` → 201; new meeting visible before and after reload (from `GET /api/meetings`, not client cache) |
| TC-2.2 | `call_link`-only and `place`-only both valid | UC-2-A1 | MANUAL-LOCAL | Stack up | 1. Create meeting with `call_link` only 2. Create another with `place` only | Both return 201 and persist |
| TC-2.3 | Missing both `call_link`/`place` | UC-2-E1 | MANUAL-LOCAL | Stack up | 1. Submit form with neither field | `POST /api/meetings` → 422; form shows validation error; no row created |
| TC-2.4 | `ends_at` not after `starts_at` | UC-2-E2 | MANUAL-LOCAL | Stack up | 1. Submit `ends_at <= starts_at` | 422; no row created |
| TC-2.5 | Backend restart mid-session preserves data | UC-2-E3 | MANUAL-LOCAL | Meeting created; `pgdata` volume intact | 1. Create meeting 2. `make restart` 3. Reload once healthy | Meeting still present |
| TC-2.6 | Reload immediately post-submit is a server-persistence check | UC-2-X1 | MANUAL-LOCAL | Stack up | 1. Submit, immediately reload before any optimistic UI settle | Fresh `GET` returns the meeting — proves DB persistence, not client cache |

---

## 3. `PROJECT.md` repository contract (AC-1)

| TC | Title | Maps to | Type | Preconditions | Steps | Expected Result |
|---|---|---|---|---|---|---|
| TC-3.1 | `PROJECT.md` documents every folder/service/contract/version/trade-off | UC-3 (primary) | AUTO-LOCAL (H-DOCS) | `PROJECT.md` exists; run only after the final docs refresh (PROJECT.md/README/`infra/`/workflows all settled) | Parse `PROJECT.md`; cross-check: (a) folder list against tracked directories only, derived from `git ls-files` (never a raw `find`, which would also surface `node_modules`/`.venv`/`dist`/`__pycache__`/build artifacts); (b) `compose.yaml` services' image/build/ports/`depends_on`/healthcheck; (c) `GET/POST /api/meetings` fields/status codes against `back/app/routers/` + `back/app/schemas.py`; (d) versions against `back/pyproject.toml`/`front/package.json`/`compose.yaml`; (e) presence of a monorepo trade-off paragraph; (f) glob-based completeness — every file matching `infra/*.yaml` and `.github/workflows/*.yml` is mentioned by name somewhere in `PROJECT.md` | All six checks pass; every folder/service/field/version/trade-off item is present and matches source; check (f) is deliberately run last/after the final docs refresh, since it is only meaningful once `infra/backend-domain.yaml`, `infra/github-oidc.yaml`, and `deploy.yml` all actually exist |
| TC-3.2 | Spot-check one field type against `schemas.py` | UC-3-A1 | AUTO-LOCAL (H-DOCS) | TC-3.1 fixtures | Extract `starts_at` type/format from `PROJECT.md`; extract from `back/app/schemas.py` | Exact match (ISO 8601 with timezone) |
| TC-3.3 | Harness catches a wrong status code (negative test of the harness) | UC-3-E1 | AUTO-LOCAL (H-DOCS) | Fixture copy of `PROJECT.md` with `POST /api/meetings` success code mutated to `200` | Run the field/status-code check from TC-3.1 against the mutated fixture | Check fails, reporting the mismatch — proves the harness would catch a real NFR-7 defect |
| TC-3.4 | Harness catches an invented version number | UC-3-E2 | AUTO-LOCAL (H-DOCS) | Fixture `PROJECT.md` with a fastapi version not present in `back/pyproject.toml` | Run version cross-check | Check fails on the invented version |
| TC-3.5 | Version ranges copied verbatim, not collapsed | UC-3-X1 | AUTO-LOCAL (H-DOCS) | `back/pyproject.toml` has a range constraint (e.g. `>=x,<y`) for at least one listed library | Compare `PROJECT.md`'s string for that library byte-for-byte to `pyproject.toml`'s constraint string | Exact string match, not a single collapsed version number |

---

## 4. OIDC identity provider and deploy role bootstrap (`infra/github-oidc.yaml`, `make aws-oidc-deploy`)

| TC | Title | Maps to | Type | Preconditions | Steps | Expected Result |
|---|---|---|---|---|---|---|
| TC-4.1 | Template shape: trust policy, IAM role, capabilities, DeletionPolicy | UC-4 (primary) | AUTO-LOCAL (H-CFN) | `infra/github-oidc.yaml` exists | Parse template; assert: trust policy has `StringEquals` (not `StringLike`) on both `...:sub` = literal `repo:MasterDay3/OneTwoThree:ref:refs/heads/main` and `...:aud` = `sts.amazonaws.com`; `RoleName` is the literal `$(PROJECT)-github-deploy` form (fixed, not `!Ref AWS::StackName`-derived random); `AWS::IAM::OIDCProvider` resource has `DeletionPolicy: Retain` | All assertions pass |
| TC-4.2 | `ExistingOidcProviderArn` conditionally skips provider creation | UC-4-A1 | AUTO-LOCAL (H-CFN) | Template has `Parameters.ExistingOidcProviderArn` (default empty) | Assert a `Condition` gates the `AWS::IAM::OIDCProvider` resource on `ExistingOidcProviderArn` being empty, and the role's trust policy / outputs reference `!If` to pick the discovered ARN otherwise | Provider resource is conditional; no duplicate-provider path exists in the template |
| TC-4.3 | Makefile auto-fills `ExistingOidcProviderArn` and passes `--no-fail-on-empty-changeset` | UC-4-A2 | AUTO-LOCAL (H-MAKE) | — | `make -n aws-oidc-deploy` (dry run) | Recipe includes an `aws iam list-open-id-connect-providers` lookup feeding the parameter, `--capabilities CAPABILITY_NAMED_IAM`, and `--no-fail-on-empty-changeset` |
| TC-4.4 | Insufficient IAM privileges to bootstrap | UC-4-E1 | MANUAL-AWS | AWS credentials lacking `iam:CreateRole`/`iam:CreateOpenIDConnectProvider` | `make aws-oidc-deploy` | `aws cloudformation deploy` fails with `AccessDenied`/`InsufficientCapabilitiesException`; re-run with a privileged role + correct `--capabilities` succeeds |
| TC-4.5 | Failed stack in `ROLLBACK_COMPLETE` is cleared before retry | UC-4-E2 | AUTO-LOCAL (H-MAKE) | — | Grep `Makefile`'s `aws-oidc-deploy` recipe | Calls the same `clear_failed_stack`-style helper used by `aws-backend-ecr`/`aws-backend-stack`/`aws-frontend-stack`/`aws-cognito-stack` |
| TC-4.6 | Trust condition is scoped to this exact fork, not a wildcard-able pattern | UC-4-X1 | AUTO-LOCAL (H-CFN) | Template loaded | Assert the `sub` condition value is a literal string containing `MasterDay3/OneTwoThree` with exact casing and no `*`/`StringLike` anywhere in the trust policy | Literal exact-match only; a different fork's `sub` cannot match |
| TC-4.7 | Live bootstrap end-to-end | UC-4 (primary) | MANUAL-AWS | AWS account, frontend stack already deployed (UC-5) | 1. `make aws-oidc-deploy` 2. `gh variable set AWS_DEPLOY_ROLE_ARN --body <role-arn>` (or set manually in repo Settings) | Stack `CREATE_COMPLETE`; role ARN output captured; repo variable `AWS_DEPLOY_ROLE_ARN` set (not a secret) |
| TC-4.8 | Second `aws-oidc-deploy` run keeps a stack-managed provider stack-managed | UC-4-A2 | AUTO-LOCAL (H-MAKE) | Stack-output stub reports `ProviderManagedByStack=true` (this stack created the provider on the prior run) | `make aws-oidc-deploy` (aws stub) a second time | Before invoking `list-open-id-connect-providers` auto-fill, the Makefile checks the existing stack's `ProviderManagedByStack` output; since it is `true`, it passes `ExistingOidcProviderArn=` (empty) to `cloudformation deploy` rather than the discovered ARN — so the template's `Condition` does not flip to "reuse existing" and orphan the provider outside this stack's management |

---

## 5. First-time AWS bootstrap of ECR/backend/frontend stacks

| TC | Title | Maps to | Type | Preconditions | Steps | Expected Result |
|---|---|---|---|---|---|---|
| TC-5.1 | Full first-time bootstrap sequence | UC-5 (primary) | MANUAL-AWS | Brand-new AWS account, `us-east-1` | 1. `make aws-cognito-deploy` 2. `make aws-backend-deploy` 3. `make aws-frontend-deploy` | ECR repo, VPC/Aurora/Lambda/function URL, S3/CloudFront/WAF, Cognito pool all created and wired; app reachable on function-URL/CloudFront domains |
| TC-5.2 | Aggregate `make aws-deploy` matches manual order | UC-5-A1 | AUTO-LOCAL (H-MAKE) | — | `make -n aws-deploy` | Prerequisite/recipe order is `aws-cognito-deploy` → `aws-backend-deploy` → `aws-frontend-deploy` |
| TC-5.3 | Re-running a `*-stack` deploy with no changes is a no-op | UC-5-A2 | MANUAL-AWS | Stack already deployed | Re-run `make aws-backend-stack` | Succeeds with `No updates are to be performed` (empty changeset), no error |
| TC-5.4 | `aws-backend-stack` guarded on missing Cognito | UC-5-E1 | AUTO-LOCAL (H-MAKE) | `COGNITO_POOL_ID` unset | `make aws-backend-stack` (aws stub) | Exits non-zero with "Cognito not deployed..." before any `cloudformation deploy` stub call |
| TC-5.5 | `aws-frontend-publish` guarded on missing backend | UC-5-E2 | AUTO-LOCAL (H-MAKE) | Backend stack output stub returns empty `ApiUrl` | `make aws-frontend-publish` | Exits non-zero with "Backend not deployed..." before any build/upload stub call |
| TC-5.6 | Failed-stack cleanup applies to every `*-stack` target | UC-5-E3 | AUTO-LOCAL (H-MAKE) | — | Grep each of `aws-backend-ecr`, `aws-backend-stack`, `aws-frontend-stack`, `aws-cognito-stack` recipes | All call the same `ROLLBACK_COMPLETE`-clearing helper |
| TC-5.7 | CloudFront Free-plan ineligibility recovery | UC-5-E4 | MANUAL-AWS | Account already has 3 CloudFront Free plans | `make aws-frontend-stack` (default `CLOUDFRONT_PLAN=FREE`) then retry with `CLOUDFRONT_PLAN=PAY_AS_YOU_GO` | First run fails at plan subscription; retry with override succeeds |
| TC-5.8 | `ARCH=amd64` override builds x86_64 image | UC-5-X1 | AUTO-LOCAL (H-MAKE) | docker/buildx stub | `make aws-backend-push ARCH=amd64` | Stub invoked with `--platform linux/amd64` (not the default `arm64`) |
| TC-5.9 | `KEEP_IMAGE=1` passes the *live* `ImageUri` (FR-31, AC-17) | UC-5-X2 | AUTO-LOCAL (H-MAKE) | `aws lambda get-function` stub returns a known `Code.ImageUri` | `make aws-backend-stack KEEP_IMAGE=1` | The `aws cloudformation deploy` stub call includes `ParameterKey=ImageUri,ParameterValue=<that live URI>` — never omitted, never the Makefile-computed `$(ECR_URI):$(TAG)` |
| TC-5.10 | Re-deploy with `DOMAIN` unset does not detach an attached custom domain (FR-8, AC-13) | UC-5-X3 | AUTO-LOCAL (H-MAKE) | `DOMAIN`/`FRONTEND_DOMAIN` unset on this invocation; `DETACH_DOMAIN` unset | `make aws-frontend-stack` | The `aws cloudformation deploy --parameter-overrides` stub call OMITS `DomainName=`/`CertificateArn=`/`HostedZoneId=` entirely — `--parameter-overrides` has no `UsePreviousValue` syntax; omitting a parameter on an existing stack is what makes CloudFormation keep its previous value. The stub call never passes these as empty strings |
| TC-5.11 | Live confirmation: domain survives a `DOMAIN`-unset re-deploy | UC-5-X3 | MANUAL-AWS | Custom domain already attached to frontend stack; fresh `.env` with `DOMAIN` unset | 1. `make aws-frontend-stack` 2. `curl -I https://app.<domain>/` | `app.<domain>` still resolves and serves TLS after the re-deploy |
| TC-5.12 | `DETACH_DOMAIN=1` explicitly clears the custom domain (documented escape hatch, FR-8) | UC-5-X3 | AUTO-LOCAL (H-MAKE) | `DETACH_DOMAIN=1` set | `make aws-frontend-stack DETACH_DOMAIN=1` | The `--parameter-overrides` stub call explicitly includes `DomainName=`, `CertificateArn=`, `HostedZoneId=` all set to empty strings — this flag is the only path that can detach an already-attached custom domain |

---

## 6. ACM certificates for `api.<domain>` / `app.<domain>` (`cert.sh`, `aws-backend-cert`)

| TC | Title | Maps to | Type | Preconditions | Steps | Expected Result |
|---|---|---|---|---|---|---|
| TC-6.1 | Request + auto-validate a fresh certificate | UC-6 (primary) | MANUAL-AWS | Route 53 public zone for `<domain>` exists in-account | `make aws-backend-cert BACKEND_DOMAIN=api.<domain>` then `cert.sh wait api.<domain>` | ACM cert requested (`DNS` validation); CNAME UPSERTed into the zone automatically; `cert.sh wait` blocks then reports `ISSUED` |
| TC-6.2 | Reuse an already-`ISSUED` certificate | UC-6-A1 | AUTO-LOCAL (script stub harness) | `aws acm list-certificates`/`describe-certificate` stub returns `ISSUED` for the domain | `cert.sh request <domain>` | No `request-certificate` call made; script prints reuse/`Status: ISSUED` message, exits 0 |
| TC-6.3 | Propagation delay is safe to Ctrl+C | UC-6-A2 | MANUAL-AWS | CNAME just UPSERTed, not yet observed by ACM | `cert.sh wait <domain>`, Ctrl+C, re-run later | Script's own message confirms Ctrl+C safety; re-run picks up `PENDING_VALIDATION` → eventually `ISSUED`, no duplicate request |
| TC-6.4 | No Route 53 zone found → manual fallback | UC-6-E1 | AUTO-LOCAL (script stub harness) | `route53 list-hosted-zones-by-name` stub returns no matching zone at any label level | `cert.sh request <domain>` | No UPSERT call made; script prints the manual CNAME name/value instructions |
| TC-6.5 | Validation-record publish times out (60s) | UC-6-E2 | AUTO-LOCAL (script stub harness, `sleep` stubbed) | `describe-certificate` stub never returns the CNAME across 20 polls | `cert.sh request <domain>` | Exits non-zero with "Timed out waiting for ACM to publish the validation record" to stderr |
| TC-6.6 | `EXPIRED` prior cert is not reused | UC-6-E3 | AUTO-LOCAL (script stub harness) | `describe-certificate` stub returns a prior cert with status `EXPIRED` | `cert.sh request <domain>` | A brand-new `request-certificate` call IS made (old cert not matched by the `ISSUED`/`PENDING_VALIDATION` filter) |
| TC-6.7 | `api.` and `app.` sibling labels share one hosted zone without conflict | UC-6-X1 | AUTO-LOCAL (script stub harness) | `list-hosted-zones-by-name` stub returns the same zone for both `api.example.com` and `app.example.com` | `cert.sh request api.example.com` then `cert.sh request app.example.com` | Both succeed; two distinct CNAME record names UPSERTed into the same zone ID, no overwrite of one by the other |
| TC-6.8 | Re-running after `ISSUED` is a no-op | UC-6-X2 | AUTO-LOCAL (script stub harness) | Cert already `ISSUED` | `cert.sh request <domain>` (again) | Immediate `Status: ISSUED` exit, no further DNS/API action |
| TC-6.9 | Cert/HTTPS targets fail fast on empty domain (FR-8, AC-13) | UC-6 (crosscut) | AUTO-LOCAL (H-MAKE) | `DOMAIN` and `BACKEND_DOMAIN` both unset | Run each of `aws-frontend-cert`, `aws-backend-cert`, `aws-frontend-https`, `aws-backend-https` | Each exits non-zero with a clear "domain is empty" message; zero `aws`/`cert.sh` stub invocations logged |
| TC-6.10 | `DOMAIN` derivation never yields dangling `app.`/`api.` prefixes | UC-6 (crosscut, FR-8) | AUTO-LOCAL (H-MAKE) | `DOMAIN` unset | Print `$(FRONTEND_DOMAIN)`/`$(BACKEND_DOMAIN)` via a debug target (or `make -p`) | Both are the empty string, never the literal `app.`/`api.` |

---

## 7. `api.<domain>` (API Gateway) and `app.<domain>` (CloudFront) routing

### 7.1 `infra/backend-domain.yaml` structural correctness

| TC | Title | Maps to | Type | Preconditions | Steps | Expected Result |
|---|---|---|---|---|---|---|
| TC-7.1a | HTTP API disables the default endpoint; `AWS_PROXY` integration is v2.0 with 30s timeout | UC-7 (primary) | AUTO-LOCAL (H-CFN) | `infra/backend-domain.yaml` exists | Parse template | `AWS::ApiGatewayV2::Api.DisableExecuteApiEndpoint == true`; the integration's `IntegrationType == AWS_PROXY`, `PayloadFormatVersion == "2.0"`, `TimeoutInMillis == 30000` |
| TC-7.1b | `$default` route/stage with autodeploy + throttling | UC-7 (primary) | AUTO-LOCAL (H-CFN) | — | Parse template | A `Route` with `RouteKey: $default`; a `Stage` named `$default` with `AutoDeploy: true` and non-empty `DefaultRouteSettings.ThrottlingBurstLimit` / `DefaultRouteSettings.ThrottlingRateLimit` (HTTP API stages have no top-level `ThrottleSettings` property — that shape is REST-API-only) |
| TC-7.1c | Lambda permission `SourceArn` is scoped, not wildcard | UC-7 (primary) | AUTO-LOCAL (H-CFN) | — | Parse template | `AWS::Lambda::Permission.SourceArn` references this API's own execution ARN (`execute-api/<ApiId>/*/*` shape via `!Sub`), never `*` across all APIs |
| TC-7.1d | Regional custom domain at TLS 1.2, root path mapping | UC-7 (primary) | AUTO-LOCAL (H-CFN) | — | Parse template | `AWS::ApiGatewayV2::DomainName.DomainNameConfigurations[0].EndpointType == REGIONAL`, `.SecurityPolicy == TLS_1_2`; `AWS::ApiGatewayV2::ApiMapping.ApiMappingKey == ""` |
| TC-7.1e | Route 53 alias conditional on `HostedZoneId` | UC-7 (primary) | AUTO-LOCAL (H-CFN) | — | Parse template | An `AWS::Route53::RecordSet` (Alias `A`) exists, gated by a `Condition` keyed on `HostedZoneId` non-empty (mirrors `infra/frontend.yaml`'s `CreateDnsRecords` pattern) |
| TC-7.1f | `ApiUrl` output has a trailing slash | UC-7 (primary) | AUTO-LOCAL (H-CFN) | — | Inspect the `ApiUrl` Output's `!Sub`/`!Join` value | Resolves to `https://api.<domain>/` — string literally ends in `/` |
| TC-7.1g | No `CorsConfiguration` on the HTTP API | UC-7 (primary) | AUTO-LOCAL (H-CFN) | — | Parse template | `AWS::ApiGatewayV2::Api` resource has no `CorsConfiguration` property at all |
| TC-7.1h | No `Export`/`Fn::ImportValue` cross-stack coupling; function name is a plain parameter | UC-7 (primary) | AUTO-LOCAL (H-CFN) | — | Parse template | No `Export:` block under `Outputs`; no `Fn::ImportValue` anywhere; `Parameters` includes a backend-Lambda-function-name input |
| TC-7.1i | Live health check | UC-7 (primary), AC-3 | MANUAL-AWS | Stack deployed, cert issued, DNS live | `curl -fsS https://api.<domain>/api/health` | HTTP 200 |
| TC-7.1j | Live SPA + backend over custom domains | UC-7 (primary), AC-2 | MANUAL-AWS | Both stacks deployed and DNS live | Visit `https://app.<domain>/` in a browser | SPA loads over valid HTTPS; a signed-in user's meeting list loads via calls to `https://api.<domain>` |

### 7.2 Alternative/error/edge flows

| TC | Title | Maps to | Type | Preconditions | Steps | Expected Result |
|---|---|---|---|---|---|---|
| TC-7.2 | Plain HTTP is redirected/refused | UC-7-A1 | MANUAL-AWS | Domains live | `curl -I http://app.<domain>/`; `curl http://api.<domain>/api/health` | Frontend: 301/302 to HTTPS. Backend: connection fails/refused — no plaintext 200 ever returned |
| TC-7.3 | Function URL fallback still works | UC-7-A2 | MANUAL-AWS | Backend stack deployed | `curl -fsS https://<id>.lambda-url.us-east-1.on.aws/api/health` | HTTP 200, independent of the custom domain |
| TC-7.4 | DNS propagation check always reports state before exiting | UC-7-E1 | AUTO-LOCAL (H-MAKE) partial + MANUAL-AWS full | `dig`/`curl` stubbed to simulate NXDOMAIN/non-200 | `make aws-backend-https-check` | Target always prints the `dig +short` line and the HTTP status line first, and only then exits non-zero with a propagation-in-progress hint — it never skips straight to a hard failure without printing that status; MANUAL-AWS: repeat against live DNS immediately after a domain attach and again a few minutes later, confirming the same status-then-hint behavior as DNS converges |
| TC-7.5 | Wrong/unissued cert ARN causes TLS mismatch | UC-7-E2 | MANUAL-AWS | Stack deployed with an incorrect `CertificateArn` | `curl -v https://api.<domain>/api/health` | TLS handshake / certificate mismatch error; fixed by redeploying with the correct `ISSUED` ARN from UC-6 |
| TC-7.6 | Stale `CorsOrigins` blocks the browser (see Section 13) | UC-7-E3 | MANUAL-AWS | — | Cross-referenced — see TC-13.3 | See TC-13.3 |
| TC-7.7 | Backend/frontend domain attach order is independent | UC-7-X1 | AUTO-LOCAL (H-MAKE) | — | Inspect `make -n` dependency graph for `aws-backend-https` and `aws-frontend-https` | Neither target lists the other as a prerequisite; either order is valid |
| TC-7.8 | 30s API Gateway ceiling matches Lambda's own 30s timeout | UC-7-X2 | AUTO-LOCAL (H-CFN) | Both templates exist | Compare `infra/backend.yaml`'s Lambda `Timeout` default to `infra/backend-domain.yaml`'s `TimeoutInMillis` | `Timeout == 30` and `TimeoutInMillis == 30000` (equal budgets, gateway never the shorter one) |

---

## 8. Manual `make deploy-backend` / `make deploy-frontend` (AC-4, AC-5, AC-16)

| TC | Title | Maps to | Type | Preconditions | Steps | Expected Result |
|---|---|---|---|---|---|---|
| TC-8.1 | `deploy-backend` chain never touches CloudFormation | UC-8 (primary) | AUTO-LOCAL (H-MAKE) | — | `make -n deploy-backend` | Prints exactly: `aws-check` → `aws-backend-push` → `aws-backend-update-code` (+ `function-updated-v2` wait) → `aws-backend-migrate`; no `aws-backend-stack`/`cloudformation deploy` line anywhere |
| TC-8.2 | `deploy-frontend` upload ordering is safe (no missing-chunk window) | UC-8 (primary) | AUTO-LOCAL (H-MAKE) | `aws s3 sync`/`aws s3 cp` stubbed, logging call order | `make deploy-frontend` | Stub call log shows exactly this order: (a) `aws s3 sync` the hashed asset files excluding `index.html`, with long-lived cache headers, WITHOUT `--delete`; (b) `aws s3 cp index.html` with `Cache-Control: no-cache`; (c) a second `aws s3 sync --delete --exclude index.html` pass (removing now-orphaned old assets only after the new `index.html` is already live); (d) `aws cloudfront create-invalidation` |
| TC-8.3 | Legacy static `.env` keys still work locally | UC-8-A1 | AUTO-LOCAL (H-MAKE) | `.env` has non-empty static `AWS_ACCESS_KEY_ID`/`SECRET` | `make aws-check` | `aws sts get-caller-identity` stub succeeds regardless of credential source |
| TC-8.4 | Dirty-tree `TAG` is unique across two runs | UC-8-E1 | AUTO-LOCAL (H-MAKE) | Temp git repo with an uncommitted change | Compute `TAG` twice, sleeping ≥1s between (or stub `date`) | Both values match `<40-hex-sha>-dirty-<timestamp>`; the two timestamps differ |
| TC-8.5 | Missing git repo falls back to bare timestamp | UC-8-E2 | AUTO-LOCAL (H-MAKE) | Temp dir with no `.git`; `git rev-parse HEAD` stub fails | Compute `TAG` | Value matches `date +%Y%m%d%H%M%S` format only (no SHA component); target does not hard-fail |
| TC-8.6 | `VITE_API_URL` prefers `backend-domain`'s `ApiUrl`, falls back to `backend.yaml`'s | UC-8-E3 | AUTO-LOCAL (H-MAKE) | `describe-stacks` stub returns an `ApiUrl` output for both `backend-domain` and `backend` stacks | Resolve `VITE_API_URL` with `DOMAIN`/`BACKEND_DOMAIN` unset | Value equals the `backend-domain` stack's `ApiUrl`; when that stack's output stub is empty, falls back to `backend.yaml`'s |
| TC-8.7 | Expired SSO session fails `aws-check` clearly | UC-8-E4 | AUTO-LOCAL (H-MAKE) | `aws sts get-caller-identity` stub returns an expired-token error | `make aws-check` | Non-zero exit; message references credentials being missing/invalid/expired |
| TC-8.8 | `.env`-sourced empty credentials never override non-empty env credentials (FR-19b, AC-16) | UC-8-E5 | AUTO-LOCAL (H-MAKE) | `.env` present with `AWS_ACCESS_KEY_ID=` (empty); shell has `AWS_ACCESS_KEY_ID=x AWS_SECRET_ACCESS_KEY=y AWS_SESSION_TOKEN=z` exported | `AWS_ACCESS_KEY_ID=x AWS_SECRET_ACCESS_KEY=y AWS_SESSION_TOKEN=z make aws-check` (stubbed `aws sts get-caller-identity` echoes the env it received) | Stub receives `x`/`y`/`z` unchanged — `.env`'s empty values never clobber them |
| TC-8.9 | Re-running `deploy-backend` twice with the same `TAG` is idempotent in effect | UC-8-X1 | AUTO-LOCAL (H-MAKE) | docker build/push stub produces a different fake digest each call | `make deploy-backend TAG=fixed` twice | Both runs succeed (exit 0); `update-function-code`+wait and `migrate` stubs invoked successfully both times; no error from the digest change |
| TC-8.10 | `make help` (and bare `make`) never creates `.env` | UC-8-X2 | AUTO-LOCAL (H-MAKE) | Temp dir, no `.env` | `make help`; `make` (default) | `.env` absent from disk after both commands |
| TC-8.11 | `-include $(wildcard .env)` guard prevents implicit rebuild | UC-8 (FR-19c) | AUTO-LOCAL (H-MAKE) | Temp dir, no `.env`, no other target requested | Static: grep `Makefile` for `-include $(wildcard .env)` (not bare `-include .env`). Dynamic: `make help` | Guarded form present; `.env` never created as a side effect of the include directive itself |
| TC-8.12 | `.env.example`'s AWS static-key lines are commented (FR-19a) | UC-8 (FR-19a) | AUTO-LOCAL (H-DOCS/grep) | — | `grep -nE '^#?\s*AWS_(ACCESS_KEY_ID|SECRET_ACCESS_KEY)=' .env.example` | Both lines match `^# AWS_ACCESS_KEY_ID=` / `^# AWS_SECRET_ACCESS_KEY=` (commented, not bare `KEY=`) |
| TC-8.13 | `.env:` rule is a prerequisite only of `start`/`test-back` | UC-8 (FR-19c) | AUTO-LOCAL (H-MAKE) | Temp dir, no `.env`, docker/aws stubbed | 1. Grep `Makefile` for which targets declare `.env` as a prerequisite 2. Run `make deploy-backend` and `make deploy-frontend` | Only `start`/`test-back` (and `up`, transitively via `start`) list `.env` as a prerequisite; `deploy-backend`/`deploy-frontend` runs leave `.env` absent |

---

## 9. CI/CD workflow (`.github/workflows/deploy.yml`, `code-style.yml`) — AC-4, AC-5, AC-8, AC-9, AC-10, AC-11, AC-12

### 9.1 Static workflow-file assertions

| TC | Title | Maps to | Type | Preconditions | Steps | Expected Result |
|---|---|---|---|---|---|---|
| TC-9.1a | Trigger/permissions shape | UC-9 (primary) | AUTO-LOCAL (H-WORKFLOW) | `deploy.yml` exists | Parse workflow | `on: push: branches: [main]` only, no `workflow_dispatch`/`pull_request`; workflow-level `permissions: contents: read` only; `id-token: write` appears only under the `deploy` job |
| TC-9.1b | `lint` job reuses `code-style.yml` via `workflow_call` | UC-9 (primary), FR-21 | AUTO-LOCAL (H-WORKFLOW) | — | Parse `lint` job | `uses: ./.github/workflows/code-style.yml`; no duplicated ruff/eslint/tsc/cfn-lint steps inline in `deploy.yml` |
| TC-9.1c | `back-test` job runs pytest directly against a postgres service | UC-9 (primary), FR-22 | AUTO-LOCAL (H-WORKFLOW) | — | Parse `back-test` job | `services.postgres.image == postgres:16-alpine`; `TEST_DATABASE_URL` env set pointing at it; a step runs `uv run pytest` inside `back/` (not `make test`) |
| TC-9.1d | `front-test` job runs `npm ci && npm test` | UC-9 (primary), FR-22 | AUTO-LOCAL (H-WORKFLOW) | — | Parse `front-test` job | Steps run `npm ci` then `npm test` inside `front/` |
| TC-9.1e | `deploy` job gating and runner | UC-9 (primary), FR-22/FR-23/FR-32 | AUTO-LOCAL (H-WORKFLOW) | — | Parse `deploy` job | `needs: [lint, back-test, front-test]`; no `environment:` key; `runs-on: ubuntu-24.04-arm` |
| TC-9.1f | `code-style.yml` is reusable and no longer double-triggers on push | UC-9 (primary), FR-21 | AUTO-LOCAL (H-WORKFLOW) | `code-style.yml` exists | Parse `code-style.yml` | Has `workflow_call:` trigger; `push: branches: [main]` trigger removed; `pull_request:` trigger retained |
| TC-9.2 | Actions pinned by SHA; checkout hardened; role sourced from `vars.*` | UC-9 (primary), FR-23 | AUTO-LOCAL (H-WORKFLOW) | — | Regex every `uses:` line in `deploy.yml` | Every third-party action matches `owner/repo@[0-9a-f]{40}` (no mutable tag); the `checkout` step sets `persist-credentials: false`; `configure-aws-credentials`'s `role-to-assume: ${{ vars.AWS_DEPLOY_ROLE_ARN }}` |
| TC-9.3 | `arm64` image build guaranteed | UC-9 (primary), FR-32, AC-11 | AUTO-LOCAL (H-WORKFLOW) | — | Inspect `deploy` job's runner + build step | Either `runs-on: ubuntu-24.04-arm` with a plain `docker build`, or (if a non-arm runner is ever used) the build step explicitly passes `--platform linux/arm64` via buildx — never silently defaults to amd64 |

### 9.2 Live pipeline behavior

| TC | Title | Maps to | Type | Preconditions | Steps | Expected Result |
|---|---|---|---|---|---|---|
| TC-9.4 | Merge commit behaves identically to a direct push | UC-9-A1 | AUTO-CI | Approved PR ready to merge | Merge PR into `main` | Pipeline runs identically to a direct push (same jobs, same gating) |
| TC-9.5 | Red pipeline on lint failure, green after fix (AC-9) | UC-9-E1 | AUTO-CI | Working pipeline | 1. Push a commit with a deliberate lint violation (unused import) to `main` 2. Push a follow-up fix | Run 1: `code-style.yml`-reused job fails red; `deploy` job never starts. Run 2: fully green, ends in successful deploy |
| TC-9.6 | Migration failure after code update blocks frontend deploy | UC-9-E2 | AUTO-LOCAL (H-MAKE) primary; AUTO-CI confirmatory | `aws lambda invoke` stub returns a `FunctionError` for the migrate payload | `make deploy-backend` (shell `&&` chain with `deploy-frontend`) | `deploy-backend` exits non-zero after `update-function-code`+wait already succeeded; `deploy-frontend` never runs (chain short-circuits) |
| TC-9.7 | Image push failure stops before code update | UC-9-E3 | AUTO-LOCAL (H-MAKE) | `docker ... --push` stub fails | `make deploy-backend` | Exits non-zero before `aws-backend-update-code`/`aws-backend-migrate` stubs are ever invoked |
| TC-9.8 | CloudFront invalidation failure after successful S3 sync | UC-9-E4 | AUTO-LOCAL (H-MAKE) | `aws cloudfront create-invalidation` stub fails; `aws s3 sync` stub succeeds | `make deploy-frontend` | Non-zero exit; s3-sync stub WAS called; re-running `deploy-frontend` after fixing IAM/API succeeds (idempotent) |
| TC-9.9a | `.env`-absence assertion step exists before deploy steps (FR-19d) | UC-9-E5 | AUTO-LOCAL (H-WORKFLOW) | — | Parse `deploy` job's steps | A step running `test ! -f .env` (or equivalent) appears before the `make deploy-backend` step |
| TC-9.9b | Live `.env`-absence assertion fails the job when triggered (AC-12) | UC-9-E5 | AUTO-CI | Temporary test workflow/branch that deliberately writes a stray `.env` with `AWS_ACCESS_KEY_ID=` before the assertion step | Trigger the workflow | Assertion step fails the job; deploy steps never run |
| TC-9.10a | Workflow-level concurrency group declared correctly | UC-9-X1/X1a | AUTO-LOCAL (H-WORKFLOW) | — | Parse `deploy.yml` top level | `concurrency: { group: deploy-${{ github.ref }}, cancel-in-progress: false }` declared at workflow level, not per-job |
| TC-9.10b | Two rapid pushes queue, never run concurrently (AC-10) | UC-9-X1 | AUTO-CI | Working deploy pipeline | Push twice to `main` within a few seconds | Actions history shows one run "in progress" and the second "queued", never two simultaneously "in progress" |
| TC-9.10c | Three rapid pushes: middle queued run reported cancelled (AC-10) | UC-9-X1a | AUTO-CI | Working deploy pipeline | Push three times to `main` within a few seconds | First run proceeds; second (queued) run is reported `cancelled` once the third supersedes it in the queue; third eventually runs; final deploy reflects the third commit |
| TC-9.11 | No path filters — docs/infra-only pushes still run the full pipeline | UC-9-X2 | AUTO-LOCAL (H-WORKFLOW) | — | Parse `deploy.yml`'s `on.push` | No `paths:`/`paths-ignore:` key present |

---

## 10. OIDC trust boundary — unauthorized callers denied (AC-7)

| TC | Title | Maps to | Type | Preconditions | Steps | Expected Result |
|---|---|---|---|---|---|---|
| TC-10.1a | Trust policy structurally rejects any non-exact `sub` (static) | UC-10-E1 | AUTO-LOCAL (H-CFN) | `infra/github-oidc.yaml` | Assert the trust condition operator is `StringEquals` (never `StringLike`) on `sub` | Any `sub` differing by even one path segment (e.g. `ref:refs/heads/feature/x`) cannot match |
| TC-10.1b | Live denial from a non-`main` branch | UC-10-E1 | MANUAL-AWS | Deploy role live; a workflow on a non-`main` branch attempts `AssumeRoleWithWebIdentity` | Trigger a test workflow run on `feature/x` that calls `aws sts get-caller-identity` after `configure-aws-credentials` | `AssumeRoleWithWebIdentity` denied (`AccessDenied`) |
| TC-10.2 | PR runs cannot deploy (structural + trust) | UC-10-E2 | AUTO-LOCAL (H-WORKFLOW) | — | Confirm `deploy.yml` has no `pull_request:` trigger (cross-ref TC-9.1a) | No PR-triggered run of `deploy.yml` can ever attempt the assumption in normal operation |
| TC-10.3 | Fork-of-fork denied (owner mismatch) | UC-10-E3 | MANUAL-AWS | A fork of `MasterDay3/OneTwoThree` with the same workflow, pushed to its own `main` | Trigger the fork's workflow | `AssumeRoleWithWebIdentity` denied — `sub` owner segment (`repo:<other-org>/OneTwoThree...`) fails `StringEquals` |
| TC-10.4 | Unrelated repo with a leaked ARN denied | UC-10-E4 | MANUAL-AWS | Unrelated repo configured with the (test) role ARN | Trigger a workflow there | Denied identically to TC-10.3 regardless of that repo's branch |
| TC-10.5 | Audience mismatch denied | UC-10-E5 | AUTO-LOCAL (H-CFN) | — | Assert a distinct `StringEquals` condition on `token.actions.githubusercontent.com:aud == sts.amazonaws.com` exists independently of the `sub` condition | A token with any other `aud` fails this condition regardless of `sub` |
| TC-10.6a | No job-level `environment:` on `deploy` (static) | UC-10-E6 | AUTO-LOCAL (H-WORKFLOW) | — | Cross-ref TC-9.1e | Confirmed absent |
| TC-10.6b | Adding `environment:` breaks a legitimate `main` push (live demonstration) | UC-10-E6, AC-7 | MANUAL-AWS | Test copy of the role/workflow | Add `environment: production` to the `deploy` job on a test branch pointed at a test role with the standard trust condition; push | `AssumeRoleWithWebIdentity` denied even though the push is to the trusted ref — confirms `sub`-shape sensitivity per AC-7 |
| TC-10.7 | IAM condition governs *who*, not *when* (documentation-alignment check) | UC-10-X1 | AUTO-LOCAL (H-WORKFLOW + H-CFN) | — | Confirm `deploy.yml` has neither `workflow_dispatch` nor `schedule` triggers today (cross-ref TC-9.1a/TC-9.11) | No such trigger exists; if one were ever added it would still satisfy the trust policy — noted so a reviewer doesn't conflate the two controls |
| TC-10.8 | Deploy role's own permission policy has no unscoped/forbidden actions | UC-10-X2, AC-7 | AUTO-LOCAL (H-CFN) | — | Parse the role's policy document; diff its action list per service against the FR-18 itemized list | Exact action-list match per service; the only `Resource: "*"` statement is `ecr:GetAuthorizationToken`; no `iam:*`, `cognito-idp:*`, `acm:*`, `route53:*`, or any `cloudformation:*Stack*`/`ExecuteChangeSet`/`CreateStack`/`UpdateStack`/`DeleteStack` action anywhere |

---

## 11. Rollback — `make aws-backend-rollback TAG=<sha>` (AC-6)

| TC | Title | Maps to | Type | Preconditions | Steps | Expected Result |
|---|---|---|---|---|---|---|
| TC-11.1 | Happy-path rollback: verify-then-point, no rebuild/migrate | UC-11 (primary) | AUTO-LOCAL (H-MAKE) | `aws ecr describe-images` stub finds the tag | `make aws-backend-rollback TAG=<sha>` | `describe-images` called first; then `update-function-code --image-uri ...:<sha>` + `function-updated-v2` wait; docker build/push and migrate stubs are NEVER invoked |
| TC-11.2 | CI-native rollback via revert commit uses a new tag | UC-11-A1 | AUTO-CI / MANUAL-AWS | A bad commit is live | Revert it, push the revert to `main` | New ECR tag equals the revert commit's own SHA (old tag is not reused/re-pushed) |
| TC-11.3 | Tag not found fails loudly before any Lambda call | UC-11-E1 | AUTO-LOCAL (H-MAKE) | `describe-images` stub returns `ImageNotFoundException`/empty | `make aws-backend-rollback TAG=<never-pushed-sha>` | Non-zero exit with a clear error; `update-function-code` stub NEVER invoked (no partial rollback) |
| TC-11.4 | Rollback never invokes a migration | UC-11-E2 | AUTO-LOCAL (H-MAKE) | — | Static: grep the `aws-backend-rollback` recipe for any `aws-backend-migrate`/`lambda invoke ... migrate` reference. Dynamic: run TC-11.1 and assert zero migrate-stub invocations | Recipe contains no migration step; runtime confirms zero calls |
| TC-11.5 | `TAG` is always derived from the current commit, never an arbitrary reused value in `deploy-backend` | UC-11-X1 | AUTO-LOCAL (H-MAKE) | — | Cross-ref TC-8.4/TC-8.5 | `deploy-backend`'s default `TAG` always reflects the checked-out commit; "re-deploying a bad SHA" only happens via a new commit with its own new SHA, never a silent tag reuse |
| TC-11.6 | Live rollback demonstration (AC-6) | UC-11 (primary) | MANUAL-AWS | At least two prior successful deploys exist in ECR (within the 5-image retention window) | 1. `make aws-backend-rollback TAG=<previous-good-sha>` 2. `aws lambda get-function --query Code.ImageUri` 3. `make aws-backend-rollback TAG=<never-pushed-sha>` | Step 2's `ImageUri` matches the previous tag's digest, no migration ran, no rebuild occurred. Step 3 fails loudly instead of building/pushing new code under that tag |

---

## 12. Teardown — `make aws-destroy` (NFR-3)

| TC | Title | Maps to | Type | Preconditions | Steps | Expected Result |
|---|---|---|---|---|---|---|
| TC-12.1 | Static ordering: `backend-domain` destroyed before `backend`; OIDC/ACM left standing by default | UC-12 (primary) | AUTO-LOCAL (H-MAKE) | `DESTROY_OIDC` unset | `make -n aws-destroy` | Order is `aws-frontend-destroy` → `aws-backend-domain-destroy` → `aws-backend-destroy` → `aws-cognito-destroy`; no `aws-oidc-destroy`/ACM-delete step appears by default (opt-in only, see TC-12.9) |
| TC-12.2 | Declining a prompt skips only that stack | UC-12-A1 | MANUAL-AWS | Live stacks | Run `make aws-destroy`, answer `N` to one prompt | That stack survives; teardown continues to the remaining stacks |
| TC-12.3 | Non-empty bucket (versioning) blocks frontend stack delete | UC-12-E1 | MANUAL-AWS | Versioning was enabled on the frontend bucket at some point | `make aws-frontend-destroy` | `delete-stack` fails ("bucket not empty"); manually removing all versions then re-running succeeds |
| TC-12.4 | Aurora snapshot/delete-wait failure | UC-12-E2 | MANUAL-AWS | A dependent SG/subnet still referenced elsewhere | `make aws-backend-destroy` | `wait stack-delete-complete` hangs/fails; console shows the specific blocking resource; retry after resolving succeeds |
| TC-12.5 | API Gateway domain delete blocked by lingering alias | UC-12-E3 | MANUAL-AWS | An out-of-band manually-created alias record still points at the API GW domain | `make aws-backend-domain-destroy` | Stack delete fails until the alias is removed (or the record is CFN-managed and deletes with the stack) |
| TC-12.6a | Correct order prevents the dangling-permission failure by construction | UC-12-E4 | AUTO-LOCAL (H-MAKE) | — | Cross-ref TC-12.1 | `make aws-destroy` can never hit this case because the static order is enforced |
| TC-12.6b | Deliberately wrong manual order reproduces the documented partial failure | UC-12-E4 | MANUAL-AWS | Both stacks live | Manually run `make aws-backend-destroy` then `make aws-backend-domain-destroy` (reversed order) | `backend-domain` stack's later deletion partially fails (permission/integration reference a nonexistent function) — matches the documented failure mode |
| TC-12.7 | Re-running teardown after success does not hard-crash | UC-12-X1 | MANUAL-AWS | Full teardown already completed | `make aws-destroy` again | Each already-deleted stack's status check/describe-stacks reports "does not exist"; aggregate does not crash |
| TC-12.8 | OIDC provider retained after its stack is deleted | UC-12 (postconditions) | MANUAL-AWS | OIDC stack deployed | Delete the OIDC/deploy-role stack; `aws iam list-open-id-connect-providers` | Provider for `token.actions.githubusercontent.com` still present (only the role/policy were removed) — cross-ref TC-4.1's `DeletionPolicy: Retain` check |
| TC-12.9 | `DESTROY_OIDC=1` opts into also deleting the OIDC/deploy-role stack, last | UC-12 (postconditions, extended) | AUTO-LOCAL (H-MAKE) | `DESTROY_OIDC=1` | `make -n aws-destroy DESTROY_OIDC=1` | `aws-oidc-destroy` is appended as the final step, after `aws-cognito-destroy`; the underlying `AWS::IAM::OIDCProvider` resource itself is still retained (`DeletionPolicy: Retain`, cross-ref TC-4.1) even though the role/policy stack is now gone |

---

## 13. CORS + Cognito redirect wiring for the custom domain

| TC | Title | Maps to | Type | Preconditions | Steps | Expected Result |
|---|---|---|---|---|---|---|
| TC-13.1 | Preflight + actual request succeed end-to-end | UC-13 (primary) | MANUAL-AWS | `app.<domain>`/`api.<domain>` live; `CorsOrigins` and Cognito redirect URLs updated (FR-9) | From a signed-in browser session at `https://app.<domain>/home`, load the meetings list (devtools network tab) | `OPTIONS` preflight to `api.<domain>` returns the right `Access-Control-Allow-*` headers from FastAPI's `CORSMiddleware`; subsequent `GET`/`POST` succeeds; no gateway-level CORS config involved |
| TC-13.2 | CloudFront domain and custom domain both remain allowed | UC-13-A1 | AUTO-LOCAL (H-MAKE) | `SiteOrigins` stack-output stub contains both the CloudFront domain and `app.<domain>` | Run the `aws-frontend-cors` origin-derivation logic | Resulting `CorsOrigins` value is a comma-joined list retaining both entries (append, not replace) |
| TC-13.3 | Stale `CorsOrigins` blocks the browser before the request is even sent | UC-13-E1 | MANUAL-AWS | `CorsOrigins` intentionally not yet refreshed after domain went live | Load `https://app.<domain>/home` and attempt to fetch meetings | Preflight fails; browser never sends the actual `GET`/`POST`; backend route handler is never invoked (confirmed via absence of a server-side log entry) |
| TC-13.4 | Missing Cognito redirect URL breaks sign-in | UC-13-E2 | MANUAL-AWS | `CallbackUrls` not yet updated with `https://app.<domain>/auth/callback` | Attempt Google sign-in via the Hosted UI from `app.<domain>` | Hosted UI shows a `redirect_uri` mismatch error instead of returning to the app |
| TC-13.5 | Origin not in the allow-list is correctly blocked | UC-13-X1 | MANUAL-AWS | — | From a script on an unrelated origin, `fetch('https://api.<domain>/api/meetings')` | Request is blocked by CORS (unchanged pre-existing behavior, not a regression) |

---

## 14. Aurora Serverless v2 cold start through the API Gateway front door (NFR-5)

| TC | Title | Maps to | Type | Preconditions | Steps | Expected Result |
|---|---|---|---|---|---|---|
| TC-14.1a | Static timeout budgets match (no regression possible via the template) | UC-14 (primary) | AUTO-LOCAL (H-CFN) | — | Cross-ref TC-7.8 | `TimeoutInMillis == 30000`, Lambda `Timeout == 30` |
| TC-14.1b | Live cold-start request succeeds within budget | UC-14 (primary) | MANUAL-AWS | Aurora paused (5+ idle minutes) | `curl -w "\ntime_total=%{time_total}\n" -o /dev/null -s https://api.<domain>/api/meetings` (with a valid token) | HTTP 200; `time_total` roughly ~15s, well under 30s |
| TC-14.2 | Baseline via raw function URL (no gateway timeout in path) | UC-14-A1 | MANUAL-AWS | Aurora paused | `curl -w "%{time_total}" https://<id>.lambda-url.us-east-1.on.aws/api/meetings` | Same ~15s cold-start latency observed, succeeds with no API-Gateway-imposed cap |
| TC-14.3 | Regression guard: `TimeoutInMillis` can never silently drop below 30000 | UC-14-E1 | AUTO-LOCAL (H-CFN) | — | Assert the value is a literal `30000`, not a parameter with a lower default | Confirmed literal, not configurable below 30000 without an explicit template edit |
| TC-14.4 | Resume exceeding the 30s combined budget (accepted risk) | UC-14-E2 | MANUAL-AWS (best-effort, informational) | Unusual load / simulated AWS-side delay | Repeat TC-14.1b under load | If it fails, failure is a gateway/Lambda timeout — documented as an accepted, out-of-scope risk in `docs/lab2-discussion.md` (cross-ref TC-15.1), not a functional defect of this feature |
| TC-14.5 | Two concurrent requests during resume both succeed | UC-14-X1 | MANUAL-AWS | Aurora paused | Fire two `curl` requests to `/api/meetings` in parallel immediately after confirming pause | Both eventually return 200 within the ~15s + normal-latency budget |

---

## 15. `docs/lab2-discussion.md` (AC-14)

| TC | Title | Maps to | Type | Preconditions | Steps | Expected Result |
|---|---|---|---|---|---|---|
| TC-15.1 | Every FR-27 topic is present with at least one file citation | UC-15 (primary) | AUTO-LOCAL (H-DOCS) | `docs/lab2-discussion.md` exists | Keyword/section search for each FR-27 topic (compose keys, readiness, Dockerfile vs compose, base images, backend layering, SQLAlchemy trade-offs, Alembic timing, CloudFront vs S3, ECR vs ECS, health-check contrast, CNAME-validation vs CNAME-routing, static key vs OIDC, 1000-orgs bottlenecks); for each, regex for a backtick-quoted path matching a real repo file | All topics present; each has ≥1 real-file citation pattern. Correctness of each citation's *content* is confirmed by MANUAL spot-check (see TC-15.2) |
| TC-15.2 | Reviewer spot-check of citations | UC-15-A1 | MANUAL-LOCAL | TC-15.1 passed | Reviewer opens 3–5 cited files/lines and confirms the claim matches | All spot-checked citations match reality; any mismatch is corrected in the doc (re-run TC-15.1) |
| TC-15.3 | Unflagged, unverifiable figures are treated as defects | UC-15-E1 | AUTO-LOCAL (H-DOCS) heuristic + MANUAL review | — | Regex for numeric claims (e.g. ACU counts, timing numbers) not adjacent to a citation or an explicit "not verified"/"approximate" flag | Heuristic flags any such occurrence for MANUAL review; final judgment is a human call per NFR-7 |
| TC-15.4 | Explicit Lambda + Aurora Serverless v2 (not ECS/ALB) acknowledgement (FR-28) | UC-15-E2 | AUTO-LOCAL (H-DOCS) + MANUAL review | — | Regex for a sentence explicitly stating Lambda+Aurora Serverless v2 vs the lab's ECS/ALB pattern | At least one unambiguous statement found; MANUAL review confirms no contradicting statement elsewhere in the doc |
| TC-15.5 | Manual submission artefacts explicitly marked manual (FR-29) | UC-15-E3 | AUTO-LOCAL (H-DOCS) | — | Regex for screenshot / live-URL / repo-link mentions co-located with "manual"/"not automated" | All three artefacts present and explicitly marked manual |
| TC-15.6 | Document reflects the as-built system, not the as-proposed design (FR-33) | UC-15-X1 | AUTO-LOCAL (H-DOCS) partial + MANUAL review | Infra actually implemented | Grep the doc for the literal `RoleName` (`$(PROJECT)-github-deploy`) and stack name (`backend-domain`) rather than placeholder text | Real identifiers appear; MANUAL review confirms no stale as-proposed claims that diverged during implementation |
| TC-15.7 | Cost/teardown note + rollback-window + schema-rollback caveat (FR-30) | UC-15 (primary) | AUTO-LOCAL (H-DOCS) | — | Keyword search for `aws-destroy`, "last 5 images"/"5-image" retention window, and a schema-rollback-does-not-revert caveat | All three present |

---

## 16. `README.md` / `back/app/lambda_handler.py` documentation consistency (FR-34, AC-15) and secrets hygiene (AC-8)

| TC | Title | Maps to | Type | Preconditions | Steps | Expected Result |
|---|---|---|---|---|---|---|
| TC-16.1 | `## Layout` is a pointer, not a duplicated listing | FR-34, AC-15 | AUTO-LOCAL (H-DOCS) | `README.md` | Extract the `## Layout` section body | Contains a markdown link to `PROJECT.md`; body is short (heuristic: under a small line-count threshold) and does not re-enumerate every subfolder |
| TC-16.2 | No lecturer-domain reference | FR-34, AC-15 | AUTO-LOCAL (H-DOCS/grep) | — | `git grep -i onetwothree.dobosevych.com README.md` | Zero matches |
| TC-16.3 | Static AWS keys not framed as the CI/deploy method | FR-34, AC-15 | AUTO-LOCAL (H-DOCS) heuristic + MANUAL review | — | Search for `AWS_ACCESS_KEY_ID`/"static key" occurrences near "CI"/"GitHub Actions" context | None found in a CI-framing context; local-only fallback mentions are allowed; ambiguous hits flagged for MANUAL review |
| TC-16.4 | Mermaid diagram shows both entry points | FR-34, AC-15 | AUTO-LOCAL (H-DOCS) | — | Parse the Mermaid block | Contains both an API Gateway/custom-domain node and a Lambda Function URL (fallback) node |
| TC-16.5 | `lambda_handler.py` docstring mentions both entry points | FR-34, AC-15 | AUTO-LOCAL (H-DOCS) | — | Read the module docstring | Mentions both the function-URL entry point and the new API Gateway custom-domain entry point |
| TC-16.6 | No committed long-lived key values | AC-8, NFR-1 | AUTO-LOCAL (H-DOCS/grep) | — | `git grep -nE 'AWS_(ACCESS_KEY_ID|SECRET_ACCESS_KEY)\s*=\s*[A-Za-z0-9/+=]{16,}'` across the repo | Zero matches (only empty/commented variable-name occurrences in `.env.example`/`Makefile`) |
| TC-16.7 | Workflow references `vars.AWS_DEPLOY_ROLE_ARN`, never a secret | AC-8, FR-20 | AUTO-LOCAL (H-WORKFLOW/grep) | — | Grep `deploy.yml` for `AWS_DEPLOY_ROLE_ARN` | Only `${{ vars.AWS_DEPLOY_ROLE_ARN }}` form appears; no `secrets.AWS_DEPLOY_ROLE_ARN` or `secrets.AWS_ACCESS_KEY_ID`/`secrets.AWS_SECRET_ACCESS_KEY` anywhere |
| TC-16.8 | Repo Settings confirm variable (not secret) configuration | AC-8 | MANUAL-AWS (GitHub UI, not grep-able) | Repo admin access | Open Settings → Secrets and variables → Actions | `AWS_DEPLOY_ROLE_ARN` listed under **Variables**; no AWS static-key secret is configured anywhere |
| TC-16.9 | New CFN templates pass `cfn-lint` | AC-1 (infra correctness), NFR-2 | AUTO-LOCAL (H-CFNLINT); also AUTO-CI (existing `infra` job) | `infra/backend-domain.yaml`, `infra/github-oidc.yaml` exist | `cfn-lint infra/*.yaml` | Zero errors/warnings requiring action on both new templates |

---

## Coverage Matrix — Use Case → Test Case

| Use Case | Scenarios | Test Cases |
|---|---|---|
| UC-1 | primary, A1, A2, A3, E1, E2, E3, X1, X2 | TC-1.1 – TC-1.9 |
| UC-2 | primary, A1, E1, E2, E3, X1 | TC-2.1 – TC-2.6 |
| UC-3 | primary, A1, E1, E2, X1 | TC-3.1 – TC-3.5 |
| UC-4 | primary, A1, A2, E1, E2, X1 | TC-4.1 – TC-4.8 |
| UC-5 | primary, A1, A2, E1, E2, E3, E4, X1, X2, X3 | TC-5.1 – TC-5.12 |
| UC-6 | primary, A1, A2, E1, E2, E3, X1, X2 | TC-6.1 – TC-6.10 |
| UC-7 | primary, A1, A2, E1, E2, E3, X1, X2 | TC-7.1a–j, TC-7.2 – TC-7.8 |
| UC-8 | primary, A1, E1, E2, E3, E4, E5, X1, X2 | TC-8.1 – TC-8.13 |
| UC-9 | primary, A1, E1, E2, E3, E4, E5, X1, X1a, X2 | TC-9.1a–f, TC-9.2 – TC-9.11 |
| UC-10 | E1, E2, E3, E4, E5, E6, X1, X2 | TC-10.1a/b – TC-10.8 |
| UC-11 | primary, A1, E1, E2, X1 | TC-11.1 – TC-11.6 |
| UC-12 | primary, A1, E1, E2, E3, E4, X1 | TC-12.1 – TC-12.9 |
| UC-13 | primary, A1, E1, E2, X1 | TC-13.1 – TC-13.5 |
| UC-14 | primary, A1, E1, E2, X1 | TC-14.1a/b – TC-14.5 |
| UC-15 | primary, A1, E1, E2, X1 | TC-15.1 – TC-15.7 |
| N/A (FR-34 README/docstring, AC-8 secrets hygiene) | — | TC-16.1 – TC-16.9 |

Every documented scenario tag (primary/-A/-E/-X, including the doubled sub-tags UC-9-X1a and UC-9-X1) has at least one mapped test case above.

---

## Coverage Matrix — Acceptance Criterion → Test Case

| AC | Summary | Test Cases |
|---|---|---|
| AC-1 | `PROJECT.md` verifiable against repo tree | TC-3.1 – TC-3.5, TC-16.9 |
| AC-2 | `app.<domain>` loads SPA over HTTPS, calls backend over HTTPS | TC-7.1j |
| AC-3 | `curl -fsS https://api.<domain>/api/health` → 200 | TC-7.1i |
| AC-4 | `deploy-backend`/`deploy-frontend` standalone, idempotent, no CFN deploy | TC-8.1, TC-8.2, TC-8.9, TC-9.1a–f |
| AC-5 | Pushed ECR tag is the full commit SHA, never `latest` | TC-8.4, TC-8.5, TC-9.3 (arm64 tag), TC-11.2 |
| AC-6 | Rollback updates Lambda to exact prior image; fails loudly on unknown tag | TC-11.1, TC-11.3, TC-11.6 |
| AC-7 | OIDC AssumeRole denied off-`main`/off-repo/PR/fork/`environment:`; role has no unscoped/forbidden perms | TC-10.1a/b – TC-10.8 |
| AC-8 | No committed long-lived keys; `AWS_DEPLOY_ROLE_ARN` is a repo variable | TC-16.6, TC-16.7, TC-16.8 |
| AC-9 | Red pipeline on lint failure blocks deploy; green after fix | TC-9.5 |
| AC-10 | Concurrency group queues/cancels correctly | TC-9.10a – TC-9.10c |
| AC-11 | Backend image is `linux/arm64` | TC-9.3 |
| AC-12 | `.env`-absence assertion fails the job when triggered | TC-9.9a, TC-9.9b |
| AC-13 | Empty-`DOMAIN` fail-fast; existing custom domain never silently detached | TC-6.9, TC-6.10, TC-5.10, TC-5.11, TC-5.12 |
| AC-14 | `docs/lab2-discussion.md` answers every FR-27 topic, as-built, cited | TC-15.1 – TC-15.7 |
| AC-15 | `README.md`/`lambda_handler.py` consistency | TC-16.1 – TC-16.5 |
| AC-16 | `make help`/bare `make` never creates `.env`; env credentials win over `.env` | TC-8.10, TC-8.11, TC-8.8 |
| AC-17 | `KEEP_IMAGE=1` never reverts the Lambda to a stale image | TC-5.9 |

Every AC (1–17) has at least one mapped test case.
