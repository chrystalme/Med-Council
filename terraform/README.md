# Infra — GCP

Terraform for the MedAI Council API on Cloud Run with Cloud SQL (Postgres +
pgvector) and Cloud Storage (GCS) for attachment blobs. Secrets live in
Secret Manager; the image is pulled from Artifact Registry.

## Layout

- `main.tf`       — providers, core resources (Cloud SQL, GCS, Cloud Run, AR, IAM).
- `variables.tf`  — inputs (project, region, image tag, secret names).
- `outputs.tf`    — service URL, connection name, bucket, SA email.
- `secrets.tf`    — Secret Manager resources (values provisioned out-of-band).
- `versions.tf`   — provider/version pins.

## First run

```bash
# Auth + project
gcloud auth application-default login
gcloud config set project <YOUR_PROJECT_ID>

# Seed backend state bucket (one-time). Replace <PROJECT_ID>.
gsutil mb -l us-central1 gs://<PROJECT_ID>-tfstate
gsutil versioning set on gs://<PROJECT_ID>-tfstate

# Init + plan + apply
cd terraform
terraform init \
  -backend-config="bucket=<PROJECT_ID>-tfstate"
cp terraform.tfvars.example terraform.tfvars  # edit values
terraform plan
terraform apply
```

## Provisioning secrets

`secrets.tf` creates the Secret Manager resources but does not write values.
Populate them after `apply`:

```bash
for key in OPENROUTER_API_KEY OPENAI_API_KEY CLERK_SECRET_KEY CLERK_ISSUER RESEND_API_KEY FEEDBACK_SECRET DATABASE_URL; do
  read -s -p "$key: " val
  echo
  printf '%s' "$val" | gcloud secrets versions add "$key" --data-file=-
done
```

`DATABASE_URL` format for Cloud SQL via the Unix socket that Cloud Run auto-mounts:

```
postgresql://medai:<PASSWORD>@/medai_council?host=/cloudsql/<PROJECT>:<REGION>:medai-db
```

## Deploy a new image

```bash
# From repo root — build + push to Artifact Registry.
REGION=us-central1
PROJECT=$(gcloud config get-value project)
TAG=$(git rev-parse --short HEAD)
AR=${REGION}-docker.pkg.dev/${PROJECT}/medai/api

gcloud auth configure-docker ${REGION}-docker.pkg.dev -q
docker buildx build \
  --platform linux/amd64 \
  -f apps/api/Dockerfile \
  -t ${AR}:${TAG} \
  -t ${AR}:latest \
  --push \
  apps/api

# Roll the Cloud Run service onto the new image.
cd terraform
terraform apply -var="image_tag=${TAG}"
```

## pgvector extension

Cloud SQL Postgres 15+ ships with pgvector available; the extension itself is
created by the API at startup (see `main._init_cases_db`, which runs
`CREATE EXTENSION IF NOT EXISTS vector`). The DB user therefore needs the
`CREATE` privilege once — granted by `google_sql_user.api_user` at
`cloudsqlsuperuser` role scope in dev. Tighten to a plain role + pre-created
extension for prod.
