# Bootstrap: what HCP Terraform needs before it can do anything in this account. Applied once from a
# laptop with your own credentials (local state, gitignored):
#   cd infra/bootstrap && terraform init && terraform apply
# Then set TFC_AWS_PROVIDER_AUTH=true and TFC_AWS_RUN_ROLE_ARN=<role_arn> in the workspace.

terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.0"
    }
  }
}

provider "aws" {
  region = "us-east-1"

  default_tags {
    tags = {
      PROJECT_NAME = var.project_name
    }
  }
}

variable "project_name" {
  type    = string
  default = "meetings"
}

variable "tfc_organization" {
  type        = string
  description = "HCP Terraform organization that owns the workspace."
}

variable "tfc_workspace" {
  type        = string
  default     = "meetings-dev"
  description = "The only workspace whose runs may assume the role."
}

data "tls_certificate" "tfc" {
  url = "https://app.terraform.io"
}

resource "aws_iam_openid_connect_provider" "tfc" {
  url             = "https://app.terraform.io"
  client_id_list  = ["aws.workload.identity"]
  thumbprint_list = [data.tls_certificate.tfc.certificates[0].sha1_fingerprint]
}

resource "aws_iam_role" "tfc" {
  name = "hcp-terraform-${var.tfc_organization}"

  # Plan and apply runs of ONE workspace, not every workspace of the organization.
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = aws_iam_openid_connect_provider.tfc.arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = { "app.terraform.io:aud" = "aws.workload.identity" }
        StringLike = {
          "app.terraform.io:sub" = "organization:${var.tfc_organization}:project:*:workspace:${var.tfc_workspace}:run_phase:*"
        }
      }
    }]
  })
}

# For the class project. A real team narrows this to what infra/*.tf manages: Cognito, Lambda, S3, SQS,
# EventBridge Scheduler, SES, Budgets, CloudWatch Logs and IAM roles with a path or name prefix.
resource "aws_iam_role_policy_attachment" "tfc" {
  role       = aws_iam_role.tfc.name
  policy_arn = "arn:aws:iam::aws:policy/AdministratorAccess"
}

output "role_arn" {
  value = aws_iam_role.tfc.arn
}
