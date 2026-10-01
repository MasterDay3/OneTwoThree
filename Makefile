# Meetings App — local Docker Compose workflow and AWS deployment (CloudFormation).
# Run `make` or `make help` to list targets.

SHELL := /bin/bash
.DEFAULT_GOAL := help
# Prerequisite chains (deploy-backend: push → update → migrate) must run in order even under -j.
.NOTPARALLEL:

# .env holds compose settings and, for local use only, optional static AWS keys. Only the keys defined
# in .env are exported. AWS credentials from the environment (`aws sso login` / AWS_PROFILE, exported
# session keys, CI's OIDC role) always win: if any of the four below is set in the environment, .env
# supplies none of them. The wildcard keeps make from creating .env just because it is included.
AWS_CRED_VARS := AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN AWS_PROFILE
$(foreach v,$(AWS_CRED_VARS),$(eval _env_$(v) := $$($(v))))
-include $(wildcard .env)
export $(shell sed -n 's/^\([A-Za-z_][A-Za-z0-9_]*\)=.*/\1/p' .env 2>/dev/null)
AWS_CREDS_FROM_ENV := $(if $(strip $(foreach v,$(AWS_CRED_VARS),$(_env_$(v)))),1)
ifdef AWS_CREDS_FROM_ENV
$(foreach v,$(AWS_CRED_VARS),$(eval $(v) := $$(_env_$(v))))
$(foreach v,$(AWS_CRED_VARS),$(eval $(if $(_env_$(v)),export,unexport) $(v)))
endif
# Defaults for a .env that `start`/`test-back` create during this run (make only reads .env at startup).
POSTGRES_USER     ?= meetings
POSTGRES_PASSWORD ?= meetings
POSTGRES_DB       ?= meetings

# ---------------------------------------------------------------------------
# Settings (override on the command line, e.g. `make aws-deploy ARCH=amd64`)
# ---------------------------------------------------------------------------
PROJECT     ?= meetings
# Everything is deployed to us-east-1: CloudFront takes custom-domain certificates only from there.
# (Set here, not in .env; `make … AWS_REGION=…` still overrides it for the backend.)
AWS_REGION  := us-east-1
export AWS_REGION
export AWS_DEFAULT_REGION := $(AWS_REGION)

# arm64 (Graviton, cheaper; native on Apple Silicon) or amd64
ARCH        ?= arm64
# Image tag = the full commit SHA (CI passes the pushed commit's SHA), so the running image names its commit
# and a rollback is "deploy the previous tag". Lambda only picks up a new image when its URI changes,
# so uncommitted builds get a unique -dirty-<timestamp> suffix. Never `latest`.
# Only an explicitly given TAG may be a rollback target (see aws-backend-rollback).
_USER_TAG   := $(TAG)
ifndef TAG
TAG         := $(shell sha=$$(git rev-parse HEAD 2>/dev/null) && { git diff --quiet HEAD 2>/dev/null && echo $$sha || echo $$sha-dirty-$$(date +%Y%m%d%H%M%S); } || date +%Y%m%d%H%M%S)
endif

BACKEND_ECR_STACK := $(PROJECT)-backend-ecr
BACKEND_STACK     := $(PROJECT)-backend
BACKEND_FUNCTION  := $(PROJECT)-backend
# Aurora Serverless v2 with express configuration (`make aws-db`): the only Aurora the AWS Free plan allows.
DB_CLUSTER        := $(PROJECT)-db
DB_MIN_ACU        ?= 0
DB_MAX_ACU        ?= 1
DB_AUTO_PAUSE     ?= 300
BACKEND_PARAMS    := infra/backend.params.env
BACKEND_DOMAIN_STACK := $(PROJECT)-backend-domain
OIDC_STACK        := $(PROJECT)-github-oidc
GITHUB_REPO       := MasterDay3/OneTwoThree
FRONTEND_STACK    := $(PROJECT)-frontend
COGNITO_STACK     := $(PROJECT)-cognito
# Every resource gets this tag (in the templates and as a stack tag).
STACK_TAGS        := PROJECT_NAME=$(PROJECT)

# CloudFront flat-rate Free plan ($0/month); PAY_AS_YOU_GO if the account can't subscribe (AWS Free Tier
# accounts, or 3 free plans already in use).
CLOUDFRONT_PLAN   ?= FREE

# Optional custom domain: DOMAIN=example.com serves the site at app.example.com (`make aws-frontend-https`)
# and the API at api.example.com; each can be overridden on its own. Empty = CloudFront/AWS domains only.
DOMAIN            ?=
FRONTEND_DOMAIN   ?= $(if $(DOMAIN),app.$(DOMAIN))
BACKEND_DOMAIN    ?= $(if $(DOMAIN),api.$(DOMAIN))
CERT_SH           := PROJECT_NAME=$(PROJECT) infra/scripts/cert.sh
# Browser origins the API always allows besides the frontend's (local development).
CORS_LOCAL        ?= http://localhost:3000,http://localhost:5173

LAMBDA_ARCH  = $(if $(filter arm64,$(ARCH)),arm64,x86_64)
HASH        := \#
COMMA       := ,
# $(shell) does not inherit exported variables in GNU make < 4.4 (macOS ships 3.81),
# so AWS lookups inside $(shell) load .env themselves — minus its AWS credential lines when the
# environment has credentials (eval, not `. <(...)`: bash 3.2 can't source a process substitution).
ENV_FILE_SOURCE := $(if $(AWS_CREDS_FROM_ENV),eval "$$(grep -vE '^[[:space:]]*(export[[:space:]]+)?(AWS_ACCESS_KEY_ID|AWS_SECRET_ACCESS_KEY|AWS_SESSION_TOKEN|AWS_PROFILE)=' .env)",. ./.env)
LOAD_ENV    := set -a; [ -f .env ] && $(ENV_FILE_SOURCE); set +a; export AWS_REGION=$(AWS_REGION) AWS_DEFAULT_REGION=$(AWS_REGION);
AWS_SHELL   := $(LOAD_ENV) aws
# Recursive (=) so they are looked up only when a recipe needs them, after the stacks exist.
stack_output = $(shell $(AWS_SHELL) cloudformation describe-stacks --stack-name $(1) \
                 --query "Stacks[0].Outputs[?OutputKey=='$(2)'].OutputValue" --output text 2>/dev/null)
ECR_URI      = $(call stack_output,$(BACKEND_ECR_STACK),RepositoryUri)
ECR_REGISTRY = $(firstword $(subst /, ,$(ECR_URI)))
BACKEND_PARAMS_ARGS = $(shell [ -f $(BACKEND_PARAMS) ] && grep -v -e '^[[:space:]]*$(HASH)' -e '^[[:space:]]*$$' $(BACKEND_PARAMS))
backend_output  = $(call stack_output,$(BACKEND_STACK),$(1))
db_cluster_field = $(shell $(AWS_SHELL) rds describe-db-clusters --db-cluster-identifier $(DB_CLUSTER) \
                     --query "DBClusters[0].$(1)" --output text 2>/dev/null)
frontend_output = $(call stack_output,$(FRONTEND_STACK),$(1))
cognito_output  = $(call stack_output,$(COGNITO_STACK),$(1))
backend_domain_output = $(call stack_output,$(BACKEND_DOMAIN_STACK),$(1))
API_URL          = $(call backend_output,ApiUrl)
# The SPA calls https://api.<domain>/ once the backend-domain stack exists, else the function URL.
FRONTEND_API_URL = $(or $(call backend_domain_output,ApiUrl),$(API_URL))
FRONTEND_ORIGINS = $(call frontend_output,SiteOrigins)
CORS_ORIGINS_AWS = $(CORS_LOCAL)$(if $(FRONTEND_ORIGINS),$(COMMA)$(FRONTEND_ORIGINS))
# Cognito may redirect back (Google sign-in) to every origin the API allows.
COGNITO_CALLBACK_URLS = $(subst $(COMMA),/auth/callback$(COMMA),$(CORS_ORIGINS_AWS))/auth/callback
COGNITO_LOGOUT_URLS   = $(subst $(COMMA),/$(COMMA),$(CORS_ORIGINS_AWS))/
COGNITO_POOL_ID   = $(call cognito_output,UserPoolId)
COGNITO_CLIENT    = $(call cognito_output,UserPoolClientId)
FRONTEND_CERT_ARN = $(if $(FRONTEND_DOMAIN),$(shell $(LOAD_ENV) $(CERT_SH) arn $(FRONTEND_DOMAIN) 2>/dev/null))
FRONTEND_ZONE_ID  = $(shell $(LOAD_ENV) $(CERT_SH) zone-id $(FRONTEND_DOMAIN) 2>/dev/null)
BACKEND_CERT_ARN  = $(if $(BACKEND_DOMAIN),$(shell $(LOAD_ENV) $(CERT_SH) arn $(BACKEND_DOMAIN) 2>/dev/null))
BACKEND_ZONE_ID   = $(shell $(LOAD_ENV) $(CERT_SH) zone-id $(BACKEND_DOMAIN) 2>/dev/null)
oidc_output       = $(call stack_output,$(OIDC_STACK),$(1))
# The custom domain is attached once its certificate is issued; until then only the CloudFront domain serves.
FRONTEND_DOMAIN_ARGS = $(if $(FRONTEND_CERT_ARN),CertificateArn=$(FRONTEND_CERT_ARN) DomainName=$(FRONTEND_DOMAIN) HostedZoneId=$(FRONTEND_ZONE_ID))

# A stack whose first create failed is left in ROLLBACK_COMPLETE (holding no resources) and can't be
# updated; delete it so the next deploy creates it again.
define clear_failed_stack
	@if [ "$$(aws cloudformation describe-stacks --stack-name $(1) --query 'Stacks[0].StackStatus' --output text 2>/dev/null)" = ROLLBACK_COMPLETE ]; then \
	  echo "Stack $(1) is in ROLLBACK_COMPLETE after a failed create; deleting it before deploying again"; \
	  aws cloudformation delete-stack --stack-name $(1) && aws cloudformation wait stack-delete-complete --stack-name $(1); \
	fi
endef

TEST_DB_URL := postgresql+psycopg://$(POSTGRES_USER):$(POSTGRES_PASSWORD)@localhost:$(or $(DB_PORT),5432)/meetings_test

##@ Local (Docker Compose)

.env:
	cp .env.example .env
	@echo "Created .env from .env.example — review it (ports, AWS credentials)."

.PHONY: up
up: start ## Start the stack, then rebuild backend/frontend whenever their files change (Ctrl+C stops watching)
	@echo "Watching back/ and front/ for changes — Ctrl+C stops watching, containers keep running."
	docker compose watch --no-up

.PHONY: start
start: .env ## Build and start db, backend and frontend in the background, without watching
	docker compose up -d --build --wait
	@echo "App: http://localhost:$(or $(FRONTEND_PORT),3000)   API docs: http://localhost:$(or $(FRONTEND_PORT),3000)/api/docs"

.PHONY: down
down: ## Stop all containers (data is kept)
	docker compose down

.PHONY: clean
clean: ## Stop containers and delete the database volume
	docker compose down -v --remove-orphans

.PHONY: restart
restart: down start ## Restart the stack

.PHONY: logs
logs: ## Follow logs of all services (SERVICE=backend to narrow)
	docker compose logs -f $(SERVICE)

.PHONY: ps
ps: ## Show container status
	docker compose ps

##@ Quality

.PHONY: test
test: test-back test-front ## Run all tests

.PHONY: test-back
test-back: .env ## Backend tests (starts the db container, creates meetings_test)
	docker compose up -d --wait db
	docker compose exec -T db psql -U $(POSTGRES_USER) -d $(POSTGRES_DB) -tAc \
	  "SELECT 1 FROM pg_database WHERE datname = 'meetings_test'" | grep -q 1 || \
	  docker compose exec -T db psql -U $(POSTGRES_USER) -d $(POSTGRES_DB) -c "CREATE DATABASE meetings_test"
	cd back && TEST_DATABASE_URL=$(TEST_DB_URL) uv run pytest -q

.PHONY: test-front
test-front: ## Frontend tests
	cd front && npm test

.PHONY: test-infra
test-infra: ## Infra tests: Makefile, CloudFormation templates, scripts (stubbed, no AWS calls)
	uvx --with pyyaml pytest infra/tests -q

.PHONY: lint
lint: ## Code style checks (same as CI)
	cd back && uv run ruff check . && uv run ruff format --check .
	cd front && npm run lint && npm run format:check && npm run typecheck
	uvx cfn-lint infra/*.yaml

.PHONY: format
format: ## Auto-format backend and frontend
	cd back && uv run ruff check --fix . && uv run ruff format .
	cd front && npm run format

##@ AWS backend (Lambda + Aurora Serverless v2 via CloudFormation)

.PHONY: aws-check
aws-check: ## Verify the AWS credentials work (SSO/profile or exported keys; static keys in .env for local use only)
	@aws sts get-caller-identity --query '[Account, Arn]' --output text \
	  || { echo "AWS credentials missing/invalid/expired: run \`aws sso login\` / export credentials (CI: OIDC), or set static keys in .env for local use only"; exit 1; }

# ---- Deploy contract: what CI runs on every push to main, and what you can run yourself ----
# Code and assets only. Infrastructure (the *-stack targets) is changed deliberately from a laptop.

.PHONY: deploy-backend
deploy-backend: aws-check aws-backend-push aws-backend-update-code aws-backend-migrate ## Roll out backend code: build + push image $(TAG), update the Lambda, migrate (never CloudFormation)

.PHONY: deploy-frontend
deploy-frontend: aws-check aws-frontend-publish ## Roll out the frontend: build, upload to S3, invalidate CloudFront (never CloudFormation)

.PHONY: aws-backend-deploy
aws-backend-deploy: aws-check aws-backend-ecr aws-backend-push aws-db aws-backend-stack aws-backend-migrate ## Deploy backend: ECR, image, Aurora, Lambda + function URL, migrations
	@echo
	@echo "API:      $(API_URL)"
	@echo "API docs: $(call backend_output,ApiDocsUrl)"

.PHONY: aws-backend-ecr
aws-backend-ecr: ## Create/update the ECR repository stack
	$(call clear_failed_stack,$(BACKEND_ECR_STACK))
	aws cloudformation deploy --stack-name $(BACKEND_ECR_STACK) --template-file infra/backend-ecr.yaml \
	  --parameter-overrides ProjectName=$(PROJECT) --tags $(STACK_TAGS) --no-fail-on-empty-changeset

.PHONY: aws-backend-login
aws-backend-login: ## Log Docker in to ECR
	@test -n "$(ECR_URI)" || { echo "ECR repository not found: run \`make aws-backend-ecr\` first (and check AWS credentials in .env)"; exit 1; }
	aws ecr get-login-password | docker login --username AWS --password-stdin $(ECR_REGISTRY)

.PHONY: aws-backend-push
aws-backend-push: aws-backend-login ## Build the Lambda image for linux/$(ARCH) and push it with tag $(TAG) (the commit SHA)
	docker buildx build --platform linux/$(ARCH) --provenance=false -f back/Dockerfile.lambda \
	  -t $(ECR_URI):$(TAG) --push back

.PHONY: aws-db
aws-db: ## Create the Aurora Serverless v2 cluster once (express configuration: no VPC, IAM sign-in only)
	@if aws rds describe-db-clusters --db-cluster-identifier $(DB_CLUSTER) >/dev/null 2>&1; then \
	  echo "Aurora cluster $(DB_CLUSTER) exists"; \
	else \
	  aws rds create-db-cluster --db-cluster-identifier $(DB_CLUSTER) --engine aurora-postgresql \
	    --with-express-configuration --tags Key=PROJECT_NAME,Value=$(PROJECT) \
	    --serverless-v2-scaling-configuration MinCapacity=$(DB_MIN_ACU),MaxCapacity=$(DB_MAX_ACU),SecondsUntilAutoPause=$(DB_AUTO_PAUSE) \
	    --query DBCluster.Status --output text; \
	fi
	aws rds wait db-cluster-available --db-cluster-identifier $(DB_CLUSTER)
	aws rds wait db-instance-available --filters Name=db-cluster-id,Values=$(DB_CLUSTER)

.PHONY: aws-backend-stack
aws-backend-stack: ## Create/update the backend stack (Lambda + function URL) with image tag $(TAG); KEEP_IMAGE=1 keeps the image running now
	$(call clear_failed_stack,$(BACKEND_STACK))
	@test -n "$(ECR_URI)" || { echo "ECR repository not found: run \`make aws-backend-ecr\` first (and check AWS credentials in .env)"; exit 1; }
	@test -n "$(COGNITO_POOL_ID)" || { echo "Cognito not deployed: run \`make aws-cognito-deploy\` first"; exit 1; }
	@test -n "$(call db_cluster_field,DbClusterResourceId)" || { echo "Aurora cluster not found: run \`make aws-db\` first"; exit 1; }
	@# The pool's signing keys are passed in with the stack, so requests never wait on downloading them.
	@# KEEP_IMAGE=1 passes the image the Lambda runs now: deploy-backend/rollback change it outside
	@# CloudFormation, so the stack's own last-known ImageUri may be stale.
	$(if $(KEEP_IMAGE),image=$$(aws lambda get-function --function-name $(BACKEND_FUNCTION) \
	  --query Code.ImageUri --output text) || exit 1; [ -n "$$image" ] && [ "$$image" != None ] \
	  || { echo "No live Lambda image found: deploy the backend first"; exit 1; };,image=$(ECR_URI):$(TAG);) \
	jwks=$$(curl -fsS "$(call cognito_output,Issuer)/.well-known/jwks.json") && \
	aws cloudformation deploy --stack-name $(BACKEND_STACK) --template-file infra/backend.yaml \
	  --capabilities CAPABILITY_IAM --no-fail-on-empty-changeset --tags $(STACK_TAGS) \
	  --parameter-overrides ProjectName=$(PROJECT) "ImageUri=$$image" \
	    Architecture=$(LAMBDA_ARCH) "CorsOrigins=$(CORS_ORIGINS_AWS)" \
	    CognitoUserPoolId=$(COGNITO_POOL_ID) CognitoClientId=$(COGNITO_CLIENT) "CognitoJwks=$$jwks" \
	    DbHost=$(call db_cluster_field,Endpoint) DbResourceId=$(call db_cluster_field,DbClusterResourceId) \
	    DbUsername=$(call db_cluster_field,MasterUsername) \
	    $(BACKEND_PARAMS_ARGS)

.PHONY: aws-backend-update-code
aws-backend-update-code: ## Point the Lambda at image tag $(TAG) and wait until it is live (no CloudFormation)
	@test -n "$(ECR_URI)" || { echo "ECR repository not found: run \`make aws-backend-ecr\` first"; exit 1; }
	aws lambda wait function-updated-v2 --function-name $(BACKEND_FUNCTION)
	aws lambda update-function-code --function-name $(BACKEND_FUNCTION) --image-uri $(ECR_URI):$(TAG) \
	  --query CodeSha256 --output text
	aws lambda wait function-updated-v2 --function-name $(BACKEND_FUNCTION)

.PHONY: aws-backend-migrate
aws-backend-migrate: ## Run Alembic migrations (and seeding) inside the Lambda
	@resp=$$(mktemp); \
	err=$$(aws lambda invoke --function-name $(BACKEND_FUNCTION) --cli-binary-format raw-in-base64-out \
	  --payload '{"action": "migrate"}' --cli-read-timeout 0 --query FunctionError --output text "$$resp"); \
	echo "Migrations: $$(cat "$$resp")"; rm -f "$$resp"; \
	[ "$$err" = None ] || { echo "Migration failed ($$err): make aws-backend-logs"; exit 1; }

.PHONY: aws-backend-rollback
aws-backend-rollback: require-rollback-tag aws-check ## Run an earlier image again: TAG=<sha> (ECR keeps the last 5; no rebuild, no migration, schema is not reverted)
	@aws ecr describe-images --repository-name $(PROJECT)-backend --image-ids imageTag=$(TAG) \
	  --query 'imageDetails[0].imagePushedAt' --output text >/dev/null \
	  || { echo "Image tag $(TAG) not in ECR (the repository keeps only the last 5 images)"; exit 1; }
	aws lambda wait function-updated-v2 --function-name $(BACKEND_FUNCTION)
	aws lambda update-function-code --function-name $(BACKEND_FUNCTION) --image-uri $(ECR_URI):$(TAG) \
	  --query CodeSha256 --output text
	aws lambda wait function-updated-v2 --function-name $(BACKEND_FUNCTION)
	@echo "Backend now runs $(TAG). Migrations are not rolled back."

.PHONY: require-rollback-tag
require-rollback-tag:
	@test -n "$(_USER_TAG)" || { echo "Usage: make aws-backend-rollback TAG=<commit sha> (see: aws ecr describe-images --repository-name $(PROJECT)-backend)" >&2; exit 1; }

.PHONY: aws-backend-outputs
aws-backend-outputs: ## Show backend stack outputs (API URL, DB endpoint, ...)
	@aws cloudformation describe-stacks --stack-name $(BACKEND_STACK) \
	  --query "Stacks[0].Outputs[].[OutputKey, OutputValue]" --output table

.PHONY: aws-backend-status
aws-backend-status: ## Show Lambda and Aurora status
	@aws lambda get-function-configuration --function-name $(BACKEND_FUNCTION) \
	  --query "{state:State, lastUpdate:LastUpdateStatus, image:CodeSha256, memory:MemorySize, timeout:Timeout}" --output yaml
	@aws rds describe-db-clusters --db-cluster-identifier $(DB_CLUSTER) \
	  --query "DBClusters[0].{status:Status, capacity:ServerlessV2ScalingConfiguration}" --output yaml

.PHONY: aws-backend-logs
aws-backend-logs: ## Tail backend logs from CloudWatch
	aws logs tail /aws/lambda/$(BACKEND_FUNCTION) --follow --since 30m

.PHONY: aws-backend-health
aws-backend-health: ## Call /api/health on the function URL (the first call after a pause wakes Aurora, ~15 s)
	curl -fsS --max-time 60 $(API_URL)api/health && echo

.PHONY: aws-backend-destroy
aws-backend-destroy: aws-check ## Delete backend stacks and the Aurora cluster (a final Aurora snapshot is kept)
	@read -p "Delete stacks $(BACKEND_STACK), $(BACKEND_ECR_STACK) and cluster $(DB_CLUSTER) in $(AWS_REGION)? [y/N] " ok && [ "$$ok" = y ]
	aws cloudformation delete-stack --stack-name $(BACKEND_STACK)
	aws cloudformation wait stack-delete-complete --stack-name $(BACKEND_STACK)
	@for instance in $$(aws rds describe-db-clusters --db-cluster-identifier $(DB_CLUSTER) \
	    --query "DBClusters[0].DBClusterMembers[].DBInstanceIdentifier" --output text 2>/dev/null); do \
	  aws rds delete-db-instance --db-instance-identifier $$instance --query DBInstance.DBInstanceStatus --output text; \
	  aws rds wait db-instance-deleted --db-instance-identifier $$instance; \
	done
	@if aws rds describe-db-clusters --db-cluster-identifier $(DB_CLUSTER) >/dev/null 2>&1; then \
	  aws rds delete-db-cluster --db-cluster-identifier $(DB_CLUSTER) \
	    --final-db-snapshot-identifier $(DB_CLUSTER)-final-$$(date +%Y%m%d%H%M%S) --query DBCluster.Status --output text; \
	fi
	aws cloudformation delete-stack --stack-name $(BACKEND_ECR_STACK)
	aws cloudformation wait stack-delete-complete --stack-name $(BACKEND_ECR_STACK)
	@echo "Done. Remove the final snapshot with: aws rds describe-db-cluster-snapshots --snapshot-type manual"

##@ AWS frontend (S3 + CloudFront on the flat-rate Free plan, built with the backend URL)

.PHONY: aws-frontend-deploy
aws-frontend-deploy: aws-check aws-frontend-stack aws-frontend-publish aws-frontend-cors ## Deploy frontend: stack, build with the API URL, upload, allow its origin in the API and Cognito
	@echo
	@echo "Site: $(call frontend_output,SiteUrl)"

.PHONY: aws-frontend-stack
aws-frontend-stack: ## Create/update the frontend stack (S3, CloudFront + WAF on the Free plan, custom domain once its certificate is issued; an attached domain is kept, DETACH_DOMAIN=1 removes it)
	$(call clear_failed_stack,$(FRONTEND_STACK))
	aws cloudformation deploy --stack-name $(FRONTEND_STACK) --template-file infra/frontend.yaml \
	  --no-fail-on-empty-changeset --tags $(STACK_TAGS) \
	  --parameter-overrides ProjectName=$(PROJECT) PricingPlan=$(CLOUDFRONT_PLAN) \
	    $(if $(DETACH_DOMAIN),CertificateArn= DomainName= HostedZoneId=,$(FRONTEND_DOMAIN_ARGS))

# Upload order: new hashed assets first, then the index.html that references them, and only then
# delete old chunks, so a browser still holding the previous index.html never requests a missing file.
.PHONY: aws-frontend-publish
aws-frontend-publish: ## Build the SPA with VITE_API_URL=<api.<domain> or function URL>, upload it, invalidate CloudFront
	@api="$(FRONTEND_API_URL)"; bucket="$(call frontend_output,BucketName)"; dist="$(call frontend_output,DistributionId)"; \
	[ -n "$$api" ] || { echo "Backend not deployed: run \`make aws-backend-deploy\` first"; exit 1; }; \
	[ -n "$$bucket" ] || { echo "Frontend stack not found: run \`make aws-frontend-stack\` first"; exit 1; }; \
	[ -n "$(COGNITO_POOL_ID)" ] || { echo "Cognito not deployed: run \`make aws-cognito-deploy\` first"; exit 1; }; \
	echo "Building frontend with VITE_API_URL=$$api" && \
	(cd front && npm ci --no-audit --no-fund && VITE_API_URL="$$api" \
	  VITE_COGNITO_REGION=$(AWS_REGION) VITE_COGNITO_USER_POOL_ID=$(COGNITO_POOL_ID) \
	  VITE_COGNITO_CLIENT_ID=$(COGNITO_CLIENT) VITE_COGNITO_DOMAIN=$(call cognito_output,HostedUiDomain) \
	  VITE_COGNITO_GOOGLE=$(call cognito_output,GoogleEnabled) npm run build) && \
	aws s3 sync front/dist "s3://$$bucket" --exclude index.html \
	  --cache-control "public,max-age=31536000,immutable" && \
	aws s3 cp front/dist/index.html "s3://$$bucket/index.html" --cache-control "no-cache" && \
	aws s3 sync front/dist "s3://$$bucket" --delete --exclude index.html \
	  --cache-control "public,max-age=31536000,immutable" && \
	aws cloudfront create-invalidation --distribution-id "$$dist" --paths "/*" \
	  --query "Invalidation.Status" --output text

.PHONY: aws-frontend-cors
aws-frontend-cors: ## Allow the frontend's origins in the backend's CORS settings and as Cognito redirect URLs (keeps the current image)
	@$(MAKE) --no-print-directory aws-backend-stack KEEP_IMAGE=1
	@$(MAKE) --no-print-directory aws-cognito-stack

.PHONY: aws-frontend-outputs
aws-frontend-outputs: ## Show frontend stack outputs (site URL, bucket, distribution)
	@aws cloudformation describe-stacks --stack-name $(FRONTEND_STACK) \
	  --query "Stacks[0].Outputs[].[OutputKey, OutputValue]" --output table

.PHONY: aws-frontend-destroy
aws-frontend-destroy: aws-check ## Empty the bucket and delete the frontend stack
	@read -p "Delete stack $(FRONTEND_STACK) in $(AWS_REGION)? [y/N] " ok && [ "$$ok" = y ]
	@bucket="$(call frontend_output,BucketName)"; [ -z "$$bucket" ] || aws s3 rm "s3://$$bucket" --recursive --quiet
	aws cloudformation delete-stack --stack-name $(FRONTEND_STACK)
	aws cloudformation wait stack-delete-complete --stack-name $(FRONTEND_STACK)

##@ AWS frontend custom domain (DOMAIN=example.com -> FRONTEND_DOMAIN=app.example.com, optional)

# Guards: domain targets stop here, before any AWS call, when their domain is empty.
.PHONY: require-frontend-domain require-backend-domain
require-frontend-domain:
	@test -n "$(FRONTEND_DOMAIN)" || { echo "FRONTEND_DOMAIN is empty: set DOMAIN=example.com (or FRONTEND_DOMAIN=...)" >&2; exit 1; }

require-backend-domain:
	@test -n "$(BACKEND_DOMAIN)" || { echo "BACKEND_DOMAIN is empty: set DOMAIN=example.com (or BACKEND_DOMAIN=...)" >&2; exit 1; }

.PHONY: aws-frontend-cert
aws-frontend-cert: require-frontend-domain aws-check ## Request (or reuse) the ACM certificate for FRONTEND_DOMAIN (us-east-1) and set up DNS validation
	@$(CERT_SH) request $(FRONTEND_DOMAIN)

.PHONY: aws-frontend-cert-status
aws-frontend-cert-status: require-frontend-domain ## Show the certificate status and its DNS validation record
	@$(CERT_SH) status $(FRONTEND_DOMAIN)

.PHONY: aws-frontend-https
aws-frontend-https: aws-frontend-cert ## Attach FRONTEND_DOMAIN: wait for the certificate, add it to CloudFront, update CORS, set up DNS
	@$(CERT_SH) wait $(FRONTEND_DOMAIN)
	@$(MAKE) --no-print-directory aws-frontend-stack
	@$(MAKE) --no-print-directory aws-frontend-cors
	@$(MAKE) --no-print-directory aws-frontend-dns

.PHONY: aws-frontend-dns
aws-frontend-dns: require-frontend-domain ## Show the DNS record that points FRONTEND_DOMAIN at CloudFront
	@if [ -n "$(FRONTEND_ZONE_ID)" ]; then \
	  echo "Route 53 alias $(FRONTEND_DOMAIN) -> CloudFront is managed by stack $(FRONTEND_STACK) (zone $(FRONTEND_ZONE_ID))."; \
	else \
	  echo "Add this record at the DNS provider of $(FRONTEND_DOMAIN):"; echo; \
	  echo "  Type:  CNAME"; echo "  Name:  $(FRONTEND_DOMAIN)"; \
	  echo "  Value: $(call frontend_output,DistributionDomain)"; \
	fi
	@echo; echo "Site: $(call frontend_output,SiteUrl)   (check: make aws-frontend-https-check)"

.PHONY: aws-frontend-https-check
aws-frontend-https-check: require-frontend-domain ## Check that https://FRONTEND_DOMAIN answers
	@echo "DNS: $$(dig +short $(FRONTEND_DOMAIN) | tr '\n' ' ')"
	curl -fsS -o /dev/null -w "%{http_code} %{url_effective}\n" https://$(FRONTEND_DOMAIN)/

##@ AWS backend custom domain (DOMAIN=example.com -> BACKEND_DOMAIN=api.example.com, optional)

.PHONY: aws-backend-cert
aws-backend-cert: require-backend-domain aws-check ## Request (or reuse) the ACM certificate for BACKEND_DOMAIN and set up DNS validation
	@$(CERT_SH) request $(BACKEND_DOMAIN)

.PHONY: aws-backend-cert-status
aws-backend-cert-status: require-backend-domain ## Show the backend certificate status and its DNS validation record
	@$(CERT_SH) status $(BACKEND_DOMAIN)

.PHONY: aws-backend-https
aws-backend-https: aws-backend-cert ## Put BACKEND_DOMAIN in front of the Lambda (API Gateway HTTP API), then rebuild the frontend against it
	@$(CERT_SH) wait $(BACKEND_DOMAIN)
	$(call clear_failed_stack,$(BACKEND_DOMAIN_STACK))
	aws cloudformation deploy --stack-name $(BACKEND_DOMAIN_STACK) --template-file infra/backend-domain.yaml \
	  --no-fail-on-empty-changeset --tags $(STACK_TAGS) \
	  --parameter-overrides ProjectName=$(PROJECT) BackendFunctionName=$(BACKEND_FUNCTION) \
	    DomainName=$(BACKEND_DOMAIN) CertificateArn=$(BACKEND_CERT_ARN) HostedZoneId=$(BACKEND_ZONE_ID)
	@$(if $(BACKEND_ZONE_ID),,echo "Add at your DNS provider: CNAME $(BACKEND_DOMAIN) -> $(call backend_domain_output,RegionalDomainName)";)
	@# VITE_API_URL is baked in at build time, so the SPA is rebuilt to call the new domain.
	$(MAKE) --no-print-directory aws-frontend-publish

.PHONY: aws-backend-https-check
aws-backend-https-check: require-backend-domain ## Check that https://BACKEND_DOMAIN/api/health answers
	@echo "DNS: $$(dig +short $(BACKEND_DOMAIN) | tr '\n' ' ')"
	@code=$$(curl -sS -o /dev/null --max-time 60 -w "%{http_code}" https://$(BACKEND_DOMAIN)/api/health); \
	echo "HTTP $$code https://$(BACKEND_DOMAIN)/api/health"; \
	[ "$$code" = 200 ] || { echo "Not answering yet: DNS may still be propagating (check again in a few minutes)"; exit 1; }

.PHONY: aws-backend-domain-destroy
aws-backend-domain-destroy: aws-check ## Delete the api.<domain> stack (the function URL keeps working)
	@read -p "Delete stack $(BACKEND_DOMAIN_STACK) in $(AWS_REGION)? [y/N] " ok && [ "$$ok" = y ]
	aws cloudformation delete-stack --stack-name $(BACKEND_DOMAIN_STACK)
	aws cloudformation wait stack-delete-complete --stack-name $(BACKEND_DOMAIN_STACK)
	@echo "Run \`make aws-frontend-publish\` to rebuild the frontend against the function URL."

##@ CI/CD (GitHub Actions deploys through an OIDC role; no stored AWS keys)

.PHONY: aws-oidc-deploy
aws-oidc-deploy: aws-check ## Create/update the GitHub OIDC deploy role (after the frontend stack exists; re-run if it is recreated)
	$(call clear_failed_stack,$(OIDC_STACK))
	@bucket="$(call frontend_output,BucketName)"; dist="$(call frontend_output,DistributionId)"; \
	[ -n "$$bucket" ] && [ -n "$$dist" ] || { echo "Frontend stack not found: run \`make aws-frontend-deploy\` first"; exit 1; }; \
	existing=$$(aws iam list-open-id-connect-providers \
	  --query "OpenIDConnectProviderList[?ends_with(Arn,'/token.actions.githubusercontent.com')].Arn | [0]" \
	  --output text | sed 's/^None$$//'); \
	[ "$(call oidc_output,ProviderManagedByStack)" = true ] && existing=; \
	aws cloudformation deploy --stack-name $(OIDC_STACK) --template-file infra/github-oidc.yaml \
	  --capabilities CAPABILITY_NAMED_IAM --no-fail-on-empty-changeset --tags $(STACK_TAGS) \
	  --parameter-overrides ProjectName=$(PROJECT) FrontendBucketName=$$bucket \
	    FrontendDistributionId=$$dist ExistingOidcProviderArn=$$existing && \
	role=$$(aws cloudformation describe-stacks --stack-name $(OIDC_STACK) \
	  --query "Stacks[0].Outputs[?OutputKey=='DeployRoleArn'].OutputValue" --output text) && \
	echo && echo "Deploy role: $$role" && \
	echo "Store it as a repository variable (not a secret):" && \
	echo "  gh variable set AWS_DEPLOY_ROLE_ARN --repo $(GITHUB_REPO) --body $$role"

.PHONY: aws-oidc-outputs
aws-oidc-outputs: ## Show the OIDC stack outputs (deploy role ARN, provider)
	@aws cloudformation describe-stacks --stack-name $(OIDC_STACK) \
	  --query "Stacks[0].Outputs[].[OutputKey, OutputValue]" --output table

.PHONY: aws-oidc-destroy
aws-oidc-destroy: aws-check ## Delete the deploy role stack (the account's GitHub OIDC provider is retained)
	@read -p "Delete stack $(OIDC_STACK)? CI can no longer deploy afterwards [y/N] " ok && [ "$$ok" = y ]
	aws cloudformation delete-stack --stack-name $(OIDC_STACK)
	aws cloudformation wait stack-delete-complete --stack-name $(OIDC_STACK)

##@ AWS auth (Cognito user pool; Google sign-in once GOOGLE_CLIENT_ID/SECRET are set in .env)

.PHONY: aws-cognito-deploy
aws-cognito-deploy: aws-check aws-cognito-stack aws-cognito-env ## Deploy Cognito, then print the lines to add to .env
	@echo "Then: \`make up\` for local sign-in; on AWS \`make aws-deploy\` (or aws-backend-stack KEEP_IMAGE=1 + aws-frontend-publish)."

.PHONY: aws-cognito-stack
aws-cognito-stack: ## Create/update the Cognito stack (redirect URLs = local origins + the site's)
	$(call clear_failed_stack,$(COGNITO_STACK))
	@# Not echoed: the command line would show the Google client secret.
	@echo "Deploying $(COGNITO_STACK) (Google sign-in: $(if $(GOOGLE_CLIENT_ID),on,off))"
	@aws cloudformation deploy --stack-name $(COGNITO_STACK) --template-file infra/cognito.yaml \
	  --no-fail-on-empty-changeset --tags $(STACK_TAGS) \
	  --parameter-overrides ProjectName=$(PROJECT) "CallbackUrls=$(COGNITO_CALLBACK_URLS)" \
	    "LogoutUrls=$(COGNITO_LOGOUT_URLS)" "GoogleClientId=$(GOOGLE_CLIENT_ID)" \
	    "GoogleClientSecret=$(GOOGLE_CLIENT_SECRET)"

.PHONY: aws-cognito-env
aws-cognito-env: ## Print the .env lines for the deployed user pool (replace the empty COGNITO_* ones)
	@test -n "$(COGNITO_POOL_ID)" || { echo "Cognito stack not found: run \`make aws-cognito-deploy\` first"; exit 1; }
	@echo
	@echo "Add to .env (replacing the empty COGNITO_* lines):"
	@echo
	@echo "COGNITO_REGION=$(AWS_REGION)"
	@echo "COGNITO_USER_POOL_ID=$(COGNITO_POOL_ID)"
	@echo "COGNITO_CLIENT_ID=$(COGNITO_CLIENT)"
	@echo "COGNITO_DOMAIN=$(call cognito_output,HostedUiDomain)"
	@echo "COGNITO_GOOGLE_ENABLED=$(call cognito_output,GoogleEnabled)"
	@echo

.PHONY: aws-cognito-outputs
aws-cognito-outputs: ## Show Cognito stack outputs (pool, client, Hosted UI domain)
	@aws cloudformation describe-stacks --stack-name $(COGNITO_STACK) \
	  --query "Stacks[0].Outputs[].[OutputKey, OutputValue]" --output table

.PHONY: aws-cognito-destroy
aws-cognito-destroy: aws-check ## Delete the Cognito stack — ALL user accounts are deleted with it
	@read -p "Delete stack $(COGNITO_STACK) and every user account in it? [y/N] " ok && [ "$$ok" = y ]
	aws cloudformation delete-stack --stack-name $(COGNITO_STACK)
	aws cloudformation wait stack-delete-complete --stack-name $(COGNITO_STACK)

##@ AWS (all parts)

.PHONY: aws-deploy
aws-deploy: aws-cognito-deploy aws-backend-deploy aws-frontend-deploy ## Deploy everything: Cognito, backend, then the frontend built with their IDs and URL

.PHONY: aws-destroy
aws-destroy: aws-frontend-destroy aws-backend-domain-destroy aws-backend-destroy aws-cognito-destroy $(if $(DESTROY_OIDC),aws-oidc-destroy) ## Delete everything on AWS (each stack asks first; DESTROY_OIDC=1 also removes the CI role)

##@ Help

.PHONY: help
help: ## Show this help
	@awk 'BEGIN {FS = ":.*##"; printf "Usage: make \033[36m<target>\033[0m\n"} \
	  /^[a-zA-Z_.-]+:.*?##/ { printf "  \033[36m%-26s\033[0m %s\n", $$1, $$2 } \
	  /^##@/ { printf "\n\033[1m%s\033[0m\n", substr($$0, 5) }' $(MAKEFILE_LIST)
