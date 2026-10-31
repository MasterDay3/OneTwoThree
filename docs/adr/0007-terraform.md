# ADR 0007: Cognito, the weekly report and the agenda parser move from CloudFormation to Terraform

- **Status:** accepted, 2026-10-31
- **Code:** `infra/versions.tf`, `variables.tf`, `auth.tf`, `reports.tf`, `ai.tf`, `outputs.tf`,
  `terraform.tfvars`, `infra/bootstrap/`; replaces `infra/cognito.yaml`, `reports.yaml`, `ai.yml`

## Context

Labs 4–6 added three CloudFormation stacks: `meetings-cognito`, `meetings-reports` and `meetings-ai`.
They work, but the project already reaches outside AWS (the Google OAuth client of Lab 4, the GitHub
repository and its OIDC trust of Lab 2, DNS for the SES domain at nic.ua), and it will need a second
environment. The stacks were also deployed from a laptop with `make aws-*-stack`, so who changed what
was only in shell history. The three stacks hold data that must survive the move: the user pool
(4 accounts) and the reports bucket.

## Decision

- **Terraform**, one root module in `infra/`, for the three former stacks: 37 resources. The other
  stacks (ECR, backend, frontend, backend domain, GitHub OIDC) stay CloudFormation for now. Terraform
  could read their outputs with `aws_cloudformation_stack` if it needed them.
- **State and runs in HCP Terraform**, workspace `meetings-dev`, Remote execution, connected to the
  repository with `infra` as the working directory. A pull request gets a speculative plan, and a merge
  to `main` applies it. The Makefile only plans and reads outputs (`infra-plan`, `infra-outputs`).
- **Dynamic credentials.** HCP Terraform exchanges a per-run OIDC token for the role from
  `infra/bootstrap/` (applied once from a laptop). The role trusts only the `meetings-dev` workspace.
- **Adopted, not re-created.** Every resource got `DeletionPolicy: Retain`, the stacks were deleted,
  and `import` blocks handed the resources to Terraform. The plan was `37 to import, 0 to add,
  2 to change, 0 to destroy`. The two changes were safe: the Google client secret was only marked
  sensitive (same value), and the mailer's identical code was re-uploaded from
  `infra/lambda/report_mailer/index.py` instead of the inline `ZipFile`. Nothing was re-created. Names
  CloudFormation generated (IAM roles, Lambda permission statement IDs) are kept as they are.
  `prevent_destroy` guards the user pool and the reports bucket.
- **Two roads.** GitHub Actions still deploys the application (image, migrations, frontend); HCP
  Terraform deploys the infrastructure. Terraform ignores the functions' `image_uri`, which CI moves.
  When a change needs both (for example a new environment variable the new code reads), the
  infrastructure goes first: the old code ignores an extra variable, but the new code fails without it.

## Alternatives considered

- **Stay on CloudFormation.** AWS keeps the state, and failed changes roll back. But it stops at the
  edge of one AWS account. The Google client and the GitHub settings stay manual, and a second
  environment means more parameter plumbing in the Makefile.
- **AWS CDK.** It is real code instead of YAML, but it still produces CloudFormation, with the same
  single-cloud limit, and adds a Node toolchain to an infrastructure that is small.
- **OpenTofu.** It accepts the same files and has an open-source licence. It lost only because the
  course uses HCP Terraform for state and runs. Switching later means `tofu` instead of `terraform` and
  another state backend (S3 with locking).
- **State in S3 instead of HCP Terraform.** It works, but it is one more bucket to bootstrap, and it
  gives no runs in the cloud and no plan on pull requests without extra CI work.

## Consequences

- **Who can change the infrastructure:** whoever can merge to `main`. Applies run in HCP Terraform with
  short-lived credentials. Nobody needs AWS keys, and an emergency apply from a laptop is still possible
  with `terraform login` and owner rights in the organization.
- **Secrets:** `google_client_secret` is a sensitive variable of the workspace, never in a file. It
  still ends up in plain text in the **state** (as an attribute of the Google identity provider), in the
  state versions HCP Terraform keeps, and in Cognito itself. Access to the workspace's state is
  therefore access to the secret. `budget_email` and `report_recipients` are workspace variables, so the
  personal email address is not in the repository.
- **CI** reads outputs from the state (`TF_API_TOKEN` secret) to build the frontend. That token can
  read the state, secret included, so it should be a team token with read-only access, not a user token.
- **If HCP Terraform is down:** nothing in production changes. The app, the report schedule and the
  parser keep running, because Terraform is not in the request path. Infrastructure changes wait. CI's
  frontend build fails at `terraform init` until it is back, and the backend deploy still works.
- **Drift** is found by every plan. A change made in the console is reverted at the next apply unless
  it is first written into `infra/*.tf` and merged.
- **Failed apply:** no automatic rollback. The state records what was created, and the fix is another
  commit and apply.
