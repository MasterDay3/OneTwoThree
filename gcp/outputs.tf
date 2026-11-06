output "registry" {
  description = "Where `make gcp-deploy-backend` pushes the backend image."
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.backend.repository_id}"
}

output "api_service" {
  value = google_cloud_run_v2_service.api.name
}

output "api_url" {
  value = google_cloud_run_v2_service.api.uri
}

output "db_instance" {
  value = google_sql_database_instance.db.name
}

output "db_connection_name" {
  value = google_sql_database_instance.db.connection_name
}

output "site_url" {
  value = google_firebase_hosting_site.default.default_url
}
