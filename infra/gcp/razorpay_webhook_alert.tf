variable "razorpay_webhook_alert_enabled" {
  type    = bool
  default = false
}

locals {
  razorpay_webhook_failure_filter = <<-EOT
    resource.type="cloud_run_revision"
    resource.labels.service_name="ai-resume-parser"
    resource.labels.location="us-central1"
    log_id("run.googleapis.com/requests")
    httpRequest.requestMethod="POST"
    httpRequest.requestUrl=~"^https?://[^/?#]+/api/billing/webhooks/razorpay([?].*)?$"
    httpRequest.status>=500
    httpRequest.status<600
  EOT
}

resource "google_monitoring_alert_policy" "razorpay_webhook_errors" {
  count                 = var.razorpay_webhook_alert_enabled ? 1 : 0
  project               = "ai-resume-parser-482412"
  display_name          = "HireWiz Razorpay webhook server errors"
  combiner              = "OR"
  notification_channels = var.document_refresh_notification_channels

  conditions {
    display_name = "Razorpay callback returned HTTP 5xx"
    condition_matched_log {
      filter = local.razorpay_webhook_failure_filter
    }
  }

  alert_strategy {
    auto_close = "86400s"
    notification_rate_limit {
      period = "3600s"
    }
  }

  lifecycle {
    precondition {
      condition     = length(var.document_refresh_notification_channels) > 0
      error_message = "An enabled webhook alert requires a reviewed notification destination."
    }
  }
}
