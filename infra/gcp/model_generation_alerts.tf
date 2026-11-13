# Opt-in production telemetry. Review volume and the existing destination
# before applying these resources; activating them does not send a test alert.
variable "model_generation_alerts_enabled" {
  type    = bool
  default = false
}

locals {
  hirewiz_model_latency_metric = "hirewiz_model_generation_latency_ms"
  hirewiz_model_latency_filter = "resource.type=\"cloud_run_revision\" AND metric.type=\"logging.googleapis.com/user/${local.hirewiz_model_latency_metric}\""
}

resource "google_logging_metric" "model_generation_latency" {
  count           = var.model_generation_alerts_enabled ? 1 : 0
  project         = "ai-resume-parser-482412"
  name            = local.hirewiz_model_latency_metric
  description     = "Completed model generation attempts, excluding token preflight. Values above five minutes are capped."
  filter          = <<-FILTER
    resource.type="cloud_run_revision"
    resource.labels.location="us-central1"
    resource.labels.service_name=("ai-resume-parser" OR "hirewiz-analysis-worker" OR "hirewiz-resume-coordinator")
    jsonPayload.event="hirewiz_model_generation_latency_v1"
  FILTER
  value_extractor = "EXTRACT(jsonPayload.latency_ms)"

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "DISTRIBUTION"
    unit        = "ms"
  }

  bucket_options {
    explicit_buckets {
      bounds = [100, 250, 500, 1000, 2000, 4000, 8000, 12000, 20000, 30000, 45000, 60000, 90000, 120000, 180000, 300001]
    }
  }
}

resource "google_monitoring_alert_policy" "model_generation_latency" {
  count                 = var.model_generation_alerts_enabled ? 1 : 0
  project               = "ai-resume-parser-482412"
  display_name          = "HireWiz model generation sustained high latency"
  combiner              = "OR"
  notification_channels = var.document_refresh_notification_channels
  depends_on            = [google_logging_metric.model_generation_latency]

  conditions {
    display_name = "Model generation attempt p95 exceeds 30 seconds for ten minutes"
    condition_threshold {
      filter                  = local.hirewiz_model_latency_filter
      comparison              = "COMPARISON_GT"
      threshold_value         = 30000
      duration                = "600s"
      evaluation_missing_data = "EVALUATION_MISSING_DATA_INACTIVE"

      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_PERCENTILE_95"
      }
    }
  }

  lifecycle {
    precondition {
      condition     = length(var.document_refresh_notification_channels) > 0
      error_message = "An enabled generation latency alert requires a reviewed notification destination."
    }
  }
}
