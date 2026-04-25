# Production environment overrides.
# Selected by deploy-prod.yml via the `default` Terraform workspace
# (preserves existing prod state — do not rename the workspace).

env_suffix        = ""
service_name      = "medai-api"
web_service_name  = "medai-web"
db_instance_name  = "medai-db"
db_name           = "medai_council"
db_tier           = "db-custom-1-3840"
min_instances     = 0
max_instances     = 10
web_min_instances = 1
web_max_instances = 10
