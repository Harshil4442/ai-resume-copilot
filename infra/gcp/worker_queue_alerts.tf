variable "worker_queue_alerts_enabled" {
  description = "Manage the reviewed production Cloud Tasks backlog and Cloud Run worker error policies."
  type        = bool
  default     = false
}

resource "google_monitoring_alert_policy" "queue_backlog" {
  for_each = var.worker_queue_alerts_enabled ? toset([
    "hirewiz-analysis",
    "hirewiz-employer-application",
    "hirewiz-employer-ingestion",
    "hirewiz-employer-search",
  ]) : toset([])

  project               = "ai-resume-parser-482412"
  display_name          = "HireWiz queue backlog: ${each.key}"
  combiner              = "OR"
  notification_channels = var.document_refresh_notification_channels

  conditions {
    display_name = "Queue depth exceeds 20 for ten minutes"
    condition_threshold {
      filter = join(" AND ", [
        "resource.type=\"cloud_tasks_queue\"",
        "resource.labels.project_id=\"ai-resume-parser-482412\"",
        "resource.labels.location=\"us-central1\"",
        "resource.labels.queue_id=${jsonencode(each.key)}",
        "metric.type=\"cloudtasks.googleapis.com/queue/depth\"",
      ])
      comparison      = "COMPARISON_GT"
      threshold_value = 20
      duration        = "600s"
      aggregations {
        alignment_period   = "60s"
        per_series_aligner = "ALIGN_MAX"
      }
    }
  }

  lifecycle {
    precondition {
      condition     = length(var.document_refresh_notification_channels) > 0
      error_message = "An enabled queue backlog alert requires a reviewed notification destination."
    }
  }
}

resource "google_monitoring_alert_policy" "worker_errors" {
  for_each = var.worker_queue_alerts_enabled ? toset([
    "hirewiz-analysis-worker",
    "hirewiz-employer-worker",
  ]) : toset([])

  project               = "ai-resume-parser-482412"
  display_name          = "HireWiz worker errors: ${each.key}"
  combiner              = "OR"
  notification_channels = var.document_refresh_notification_channels

  conditions {
    display_name = "At least three worker HTTP 5xx responses in five minutes"
    condition_threshold {
      filter = join(" AND ", [
        "resource.type=\"cloud_run_revision\"",
        "resource.labels.project_id=\"ai-resume-parser-482412\"",
        "resource.labels.location=\"us-central1\"",
        "resource.labels.service_name=${jsonencode(each.key)}",
        "metric.type=\"run.googleapis.com/request_count\"",
        "metric.labels.response_code_class=\"5xx\"",
      ])
      comparison      = "COMPARISON_GT"
      threshold_value = 2
      duration        = "0s"
      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_SUM"
        cross_series_reducer = "REDUCE_SUM"
        group_by_fields      = ["resource.labels.service_name"]
      }
    }
  }

  lifecycle {
    precondition {
      condition     = length(var.document_refresh_notification_channels) > 0
      error_message = "An enabled worker error alert requires a reviewed notification destination."
    }
  }
}
