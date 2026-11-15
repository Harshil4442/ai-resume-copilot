mock_provider "google" {
  mock_data "google_project" {
    defaults = { number = "123456789012" }
  }
  mock_resource "google_service_account" {
    defaults = {
      email = "hirewiz-security-mail-task@synthetic-mail.iam.gserviceaccount.com"
      name  = "projects/synthetic-mail/serviceAccounts/hirewiz-security-mail-task@synthetic-mail.iam.gserviceaccount.com"
    }
  }
  mock_resource "google_project_iam_custom_role" {
    defaults = { name = "projects/synthetic-mail/roles/hirewizSecurityMailWakePublisher" }
  }
}

variables {
  project_id             = "synthetic-mail"
  region                 = "us-central1"
  environment            = "test"
  artifact_bucket        = "synthetic-mail-artifacts"
  api_service_account    = "synthetic-publisher@synthetic-mail.iam.gserviceaccount.com"
  worker_service_account = "synthetic-worker@synthetic-mail.iam.gserviceaccount.com"
  tasks_service_account  = "synthetic-tasks@synthetic-mail.iam.gserviceaccount.com"
  database_secret        = "synthetic-db"
  task_token_secret      = "synthetic-token"
}

run "default_leaves_security_mail_infrastructure_absent" {
  command = plan

  assert {
    condition = alltrue([
      length(data.google_project.security_mail_wake) == 0,
      length(google_service_account.security_mail_wake) == 0,
      length(google_cloud_tasks_queue.security_mail_wake) == 0,
      length(google_project_iam_custom_role.security_mail_wake_publisher) == 0,
      length(google_cloud_tasks_queue_iam_member.security_mail_wake_publisher) == 0,
      length(google_service_account_iam_member.security_mail_wake_cloud_tasks) == 0,
      length(google_cloud_run_v2_service_iam_member.security_mail_wake_invoker) == 0,
    ])
    error_message = "A default plan must create no security mail wake resources or grants."
  }
}

run "enabled_lane_is_private_and_bounded" {
  command = apply

  variables {
    security_mail_wake_resources_enabled = true
    security_mail_wake_worker_origin     = "https://hirewiz-security-mail-worker-synthetic.us-central1.run.app"
  }

  assert {
    condition = alltrue([
      google_cloud_tasks_queue.security_mail_wake[0].name == "hirewiz-security-mail-wake",
      google_cloud_tasks_queue.security_mail_wake[0].project == var.project_id,
      google_cloud_tasks_queue.security_mail_wake[0].location == var.region,
      one(google_cloud_tasks_queue.security_mail_wake[0].rate_limits).max_concurrent_dispatches == 1,
      one(google_cloud_tasks_queue.security_mail_wake[0].rate_limits).max_dispatches_per_second == 1,
      one(google_cloud_tasks_queue.security_mail_wake[0].retry_config).max_attempts == 8,
      one(google_cloud_tasks_queue.security_mail_wake[0].retry_config).max_retry_duration == "0s",
      one(google_cloud_tasks_queue.security_mail_wake[0].retry_config).min_backoff == "30s",
      one(google_cloud_tasks_queue.security_mail_wake[0].retry_config).max_backoff == "60s",
    ])
    error_message = "Security mail queue needs its reviewed same-project dispatch limits."
  }

  assert {
    condition = alltrue([
      one(google_cloud_tasks_queue.security_mail_wake[0].http_target).http_method == "POST",
      length(one(google_cloud_tasks_queue.security_mail_wake[0].http_target).oauth_token) == 0,
      one(one(google_cloud_tasks_queue.security_mail_wake[0].http_target).oidc_token).audience == var.security_mail_wake_worker_origin,
      one(one(google_cloud_tasks_queue.security_mail_wake[0].http_target).oidc_token).service_account_email == google_service_account.security_mail_wake[0].email,
      one(one(google_cloud_tasks_queue.security_mail_wake[0].http_target).uri_override).uri_override_enforce_mode == "ALWAYS",
      one(one(google_cloud_tasks_queue.security_mail_wake[0].http_target).uri_override).scheme == "HTTPS",
      one(one(google_cloud_tasks_queue.security_mail_wake[0].http_target).uri_override).host == trimprefix(var.security_mail_wake_worker_origin, "https://"),
      one(one(google_cloud_tasks_queue.security_mail_wake[0].http_target).uri_override).port == "443",
      length(one(one(google_cloud_tasks_queue.security_mail_wake[0].http_target).uri_override).path_override) == 0,
    ])
    error_message = "Pin POST, HTTPS origin and queue OIDC while preserving the exact opaque path."
  }

  assert {
    condition = alltrue([
      google_service_account.security_mail_wake[0].account_id == "hirewiz-security-mail-task",
      toset(google_project_iam_custom_role.security_mail_wake_publisher[0].permissions) == toset(["cloudtasks.tasks.create"]),
      google_cloud_tasks_queue_iam_member.security_mail_wake_publisher[0].name == "hirewiz-security-mail-wake",
      google_cloud_tasks_queue_iam_member.security_mail_wake_publisher[0].member == "serviceAccount:${var.api_service_account}",
      google_cloud_tasks_queue_iam_member.security_mail_wake_publisher[0].role == google_project_iam_custom_role.security_mail_wake_publisher[0].name,
      google_service_account_iam_member.security_mail_wake_cloud_tasks[0].service_account_id == google_service_account.security_mail_wake[0].name,
      google_service_account_iam_member.security_mail_wake_cloud_tasks[0].role == "roles/iam.serviceAccountUser",
      google_service_account_iam_member.security_mail_wake_cloud_tasks[0].member == "serviceAccount:service-123456789012@gcp-sa-cloudtasks.iam.gserviceaccount.com",
      google_service_account_iam_member.security_mail_wake_cloud_tasks[0].member != "serviceAccount:${var.api_service_account}",
      google_cloud_run_v2_service_iam_member.security_mail_wake_invoker[0].name == "hirewiz-security-mail-worker",
      google_cloud_run_v2_service_iam_member.security_mail_wake_invoker[0].role == "roles/run.invoker",
      google_cloud_run_v2_service_iam_member.security_mail_wake_invoker[0].member == "serviceAccount:${google_service_account.security_mail_wake[0].email}",
    ])
    error_message = "The API receives only task create, while OIDC and service invocation belong to separate identities."
  }
}

run "enabled_lane_requires_worker_origin" {
  command = plan

  variables {
    security_mail_wake_resources_enabled = true
  }

  expect_failures = [google_cloud_tasks_queue.security_mail_wake]
}

run "rejects_tagged_receiver" {
  command = plan

  variables {
    security_mail_wake_resources_enabled = true
    security_mail_wake_worker_origin     = "https://hirewiz-security-mail-worker---other-service.run.app"
  }

  expect_failures = [var.security_mail_wake_worker_origin]
}

run "rejects_collapsing_publisher_and_invoker" {
  command = plan

  variables {
    security_mail_wake_resources_enabled = true
    security_mail_wake_worker_origin     = "https://hirewiz-security-mail-worker-synthetic.run.app"
    api_service_account                  = "hirewiz-security-mail-task@synthetic-mail.iam.gserviceaccount.com"
  }

  expect_failures = [google_cloud_tasks_queue_iam_member.security_mail_wake_publisher]
}
