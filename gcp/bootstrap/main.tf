# Bootstrap: what the gcp/ configuration needs before it can run in this project. Applied once from a
# laptop as yourself (`gcloud auth login --update-adc`), local state (gitignored):
#   cd gcp/bootstrap && terraform init && terraform apply
# It creates the two service accounts, one per program (Terraform, the API), with only the roles each
# needs, lets you act as the Terraform one without a key, and adds Firebase to the project.

terraform {
  required_version = ">= 1.10"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 7.0"
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = "~> 7.0"
    }
  }
}

variable "project_id" {
  type        = string
  description = "The Google Cloud project (created by hand, billing linked)."
}

provider "google" {
  project = var.project_id
}

# Firebase APIs reject user credentials unless the request names a quota project.
provider "google-beta" {
  project               = var.project_id
  user_project_override = true
  billing_project       = var.project_id
}

# Whoever runs the bootstrap may impersonate the Terraform service account.
data "google_client_openid_userinfo" "me" {}

locals {
  # Only what this bootstrap and service account impersonation need; gcp/ enables the rest.
  bootstrap_apis = [
    "cloudresourcemanager.googleapis.com",
    "serviceusage.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "firebase.googleapis.com",
  ]

  # The roles the lab lists for Terraform, minus Service Account User (granted on the API account
  # only, below), plus Firebase Hosting for the site.
  terraform_roles = [
    "roles/run.admin",
    "roles/cloudsql.admin",
    "roles/artifactregistry.admin",
    "roles/secretmanager.admin",
    "roles/serviceusage.serviceUsageAdmin",
    "roles/firebasehosting.admin",
  ]
}

resource "google_project_service" "bootstrap" {
  for_each           = toset(local.bootstrap_apis)
  service            = each.value
  disable_on_destroy = false
}

resource "google_service_account" "terraform" {
  account_id   = "terraform"
  display_name = "Terraform (gcp/)"
  depends_on   = [google_project_service.bootstrap]
}

resource "google_service_account" "api" {
  account_id   = "meetings-api"
  display_name = "Meetings API on Cloud Run"
  depends_on   = [google_project_service.bootstrap]
}

resource "google_project_iam_member" "terraform" {
  for_each = toset(local.terraform_roles)
  project  = var.project_id
  role     = each.value
  member   = google_service_account.terraform.member
}

# Terraform may deploy Cloud Run as the API account, and as no other.
resource "google_service_account_iam_member" "terraform_uses_api" {
  service_account_id = google_service_account.api.name
  role               = "roles/iam.serviceAccountUser"
  member             = google_service_account.terraform.member
}

# The API only connects to Cloud SQL; it reads its password through a binding on that one secret (gcp/).
resource "google_project_iam_member" "api_sql_client" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = google_service_account.api.member
}

# No key: you get short-lived tokens of the Terraform account with your own sign-in.
resource "google_service_account_iam_member" "me_impersonates_terraform" {
  service_account_id = google_service_account.terraform.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "user:${data.google_client_openid_userinfo.me.email}"
}

resource "google_firebase_project" "this" {
  provider   = google-beta
  project    = var.project_id
  depends_on = [google_project_service.bootstrap]
}

output "terraform_service_account" {
  value = google_service_account.terraform.email
}

output "api_service_account" {
  value = google_service_account.api.email
}
