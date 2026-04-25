# Pipelines

Three pipelines drive the release flow. All three share the same GCP project,
Artifact Registry repo, and Secret Manager values; they are isolated by
Cloud Run service name, Cloud SQL instance, GCS bucket, and Terraform
workspace.

| Pipeline | Trigger | Workflow | Target |
|---|---|---|---|
| Testing (CI) | PR or push to `main`, `develop` | `ci.yml` | Lint · typecheck · build · pytest |
| Develop | push to `develop` | `deploy-dev.yml` → `_deploy-gcp.yml` | `medai-api-dev` + `medai-web-dev`, TF workspace `dev` |
| Production | push to `main` | `deploy-prod.yml` → `_deploy-gcp.yml` | `medai-api` + `medai-web`, TF workspace `default` |

`_deploy-gcp.yml` is the reusable workflow both deploys call. It builds and
pushes the api + web images, then runs `terraform apply` against the
requested workspace + tfvars file so the two services always roll together.

## Branch flow

```
feature → PR (CI) → develop (deploy-dev) → PR (CI) → main (deploy-prod, manual approval)
```

## One-time setup per environment

For each GitHub environment (`dev`, `production`) configure under
**Settings → Environments**:

**Vars**
- `GCP_PROJECT_ID`
- `GCP_REGION`
- `GCP_TF_STATE_BUCKET`
- `API_BASE_URL` — deployed api URL for that env (baked into the web build)

**Secrets**
- `GCP_WORKLOAD_IDENTITY_PROVIDER`
- `GCP_DEPLOY_SA`
- `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`

For `production`, add a **required reviewer** on the environment so the
deploy job pauses for approval before any cloud changes happen.

The dev env can re-use the same WIF provider and deploy SA, or you can
provision a separate SA scoped to dev resources only.

## Terraform state

Backend is shared (one GCS bucket, key prefix per workspace):
- prod → `default` workspace (preserves existing state)
- dev → `dev` workspace, created on first apply via `workspace select -or-create`

## Manual deploy

Both deploy workflows expose `workflow_dispatch` with an optional
`image_tag` input — useful for redeploying a known-good SHA without
pushing a new commit.
