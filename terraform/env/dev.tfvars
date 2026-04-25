# Dev environment overrides.
# Selected by deploy-dev.yml via the `dev` Terraform workspace.
#
# All Cloud Run / Cloud SQL / GCS resource names are suffixed so they can
# co-exist alongside prod in the same GCP project. Cheaper Cloud SQL tier
# and scale-to-zero defaults keep idle cost near zero.

env_suffix        = "-dev"
service_name      = "medai-api-dev"
web_service_name  = "medai-web-dev"
db_instance_name  = "medai-db-dev"
db_name           = "medai_council_dev"
db_tier           = "db-f1-micro"
min_instances     = 0
max_instances     = 3
web_min_instances = 0
web_max_instances = 3
