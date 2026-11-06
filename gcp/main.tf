# Spry's core on Google Cloud: Artifact Registry, Cloud SQL for PostgreSQL, the API on Cloud Run and
# the frontend on Firebase Hosting. Sign-in, the weekly report and the agenda parser stay on AWS.

# APIs are off in a new project; enabling them here keeps a fresh project reproducible.
locals {
  apis = [
    "run.googleapis.com",
    "sqladmin.googleapis.com",
    "artifactregistry.googleapis.com",
    "secretmanager.googleapis.com",
    "firebasehosting.googleapis.com",
  ]
}

resource "google_project_service" "apis" {
  for_each           = toset(local.apis)
  service            = each.value
  disable_on_destroy = false
}

# --- Container registry ------------------------------------------------------------------------------

resource "google_artifact_registry_repository" "backend" {
  repository_id = "${var.project_name}-backend"
  location      = var.region
  format        = "DOCKER"
  description   = "Backend images, tagged with the commit SHA"

  # 0.5 GB is free: keep only the last three images.
  cleanup_policy_dry_run = false
  cleanup_policies {
    id     = "keep-last-3"
    action = "KEEP"
    most_recent_versions {
      keep_count = 3
    }
  }
  cleanup_policies {
    id     = "delete-older"
    action = "DELETE"
    condition {
      older_than = "0s"
    }
  }

  depends_on = [google_project_service.apis]
}

# --- Database ----------------------------------------------------------------------------------------

resource "google_sql_database_instance" "db" {
  name                = "${var.project_name}-db"
  database_version    = "POSTGRES_16"
  region              = var.region
  deletion_protection = false # `terraform destroy` is the lab's clean-up step

  settings {
    tier              = var.db_tier
    edition           = "ENTERPRISE" # shared-core tiers exist only in Enterprise
    availability_type = "ZONAL"
    disk_type         = "PD_SSD"
    disk_size         = 10
    disk_autoresize   = false
    activation_policy = "ALWAYS"

    backup_configuration {
      enabled = false
    }

    # A public IP with no authorized networks: only the Cloud SQL connector (IAM-checked, encrypted)
    # gets in, which is what Cloud Run uses. No VPC needed.
    ip_configuration {
      ipv4_enabled = true
      ssl_mode     = "ENCRYPTED_ONLY"
    }
  }

  # `make gcp-db-stop` / `gcp-db-start` switch it off and on outside Terraform.
  lifecycle {
    ignore_changes = [settings[0].activation_policy]
  }

  depends_on = [google_project_service.apis]
}

resource "google_sql_database" "app" {
  name     = var.project_name
  instance = google_sql_database_instance.db.name
}

# The password exists only during the run: write-only arguments keep it out of the state as well as
# out of the repository. Bump db_password_version to rotate it.
ephemeral "random_password" "db" {
  length  = 32
  special = false
}

locals {
  db_password_version = 1
}

resource "google_secret_manager_secret" "db_password" {
  secret_id = "${var.project_name}-db-password"

  replication {
    auto {}
  }

  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_version" "db_password" {
  secret                 = google_secret_manager_secret.db_password.id
  secret_data_wo         = ephemeral.random_password.db.result
  secret_data_wo_version = local.db_password_version
}

resource "google_sql_user" "app" {
  name                = var.project_name
  instance            = google_sql_database_instance.db.name
  password_wo         = ephemeral.random_password.db.result
  password_wo_version = local.db_password_version
}

# The API reads this one secret, and no other.
resource "google_secret_manager_secret_iam_member" "api_reads_db_password" {
  secret_id = google_secret_manager_secret.db_password.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${local.api_sa}"
}

# --- API on Cloud Run --------------------------------------------------------------------------------

resource "google_cloud_run_v2_service" "api" {
  name                = "${var.project_name}-api"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = false

  template {
    service_account = local.api_sa

    scaling {
      min_instance_count = 0 # scales to zero
      max_instance_count = 2 # caps the bill and the connections to db-f1-micro
    }

    volumes {
      name = "cloudsql"
      cloud_sql_instance {
        instances = [google_sql_database_instance.db.connection_name]
      }
    }

    containers {
      # A placeholder until `make gcp-deploy-backend` pushes our image and points the service at it;
      # like the Lambdas in infra/, Terraform ignores the image afterwards.
      image = "us-docker.pkg.dev/cloudrun/container/hello"

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
        cpu_idle          = true # request-based billing: CPU only while a request runs
        startup_cpu_boost = true
      }

      volume_mounts {
        name       = "cloudsql"
        mount_path = "/cloudsql"
      }

      env {
        name  = "DB_HOST"
        value = "/cloudsql/${google_sql_database_instance.db.connection_name}"
      }
      env {
        name  = "DB_USER"
        value = google_sql_user.app.name
      }
      env {
        name  = "DB_NAME"
        value = google_sql_database.app.name
      }
      env {
        name = "DB_PASSWORD"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.db_password.secret_id
            version = "latest"
          }
        }
      }
      env {
        name  = "SEED"
        value = "true"
      }
      env {
        name  = "CORS_ORIGINS"
        value = join(",", var.app_origins)
      }
      env {
        name  = "COGNITO_REGION"
        value = var.cognito_region
      }
      env {
        name  = "COGNITO_USER_POOL_ID"
        value = var.cognito_user_pool_id
      }
      env {
        name  = "COGNITO_CLIENT_ID"
        value = var.cognito_client_id
      }
    }
  }

  lifecycle {
    ignore_changes = [
      template[0].containers[0].image,
      client,
      client_version,
    ]
  }

  depends_on = [
    google_project_service.apis,
    google_secret_manager_secret_version.db_password,
    google_secret_manager_secret_iam_member.api_reads_db_password,
  ]
}

# Public: the API checks Cognito ID tokens itself (everything but /api/health needs one).
resource "google_cloud_run_v2_service_iam_member" "public" {
  name     = google_cloud_run_v2_service.api.name
  location = google_cloud_run_v2_service.api.location
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# --- Frontend on Firebase Hosting --------------------------------------------------------------------

# The project's default site (<project>.web.app). Firebase creates it when it is added to the project
# (gcp/bootstrap), so Terraform adopts it. Files and the /api/** rewrite to Cloud Run are deployed with
# the Firebase CLI from front/firebase.json (`make gcp-deploy-frontend`).
import {
  to = google_firebase_hosting_site.default
  id = "projects/${var.project_id}/sites/${var.project_id}"
}

resource "google_firebase_hosting_site" "default" {
  provider = google-beta
  site_id  = var.project_id

  depends_on = [google_project_service.apis]
}
