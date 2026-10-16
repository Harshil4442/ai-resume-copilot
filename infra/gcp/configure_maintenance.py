#!/usr/bin/env python3
"""Configure private recovery without writing the task token to argv, files or state."""
from __future__ import annotations

import argparse
import json
import subprocess
import urllib.error
import urllib.request
from urllib.parse import urlsplit


def command(*arguments: str) -> str:
    result = subprocess.run(["gcloud", *arguments], capture_output=True, text=True, check=False)
    if result.returncode:
        raise SystemExit("Required authenticated gcloud operation failed; no credentials were printed.")
    return result.stdout.strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Create/update the scheduled private recovery job")
    args = parser.parse_args()
    project, region = "ai-resume-parser-482412", "us-central1"
    worker = "hirewiz-analysis-worker"
    url = command("run", "services", "describe", worker, "--project", project,
                  "--region", region, "--format=value(status.url)")
    destination = urlsplit(url)
    if (destination.scheme != "https" or not destination.hostname or
            not destination.hostname.endswith(".run.app") or destination.path not in {"", "/"} or
            destination.query or destination.fragment or destination.username):
        raise SystemExit("Cloud Run returned an unexpected recovery destination")
    job_name = f"projects/{project}/locations/{region}/jobs/hirewiz-maintenance"
    job = {
        "name": job_name,
        "description": "Bounded outbox recovery, source refresh and private artifact cleanup",
        "schedule": "*/5 * * * *", "timeZone": "Etc/UTC", "attemptDeadline": "300s",
        "retryConfig": {"retryCount": 2, "minBackoffDuration": "30s", "maxBackoffDuration": "120s"},
        "httpTarget": {
            "uri": url.rstrip("/") + "/internal/tasks/maintenance", "httpMethod": "POST",
            "headers": {"Content-Type": "application/json", "X-HireWiz-Task-Token": "[Secret Manager]"},
            "oidcToken": {"serviceAccountEmail": f"hirewiz-scheduler@{project}.iam.gserviceaccount.com", "audience": url},
        },
    }
    if not args.apply:
        print(json.dumps(job, indent=2))
        return
    # The Cloud Run IAM invoker boundary remains mandatory. Scheduler job headers
    # contain the defence-in-depth token; restrict Scheduler read/edit access.
    job["httpTarget"]["headers"]["X-HireWiz-Task-Token"] = command(
        "secrets", "versions", "access", "latest", "--secret=hirewiz-analysis-task-token", "--project", project)
    access_token = command("auth", "print-access-token")
    api = "https://cloudscheduler.googleapis.com/v1/"

    def request(method: str, endpoint: str, payload=None):
        body = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(api + endpoint, data=body, method=method,
                                     headers={"Authorization": "Bearer " + access_token, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.load(response)

    try:
        request("GET", job_name)
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise SystemExit(f"Scheduler lookup failed with HTTP {error.code}; response body suppressed.") from None
        method, endpoint = "POST", f"projects/{project}/locations/{region}/jobs"
    else:
        method, endpoint = "PATCH", job_name + "?updateMask=description,schedule,timeZone,attemptDeadline,retryConfig,httpTarget"
    try:
        configured = request(method, endpoint, job)
    except urllib.error.HTTPError as error:
        raise SystemExit(f"Scheduler configuration failed with HTTP {error.code}; response body suppressed.") from None
    print(json.dumps({"name": configured["name"], "state": configured["state"], "schedule": configured["schedule"]}))


if __name__ == "__main__":
    main()
