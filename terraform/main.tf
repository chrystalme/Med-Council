provider "google" {
  project = var.project_id
  region  = var.region
}

locals {
  bucket_name = var.gcs_bucket_name != "" ? var.gcs_bucket_name : "${var.project_id}-medai-attachments"
  image_uri   = "${var.region}-docker.pkg.dev/${var.project_id}/${var.ar_repo}/api:${var.image_tag}"
}

# ── APIs ─────────────────────────────────────────────────────────────────────
resource "google_project_service" "services" {
  for_each = toset([
    "run.googleapis.com",
    "sqladmin.googleapis.com",
    "secretmanager.googleapis.com",
    "artifactregistry.googleapis.com",
    "iam.googleapis.com",
    "compute.googleapis.com",
    "storage.googleapis.com",
    "servicenetworking.googleapis.com",
  ])
  service            = each.value
  disable_on_destroy = false
}

# ── Artifact Registry ────────────────────────────────────────────────────────
resource "google_artifact_registry_repository" "api" {
  location      = var.region
  repository_id = var.ar_repo
  description   = "Container images for MedAI Council."
  format        = "DOCKER"

  depends_on = [google_project_service.services]
}

# ── Cloud SQL (Postgres 15 + pgvector) ──────────────────────────────────────
resource "random_password" "db" {
  length  = 32
  special = true
}

resource "google_sql_database_instance" "main" {
  name             = var.db_instance_name
  region           = var.region
  database_version = "POSTGRES_15"

  settings {
    tier              = var.db_tier
    availability_type = "ZONAL" # flip to REGIONAL for HA in prod
    disk_autoresize   = true
    disk_type         = "PD_SSD"

    ip_configuration {
      ipv4_enabled = true # Cloud Run connects via /cloudsql socket regardless
    }

    backup_configuration {
      enabled                        = true
      point_in_time_recovery_enabled = true
      start_time                     = "03:00"
    }

    database_flags {
      name  = "cloudsql.iam_authentication"
      value = "on"
    }
  }

  deletion_protection = true

  depends_on = [google_project_service.services]
}

resource "google_sql_database" "app" {
  name     = var.db_name
  instance = google_sql_database_instance.main.name
}

resource "google_sql_user" "api_user" {
  name     = var.db_user
  instance = google_sql_database_instance.main.name
  password = random_password.db.result
}

# ── GCS bucket for attachment blobs ──────────────────────────────────────────
resource "google_storage_bucket" "attachments" {
  name                        = local.bucket_name
  location                    = var.region
  force_destroy               = false
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  versioning {
    enabled = true
  }

  lifecycle_rule {
    condition {
      age = 365
    }
    action {
      type          = "SetStorageClass"
      storage_class = "NEARLINE"
    }
  }

  depends_on = [google_project_service.services]
}

# ── Runtime service account ─────────────────────────────────────────────────
resource "google_service_account" "run_sa" {
  account_id   = "${var.service_name}-runtime"
  display_name = "Runtime SA for ${var.service_name} Cloud Run service"
}

# SPEECH_PROVIDER=gcloud requires Speech-to-Text + Text-to-Speech APIs.
resource "google_project_service" "speech_services" {
  for_each = toset([
    "speech.googleapis.com",
    "texttospeech.googleapis.com",
  ])
  service            = each.value
  disable_on_destroy = false
}

resource "google_project_iam_member" "run_sa_sql_client" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.run_sa.email}"
}

resource "google_project_iam_member" "run_sa_sql_instance_user" {
  project = var.project_id
  role    = "roles/cloudsql.instanceUser"
  member  = "serviceAccount:${google_service_account.run_sa.email}"
}

resource "google_storage_bucket_iam_member" "run_sa_bucket" {
  bucket = google_storage_bucket.attachments.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.run_sa.email}"
}

# Secret accessor IAM is added per-secret in secrets.tf.

# ── Cloud Run service ───────────────────────────────────────────────────────
resource "google_cloud_run_v2_service" "api" {
  name     = var.service_name
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.run_sa.email

    scaling {
      min_instance_count = var.min_instances
      max_instance_count = var.max_instances
    }

    volumes {
      name = "cloudsql"
      cloud_sql_instance {
        instances = [google_sql_database_instance.main.connection_name]
      }
    }

    containers {
      image = local.image_uri

      resources {
        limits = {
          cpu    = var.cpu
          memory = var.memory
        }
        cpu_idle          = true
        startup_cpu_boost = true
      }

      ports {
        container_port = 8080
      }

      volume_mounts {
        name       = "cloudsql"
        mount_path = "/cloudsql"
      }

      # Non-secret runtime config.
      env {
        name  = "STORAGE_BACKEND"
        value = "gcs"
      }
      env {
        name  = "GCS_BUCKET"
        value = google_storage_bucket.attachments.name
      }
      env {
        name  = "VECTOR_STORE"
        value = "postgres"
      }
      env {
        name  = "ATTACHMENT_STORE"
        value = "postgres"
      }
      env {
        name  = "RATE_LIMIT_ENABLED"
        value = "1"
      }
      env {
        name  = "SPEECH_PROVIDER"
        value = "gcloud"
      }

      # Secrets → env vars.
      dynamic "env" {
        for_each = toset(var.secret_names)
        content {
          name = env.value
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.secrets[env.value].secret_id
              version = "latest"
            }
          }
        }
      }

      startup_probe {
        http_get {
          path = "/health"
        }
        initial_delay_seconds = 5
        timeout_seconds       = 5
        period_seconds        = 5
        failure_threshold     = 10
      }

      liveness_probe {
        http_get {
          path = "/health"
        }
        period_seconds    = 30
        timeout_seconds   = 5
        failure_threshold = 3
      }
    }

    timeout = "${var.request_timeout_seconds}s"
  }

  depends_on = [
    google_artifact_registry_repository.api,
    google_sql_user.api_user,
    google_secret_manager_secret_iam_member.run_sa_accessors,
  ]
}

resource "google_cloud_run_v2_service_iam_member" "public" {
  count    = var.allow_unauthenticated ? 1 : 0
  project  = var.project_id
  location = google_cloud_run_v2_service.api.location
  name     = google_cloud_run_v2_service.api.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}
