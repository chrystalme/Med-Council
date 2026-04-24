# Secret Manager resources for runtime env vars.
# Values are populated out-of-band (see terraform/README.md).
# Terraform owns the container; humans (or CI) own the payload.

resource "google_secret_manager_secret" "secrets" {
  for_each = toset(var.secret_names)

  secret_id = each.value

  replication {
    auto {}
  }

  # Don't clobber a versioned secret if someone renames it; require manual delete.
  lifecycle {
    prevent_destroy = true
  }

  depends_on = [google_project_service.services]
}

# Grant the runtime SA read access to each secret's latest version.
resource "google_secret_manager_secret_iam_member" "run_sa_accessors" {
  for_each = google_secret_manager_secret.secrets

  secret_id = each.value.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.run_sa.email}"
}
