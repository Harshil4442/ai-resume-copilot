# Source-only, opt-in integration. Root provisions notification destinations and
# the dedicated workflow WIF identity after the coordinator baseline is complete.
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
