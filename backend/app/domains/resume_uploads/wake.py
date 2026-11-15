"""Confirm a dedicated wake before issuing a direct upload grant."""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta

from google.api_core.exceptions import AlreadyExists, GoogleAPICallError
from google.auth.exceptions import GoogleAuthError
from google.cloud import tasks_v2

from ..common import utcnow

QUEUE_NAME = "hirewiz-resume-wake"
WAKE_DELAY_SECONDS = 30
DISPATCH_DEADLINE_SECONDS = 270
RPC_TIMEOUT_SECONDS = 2.0


class WakeUnavailable(RuntimeError):
    pass


def enabled() -> bool:
    return os.getenv("RESUME_UPLOAD_TASK_WAKE_ENABLED", "false") == "true"


def _target() -> tuple[str, str]:
    project = os.getenv("GOOGLE_CLOUD_PROJECT", "")
    location = os.getenv("RESUME_UPLOAD_TASK_LOCATION", "")
    queue = os.getenv("RESUME_UPLOAD_TASK_QUEUE", "")
    origin = os.getenv("RESUME_UPLOAD_COORDINATOR_AUDIENCE", "")
    if (
        not re.fullmatch(r"[a-z][a-z0-9-]{4,28}[a-z0-9]", project)
        or not re.fullmatch(r"[a-z]+-[a-z]+[1-9][0-9]*", location)
        or queue != QUEUE_NAME
        or not re.fullmatch(r"https://hirewiz-resume-coordinator-[a-z0-9-]+(?:\.[a-z]+-[a-z]+[1-9][0-9]*)?\.run\.app", origin)
    ):
        raise WakeUnavailable
    return f"projects/{project}/locations/{location}/queues/{queue}", origin


def _live_match(existing: tasks_v2.Task, expected: tasks_v2.Task, expires_at: datetime) -> bool:
    request = existing.http_request
    target = expected.http_request
    return bool(
        existing.name == expected.name
        and existing.schedule_time
        and existing.schedule_time < expires_at
        and existing.dispatch_deadline == expected.dispatch_deadline
        and request.http_method == target.http_method
        and request.url == target.url
        and not request.body
        and {key.lower() for key in request.headers}.issubset({"user-agent"})
        and not request.oidc_token
        and not request.oauth_token
    )


def confirm(upload_id: str, expires_at: datetime) -> None:
    if not re.fullmatch(r"rup_[a-f0-9]{32}", upload_id):
        raise WakeUnavailable
    parent, origin = _target()
    scheduled = utcnow() + timedelta(seconds=WAKE_DELAY_SECONDS)
    if scheduled >= expires_at:
        raise WakeUnavailable
    # The dedicated queue adds its OIDC identity at dispatch time.
    task = tasks_v2.Task(
        name=f"{parent}/tasks/resume-upload-{upload_id}",
        http_request=tasks_v2.HttpRequest(
            http_method=tasks_v2.HttpMethod.POST,
            url=f"{origin}/internal/resume-uploads/wake/{upload_id}",
        ),
        schedule_time=scheduled,
        dispatch_deadline=timedelta(seconds=DISPATCH_DEADLINE_SECONDS),
    )
    try:
        with tasks_v2.CloudTasksClient() as client:
            try:
                created = client.create_task(request=tasks_v2.CreateTaskRequest(
                    parent=parent, task=task, response_view=tasks_v2.Task.View.BASIC,
                ), retry=None, timeout=RPC_TIMEOUT_SECONDS)
            except AlreadyExists:
                # Recently deleted task names collide too. A live exact match is required.
                existing = client.get_task(request=tasks_v2.GetTaskRequest(
                    name=task.name, response_view=tasks_v2.Task.View.FULL,
                ), retry=None, timeout=RPC_TIMEOUT_SECONDS)
                if not _live_match(existing, task, expires_at):
                    raise WakeUnavailable from None
            else:
                if created.name != task.name:
                    raise WakeUnavailable
    except (GoogleAPICallError, GoogleAuthError):
        raise WakeUnavailable from None
