# Opt-in monitoring definitions. Import the recorded native resource names before
# managing an existing deployment with Terraform; never apply the full stack to
# activate these policies alone.
variable "document_refresh_alerts_enabled" {
  type    = bool
  default = false
}
variable "document_refresh_notification_channels" {
  type    = list(string)
  default = []
  validation {
    condition     = alltrue([for name in var.document_refresh_notification_channels : can(regex("^projects/ai-resume-parser-482412/notificationChannels/[0-9]+$", name))])
    error_message = "Use reviewed notification channels in the HireWiz project."
  }
}
variable "api_alerts_enabled" {
  type    = bool
  default = false
}
resource "google_logging_metric" "document_refresh_expiry" {
  count   = var.document_refresh_alerts_enabled ? 1 : 0
  project = "ai-resume-parser-482412"
  name    = "hirewiz_document_definition_attention"
  filter  = "logName=\"projects/ai-resume-parser-482412/logs/hirewiz-document-refresh\" AND jsonPayload.event=\"hirewiz_document_definition_maintenance_v1\" AND jsonPayload.status=(\"refresh_required\" OR \"expired\" OR \"unavailable\")"
  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
  }
}
resource "google_logging_metric" "document_refresh_heartbeat" {
  count   = var.document_refresh_alerts_enabled ? 1 : 0
  project = "ai-resume-parser-482412"
  name    = "hirewiz_document_definition_heartbeat"
  filter  = "logName=\"projects/ai-resume-parser-482412/logs/hirewiz-document-refresh\" AND jsonPayload.event=\"hirewiz_document_definition_maintenance_v1\""
  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
  }
}
resource "google_monitoring_alert_policy" "document_refresh_expiry" {
  count                 = var.document_refresh_alerts_enabled ? 1 : 0
  project               = "ai-resume-parser-482412"
  display_name          = "HireWiz document definitions require attention (24h before expiry)"
  combiner              = "OR"
  notification_channels = var.document_refresh_notification_channels
  conditions {
    display_name = "Definition refresh refused, unavailable or nearing unchanged 3-day limit"
    condition_threshold {
      filter          = "resource.type=\"global\" AND metric.type=\"logging.googleapis.com/user/${google_logging_metric.document_refresh_expiry[0].name}\""
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      duration        = "0s"
      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_SUM"
      }
    }
  }
  lifecycle {
    precondition {
      condition     = length(var.document_refresh_notification_channels) > 0
      error_message = "An enabled maintenance alert requires a reviewed notification destination."
    }
  }
}
resource "google_monitoring_alert_policy" "document_refresh_silent" {
  count                 = var.document_refresh_alerts_enabled ? 1 : 0
  project               = "ai-resume-parser-482412"
  display_name          = "HireWiz document definition monitor missing for two hours"
  combiner              = "OR"
  notification_channels = var.document_refresh_notification_channels
  conditions {
    display_name = "No hourly controller heartbeat"
    condition_absent {
      filter   = "resource.type=\"global\" AND metric.type=\"logging.googleapis.com/user/${google_logging_metric.document_refresh_heartbeat[0].name}\""
      duration = "7200s"
      aggregations {
        alignment_period   = "3600s"
        per_series_aligner = "ALIGN_SUM"
      }
    }
  }
  lifecycle {
    precondition {
      condition     = length(var.document_refresh_notification_channels) > 0
      error_message = "An enabled maintenance alert requires a reviewed notification destination."
    }
  }
}
resource "google_monitoring_alert_policy" "api_errors" {
  count                 = var.api_alerts_enabled ? 1 : 0
  project               = "ai-resume-parser-482412"
  display_name          = "HireWiz API repeated server errors"
  combiner              = "OR"
  notification_channels = var.document_refresh_notification_channels
  conditions {
    display_name = "At least three API 5xx responses in five minutes"
    condition_threshold {
      filter          = "resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"ai-resume-parser\" AND resource.labels.location=\"us-central1\" AND metric.type=\"run.googleapis.com/request_count\" AND metric.labels.response_code_class=\"5xx\""
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
      error_message = "An enabled API alert requires a reviewed notification destination."
    }
  }
}
resource "google_monitoring_alert_policy" "api_latency" {
  count                 = var.api_alerts_enabled ? 1 : 0
  project               = "ai-resume-parser-482412"
  display_name          = "HireWiz API sustained high latency"
  combiner              = "OR"
  notification_channels = var.document_refresh_notification_channels
  conditions {
    display_name = "Worst API series p95 exceeds five seconds for ten minutes"
    condition_threshold {
      filter          = "resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"ai-resume-parser\" AND resource.labels.location=\"us-central1\" AND metric.type=\"run.googleapis.com/request_latencies\""
      comparison      = "COMPARISON_GT"
      threshold_value = 5000
      duration        = "600s"
      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_PERCENTILE_95"
        cross_series_reducer = "REDUCE_MAX"
        group_by_fields      = ["resource.labels.service_name"]
      }
    }
  }
  lifecycle {
    precondition {
      condition     = length(var.document_refresh_notification_channels) > 0
      error_message = "An enabled API alert requires a reviewed notification destination."
    }
  }
}
