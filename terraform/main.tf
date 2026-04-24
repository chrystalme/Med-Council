provider "google" {
  project = var.project_id
  region  = var.region
}

locals {
  bucket_name     = var.gcs_bucket_name != "" ? var.gcs_bucket_name : "${var.project_id}-medai-attachments"
  image_uri       = "${var.region}-docker.pkg.dev/${var.project_id}/${var.ar_repo}/api:${var.image_tag}"
  web_image_tag   = var.web_image_tag != "" ? var.web_image_tag : var.image_tag
  web_image_uri   = "${var.region}-docker.pkg.dev/${var.project_id}/${var.ar_repo}/web:${local.web_image_tag}"
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
  name                = var.service_name
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = false

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
      # Where to redirect GET / to. Without this the API's `/` returns a small
      # JSON landing page instead of the legacy static UI. On prod we point
      # at the web Cloud Run service so /api typos still land the user at the
      # real UI.
      env {
        name  = "WEB_BASE_URL"
        value = google_cloud_run_v2_service.web.uri
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

# ── Web service (Next.js on Cloud Run) ──────────────────────────────────────
# Runtime SA for the web container. Only needs to read its own secret bindings;
# does not touch Cloud SQL or GCS.
resource "google_service_account" "web_sa" {
  account_id   = "${var.web_service_name}-runtime"
  display_name = "Runtime SA for ${var.web_service_name} Cloud Run service"
}

# Grant the web SA read access to the Clerk secrets it needs at runtime.
# (Publishable key is also exposed via the client bundle; it's still managed
# centrally here so rotations flow through one place.)
resource "google_secret_manager_secret_iam_member" "web_sa_accessors" {
  for_each = toset([
    "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY",
    "CLERK_SECRET_KEY",
  ])

  secret_id = google_secret_manager_secret.secrets[each.value].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.web_sa.email}"
}

resource "google_cloud_run_v2_service" "web" {
  name                = var.web_service_name
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = false

  template {
    service_account = google_service_account.web_sa.email

    scaling {
      min_instance_count = var.web_min_instances
      max_instance_count = var.web_max_instances
    }

    containers {
      image = local.web_image_uri

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
        cpu_idle          = true
        startup_cpu_boost = true
      }

      ports {
        container_port = 3000
      }

      # `API_BASE_URL` is intentionally NOT set here. Next.js freezes the
      # rewrites() result at build time in standalone output, so the API
      # origin is baked into the image via the Docker build-arg — passing a
      # runtime env of the same name would be dead-letter and also create a
      # terraform cycle with the api service (which now redirects / → web).

      # Client-side base is intentionally empty: `councilFetch` passes paths
      # that already start with `/api/...`, so prepending anything here would
      # double-prefix (saw `/api/api/me` → 404 during bootstrap). Same-origin
      # calls are forwarded to API_BASE_URL by the Next rewrite in next.config.ts.
      env {
        name  = "NEXT_PUBLIC_API_BASE_URL"
        value = ""
      }

      env {
        name = "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.secrets["NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY"].secret_id
            version = "latest"
          }
        }
      }

      env {
        name = "CLERK_SECRET_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.secrets["CLERK_SECRET_KEY"].secret_id
            version = "latest"
          }
        }
      }

      startup_probe {
        tcp_socket {
          port = 3000
        }
        initial_delay_seconds = 5
        timeout_seconds       = 5
        period_seconds        = 5
        failure_threshold     = 10
      }
    }

    timeout = "60s"
  }

  depends_on = [
    google_artifact_registry_repository.api,
    google_secret_manager_secret_iam_member.web_sa_accessors,
  ]
}

resource "google_cloud_run_v2_service_iam_member" "web_public" {
  count    = var.allow_unauthenticated ? 1 : 0
  project  = var.project_id
  location = google_cloud_run_v2_service.web.location
  name     = google_cloud_run_v2_service.web.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}
