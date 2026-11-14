variable "release_observability_dashboard_enabled" {
  type    = bool
  default = false
}

locals {
  hirewiz_release_observability_dashboard = {
    displayName = "HireWiz production operations"
    gridLayout = {
      columns = "2"
      widgets = [
        {
          title = "Cloud Tasks queue depth"
          xyChart = {
            dataSets = [for queue, filter in local.hirewiz_queue_depth_filters : {
              legendTemplate = queue
              plotType       = "LINE"
              timeSeriesQuery = {
                timeSeriesFilter = {
                  filter = filter
                  aggregation = {
                    alignmentPeriod  = "60s"
                    perSeriesAligner = "ALIGN_MAX"
                  }
                }
              }
            }]
          }
        },
        {
          title = "Worker HTTP 5xx per five minutes"
          xyChart = {
            dataSets = [for worker, filter in local.hirewiz_worker_error_filters : {
              legendTemplate = worker
              plotType       = "LINE"
              timeSeriesQuery = {
                timeSeriesFilter = {
                  filter = filter
                  aggregation = {
                    alignmentPeriod    = "300s"
                    perSeriesAligner   = "ALIGN_SUM"
                    crossSeriesReducer = "REDUCE_SUM"
                  }
                }
              }
            }]
          }
        },
        {
          title = "Model generation attempt p95 by revision (ms)"
          xyChart = {
            dataSets = [{
              plotType = "LINE"
              timeSeriesQuery = {
                timeSeriesFilter = {
                  filter = local.hirewiz_model_latency_filter
                  aggregation = {
                    alignmentPeriod  = "300s"
                    perSeriesAligner = "ALIGN_PERCENTILE_95"
                  }
                }
              }
            }]
          }
        },
        {
          title = "Razorpay webhook HTTP 5xx responses"
          logsPanel = {
            filter = local.razorpay_webhook_failure_filter
          }
        },
      ]
    }
  }
}

resource "google_monitoring_dashboard" "release_observability" {
  count      = var.release_observability_dashboard_enabled ? 1 : 0
  project    = "ai-resume-parser-482412"
  depends_on = [google_logging_metric.model_generation_latency]

  dashboard_json = jsonencode(local.hirewiz_release_observability_dashboard)

  lifecycle {
    precondition {
      condition     = var.model_generation_alerts_enabled
      error_message = "Enable the model latency metric before creating the release dashboard."
    }
  }
}
