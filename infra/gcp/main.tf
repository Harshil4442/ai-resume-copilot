terraform {
  required_version = ">= 1.10, < 2.0"
  required_providers {
    google = { source = "hashicorp/google", version = "7.27.0" }
  }
  backend "gcs" {}
}

provider "google" {
  project = var.project_id
  region  = var.region
}

variable "project_id" { type = string }
variable "region" {
  type    = string
  default = "us-central1"
}
variable "environment" {
  type    = string
  default = "production"
}
variable "artifact_bucket" { type = string }
variable "api_service_account" { type = string }
variable "worker_service_account" { type = string }
variable "tasks_service_account" { type = string }
variable "database_secret" { type = string }
variable "task_token_secret" { type = string }
variable "analysis_queue_name" {
  type    = string
  default = "hirewiz-analysis"
}
variable "worker_bootstrap_secret" {
  type    = string
  default = "hirewiz-worker-jwt-bootstrap"
}

resource "google_service_account" "employer_worker" {
  account_id   = "hirewiz-employer-worker"
  display_name = "HireWiz employer workloads (no model credentials)"
}

resource "google_secret_manager_secret_iam_member" "worker_secrets" {
  for_each  = toset([var.database_secret, var.task_token_secret, var.worker_bootstrap_secret])
  secret_id = each.value
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.employer_worker.email}"
}

resource "google_service_account_iam_member" "employer_tasks_identity" {
  service_account_id = "projects/${var.project_id}/serviceAccounts/${var.tasks_service_account}"
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.employer_worker.email}"
}

resource "google_service_account_iam_member" "analysis_tasks_identity" {
  service_account_id = "projects/${var.project_id}/serviceAccounts/${var.tasks_service_account}"
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${var.worker_service_account}"
}

resource "google_secret_manager_secret_iam_member" "analysis_task_token" {
  secret_id = var.task_token_secret
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${var.worker_service_account}"
}

resource "google_cloud_tasks_queue_iam_member" "analysis_enqueue" {
  project  = var.project_id
  location = var.region
  name     = var.analysis_queue_name
  role     = "roles/cloudtasks.enqueuer"
  member   = "serviceAccount:${var.worker_service_account}"
}

locals {
  queues = {
    ingestion   = { name = "hirewiz-employer-ingestion", concurrency = 2, rate = 2 }
    search      = { name = "hirewiz-employer-search", concurrency = 4, rate = 5 }
    application = { name = "hirewiz-employer-application", concurrency = 2, rate = 1 }
  }
}

resource "google_cloud_tasks_queue" "employer" {
  for_each = local.queues
  name     = each.value.name
  location = var.region
  rate_limits {
    max_concurrent_dispatches = each.value.concurrency
    max_dispatches_per_second = each.value.rate
  }
  retry_config {
    max_attempts       = 6
    max_retry_duration = "3600s"
    min_backoff        = "10s"
    max_backoff        = "300s"
    max_doublings      = 5
  }
  stackdriver_logging_config { sampling_ratio = 1 }
}

resource "google_cloud_tasks_queue_iam_member" "api_enqueue" {
  for_each = google_cloud_tasks_queue.employer
  project  = var.project_id
  location = var.region
  name     = each.value.name
  role     = "roles/cloudtasks.enqueuer"
  member   = "serviceAccount:${var.api_service_account}"
}

resource "google_cloud_tasks_queue_iam_member" "worker_enqueue" {
  for_each = google_cloud_tasks_queue.employer
  project  = var.project_id
  location = var.region
  name     = each.value.name
  role     = "roles/cloudtasks.enqueuer"
  member   = "serviceAccount:${var.worker_service_account}"
}

resource "google_cloud_tasks_queue_iam_member" "employer_enqueue" {
  for_each = google_cloud_tasks_queue.employer
  project  = var.project_id
  location = var.region
  name     = each.value.name
  role     = "roles/cloudtasks.enqueuer"
  member   = "serviceAccount:${google_service_account.employer_worker.email}"
}

resource "google_storage_bucket" "application_artifacts" {
  name                        = var.artifact_bucket
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false
  labels                      = { product = "hirewiz", environment = var.environment, data_class = "private" }
  # Deletion is driven by durable owner deletion records, not age-only removal.
  soft_delete_policy { retention_duration_seconds = 604800 }
  lifecycle_rule {
    condition {
      age            = 7
      matches_prefix = ["releases/"]
    }
    action { type = "Delete" }
  }
}

resource "google_storage_bucket_iam_member" "api_create" {
  bucket = google_storage_bucket.application_artifacts.name
  role   = "roles/storage.objectCreator"
  member = "serviceAccount:${var.api_service_account}"
}
resource "google_storage_bucket_iam_member" "api_preview" {
  bucket = google_storage_bucket.application_artifacts.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${var.api_service_account}"
}
resource "google_storage_bucket_iam_member" "worker_artifacts" {
  bucket = google_storage_bucket.application_artifacts.name
  role   = "roles/storage.objectUser"
  member = "serviceAccount:${google_service_account.employer_worker.email}"
}

output "queues" { value = { for key, queue in google_cloud_tasks_queue.employer : key => queue.name } }
output "artifact_bucket" { value = google_storage_bucket.application_artifacts.name }
output "employer_worker_service_account" { value = google_service_account.employer_worker.email }
