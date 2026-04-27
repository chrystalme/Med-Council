# Dev shares the GCP project with prod, so the AR repo and Secret Manager
# secrets already exist by the time dev's workspace runs. These import blocks
# adopt them into the current workspace's state. They are planned no-ops when
# the target is already managed (prod), and run once on dev's first apply.
#
# Trade-off: both workspaces now reference the same physical resources. Drift
# on the resource blocks will be applied by whichever workspace plans last.
# The long-term fix is to split shared infra into its own root module.

import {
  to = google_artifact_registry_repository.api
  id = "projects/${var.project_id}/locations/${var.region}/repositories/${var.ar_repo}"
}

import {
  for_each = toset(var.secret_names)
  to       = google_secret_manager_secret.secrets[each.value]
  id       = "projects/${var.project_id}/secrets/${each.value}"
}
