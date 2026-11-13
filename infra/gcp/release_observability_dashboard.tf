variable "release_observability_dashboard_enabled" {
  type    = bool
  default = false
}

resource "google_monitoring_dashboard" "release_observability" {
  count   = var.release_observability_dashboard_enabled ? 1 : 0
  project = "ai-resume-parser-482412"

  dashboard_json = jsonencode({
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
          title = "Model generation p95 latency in milliseconds"
          xyChart = {
            dataSets = [{
              plotType = "LINE"
              timeSeriesQuery = {
                timeSeriesFilter = {
                  filter = "resource.type=\"cloud_run_revision\" AND metric.type=\"logging.googleapis.com/user/${google_logging_metric.model_generation_latency[0].name}\""
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
  })

  lifecycle {
    precondition {
      condition     = var.model_generation_alerts_enabled
      error_message = "Enable the model latency metric before creating the release dashboard."
    }
  }
}
