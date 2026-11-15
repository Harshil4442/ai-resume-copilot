mock_provider "google" {
  mock_data "google_project" {
    defaults = { number = "123456789012" }
  }
  mock_resource "google_service_account" {
    defaults = {
      email = "hirewiz-resume-wake@synthetic-wake.iam.gserviceaccount.com"
      name  = "projects/synthetic-wake/serviceAccounts/hirewiz-resume-wake@synthetic-wake.iam.gserviceaccount.com"
    }
  }
  mock_resource "google_project_iam_custom_role" {
    defaults = { name = "projects/synthetic-wake/roles/hirewizResumeUploadWakePublisher" }
  }
}

variables {
  project_id             = "synthetic-wake"
  region                 = "us-central1"
  environment            = "test"
  artifact_bucket        = "synthetic-wake-artifacts"
  api_service_account    = "synthetic-publisher@synthetic-wake.iam.gserviceaccount.com"
  worker_service_account = "synthetic-worker@synthetic-wake.iam.gserviceaccount.com"
  tasks_service_account  = "synthetic-tasks@synthetic-wake.iam.gserviceaccount.com"
  database_secret        = "synthetic-db"
  task_token_secret      = "synthetic-token"
}

run "default_creates_no_wake_resources" {
  command = plan

  assert {
    condition = alltrue([
      length(data.google_project.resume_upload_wake) == 0,
      length(google_service_account.resume_upload_wake) == 0,
      length(google_cloud_tasks_queue.resume_upload_wake) == 0,
      length(google_project_iam_custom_role.resume_upload_wake_publisher) == 0,
      length(google_cloud_tasks_queue_iam_member.resume_upload_wake_publisher) == 0,
      length(google_service_account_iam_member.resume_upload_wake_cloud_tasks) == 0,
      length(google_cloud_run_v2_service_iam_member.resume_upload_wake_invoker) == 0,
    ])
    error_message = "A default plan must not look up the wake project or create wake infrastructure or IAM grants."
  }
}

run "enabled_pins_transport_and_least_privilege" {
  # These checks resolve computed service account and role names in mock state.
  command = apply

  variables {
    resume_upload_wake_resources_enabled  = true
    resume_upload_wake_coordinator_origin = "https://hirewiz-resume-coordinator-synthetic.us-central1.run.app"
  }

  assert {
    condition = alltrue([
      google_cloud_tasks_queue.resume_upload_wake[0].name == "hirewiz-resume-wake",
      google_cloud_tasks_queue.resume_upload_wake[0].project == var.project_id,
      google_cloud_tasks_queue.resume_upload_wake[0].location == var.region,
      one(google_cloud_tasks_queue.resume_upload_wake[0].rate_limits).max_concurrent_dispatches == 1,
      one(google_cloud_tasks_queue.resume_upload_wake[0].rate_limits).max_dispatches_per_second == 1,
      one(google_cloud_tasks_queue.resume_upload_wake[0].retry_config).max_attempts == 24,
      one(google_cloud_tasks_queue.resume_upload_wake[0].retry_config).max_retry_duration == "0s",
      one(google_cloud_tasks_queue.resume_upload_wake[0].retry_config).min_backoff == "30s",
      one(google_cloud_tasks_queue.resume_upload_wake[0].retry_config).max_backoff == "60s",
      one(google_cloud_tasks_queue.resume_upload_wake[0].retry_config).max_doublings == 1,
    ])
    error_message = "Only the dedicated queue may dispatch wakes, with one dispatch at a time and the reviewed retry policy."
  }

  assert {
    condition = alltrue([
      one(google_cloud_tasks_queue.resume_upload_wake[0].http_target).http_method == "POST",
      length(one(google_cloud_tasks_queue.resume_upload_wake[0].http_target).oauth_token) == 0,
      one(one(google_cloud_tasks_queue.resume_upload_wake[0].http_target).oidc_token).audience == var.resume_upload_wake_coordinator_origin,
      one(one(google_cloud_tasks_queue.resume_upload_wake[0].http_target).oidc_token).service_account_email == google_service_account.resume_upload_wake[0].email,
      one(one(google_cloud_tasks_queue.resume_upload_wake[0].http_target).uri_override).uri_override_enforce_mode == "ALWAYS",
      one(one(google_cloud_tasks_queue.resume_upload_wake[0].http_target).uri_override).scheme == "HTTPS",
      one(one(google_cloud_tasks_queue.resume_upload_wake[0].http_target).uri_override).host == trimprefix(var.resume_upload_wake_coordinator_origin, "https://"),
      one(one(google_cloud_tasks_queue.resume_upload_wake[0].http_target).uri_override).port == "443",
      length(one(one(google_cloud_tasks_queue.resume_upload_wake[0].http_target).uri_override).path_override) == 0,
      # The mock accepts the provider's empty/null handling. It cannot prove
      # whether a task query is stripped in transit. The receiver rejects queries.
      contains([null, ""], one(one(one(google_cloud_tasks_queue.resume_upload_wake[0].http_target).uri_override).query_override).query_params),
    ])
    error_message = "The queue must own OIDC and always force HTTPS POST to the verified coordinator host while preserving the task path and adding no query value."
  }

  assert {
    condition = alltrue([
      google_service_account.resume_upload_wake[0].account_id == "hirewiz-resume-wake",
      toset(google_project_iam_custom_role.resume_upload_wake_publisher[0].permissions) == toset([
        "cloudtasks.tasks.create", "cloudtasks.tasks.get", "cloudtasks.tasks.fullView",
      ]),
      google_cloud_tasks_queue_iam_member.resume_upload_wake_publisher[0].name == "hirewiz-resume-wake",
      google_cloud_tasks_queue_iam_member.resume_upload_wake_publisher[0].member == "serviceAccount:${var.api_service_account}",
      google_cloud_tasks_queue_iam_member.resume_upload_wake_publisher[0].role == google_project_iam_custom_role.resume_upload_wake_publisher[0].name,
      google_service_account_iam_member.resume_upload_wake_cloud_tasks[0].service_account_id == google_service_account.resume_upload_wake[0].name,
      google_service_account_iam_member.resume_upload_wake_cloud_tasks[0].role == "roles/iam.serviceAccountUser",
      google_service_account_iam_member.resume_upload_wake_cloud_tasks[0].member == "serviceAccount:service-123456789012@gcp-sa-cloudtasks.iam.gserviceaccount.com",
      google_service_account_iam_member.resume_upload_wake_cloud_tasks[0].member != "serviceAccount:${var.api_service_account}",
      google_cloud_run_v2_service_iam_member.resume_upload_wake_invoker[0].name == "hirewiz-resume-coordinator",
      google_cloud_run_v2_service_iam_member.resume_upload_wake_invoker[0].role == "roles/run.invoker",
      google_cloud_run_v2_service_iam_member.resume_upload_wake_invoker[0].member == "serviceAccount:${google_service_account.resume_upload_wake[0].email}",
    ])
    error_message = "The publisher may create/read exact tasks only on the dedicated queue. Only the Cloud Tasks service agent may act as the dedicated coordinator invoker."
  }
}

run "enabled_requires_verified_coordinator_origin" {
  command = plan

  variables {
    resume_upload_wake_resources_enabled = true
  }

  expect_failures = [google_cloud_tasks_queue.resume_upload_wake]
}

run "coordinator_origin_rejects_noncanonical_urls" {
  command = plan

  variables {
    resume_upload_wake_resources_enabled  = true
    resume_upload_wake_coordinator_origin = "https://hirewiz-resume-coordinator-synthetic.run.app/internal/resume-uploads/wake"
  }

  expect_failures = [var.resume_upload_wake_coordinator_origin]
}
