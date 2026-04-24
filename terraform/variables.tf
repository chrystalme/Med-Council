variable "project_id" {
  description = "GCP project ID."
  type        = string
}

variable "region" {
  description = "Primary region for Cloud Run, Cloud SQL, and GCS."
  type        = string
  default     = "us-central1"
}

variable "service_name" {
  description = "Cloud Run service name."
  type        = string
  default     = "medai-api"
}

variable "ar_repo" {
  description = "Artifact Registry repo name (Docker format)."
  type        = string
  default     = "medai"
}

variable "image_tag" {
  description = "Image tag to deploy (e.g. a git SHA). Required."
  type        = string
}

variable "db_instance_name" {
  description = "Cloud SQL instance name."
  type        = string
  default     = "medai-db"
}

variable "db_name" {
  description = "Application database name."
  type        = string
  default     = "medai_council"
}

variable "db_user" {
  description = "Application database user."
  type        = string
  default     = "medai"
}

variable "db_tier" {
  description = "Cloud SQL machine tier. Use db-custom-* in prod; db-f1-micro for dev."
  type        = string
  default     = "db-custom-1-3840"
}

variable "gcs_bucket_name" {
  description = "GCS bucket for attachment blobs. Must be globally unique; defaults to <project>-medai-attachments."
  type        = string
  default     = ""
}

variable "min_instances" {
  description = "Cloud Run min instances (0 = scale to zero)."
  type        = number
  default     = 0
}

variable "max_instances" {
  description = "Cloud Run max instances."
  type        = number
  default     = 10
}

variable "cpu" {
  description = "Cloud Run CPU per instance."
  type        = string
  default     = "1"
}

variable "memory" {
  description = "Cloud Run memory per instance."
  type        = string
  default     = "1Gi"
}

variable "request_timeout_seconds" {
  description = "Per-request timeout for Cloud Run."
  type        = number
  default     = 300
}

variable "allow_unauthenticated" {
  description = "If true, Cloud Run accepts public traffic. JWT verification still runs in-app."
  type        = bool
  default     = true
}

# Names of the Secret Manager secrets the service binds as env vars.
# Values are populated out-of-band after `apply`.
variable "secret_names" {
  description = "Secret Manager secret names bound as env vars into Cloud Run."
  type        = list(string)
  default = [
    "OPENROUTER_API_KEY",
    "OPENAI_API_KEY",
    "GROQ_API_KEY",
    "CLERK_ISSUER",
    "CLERK_SECRET_KEY",
    "CLERK_AUTHORIZED_PARTIES",
    "RESEND_API_KEY",
    "RESEND_FROM_EMAIL",
    "ONCALL_DOCTOR_EMAIL",
    "FEEDBACK_SECRET",
    "DATABASE_URL",
    "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY",
  ]
}

# Web (Next.js) service configuration.
variable "web_service_name" {
  description = "Cloud Run service name for the Next.js frontend."
  type        = string
  default     = "medai-web"
}

variable "web_image_tag" {
  description = "Tag of the web image to deploy. Defaults to image_tag so both services roll together."
  type        = string
  default     = ""
}

variable "web_min_instances" {
  description = "Cloud Run min instances for the web service."
  type        = number
  default     = 1
}

variable "web_max_instances" {
  description = "Cloud Run max instances for the web service."
  type        = number
  default     = 10
}
