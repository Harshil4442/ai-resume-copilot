from datetime import timedelta
from unittest.mock import MagicMock

import pytest
from backend.app.domains.common import utcnow
from backend.app.domains.resume_uploads import wake
from google.api_core.exceptions import AlreadyExists, DeadlineExceeded, NotFound
from google.cloud import tasks_v2

UPLOAD_ID = "rup_" + "2" * 32
ORIGIN = "https://hirewiz-resume-coordinator-example.us-central1.run.app"
CALLER = "hirewiz-resume-wake@synthetic-project.iam.gserviceaccount.com"
PARENT = "projects/synthetic-project/locations/us-central1/queues/hirewiz-resume-wake"


@pytest.fixture
def cloud_tasks(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "synthetic-project")
    monkeypatch.setenv("RESUME_UPLOAD_TASK_LOCATION", "us-central1")
    monkeypatch.setenv("RESUME_UPLOAD_TASK_QUEUE", "hirewiz-resume-wake")
    monkeypatch.setenv("RESUME_UPLOAD_COORDINATOR_AUDIENCE", ORIGIN)
    constructor = MagicMock()
    monkeypatch.setattr(tasks_v2, "CloudTasksClient", constructor)
    return constructor, constructor.return_value.__enter__.return_value


def acknowledge(*, request, retry, timeout):
    assert request.parent == PARENT
    assert request.response_view == tasks_v2.Task.View.BASIC
    assert retry is None and timeout == 2
    return tasks_v2.Task(name=request.task.name)


def test_create_confirms_exact_dedicated_bodyless_request(cloud_tasks, monkeypatch):
    now = utcnow()
    monkeypatch.setattr(wake, "utcnow", lambda: now)
    _, client = cloud_tasks
    client.create_task.side_effect = acknowledge
    wake.confirm(UPLOAD_ID, now + timedelta(seconds=900))
    task = client.create_task.call_args.kwargs["request"].task
    assert task.name == f"{PARENT}/tasks/resume-upload-{UPLOAD_ID}"
    assert task.schedule_time == now + timedelta(seconds=30)
    assert task.dispatch_deadline == timedelta(seconds=270)
    assert task.http_request.url == f"{ORIGIN}/internal/resume-uploads/wake/{UPLOAD_ID}"
    assert task.http_request.http_method == tasks_v2.HttpMethod.POST
    assert not task.http_request.oidc_token and not task.http_request.oauth_token
    assert not task.http_request.body and not task.http_request.headers
    client.get_task.assert_not_called()


@pytest.mark.parametrize("change", [None, "target", "method", "oidc", "oauth", "body", "auth", "expiry", "deadline", "name"])
def test_collision_confirms_only_matching_active_full_task(cloud_tasks, change):
    _, client = cloud_tasks
    expires_at = utcnow() + timedelta(seconds=900)
    client.create_task.side_effect = AlreadyExists("synthetic-collision")

    def get(*, request, retry, timeout):
        expected = client.create_task.call_args.kwargs["request"].task
        assert request.response_view == tasks_v2.Task.View.FULL and request.name == expected.name
        assert retry is None and timeout == 2
        existing = tasks_v2.Task(expected)
        existing.http_request.headers["User-Agent"] = "Google-Cloud-Tasks"
        match change:
            case "target":
                existing.http_request.url += "?wrong=1"
            case "method":
                existing.http_request.http_method = tasks_v2.HttpMethod.GET
            case "oidc":
                existing.http_request.oidc_token = tasks_v2.OidcToken(service_account_email=CALLER, audience=ORIGIN)
            case "oauth":
                existing.http_request.oauth_token = tasks_v2.OAuthToken(service_account_email=CALLER)
            case "body":
                existing.http_request.body = b"non-empty"
            case "auth":
                existing.http_request.headers["Authorization"] = "synthetic"
            case "expiry":
                existing.schedule_time = expires_at
            case "deadline":
                existing.dispatch_deadline = timedelta(seconds=269)
            case "name":
                existing.name += "-wrong"
        return existing

    client.get_task.side_effect = get
    if change is None:
        wake.confirm(UPLOAD_ID, expires_at)
    else:
        with pytest.raises(wake.WakeUnavailable):
            wake.confirm(UPLOAD_ID, expires_at)
    assert client.create_task.call_count == client.get_task.call_count == 1


def test_explicit_tombstone_does_not_authorize_or_create_another_name(cloud_tasks):
    _, client = cloud_tasks
    client.create_task.side_effect = AlreadyExists("synthetic-collision")
    client.get_task.side_effect = NotFound("synthetic-tombstone")
    expected = f"{PARENT}/tasks/resume-upload-{UPLOAD_ID}"
    for _ in range(2):
        expires_at = utcnow() + timedelta(seconds=900)
        with pytest.raises(wake.WakeUnavailable):
            wake.confirm(UPLOAD_ID, expires_at)
    assert [call.kwargs["request"].task.name for call in client.create_task.call_args_list] == [expected] * 2
    assert client.get_task.call_count == 2


def test_uncertain_rpc_or_unbound_ack_never_creates_recovery_task(cloud_tasks):
    _, client = cloud_tasks
    client.create_task.side_effect = DeadlineExceeded("synthetic-create-timeout")
    expires_at = utcnow() + timedelta(seconds=900)
    with pytest.raises(wake.WakeUnavailable):
        wake.confirm(UPLOAD_ID, expires_at)
    assert client.create_task.call_count == 1
    client.get_task.assert_not_called()

    client.create_task.side_effect = AlreadyExists("synthetic-collision")
    client.get_task.side_effect = DeadlineExceeded("synthetic-read-timeout")
    with pytest.raises(wake.WakeUnavailable):
        wake.confirm(UPLOAD_ID, expires_at)
    assert client.create_task.call_count == 2 and client.get_task.call_count == 1

    client.create_task.side_effect = None
    client.create_task.return_value = tasks_v2.Task(name="wrong-acknowledgement")
    with pytest.raises(wake.WakeUnavailable):
        wake.confirm(UPLOAD_ID, expires_at)
    assert client.create_task.call_count == 3 and client.get_task.call_count == 1


@pytest.mark.parametrize("setting, value", [
    ("RESUME_UPLOAD_TASK_QUEUE", "hirewiz-analysis"),
    ("RESUME_UPLOAD_COORDINATOR_AUDIENCE", "https://hirewiz-analysis-worker-example.run.app"),
    ("RESUME_UPLOAD_COORDINATOR_AUDIENCE", ORIGIN + "/path"),
    ("RESUME_UPLOAD_COORDINATOR_AUDIENCE", ORIGIN + "?parameter=unexpected"),
])
def test_misbound_configuration_refuses_before_rpc(cloud_tasks, monkeypatch, setting, value):
    constructor, _ = cloud_tasks
    monkeypatch.setenv(setting, value)
    with pytest.raises(wake.WakeUnavailable):
        wake.confirm(UPLOAD_ID, utcnow() + timedelta(seconds=900))
    constructor.assert_not_called()


def test_insufficient_admission_lifetime_does_not_create_task(cloud_tasks):
    _, client = cloud_tasks
    with pytest.raises(wake.WakeUnavailable):
        wake.confirm(UPLOAD_ID, utcnow() + timedelta(seconds=5))
    client.create_task.assert_not_called()
