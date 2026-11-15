variable "resume_upload_wake_resources_enabled" {
  type    = bool
  default = false
}

variable "resume_upload_wake_coordinator_origin" {
  type    = string
  default = null

  validation {
    condition = var.resume_upload_wake_coordinator_origin == null ? true : can(regex(
      "^https://hirewiz-resume-coordinator-[a-z0-9-]+(\\.[a-z0-9-]+)*\\.run\\.app$",
      var.resume_upload_wake_coordinator_origin
    ))
    error_message = "Use the exact untagged HTTPS origin of the private resume coordinator."
  }
}

data "google_project" "resume_upload_wake" {
  count      = var.resume_upload_wake_resources_enabled ? 1 : 0
  project_id = var.project_id
}

resource "google_service_account" "resume_upload_wake" {
  count        = var.resume_upload_wake_resources_enabled ? 1 : 0
  project      = var.project_id
  account_id   = "hirewiz-resume-wake"
  display_name = "HireWiz direct resume wake caller"
}

resource "google_cloud_tasks_queue" "resume_upload_wake" {
  count    = var.resume_upload_wake_resources_enabled ? 1 : 0
  project  = var.project_id
  location = var.region
  name     = "hirewiz-resume-wake"

  rate_limits {
    max_concurrent_dispatches = 1
    max_dispatches_per_second = 1
  }
  retry_config {
    max_attempts       = 24
    max_retry_duration = "0s"
    min_backoff        = "30s"
    max_backoff        = "60s"
    max_doublings      = 1
  }
  http_target {
    http_method = "POST"
    oidc_token {
      service_account_email = google_service_account.resume_upload_wake[0].email
      audience              = coalesce(var.resume_upload_wake_coordinator_origin, "https://unconfigured.invalid")
    }
    uri_override {
      uri_override_enforce_mode = "ALWAYS"
      scheme                    = "HTTPS"
      host                      = trimprefix(coalesce(var.resume_upload_wake_coordinator_origin, "https://unconfigured.invalid"), "https://")
      port                      = 443
      query_override { query_params = "" }
    }
  }
  lifecycle {
    precondition {
      condition     = var.resume_upload_wake_coordinator_origin != null
      error_message = "Select the verified private coordinator origin before provisioning its wake queue."
    }
  }
}

resource "google_project_iam_custom_role" "resume_upload_wake_publisher" {
  count       = var.resume_upload_wake_resources_enabled ? 1 : 0
  project     = var.project_id
  role_id     = "hirewizResumeUploadWakePublisher"
  title       = "HireWiz resume wake enqueue and exact task read"
  permissions = ["cloudtasks.tasks.create", "cloudtasks.tasks.fullView", "cloudtasks.tasks.get"]
}

resource "google_cloud_tasks_queue_iam_member" "resume_upload_wake_publisher" {
  count    = var.resume_upload_wake_resources_enabled ? 1 : 0
  project  = var.project_id
  location = google_cloud_tasks_queue.resume_upload_wake[0].location
  name     = google_cloud_tasks_queue.resume_upload_wake[0].name
  role     = google_project_iam_custom_role.resume_upload_wake_publisher[0].name
  member   = "serviceAccount:${var.api_service_account}"
}

resource "google_service_account_iam_member" "resume_upload_wake_cloud_tasks" {
  count              = var.resume_upload_wake_resources_enabled ? 1 : 0
  service_account_id = google_service_account.resume_upload_wake[0].name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:service-${data.google_project.resume_upload_wake[0].number}@gcp-sa-cloudtasks.iam.gserviceaccount.com"
}

resource "google_cloud_run_v2_service_iam_member" "resume_upload_wake_invoker" {
  count    = var.resume_upload_wake_resources_enabled ? 1 : 0
  project  = var.project_id
  location = var.region
  name     = "hirewiz-resume-coordinator"
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.resume_upload_wake[0].email}"
}
