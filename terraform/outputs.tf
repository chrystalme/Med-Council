output "service_url" {
  description = "HTTPS URL of the Cloud Run service."
  value       = google_cloud_run_v2_service.api.uri
}

output "service_account_email" {
  description = "Runtime service account email."
  value       = google_service_account.run_sa.email
}

output "sql_instance_connection_name" {
  description = "Cloud SQL connection name (used in DATABASE_URL host=/cloudsql/...)."
  value       = google_sql_database_instance.main.connection_name
}

output "gcs_bucket" {
  description = "GCS bucket for attachment blobs."
  value       = google_storage_bucket.attachments.name
}

output "artifact_registry_repo" {
  description = "Fully-qualified Docker image prefix."
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${var.ar_repo}/api"
}

output "web_service_url" {
  description = "HTTPS URL of the Next.js web service."
  value       = google_cloud_run_v2_service.web.uri
}

output "web_service_account_email" {
  description = "Runtime service account email for the web service."
  value       = google_service_account.web_sa.email
}

output "db_password" {
  description = "Randomly-generated DB password (reveal with `terraform output -raw db_password`)."
  value       = random_password.db.result
  sensitive   = true
}
