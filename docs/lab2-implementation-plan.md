# Lab 2 implementation plan: custom-domain HTTPS, deploy contract, OIDC CI/CD

The plan has 9 TDD slices in 5 waves. No files were written. The docs I read have 7 wording conflicts (listed under "Doc corrections to route before Wave 1"). Please send them to qa-planner / prd-writer before Wave 1 starts, or accept the interpretation this plan uses for each.

## 1. Prerequisites verified

- **PRD:** `/Users/mykolazab/Documents/GitHub/OneTwoThree/docs/PRD.md` §1 (FR-1..FR-34, NFR-1..7, AC-1..17).
- **Use cases:** `/Users/mykolazab/Documents/GitHub/OneTwoThree/docs/use-cases/lab2-deploy_use_cases.md`. It has UC-1..UC-15, 1268 lines, read in full.
- **QA test cases:** `/Users/mykolazab/Documents/GitHub/OneTwoThree/docs/qa/lab2-deploy_test_cases.md`. It has 16 sections (149 TCs per the caller).
  - Harness naming used in this plan: `infra/tests/test_cfn_*.py`, `test_makefile_*.py`, `test_workflows.py`, `test_docs_*.py`, and cert.sh tests via a "script stub harness".
- **Architecture review:** PASS. Review #2 approved with text fixes B1–B4, which are already in the PRD (see `.claude/scratchpad.md`).
- **Project CLAUDE.md:** none exists. I followed the conventions visible in `Makefile`, `infra/*.yaml`, `back/pyproject.toml` (ruff, line-length 110) and `.github/workflows/code-style.yml`.

## Harness conventions (defined in Slice 1, used by every later slice)

**Run command:** `uvx --with pyyaml pytest infra/tests -q`
- `infra/tests/pytest.ini` makes `infra/tests` the rootdir, so `back/pyproject.toml`'s `testpaths=["tests"]` and the DB-bound `back/tests/conftest.py` are never picked up.
- No `__init__.py`. Helper modules are importable in pytest's default prepend mode.

**Fixtures in `infra/tests/conftest.py`:**
- `repo_root`
- `cfn_template(name)`, backed by `infra/tests/cfn_yaml.py`. It is a SafeLoader multi-constructor for `!`-tags that converts every intrinsic to long form: `{"Ref":..}`, `{"Fn::Sub":..}`, `{"Fn::GetAtt":[..]}`, `{"Condition":..}`, `{"Fn::If":..}`, and so on.
- `sandbox`: a `MakeSandbox` built in a temp dir.
  - It copies `Makefile`, `.env.example` and `infra/` (templates plus scripts).
  - It creates `back/` and `front/dist/{index.html,assets/app.js}`.
  - It writes a stub `bin/` for `aws docker npm curl dig sleep uv uvx gh`, all backed by `infra/tests/stub_cmd.py`. The shebang is `sys.executable`, as an absolute path.
  - Each stub logs argv plus the full env as JSONL to `$STUB_LOG`. It answers from a JSON rules file (argv-substring match → stdout/exit).
  - Defaults are a happy path: `sts get-caller-identity` returns an identity, `lambda invoke` returns `None`, everything else exits 0 with empty output.

**Sandbox API:**
- `.stub(cmd, match=[...], stdout=, exit=)`
- `.write_env(text)`
- `.run(*targets, vars={}, env={}, dry_run=False)` returns a CompletedProcess
- `.calls(cmd=None)` returns the ordered call list
- `.print_vars(*names)`. This writes a wrapper makefile (`include Makefile` plus a `_print` target) instead of using `--eval`, because `--eval` does not exist in GNU make 3.81.

**Environment isolation (mandatory):**
- `PATH=<stubbin>:/usr/bin:/bin` and `HOME=<tmp>`.
- `AWS_*`, `DOMAIN`, `*_DOMAIN`, `TAG`, `MAKEFLAGS`, `MAKELEVEL` and `MFLAGS` are all removed.
- The `make` binary comes from `$MAKE_BIN` (default `make`). This lets tests run under `/usr/bin/make` (3.81, macOS) and `gmake`/ubuntu GNU make 4.x.
- Test code must pass `uvx ruff check --config back/pyproject.toml infra/tests` and `uvx ruff format --check --config back/pyproject.toml infra/tests`.

## Doc corrections to route before Wave 1 (qa-planner / prd-writer)

1. **TC-5.10** says "UsePreviousValue=true". `aws cloudformation deploy --parameter-overrides` has no such syntax. Parameters you leave out keep their previous value on an existing stack. The test therefore asserts that `DomainName=`, `CertificateArn=` and `HostedZoneId=` are absent from the overrides.
2. **TC-7.1b** refers to "ThrottleSettings". `AWS::ApiGatewayV2::Stage` has no such property. The real properties are `DefaultRouteSettings.ThrottlingBurstLimit` and `ThrottlingRateLimit`.
3. **TC-8.2 (3) vs PRD FR-11 (3).**
   - FR-11's main clause is correct: sync assets without delete, then upload `index.html`, and only then run the `--delete` pass.
   - FR-11's parenthetical ("fold delete into step 1") and TC-8.2's "no delete pass after index.html goes live" both describe today's unsafe order. Today's `sync --delete` removes old chunks while the old `index.html` is still live.
   - The plan follows the main clause. prd-writer should strike the parenthetical.
4. **TC-12.1 vs NFR-3.** UC-12 keeps the OIDC stack when you run `aws-destroy`. NFR-3 says `aws-destroy` "MUST be able to" tear it down.
   - Resolution: by default `aws-destroy` leaves the OIDC stack alone. With `DESTROY_OIDC=1` it also runs `aws-oidc-destroy`, last.
   - Add a TC for the opt-in path.
5. **TC-7.4** says "without hard-failing". Interpretation: the target always prints the DNS line and the HTTP status. It exits non-zero, with a propagation hint, only after printing both.
6. **TC-4.3 / UC-4-A2.** On the second run, `list-open-id-connect-providers` finds the provider that this stack created itself. That flips the condition and produces a non-empty changeset.
   - The Makefile must pass an empty `ExistingOidcProviderArn` when the stack output `ProviderManagedByStack=true`.
   - Add a TC for this.
7. **TC-3.1(a)** uses `find ... -maxdepth 2`. That also returns `node_modules`, `.venv`, `dist` and `__pycache__`.
   - Use tracked directories instead (`git ls-files`).
   - The glob-based completeness check moves to Slice 9. Otherwise it would fail as soon as Wave 2 adds new templates.

---

## 2. Implementation plan

### Slice 1: Infra test harness scaffold + characterization tests of current behavior
- **Wave:** 1
- **Use cases:** UC-1-A2, UC-5-A1, UC-5-E1, UC-5-E2, UC-5-E3, UC-5-X1, UC-6-A1, UC-6-E1, UC-6-E2, UC-6-E3, UC-6-X1, UC-6-X2, UC-8-A1, UC-13-A1
- **FR/AC:** enabling infra for all ACs; FR-9 (CORS derivation, existing)
- **Files:**
  - `infra/tests/pytest.ini` (new)
  - `infra/tests/conftest.py` (new)
  - `infra/tests/cfn_yaml.py` (new)
  - `infra/tests/stub_cmd.py` (new)
  - `infra/tests/test_cfn_loader.py` (new)
  - `infra/tests/test_makefile_baseline.py` (new)
  - `infra/tests/test_cert_script.py` (new)
- **Changes:**
  - Build the fixtures described in "Harness conventions" above.
  - No production files change. This slice only pins down current behavior so that Slices 3/6/7 cannot regress it.
- **Tests first (these characterization tests must pass against today's code):**
  - `test_cfn_loader.py`:
    - All 4 existing `infra/*.yaml` load.
    - `backend.yaml` `Parameters.Timeout.Default == 30`.
    - Intrinsics come out in long form.
  - `test_makefile_baseline.py`:
    - TC-1.3: an existing `.env` is untouched by `make start` (docker stubbed).
    - TC-5.2: `make -n aws-deploy` order.
    - TC-5.4: missing Cognito exits before any `cloudformation deploy` call.
    - TC-5.5: "Backend not deployed" appears before any npm/s3 call.
    - TC-5.6: all 4 `*-stack`/`-ecr` recipes use `clear_failed_stack`.
    - TC-5.8: `ARCH=amd64` produces `--platform linux/amd64`.
    - TC-8.3: static `.env` keys reach the aws stub.
    - TC-13.2: `CORS_ORIGINS_AWS` keeps both CloudFront and custom origins, via `print_vars` with a stubbed `SiteOrigins`.
    - Isolation test: `shutil.which("aws", path=sandbox PATH)` is the stub.
  - `test_cert_script.py` (runs `infra/scripts/cert.sh` directly with stubs):
    - TC-6.2 reuse ISSUED: no `request-certificate`, prints `Status: ISSUED`.
    - TC-6.4: no zone produces manual CNAME instructions and no UPSERT.
    - TC-6.5: `sleep` stubbed; 20 empty polls produce the "Timed out waiting for ACM…" message on stderr and a non-zero exit.
    - TC-6.6: EXPIRED is not reused.
    - TC-6.7: two distinct UPSERT names go to the same zone.
    - TC-6.8: re-run after ISSUED is a no-op.
- **Verify:**
  - `uvx --with pyyaml pytest infra/tests -q`
  - `MAKE_BIN=/usr/bin/make uvx --with pyyaml pytest infra/tests/test_makefile_baseline.py -q` (macOS make 3.81)
  - `MAKE_BIN=gmake ...` if GNU make is installed
  - `uvx ruff check --config back/pyproject.toml infra/tests && uvx ruff format --check --config back/pyproject.toml infra/tests`
  - `git status --porcelain` shows only new `infra/tests/*` files
- **Done when:**
  - The pytest command exits 0 with ≥20 tests passing under both make versions available locally.
  - No test calls a real `aws` binary: every AWS call in the stub log comes from the stub.
  - After the run, no file appears in the repo root: in particular, no `.env` is created by the tests.
- **Prod LOC:** 0 (test infra about 250 lines)
- **Security pre-review:** no

### Slice 2: `PROJECT.md` repository contract (as of today)
- **Wave:** 1
- **Use cases:** UC-3, UC-3-A1, UC-3-E1, UC-3-E2, UC-3-X1, UC-1 (documented behavior)
- **FR/AC:** FR-1..FR-5, NFR-7; AC-1
- **Files:**
  - `PROJECT.md` (new)
  - `infra/tests/test_docs_project.py` (new)
- **Changes:** write `PROJECT.md` covering:
  - **Folders:** top-level folders (`back/ front/ infra/ .github/ docs/`), plus the README-layout subfolders (`back/app`, `back/app/routers`, `back/app/services`, `back/alembic`, `back/tests`, `front/src/pages`, `front/src/components`, `front/src/components/ui`, `front/src/hooks`, `front/src/lib`).
  - **Compose services:** each `compose.yaml` service with image/build, ports, `depends_on` + `service_healthy`, and its healthcheck (`pg_isready`; `urllib.request` against `/api/health`; frontend has none).
  - **API contract for `GET`/`POST /api/meetings`:**
    - Fields and types taken from `back/app/schemas.py`.
    - Status codes checked in the source: 200 GET; 201 POST (`back/app/routers/meetings.py:25`); 401 (`back/app/auth.py:50`); 422 (validation).
    - ISO 8601 with timezone.
    - Bearer header.
  - **Pinned versions**, copied verbatim: `postgres:16-alpine`, `requires-python >=3.12`, CI Python 3.12 and Node 24, the pyproject ranges (for example `fastapi>=0.115`), and the package.json ranges (for example `"typescript": "~6.0"`).
  - **Monorepo trade-off:** a written paragraph.
- **Tests first** (self-contained: uses no conftest fixtures, so it can run in the same wave as Slice 1):
  - Pure checker functions: `check_folders`, `check_compose`, `check_status_codes`, `check_versions`, `check_tradeoff`.
  - TC-3.1: all five checks pass on the real file. Folder list is the fixed FR-1 list above.
  - TC-3.2: the `starts_at` type/format matches `schemas.py`.
  - TC-3.3: in-memory mutation of POST 201 to 200 makes `check_status_codes` fail.
  - TC-3.4: an invented fastapi version makes `check_versions` fail.
  - TC-3.5: each listed library's constraint string equals the pyproject/package.json string byte-for-byte.
- **Verify:** `uvx --with pyyaml pytest infra/tests/test_docs_project.py -q`
- **Done when:**
  - All TC-3.x tests pass.
  - The two negative tests (3.3, 3.4) show that the checkers reject mutated input.
- **Prod LOC:** 0 (docs)
- **Security pre-review:** no

### Slice 3: Makefile `.env`/credential fix + `DOMAIN` derivation + domain fail-fast + no silent detach
- **Wave:** 2
- **Use cases:** UC-8-E4, UC-8-E5, UC-8-X2, UC-9-E5 (Makefile side), UC-5-X3, UC-6 (FR-8 crosscut), UC-1/UC-1-A2 (no regression)
- **FR/AC:** FR-8, FR-19a/b/c, FR-15, NFR-1; AC-13 (frontend half), AC-16, AC-8 (grep half)
- **Files:**
  - `Makefile`
  - `.env.example`
  - `infra/tests/test_makefile_env.py` (new)
  - `infra/tests/test_makefile_domain.py` (new)
  - `infra/tests/test_docs_secrets.py` (new)
- **Changes:**
  - **`Makefile` header:**
    - Before the include, snapshot the environment values: `$(foreach v,AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN AWS_PROFILE,$(eval _env_$(v) := $($(v))))`.
    - Replace `-include .env` with `-include $(wildcard .env)`.
    - After the include, apply group semantics.
      - If any of the four variables was non-empty in the environment, restore all four to their environment values.
      - `unexport` the ones whose environment value was empty. This stops a `.env` static key from overriding an env `AWS_PROFILE`/session.
    - Only 3.81-compatible constructs: no `--eval`, `undefine`, `!=`, `.ONESHELL` or `$(file)`.
  - **`LOAD_ENV`** (used inside `$(shell)`, which on make <4.4 does not see exported variables):
    - If `"$${AWS_ACCESS_KEY_ID}$${AWS_SECRET_ACCESS_KEY}$${AWS_SESSION_TOKEN}$${AWS_PROFILE}"` is non-empty, source `.env` through `grep -vE '^(AWS_ACCESS_KEY_ID|AWS_SECRET_ACCESS_KEY|AWS_SESSION_TOKEN|AWS_PROFILE)='` (bash process substitution).
    - Otherwise source `.env` as today.
  - **First-run regression fix:** add `POSTGRES_USER ?= meetings`, `POSTGRES_PASSWORD ?= meetings`, `POSTGRES_DB ?= meetings` after the include.
    - Reason: with the wildcard guard, a `.env` created mid-run by `test-back` is no longer re-read by make. Today `TEST_DB_URL` would expand with an empty user on a fresh clone.
  - Keep the `.env:` rule as a prerequisite only of `start` and `test-back` (already true; locked in by a test).
  - **Domain variables:**
    - `DOMAIN ?=`
    - `FRONTEND_DOMAIN ?= $(if $(DOMAIN),app.$(DOMAIN))`
    - `BACKEND_DOMAIN ?= $(if $(DOMAIN),api.$(DOMAIN))`
    - Remove the `onetwothree.dobosevych.com` default.
  - **Guard prerequisites:** new phony `require-frontend-domain` and `require-backend-domain`. Each prints `FRONTEND_DOMAIN is empty: set DOMAIN=example.com (or FRONTEND_DOMAIN=...)` and exits 1.
    - The guard is listed first: `aws-frontend-cert: require-frontend-domain aws-check`. This means no `aws`/`cert.sh` call happens on the empty-domain path.
    - The same guard is added to `aws-frontend-cert-status`, `aws-frontend-dns` and `aws-frontend-https-check`.
    - `aws-frontend-https` inherits the guard through `aws-frontend-cert`.
  - **`aws-frontend-stack`:** replace `$(or $(FRONTEND_DOMAIN_ARGS),CertificateArn= DomainName= HostedZoneId=)` with `$(if $(DETACH_DOMAIN),CertificateArn= DomainName= HostedZoneId=,$(FRONTEND_DOMAIN_ARGS))`.
    - Leaving the parameters out equals UsePreviousValue on an existing stack.
    - Document `DETACH_DOMAIN=1` in the help text.
  - **`aws-check` message:** "AWS credentials missing/invalid/expired: run `aws sso login` / export credentials (CI: OIDC), or set static keys in .env for local use only".
  - **New `test-infra` target** (Quality section): `uvx --with pyyaml pytest infra/tests -q`.
  - **`.env.example`:**
    - Change the key lines to `# AWS_ACCESS_KEY_ID=` and `# AWS_SECRET_ACCESS_KEY=`.
    - Add a comment preferring SSO/profile.
    - Add `DOMAIN=` with a comment (drives `app.`/`api.`).
- **Tests first:**
  - `test_makefile_env.py`:
    - TC-8.10: `make help` and bare `make` in a fresh sandbox, then `.env` is absent. Run under both make versions.
    - TC-8.11: static check for `-include $(wildcard .env)` and no bare `-include .env`; dynamic check with `make help`.
    - TC-8.8a (recipe path): `.env` contains `AWS_ACCESS_KEY_ID=`; env has `x/y/z`; `make aws-check` → the `sts` stub env has `x/y/z`.
    - TC-8.8b (`$(shell)` path): same setup, then `make -n aws-backend-health` → the `describe-stacks` stub call env has `x/y/z`.
    - TC-8.8c (group rule): env `AWS_PROFILE=sso` and `.env` with a static key → stub env has no `AWS_ACCESS_KEY_ID`.
    - TC-8.7: an `sts` stub failing with `ExpiredToken` gives a non-zero exit and a message containing "expired".
    - TC-8.13 (static): the only targets listing `.env` as a prerequisite are `start` and `test-back`.
    - New regression test: fresh sandbox, `make test-back` → `.env` is created and the `uv` stub env `TEST_DATABASE_URL` contains `meetings:meetings@localhost:5432/meetings_test`.
  - `test_makefile_domain.py`:
    - TC-6.10: `DOMAIN` unset → `FRONTEND_DOMAIN` and `BACKEND_DOMAIN` are both `""`; `DOMAIN=ex.com` → `app.ex.com` / `api.ex.com`; individual overrides win.
    - TC-6.9 (frontend half): `aws-frontend-cert` and `aws-frontend-https` exit non-zero with "empty", and there are zero stub calls.
    - TC-5.10: `DOMAIN` unset → the `cloudformation deploy` args contain none of `DomainName=`, `CertificateArn=`, `HostedZoneId=`.
    - `DETACH_DOMAIN=1` → all three are present with empty values.
  - `test_docs_secrets.py`:
    - TC-8.12: grep `.env.example`.
    - TC-16.6: `git grep -nE 'AWS_(ACCESS_KEY_ID|SECRET_ACCESS_KEY)\s*=\s*[A-Za-z0-9/+=]{16,}'` finds zero matches.
    - No `onetwothree.dobosevych.com` in `Makefile` or `.env.example`.
- **Verify:**
  - `uvx --with pyyaml pytest infra/tests/test_makefile_env.py infra/tests/test_makefile_domain.py infra/tests/test_docs_secrets.py infra/tests/test_makefile_baseline.py -q`
  - The same with `MAKE_BIN=/usr/bin/make`
  - `grep -c 'onetwothree.dobosevych.com' Makefile .env.example` → 0 for each file
- **Done when:** all of the above pass on make 3.81 and GNU make 4.x.
  - The TC-8.8a/b/c environment assertions hold.
  - `.env` is never created by `help`, bare `make`, or any `aws-*` target.
- **Prod LOC:** about 45
- **Security pre-review:** yes (credential precedence, `.env` loading)

### Slice 4: `infra/github-oidc.yaml` (OIDC provider + least-privilege deploy role)
- **Wave:** 2
- **Use cases:** UC-4, UC-4-A1, UC-4-X1, UC-10-E1..E5, UC-10-X2, UC-12 (Retain)
- **FR/AC:** FR-16 (template), FR-17, FR-18, NFR-1, NFR-2; AC-7 (static)
- **Files:**
  - `infra/github-oidc.yaml` (new)
  - `infra/tests/test_cfn_github_oidc.py` (new)
- **Changes:**
  - **Parameters:** `ProjectName` (default `meetings`), `ExistingOidcProviderArn` (default `""`), `FrontendBucketName`, `FrontendDistributionId`. These names are fixed; Slice 7 depends on them.
  - **Condition:** `CreateOidcProvider: !Equals [!Ref ExistingOidcProviderArn, ""]`.
  - **`GitHubOidcProvider`** (`AWS::IAM::OIDCProvider`):
    - `Condition: CreateOidcProvider`, `DeletionPolicy: Retain`, `UpdateReplacePolicy: Retain` (avoids cfn-lint W3011).
    - `Url: https://token.actions.githubusercontent.com`, `ClientIdList: [sts.amazonaws.com]`, tags.
  - **`GitHubDeployRole`:**
    - `RoleName: !Sub ${ProjectName}-github-deploy`.
    - Trust: `Federated: !If [CreateOidcProvider, !Ref GitHubOidcProvider, !Ref ExistingOidcProviderArn]`, `Action: sts:AssumeRoleWithWebIdentity`.
    - A single `StringEquals` block containing the literal `"token.actions.githubusercontent.com:sub": "repo:MasterDay3/OneTwoThree:ref:refs/heads/main"` and `"token.actions.githubusercontent.com:aud": "sts.amazonaws.com"`. No parameter, no `StringLike`.
  - **Inline policy statements, exactly as in FR-18:**
    - `EcrAuth`: `ecr:GetAuthorizationToken` on `*`.
    - `EcrPush`: 8 actions on `arn:${AWS::Partition}:ecr:${AWS::Region}:${AWS::AccountId}:repository/${ProjectName}-backend`.
    - `LambdaDeploy`: `UpdateFunctionCode`, `GetFunction`, `GetFunctionConfiguration`, `InvokeFunction` on `function:${ProjectName}-backend`.
    - `S3List`: `s3:ListBucket` on the bucket ARN.
    - `S3Objects`: `PutObject`, `DeleteObject` on `bucket/*`.
    - `CloudFrontInvalidate`: `cloudfront:CreateInvalidation` on `arn:${AWS::Partition}:cloudfront::${AWS::AccountId}:distribution/${FrontendDistributionId}`.
    - `CfnRead`: `cloudformation:DescribeStacks` on `stack/${ProjectName}-{backend-ecr,backend,backend-domain,frontend,cognito}/*`.
  - **Outputs:** `DeployRoleArn`, `OidcProviderArn` (`!If`), `ProviderManagedByStack` (`!If [CreateOidcProvider, "true", "false"]`). No `Export`.
- **Tests first** (`test_cfn_github_oidc.py`):
  - TC-4.1: `StringEquals` on `sub` and `aud`; `RoleName` is `Fn::Sub "${ProjectName}-github-deploy"`; provider has `DeletionPolicy: Retain`.
  - TC-4.2: provider carries `Condition`; trust `Federated` is `Fn::If`.
  - TC-4.6 / TC-10.1a: the `sub` value is exactly `repo:MasterDay3/OneTwoThree:ref:refs/heads/main`, with no `*` and no `StringLike` anywhere in the file.
  - TC-10.5: an independent `aud` key.
  - TC-10.8:
    - Per-service action sets equal the FR-18 sets exactly.
    - Exactly one statement has `Resource: "*"`, and its Action is `["ecr:GetAuthorizationToken"]`.
    - No `iam:`, `cognito-idp:`, `acm:`, `route53:` actions.
    - No `cloudformation:` action other than `DescribeStacks`.
    - No `lambda:InvokeFunctionUrl`.
    - No wildcard action.
  - Plus: no `Fn::ImportValue`/`Export`; `ProviderManagedByStack` output exists.
- **Verify:**
  - `uvx --with pyyaml pytest infra/tests/test_cfn_github_oidc.py -q`
  - `uvx cfn-lint infra/github-oidc.yaml`
  - `grep -c StringLike infra/github-oidc.yaml` → 0
- **Done when:** the tests pass, and `cfn-lint` exits 0 with no errors or warnings on the template (TC-16.9, OIDC half).
- **Prod LOC:** about 100 (YAML)
- **Security pre-review:** yes (IAM trust and permission policy)

### Slice 5: `infra/backend-domain.yaml` (HTTP API front door for `api.<domain>`)
- **Wave:** 2
- **Use cases:** UC-7, UC-7-A1, UC-7-X2, UC-13 (no gateway CORS), UC-14, UC-14-E1
- **FR/AC:** FR-7 (template), NFR-3, NFR-5; AC-2/AC-3 (static support)
- **Files:**
  - `infra/backend-domain.yaml` (new)
  - `infra/tests/test_cfn_backend_domain.py` (new)
- **Changes:**
  - **Parameters:** `ProjectName`, `BackendFunctionName`, `DomainName`, `CertificateArn`, `HostedZoneId` (default `""`), `ThrottleBurstLimit` (default e.g. 100), `ThrottleRateLimit` (default e.g. 50). The names are fixed; Slice 7 depends on them.
  - **Condition:** `CreateDnsRecord: !Not [!Equals [!Ref HostedZoneId, ""]]`.
  - **`HttpApi`** (`AWS::ApiGatewayV2::Api`): `ProtocolType: HTTP`, `DisableExecuteApiEndpoint: true`, no `CorsConfiguration`.
  - **`LambdaIntegration`:**
    - `IntegrationType: AWS_PROXY`.
    - `IntegrationUri: !Sub arn:${AWS::Partition}:lambda:${AWS::Region}:${AWS::AccountId}:function:${BackendFunctionName}`.
    - `PayloadFormatVersion: "2.0"` (Mangum handles v2, the same shape as function URL events).
    - `TimeoutInMillis: 30000`, as a literal.
  - **Route and stage:** `DefaultRoute` with `RouteKey: $default`. `DefaultStage` with `StageName: $default`, `AutoDeploy: true`, and `DefaultRouteSettings` burst/rate limits.
  - **`InvokePermission`:** `Principal: apigateway.amazonaws.com`, `SourceArn: !Sub arn:${AWS::Partition}:execute-api:${AWS::Region}:${AWS::AccountId}:${HttpApi}/*`.
  - **`ApiDomain`:** `DomainNameConfigurations: [{CertificateArn, EndpointType: REGIONAL, SecurityPolicy: TLS_1_2}]`.
  - **`ApiMapping`:** `ApiMappingKey: ""`, `Stage: !Ref DefaultStage`.
  - **`DnsRecord`** (conditional): alias `A` to `!GetAtt ApiDomain.RegionalDomainName` / `RegionalHostedZoneId`.
  - **Outputs:** `ApiUrl: !Sub "https://${DomainName}/"` (with trailing slash), `ApiDocsUrl`. No `Export`. Tag the resources that support tags.
- **Tests first** (`test_cfn_backend_domain.py`):
  - TC-7.1a–7.1h. For 7.1b, use `DefaultRouteSettings`, per correction 2.
  - TC-7.1c: `SourceArn` is an `Fn::Sub` containing `${HttpApi}`, not a bare `*`.
  - TC-7.1f: `ApiUrl` ends with `/`.
  - TC-7.8 / TC-14.1a: `backend.yaml` `Timeout.Default == 30` and this template's `TimeoutInMillis == 30000`.
  - TC-14.3: literal, not `Ref`.
- **Verify:**
  - `uvx --with pyyaml pytest infra/tests/test_cfn_backend_domain.py -q`
  - `uvx cfn-lint infra/backend-domain.yaml`
- **Done when:** the tests pass and `cfn-lint` exits 0.
  - If cfn-lint rejects `ApiMappingKey: ""`, drop the property (root mapping behaves the same) and let the test accept absent-or-empty. Record that decision in the scratchpad.
- **Prod LOC:** about 110 (YAML)
- **Security pre-review:** yes (public entry point, Lambda permission scoping, TLS policy)

### Slice 6: Makefile deploy contract (`deploy-backend`, `deploy-frontend`, full-SHA `TAG`, `aws-backend-update-code`, safe upload order, `VITE_API_URL` resolution)
- **Wave:** 3
- **Use cases:** UC-8, UC-8-E1, UC-8-E2, UC-8-E3, UC-8-X1, UC-9-E2, UC-9-E3, UC-9-E4, UC-11-X1
- **FR/AC:** FR-10, FR-11, FR-12, FR-13, FR-15, NFR-4; AC-4, AC-5
- **Files:**
  - `Makefile`
  - `infra/tests/test_makefile_deploy.py` (new)
- **Changes:**
  - **`TAG` default:**
    - Before the `ifndef` block, capture `_USER_TAG := $(TAG)`; Slice 7 uses it.
    - `TAG := $(shell sha=$$(git rev-parse HEAD 2>/dev/null) && { git diff --quiet HEAD 2>/dev/null && echo $$sha || echo $$sha-dirty-$$(date +%Y%m%d%H%M%S); } || date +%Y%m%d%H%M%S)`
  - **`aws-backend-push`:** remove `-t $(ECR_URI):latest` (FR-13: never `latest`).
  - **New `aws-backend-update-code`:**
    - Guard that `ECR_URI` is non-empty.
    - `aws lambda wait function-updated-v2` first, to avoid a `ResourceConflictException` from an in-flight update.
    - `aws lambda update-function-code --function-name $(BACKEND_FUNCTION) --image-uri $(ECR_URI):$(TAG)`.
    - `aws lambda wait function-updated-v2 --function-name $(BACKEND_FUNCTION)`.
  - **New `deploy-backend`:** `aws-check aws-backend-push aws-backend-update-code aws-backend-migrate`.
  - **New `deploy-frontend`:** `aws-check aws-frontend-publish`. Neither depends on any `*-stack` or `aws-frontend-cors` target.
  - **Backend-domain stack lookup:** `BACKEND_DOMAIN_STACK := $(PROJECT)-backend-domain` and `backend_domain_output`.
  - **Frontend API URL:** `FRONTEND_API_URL = $(or $(call backend_domain_output,ApiUrl),$(API_URL))`, used only by `aws-frontend-publish`. `API_URL` stays the function URL for `aws-backend-health`.
  - **`aws-frontend-publish` upload order:**
    1. `aws s3 sync front/dist s3://$bucket --exclude index.html --cache-control "public,max-age=31536000,immutable"` (no `--delete`)
    2. `aws s3 cp index.html --cache-control no-cache`
    3. `aws s3 sync ... --delete --exclude index.html`
    4. `create-invalidation`
    - Keep the existing guard messages. The ordering rationale is in correction 3.
  - **`.NOTPARALLEL:`** so prerequisite chains stay ordered even under `make -j`.
  - Help texts for both targets state "code/asset rollout only, never CloudFormation".
- **Tests first** (`test_makefile_deploy.py`):
  - TC-8.1: `make -n deploy-backend TAG=abc` → the dry-run output contains, in order, `get-caller-identity`, `buildx build`, `update-function-code`, `function-updated-v2`, `"action": "migrate"`, and never `cloudformation deploy`. The real run's stub log has no `cloudformation deploy`/`create-stack`/`update-stack`.
  - Same check for `deploy-frontend` (no `cloudformation deploy`).
  - TC-8.2: stub log order is sync (no `--delete`, has `--exclude index.html`), then cp `index.html` `no-cache`, then sync `--delete`, then invalidation.
  - TC-8.4: in a real temp git repo with an uncommitted change, compute `TAG` twice with sleep 1 → both match `^[0-9a-f]{40}-dirty-\d{14}$`, and they differ. On a clean tree, `TAG` equals `git rev-parse HEAD`, 40 hex characters.
  - TC-8.5: in a non-repo directory, `TAG` matches `^\d{14}$`.
  - TC-8.6: `describe-stacks` stub for `meetings-backend-domain` returns `https://api.ex.com/` → the npm build stub env `VITE_API_URL=https://api.ex.com/`. With that stub empty, it falls back to the function URL.
  - TC-8.9: `deploy-backend TAG=fixed` twice → exit 0 both times.
  - TC-8.13 (dynamic): `deploy-backend` and `deploy-frontend` runs leave `.env` absent.
  - TC-9.6: a migrate stub returning `Unhandled` → non-zero exit after `update-function-code` was called; the shell chain `make deploy-backend && make deploy-frontend` never calls npm or s3.
  - TC-9.7: docker `--push` fails → `update-function-code` and `invoke` are never called.
  - TC-9.8: invalidation fails → non-zero exit, and s3 sync was called.
  - TC-11.5: no `latest` tag appears in the push args.
  - TC-5.8 and TC-5.5 in the baseline must still pass.
- **Verify:**
  - `uvx --with pyyaml pytest infra/tests/test_makefile_deploy.py infra/tests/test_makefile_baseline.py infra/tests/test_makefile_env.py infra/tests/test_makefile_domain.py -q`
  - The same with `MAKE_BIN=/usr/bin/make`
- **Done when:** all pass.
  - The `make -n deploy-backend` and `make -n deploy-frontend` dry-runs in the sandbox contain zero occurrences of `cloudformation deploy`.
  - The pushed tag equals `TAG` exactly, with no `:latest`.
- **Prod LOC:** about 50
- **Security pre-review:** yes (deploy contract; what CI executes)

### Slice 7: Makefile operations (rollback, `KEEP_IMAGE` live ImageUri, backend custom-domain targets, OIDC targets, destroy order)
- **Wave:** 4
- **Use cases:** UC-4, UC-4-A1, UC-4-A2, UC-4-E2, UC-5-X2, UC-6 (backend), UC-7, UC-7-E1, UC-7-X1, UC-11, UC-11-E1, UC-11-E2, UC-12, UC-12-E4
- **FR/AC:** FR-7 (targets + destroy order), FR-8 (backend guards), FR-14, FR-16 (Makefile), FR-30 (teardown), FR-31, NFR-3; AC-6 (static), AC-13 (backend half), AC-17
- **Files:**
  - `Makefile`
  - `infra/scripts/cert.sh`
  - `infra/tests/test_makefile_ops.py` (new)
- **Changes:**
  - **`aws-backend-rollback`:**
    - First prerequisite is `require-rollback-tag`: it fails with a usage message if `_USER_TAG` is empty, so the default HEAD tag can never be used as a rollback target.
    - `aws ecr describe-images --repository-name $(PROJECT)-backend --image-ids imageTag=$(TAG)`; on failure, print "tag $(TAG) not in ECR (repository keeps only the last 5 images)" and exit 1.
    - Then `update-function-code --image-uri $(ECR_URI):$(TAG)` and `wait function-updated-v2`.
    - No build, no push, no migrate.
    - Help text mentions the 5-image window and that schema changes are not reverted.
  - **`aws-backend-stack` with `KEEP_IMAGE=1`:**
    - `image=$$(aws lambda get-function --function-name $(BACKEND_FUNCTION) --query Code.ImageUri --output text)`.
    - Fail if the result is empty or `None`.
    - Pass `ImageUri=$$image`. Without `KEEP_IMAGE`, pass `ImageUri=$(ECR_URI):$(TAG)`.
  - **Backend certificate and domain:**
    - `BACKEND_CERT_ARN` and `BACKEND_ZONE_ID`, mirroring the frontend ones.
    - `aws-backend-cert: require-backend-domain aws-check` → `$(CERT_SH) request $(BACKEND_DOMAIN)`.
    - `aws-backend-cert-status`.
    - `aws-backend-https: aws-backend-cert`:
      - `cert.sh wait`, then `clear_failed_stack`.
      - `aws cloudformation deploy --stack-name $(BACKEND_DOMAIN_STACK) --template-file infra/backend-domain.yaml --no-fail-on-empty-changeset --tags ... --parameter-overrides ProjectName BackendFunctionName=$(BACKEND_FUNCTION) DomainName CertificateArn HostedZoneId`.
      - Then `$(MAKE) aws-frontend-publish`.
      - It does not depend on any `aws-frontend-*` stack target (TC-7.7).
    - `aws-backend-https-check`: prints the `dig` line and the curl HTTP status for `https://$(BACKEND_DOMAIN)/api/health`. On failure it prints a propagation hint, then exits 1 (correction 5).
    - `aws-backend-domain-destroy`: prompt, delete-stack, wait; print "run `make aws-frontend-publish` to rebuild against the function URL".
  - **OIDC:**
    - `OIDC_STACK := $(PROJECT)-github-oidc` and `oidc_output`.
    - `aws-oidc-deploy: aws-check`:
      - `clear_failed_stack`.
      - Guard that the frontend `BucketName`/`DistributionId` and the backend `FunctionName` outputs are non-empty.
      - `existing=$$(aws iam list-open-id-connect-providers --query "OpenIDConnectProviderList[?ends_with(Arn,'/token.actions.githubusercontent.com')].Arn | [0]" --output text | sed 's/^None$$//')`.
      - Blank `existing` if `$(call oidc_output,ProviderManagedByStack)` is `true` (correction 6).
      - `aws cloudformation deploy ... --capabilities CAPABILITY_NAMED_IAM --no-fail-on-empty-changeset` with `FrontendBucketName`, `FrontendDistributionId`, `ExistingOidcProviderArn`.
      - Print the role ARN and the exact command `gh variable set AWS_DEPLOY_ROLE_ARN --repo MasterDay3/OneTwoThree --body <arn>`. The command is printed, not executed.
      - Help text: "re-run after the frontend/backend stacks are recreated".
    - `aws-oidc-outputs`.
    - `aws-oidc-destroy`: prompt; notes that the provider is retained.
  - **`aws-destroy`:** `aws-frontend-destroy aws-backend-domain-destroy aws-backend-destroy aws-cognito-destroy $(if $(DESTROY_OIDC),aws-oidc-destroy)`.
  - **`cert.sh`:** wording only.
    - The header comment says the script serves any domain.
    - The `print_manual_record`/`status`/`wait` hints say "run the matching `make aws-*-cert` / `aws-*-https`" instead of frontend-only names.
    - Keep "Reusing certificate", "Status: ISSUED" and "Timed out waiting…" byte-identical; Slice 1 tests assert them.
- **Tests first** (`test_makefile_ops.py`):
  - TC-11.1: `describe-images` is called before `update-function-code`; zero docker or migrate `invoke` calls.
  - TC-11.3: `describe-images` exits 254 → non-zero exit, and `update-function-code` is never called.
  - TC-11.4: static check that the recipe has no `migrate`, plus the dynamic zero-calls check.
  - New: no `TAG` → usage error and zero aws calls.
  - TC-5.9 / AC-17: `get-function` stub returns `X:abc` → the deploy args include `ImageUri=X:abc` and never `$(ECR_URI):$(TAG)`. Stub returns `None` → fails.
  - TC-6.9 (backend half): `aws-backend-cert` and `aws-backend-https` fail with zero stub calls.
  - TC-7.7: neither https target lists the other in `make -n`.
  - Positive test: `aws-backend-https DOMAIN=ex.com` with issued-cert stubs → a `backend-domain` deploy with `BackendFunctionName=meetings-backend`, `DomainName=api.ex.com`, followed by publish calls.
  - TC-7.4: DNS stub gives NXDOMAIN → both lines printed, then non-zero exit.
  - TC-4.3: `make -n aws-oidc-deploy` contains `list-open-id-connect-providers`, `CAPABILITY_NAMED_IAM`, `--no-fail-on-empty-changeset`.
  - New (UC-4-A2): `ProviderManagedByStack=true` → `ExistingOidcProviderArn=` is empty.
  - TC-4.5: `clear_failed_stack` is used.
  - Frontend stack missing → fails before deploy.
  - TC-12.1 / TC-12.6a: `make -n aws-destroy` order is frontend, backend-domain, backend, cognito, with no oidc. `DESTROY_OIDC=1` appends oidc last.
  - `bash -n infra/scripts/cert.sh`.
- **Verify:**
  - `uvx --with pyyaml pytest infra/tests/test_makefile_ops.py infra/tests/test_cert_script.py infra/tests/test_makefile_deploy.py infra/tests/test_makefile_baseline.py -q`
  - The same with `MAKE_BIN=/usr/bin/make`
  - `bash -n infra/scripts/cert.sh`
- **Done when:** all pass.
  - The `aws-backend-rollback` stub log never contains `buildx` or `"action": "migrate"`.
  - `aws-backend-stack KEEP_IMAGE=1` passes exactly the stubbed live `Code.ImageUri`.
  - `make -n aws-destroy` lists `aws-backend-domain-destroy` before `aws-backend-destroy`.
- **Prod LOC:** about 110
- **Security pre-review:** yes (rollback, `KEEP_IMAGE`, OIDC bootstrap, destroy)

### Slice 8: CI/CD workflows (`deploy.yml` + reusable `code-style.yml` + infra tests in CI)
- **Wave:** 4
- **Use cases:** UC-9, UC-9-A1, UC-9-E1, UC-9-E5, UC-9-X1, UC-9-X1a, UC-9-X2, UC-10-E2, UC-10-E6, UC-10-X1
- **FR/AC:** FR-17 (no `environment:`), FR-19d, FR-20..FR-26, FR-32; AC-8 (workflow half), AC-9/10/11/12 (static)
- **Files:**
  - `.github/workflows/code-style.yml`
  - `.github/workflows/deploy.yml` (new)
  - `infra/tests/test_workflows.py` (new)
- **Changes:**
  - **`code-style.yml`:**
    - `on: {pull_request: {}, workflow_call: {}}`; drop `push`.
    - Concurrency: `group: code-style-${{ github.event_name }}-${{ github.ref }}`, `cancel-in-progress: ${{ github.event_name == 'pull_request' }}`, so a call from deploy never cancels.
    - Pin every action by full SHA with a `# vX.Y.Z` comment.
    - `persist-credentials: false` on checkout.
    - New job `infra-tests` (ubuntu-latest, setup-uv) running:
      - `uvx --with pyyaml pytest infra/tests -q`
      - `uvx ruff check --config back/pyproject.toml infra/tests`
      - `uvx ruff format --check --config back/pyproject.toml infra/tests`
    - This is the recommendation for running the harness in CI: it runs on PRs directly and gates deploys through `lint`.
  - **`deploy.yml`:**
    - `on: push: branches: [main]` only, with no paths filter. Workflow-level `permissions: contents: read`. Workflow-level `concurrency: {group: deploy-${{ github.ref }}, cancel-in-progress: false}`.
    - Job `lint: uses: ./.github/workflows/code-style.yml`.
    - Job `back-test`:
      - `services.postgres.image: postgres:16-alpine`, env `meetings/meetings/meetings_test`, health options, port 5432.
      - setup-uv, then `uv sync --python 3.12`.
      - `TEST_DATABASE_URL=postgresql+psycopg://meetings:meetings@localhost:5432/meetings_test uv run pytest -q` in `back/`.
    - Job `front-test`: setup-node 24, then `npm ci` and `npm test` in `front/`.
    - Job `deploy`:
      - `needs: [lint, back-test, front-test]`, `runs-on: ubuntu-24.04-arm`.
      - `permissions: {contents: read, id-token: write}`. No `environment:`.
      - `if: github.ref == 'refs/heads/main'`.
    - `deploy` steps:
      1. checkout (SHA-pinned, `persist-credentials: false`)
      2. setup-node 24 (SHA)
      3. "Ensure AWS CLI": `aws --version` or install the aarch64 v2 zip
      4. `aws-actions/configure-aws-credentials@<sha> # v4.x` with `role-to-assume: ${{ vars.AWS_DEPLOY_ROLE_ARN }}`, `aws-region: us-east-1`
      5. `test ! -f .env || { echo "unexpected .env in CI checkout"; exit 1; }`
      6. `make deploy-backend TAG="$TAG"` with `env: TAG: ${{ github.sha }}`
      7. `make deploy-frontend`
    - No `workflow_dispatch`/`schedule`. No `secrets.*` AWS usage.
    - Resolve action SHAs with `git ls-remote https://github.com/<owner>/<repo> refs/tags/<tag>`. Do not guess them.
- **Tests first** (`test_workflows.py`):
  - Normalize PyYAML's `on` key, which parses as boolean `True` under YAML 1.1.
  - TC-9.1a–9.1f.
  - TC-9.2: every non-`./` `uses:` matches `@[0-9a-f]{40}`, in both files; checkout has `persist-credentials: false`; `role-to-assume` is `vars.AWS_DEPLOY_ROLE_ARN`.
  - TC-9.3: `runs-on: ubuntu-24.04-arm` and no `ARCH=amd64`.
  - TC-9.9a: the `.env`-absent step index is less than the `deploy-backend` step index, which is less than the `deploy-frontend` step index (FR-24 ordering).
  - TC-9.10a: workflow-level concurrency, not per job.
  - TC-9.11: no `paths`.
  - TC-10.2: no `pull_request`.
  - TC-10.6a: no `environment`.
  - TC-10.7.
  - TC-16.7: no `secrets.AWS_*`.
  - New: `id-token` appears only under `deploy`; the code-style concurrency expression never cancels for non-PR events; the `infra-tests` job exists.
  - actionlint via `uvx --from actionlint-py actionlint` when available; otherwise `pytest.skip` with a reason.
- **Verify:**
  - `uvx --with pyyaml pytest infra/tests/test_workflows.py -q`
  - `uvx --from actionlint-py actionlint .github/workflows/code-style.yml .github/workflows/deploy.yml`
  - `grep -nE 'uses: [^.][^@]*@v[0-9]' .github/workflows/*.yml` → no output
  - `uvx --with pyyaml pytest infra/tests -q` (full suite, as CI will run it)
- **Done when:**
  - The workflow tests pass and actionlint exits 0.
  - No tag-pinned third-party action remains.
  - The full infra suite is green (it now runs on every PR and gates deploy).
  - The `deploy` job's make targets exist: `make -n deploy-backend deploy-frontend` in the sandbox resolves without "No rule to make target".
- **Prod LOC:** about 140 (YAML)
- **Security pre-review:** yes (OIDC token scope, permissions, supply-chain pinning)

### Slice 9: As-built docs (README + `lambda_handler` docstring + `PROJECT.md` refresh + `docs/lab2-discussion.md`, written last)
- **Wave:** 5
- **Use cases:** UC-15, UC-15-A1, UC-15-E1, UC-15-E2, UC-15-E3, UC-15-X1, UC-3 (refresh), UC-12 (teardown doc)
- **FR/AC:** FR-27..FR-30, FR-33, FR-34, FR-1 (refresh), NFR-7; AC-14, AC-15
- **Files:**
  - `README.md`
  - `PROJECT.md`
  - `back/app/lambda_handler.py`
  - `docs/lab2-discussion.md` (new)
  - `infra/tests/test_docs_project.py`
  - `infra/tests/test_docs_readme.py` (new)
  - `infra/tests/test_docs_discussion.py` (new)
- **Changes:**
  - **`README.md`:**
    - Replace `## Layout` with a short link to `PROJECT.md`.
    - Remove both `onetwothree.dobosevych.com` references (lines 187 and 192).
    - Replace static-key step 1 (lines 163–168) with SSO/profile, keeping static `.env` keys as a local-only fallback.
    - Add a "Custom domains (`DOMAIN=`)" section covering `aws-frontend-https` and `aws-backend-https`.
    - Add a "CI/CD (GitHub OIDC)" section: `aws-oidc-deploy`, `vars.AWS_DEPLOY_ROLE_ARN`, `deploy-*`, `aws-backend-rollback`, the 5-image window, `DESTROY_OIDC`.
    - Update the Code style section (code-style runs on PRs and through `deploy.yml`).
    - Update the Mermaid diagram: add `B -->|HTTPS api.<domain>| APIGW[API Gateway HTTP API custom domain] --> L` and keep `URL[Lambda function URL (fallback)]`.
  - **`lambda_handler.py`:** docstring names both entry points (API Gateway HTTP API custom domain, payload v2, and the function URL fallback).
  - **`PROJECT.md`:** add `infra/github-oidc.yaml`, `infra/backend-domain.yaml`, `infra/tests/`, `.github/workflows/deploy.yml`, `docs/` contents.
  - **`docs/lab2-discussion.md`:** written last against the real files.
    - Covers every FR-27 topic with backtick file citations, including the API Gateway 30 s cap.
    - An explicit statement that the app runs on Lambda + Aurora Serverless v2, not ECS/ALB.
    - The real role name `meetings-github-deploy` and stack `meetings-backend-domain`.
    - The manual artefacts, marked as manual.
    - Cost/teardown via `aws-destroy` (+`DESTROY_OIDC=1`), the 5-image rollback window, and the schema-not-reverted caveat.
    - Any figure that cannot be checked against a file is flagged "approximate/unverified".
- **Tests first:**
  - `test_docs_readme.py`:
    - TC-16.1: the Layout body is ≤5 lines and links `PROJECT.md`.
    - TC-16.2.
    - TC-16.3: a heuristic that fails on `AWS_ACCESS_KEY_ID` within N lines of "CI"/"GitHub Actions".
    - TC-16.4: the Mermaid block has an API Gateway node and a function URL node.
    - TC-16.5: `ast.get_docstring` of `lambda_handler.py` contains "API Gateway" and "function URL".
  - `test_docs_discussion.py`:
    - TC-15.1: 13 topic keywords, each with at least one backtick path that exists on disk.
    - TC-15.3: a numeric-claim heuristic. It emits `warnings` for manual review and does not fail.
    - TC-15.4: an explicit Lambda + Aurora Serverless v2 vs ECS/ALB statement.
    - TC-15.5.
    - TC-15.6: `github-deploy` and `backend-domain` literals.
    - TC-15.7: `aws-destroy`, "5", and the schema caveat.
  - `test_docs_project.py`: add a completeness test. Every `infra/*.yaml` and every `.github/workflows/*.yml` is mentioned. Tracked directories at depth ≤2 (from `git ls-files`, excluding generated ones) are mentioned.
- **Verify:**
  - `uvx --with pyyaml pytest infra/tests -q` (full suite)
  - `cd back && uv run ruff check . && uv run ruff format --check .`
  - `grep -c onetwothree.dobosevych.com README.md` → 0
- **Done when:**
  - The full infra suite passes.
  - The back ruff checks are clean.
  - The README has zero lecturer-domain references.
  - `docs/lab2-discussion.md` exists and passes TC-15.1/4/5/6/7.
  - Manual reviewer spot-check (TC-15.2) is listed for merge-ready.
- **Prod LOC:** about 5 (docstring); the rest is docs
- **Security pre-review:** no

---

## Wave summary

| Wave | Slices | Rationale |
|------|--------|-----------|
| 1 | 1, 2 | Independent. Slice 1 is the harness (test files only). Slice 2 is `PROJECT.md` plus a self-contained doc test. No shared files. |
| 2 | 3, 4, 5 | All use the Slice 1 fixtures. Disjoint files: `Makefile`/`.env.example` vs `github-oidc.yaml` vs `backend-domain.yaml`, each with its own test module. |
| 3 | 6 | `Makefile` again, so it must follow Slice 3. Builds on the Slice 3 env/TAG layout. |
| 4 | 7, 8 | Slice 7 edits the `Makefile` after Slice 6 and needs the Slice 4/5 parameter names and Slice 6's `BACKEND_DOMAIN_STACK`/`_USER_TAG`. Slice 8 needs the Slice 6 `deploy-*` targets and the harness. Disjoint files (`Makefile`/`cert.sh` vs `.github/workflows/*`). |
| 5 | 9 | Written against the as-built system (FR-33). Touches `PROJECT.md`/`test_docs_project.py` from Wave 1. |

File-overlap check:
- `Makefile` is in waves 2, 3 and 4 only.
- `PROJECT.md` and `test_docs_project.py` are in waves 1 and 5.
- No file appears twice within the same wave.

## 3. Acceptance criteria summary

- **AC-1:** Slice 2 + Slice 9 (TC-3.x and the completeness test).
- **AC-2, AC-3:** static support in Slices 5 and 7. Live: TC-7.1i and TC-7.1j (manual AWS).
- **AC-4:** Slice 6 (TC-8.1, 8.2, 8.9) and Slice 8. Live idempotency: re-run CI.
- **AC-5:** Slice 6 (full-SHA TAG, no `latest`) and Slice 8 (`TAG=${{ github.sha }}`). Live: `aws ecr describe-images`.
- **AC-6:** Slice 7 (TC-11.1/3/4). Live: TC-11.6.
- **AC-7:** Slice 4 (TC-4.x, 10.1a, 10.5, 10.8) and Slice 8 (no `environment`, no PR trigger). Live: TC-10.1b, 10.3, 10.4, 10.6b.
- **AC-8:** Slice 3 (TC-16.6, 8.12) and Slice 8 (TC-16.7). Manual: TC-16.8 in GitHub Settings.
- **AC-9, AC-10:** static in Slice 8. Live: TC-9.5, 9.10b, 9.10c (AUTO-CI).
- **AC-11:** Slice 8 (TC-9.3). Live: ECR manifest architecture.
- **AC-12:** Slice 8 (TC-9.9a). Live: TC-9.9b.
- **AC-13:** Slice 3 (frontend) and Slice 7 (backend). Live: TC-5.11.
- **AC-14, AC-15:** Slice 9.
- **AC-16:** Slice 3 (TC-8.8a/b/c, 8.10, 8.11).
- **AC-17:** Slice 7 (TC-5.9).
- **Overall:** `uvx --with pyyaml pytest infra/tests -q` and `uvx cfn-lint infra/*.yaml` are green, locally on make 3.81 and GNU make 4.x, and in CI.

## 4. Files to modify

**New:**
- `/Users/mykolazab/Documents/GitHub/OneTwoThree/PROJECT.md`
- `/Users/mykolazab/Documents/GitHub/OneTwoThree/docs/lab2-discussion.md`
- `/Users/mykolazab/Documents/GitHub/OneTwoThree/infra/github-oidc.yaml`
- `/Users/mykolazab/Documents/GitHub/OneTwoThree/infra/backend-domain.yaml`
- `/Users/mykolazab/Documents/GitHub/OneTwoThree/.github/workflows/deploy.yml`
- `/Users/mykolazab/Documents/GitHub/OneTwoThree/infra/tests/`:
  - `pytest.ini`, `conftest.py`, `cfn_yaml.py`, `stub_cmd.py`
  - `test_cfn_loader.py`, `test_makefile_baseline.py`, `test_cert_script.py`
  - `test_docs_project.py`, `test_makefile_env.py`, `test_makefile_domain.py`, `test_docs_secrets.py`
  - `test_cfn_github_oidc.py`, `test_cfn_backend_domain.py`
  - `test_makefile_deploy.py`, `test_makefile_ops.py`, `test_workflows.py`
  - `test_docs_readme.py`, `test_docs_discussion.py`

**Modified:**
- `/Users/mykolazab/Documents/GitHub/OneTwoThree/Makefile` (Slices 3, 6, 7)
- `/Users/mykolazab/Documents/GitHub/OneTwoThree/.env.example`
- `/Users/mykolazab/Documents/GitHub/OneTwoThree/infra/scripts/cert.sh` (wording only)
- `/Users/mykolazab/Documents/GitHub/OneTwoThree/.github/workflows/code-style.yml`
- `/Users/mykolazab/Documents/GitHub/OneTwoThree/README.md`
- `/Users/mykolazab/Documents/GitHub/OneTwoThree/back/app/lambda_handler.py` (docstring only)

**Unchanged by design:** `infra/backend.yaml`, `infra/frontend.yaml`, `infra/cognito.yaml`, `infra/backend-ecr.yaml`, `compose.yaml`, and the app code.

**Doc fixes, delegated:** `docs/qa/lab2-deploy_test_cases.md` (qa-planner) and `docs/PRD.md` FR-11 parenthetical (prd-writer).

## 5. Risk assessment

**Credentials and data.**
- Slice 3 changes which AWS credentials win. A mistake here either blanks the OIDC credentials or silently uses stale `.env` keys.
- Mitigation: group semantics, and tests covering both the recipe-env and `$(shell)`-env paths on both make versions.
- The harness must strip every `AWS_*` variable and put stubs first on `PATH`, so tests can never touch a real account.

**Make portability.**
- macOS ships GNU make 3.81 (`/usr/bin/make`, and bash 3.2 at `/bin/bash`); ubuntu runners ship 4.3.
- Constructs that don't exist in 3.81: `--eval`, `undefine`, `!=`, `.ONESHELL`, `$(file)`.
- In 3.81 and 4.3, `$(shell)` does not inherit exported variables. In 4.4+ it does, so never `export` recursive variables that contain `$(shell)`.
- Run the H-MAKE suite under both versions through `MAKE_BIN`.

**`$(wildcard .env)` semantics.**
- It expands once, at parse time, and make caches directory contents. A `.env` created during the run by `start`/`test-back` is not read in that same run.
- Handled with `?=` defaults for the `POSTGRES_*` variables, plus a regression test.
- docker compose reads `.env` itself, so `start` is unaffected.

**No-detach semantics.**
- They depend on `aws cloudformation deploy` treating omitted parameters as their previous values. That is documented CLI behavior.
- On a brand-new stack, omitted parameters use their `""` defaults, which is correct.

**IAM exactness (FR-18).**
- `update-function-code` with an ECR image normally needs no extra ECR permissions for the caller, because the repository policy already grants `lambda.amazonaws.com` pull.
- If the first live deploy fails with an ECR permission error, adding permissions such as `ecr:GetRepositoryPolicy`/`SetRepositoryPolicy` is a PRD change: escalate (Rule 4), don't just add them.
- `DescribeStacks` on a stack that doesn't exist (`backend-domain` before it is created) returns empty, so the `FRONTEND_API_URL` fallback still works.

**cfn-lint versions.**
- `uvx cfn-lint` pulls the latest version. `AWS::IAM::OIDCProvider.ThumbprintList` is optional in current schemas.
- If an older lint requires it, add GitHub's published thumbprints.
- `ApiMappingKey: ""` may be flagged; the fallback is to omit it (see Slice 5).
- `DeletionPolicy` without `UpdateReplacePolicy` triggers W3011, which is why both are set.

**Runners.**
- `ubuntu-24.04-arm` is free only for public repos. If the fork is private, use ubuntu-latest with docker/setup-qemu-action plus buildx; `--platform linux/arm64` is already the default `ARCH`.
- The arm image may not include the AWS CLI; the deploy job installs it if missing.
- The `function-updated-v2` waiter needs a recent AWS CLI v2, on laptops too.
- `docker buildx --push` with the default docker driver works for a single native platform.

**Reusable-workflow concurrency.**
- A cancelling concurrency block in the called workflow could cancel runs made through deploy.
- This is avoided with the event-keyed group and `cancel-in-progress` only for `pull_request`.

**Supply chain.**
- Action SHAs must be looked up (`git ls-remote`), never typed from memory. This needs network access.

**External calls and hard constraints.**
- `aws-oidc-deploy` only prints the `gh variable set --repo MasterDay3/OneTwoThree` command.
- Nothing in any slice pushes to or targets `dobosevych/OneTwoThree`.

**Persistence.**
- No database or schema changes.
- A rollback does not revert migrations. This is documented and tested (no migrate call).

**Git-flow tension (needs a user decision).**
- `deploy.yml` fires only on push to `main`, but the rules say never work on `main` except for deliberate release merges.
- The live demos need pushes to `main`: the red/green lint demo, the concurrency demo, and the stray-`.env` job test (TC-9.9b).
- The user must approve these release merges of `dev` into `main` explicitly.

## 6. Dependencies

**Local tools.**
- `uv`/`uvx`, which pull pytest, pyyaml, cfn-lint, ruff and actionlint-py on demand. No new project dependencies are added to `back/` or `front/`.
- `git`, `bash`, `make`. Optionally Homebrew `gmake` to test GNU make 4.x locally.

**GitHub actions** (SHA-pinned):
- `actions/checkout` v4
- `actions/setup-node` v4
- `astral-sh/setup-uv` v6
- `aws-actions/configure-aws-credentials` v4.x
- Optionally `docker/setup-qemu-action` (only if the arm runner is unavailable)

**AWS services added:** API Gateway HTTP API, a regional ACM certificate, an IAM OIDC provider and role. All are free or near-free at this volume (NFR-3).

**Manual steps the student must do, in order** (no AWS CLI or credentials on this machine per the scratchpad):
1. Confirm the domain's Route 53 public hosted zone exists in the account. Log in locally with `aws sso login` or a profile.
2. **GitHub:** enable Actions on the fork (it is disabled by default on forks). Confirm the repo is public (for the arm runner). Confirm no custom OIDC `sub` claim template is set.
3. If the stacks don't exist yet: `make aws-deploy` (Cognito → backend → frontend).
4. `make aws-frontend-https DOMAIN=<domain>` (cert → wait → stack → CORS/Cognito → DNS), then `make aws-frontend-https-check DOMAIN=<domain>`. Put `DOMAIN=<domain>` in the local `.env`.
5. `make aws-backend-https DOMAIN=<domain>` (cert → wait → backend-domain stack → frontend republish), then `make aws-backend-https-check`.
6. `make aws-oidc-deploy` (the frontend and backend stacks must already exist). Then run the printed `gh variable set AWS_DEPLOY_ROLE_ARN --repo MasterDay3/OneTwoThree --body <arn>`. It must be a Variable, not a Secret (TC-16.8).
7. Merge `dev` into `main` (user-approved release) → first CI deploy. Then run the live TCs:
   - red/green lint (9.5)
   - concurrency (9.10b/c)
   - `.env` guard (9.9b)
   - trust denials (10.1b/10.3/10.4/10.6b)
   - rollback (11.6)
   - `KEEP_IMAGE` check (AC-17 live)
   - cold start (14.1b)
   - CORS and Cognito (13.x)
8. Capture the manual submission artefacts: a screenshot, the live `https://app.<domain>` and `https://api.<domain>` URLs, and the fork link.
9. After grading: `make aws-destroy` (add `DESTROY_OIDC=1` to also remove the role; the provider is retained). Optionally delete the ACM certificates and the final Aurora snapshot.
