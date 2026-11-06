terraform {
  required_version = ">= 1.11"

  # A second HCP Terraform workspace, separate from infra/'s meetings-dev: one plan can never touch
  # both clouds. Local execution: the state lives in HCP Terraform, plan and apply run on the laptop.
  cloud {
    organization = "ooad-masterday3"

    workspaces {
      name = "meetings-gcp"
    }
  }

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 7.0"
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = "~> 7.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.7"
    }
  }
}

# You sign in with gcloud (application default credentials); every call is then made as the
# Terraform service account from gcp/bootstrap through short-lived tokens. No key file anywhere.
provider "google" {
  project                     = var.project_id
  region                      = var.region
  impersonate_service_account = local.terraform_sa
  default_labels              = local.labels
}

provider "google-beta" {
  project                     = var.project_id
  region                      = var.region
  impersonate_service_account = local.terraform_sa
  default_labels              = local.labels
}

locals {
  terraform_sa = "terraform@${var.project_id}.iam.gserviceaccount.com"
  api_sa       = "meetings-api@${var.project_id}.iam.gserviceaccount.com"
  labels = {
    project_name = var.project_name
    managed_by   = "terraform"
  }
}
