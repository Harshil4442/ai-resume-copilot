variable "security_mail_wake_resources_enabled" {
  type    = bool
  default = false
}

variable "security_mail_wake_worker_origin" {
  type    = string
  default = null

  validation {
    condition = var.security_mail_wake_worker_origin == null ? true : can(regex(
      "^https://hirewiz-security-mail-worker-[a-z0-9-]+(\\.[a-z]+-[a-z]+[1-9][0-9]*)?\\.run\\.app$",
      var.security_mail_wake_worker_origin
    )) && !strcontains(var.security_mail_wake_worker_origin, "---")
    error_message = "Use the exact untagged HTTPS origin of the private security mail worker."
  }
}

data "google_project" "security_mail_wake" {
  count      = var.security_mail_wake_resources_enabled ? 1 : 0
  project_id = var.project_id
}

resource "google_service_account" "security_mail_wake" {
  count        = var.security_mail_wake_resources_enabled ? 1 : 0
  project      = var.project_id
  account_id   = "hirewiz-security-mail-task"
  display_name = "HireWiz private security mail task caller"
}

resource "google_cloud_tasks_queue" "security_mail_wake" {
  count    = var.security_mail_wake_resources_enabled ? 1 : 0
  project  = var.project_id
  location = var.region
  name     = "hirewiz-security-mail-wake"

  rate_limits {
    max_concurrent_dispatches = 1
    max_dispatches_per_second = 1
  }
  retry_config {
    max_attempts       = 8
    max_retry_duration = "0s"
    min_backoff        = "30s"
    max_backoff        = "60s"
    max_doublings      = 1
  }
  http_target {
    http_method = "POST"
    oidc_token {
      service_account_email = google_service_account.security_mail_wake[0].email
      audience              = coalesce(var.security_mail_wake_worker_origin, "https://unconfigured.invalid")
    }
    uri_override {
      uri_override_enforce_mode = "ALWAYS"
      scheme                    = "HTTPS"
      host                      = trimprefix(coalesce(var.security_mail_wake_worker_origin, "https://unconfigured.invalid"), "https://")
      port                      = 443
    }
  }
  lifecycle {
    precondition {
      condition     = var.security_mail_wake_worker_origin != null
      error_message = "Verify the internal-only security mail worker before provisioning the wake queue."
    }
  }
}

resource "google_project_iam_custom_role" "security_mail_wake_publisher" {
  count       = var.security_mail_wake_resources_enabled ? 1 : 0
  project     = var.project_id
  role_id     = "hirewizSecurityMailWakePublisher"
  title       = "HireWiz security mail wake enqueue"
  permissions = ["cloudtasks.tasks.create"]
}

resource "google_cloud_tasks_queue_iam_member" "security_mail_wake_publisher" {
  count    = var.security_mail_wake_resources_enabled ? 1 : 0
  project  = var.project_id
  location = google_cloud_tasks_queue.security_mail_wake[0].location
  name     = google_cloud_tasks_queue.security_mail_wake[0].name
  role     = google_project_iam_custom_role.security_mail_wake_publisher[0].name
  member   = "serviceAccount:${var.api_service_account}"

  lifecycle {
    precondition {
      condition     = var.api_service_account != "hirewiz-security-mail-task@${var.project_id}.iam.gserviceaccount.com"
      error_message = "The API publisher and the dedicated task caller must be distinct service accounts."
    }
  }
}

resource "google_service_account_iam_member" "security_mail_wake_cloud_tasks" {
  count              = var.security_mail_wake_resources_enabled ? 1 : 0
  service_account_id = google_service_account.security_mail_wake[0].name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:service-${data.google_project.security_mail_wake[0].number}@gcp-sa-cloudtasks.iam.gserviceaccount.com"
}

resource "google_cloud_run_v2_service_iam_member" "security_mail_wake_invoker" {
  count    = var.security_mail_wake_resources_enabled ? 1 : 0
  project  = var.project_id
  location = var.region
  name     = "hirewiz-security-mail-worker"
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.security_mail_wake[0].email}"
}
