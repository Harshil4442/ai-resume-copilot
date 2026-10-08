#!/usr/bin/env bash
# Run only for the reviewed commit after CI and a protected database backup.
set -euo pipefail
project="${1:?project}"
region="${2:?region}"
image_tag="${3:?commit image tag}"
release="${4:?full commit}"
backup_object="${5:?protected pre-release PostgreSQL backup URI}"
[[ "$project" == ai-resume-parser-482412 && "$region" == us-central1 && "$release" =~ ^[a-f0-9]{40}$ ]]
[[ "$image_tag" == "${region}-docker.pkg.dev/${project}/cloud-run-source-deploy/hirewiz:${release}" ]]
digest="$(gcloud artifacts docker images describe "$image_tag" --project "$project" --format='value(image_summary.digest)')"
[[ "$digest" =~ ^sha256:[a-f0-9]{64}$ ]]
image="${image_tag%:*}@${digest}"
common=(--project "$project" --region "$region" --quiet)

[[ "$backup_object" == "gs://${project}-hirewiz-application-artifacts/releases/"*.dump ]]
backup_file="$(mktemp)"
trap 'rm -f "$backup_file"' EXIT
gcloud storage objects describe "$backup_object" --format=json > "$backup_file"
BACKUP_METADATA="$backup_file" python3 - <<'PY'
import json, os, re
from datetime import datetime, timezone, timedelta
with open(os.environ['BACKUP_METADATA']) as source:
    backup = json.load(source)
fields = backup.get('custom_fields', {})
created = datetime.fromisoformat(backup['creation_time'])
if (fields.get('format') != 'postgres-custom' or not re.fullmatch(r'[a-f0-9]{64}', fields.get('sha256', ''))
        or int(backup.get('size', 0)) < 100 or not backup.get('generation')
        or datetime.now(timezone.utc) - created > timedelta(hours=24)):
    raise SystemExit('Protected backup evidence is missing or stale')
print('Fresh protected PostgreSQL backup metadata verified.')
PY
rm -f "$backup_file"

# Legacy API images run Alembic unconditionally in their entrypoint. Prepare the
# exact serving image with a direct ASGI command before advancing the schema;
# otherwise a cold start of that image cannot recognize the new migration head.
service_status="$(gcloud run services describe ai-resume-parser "${common[@]}" --format='json(status.traffic,status.latestCreatedRevisionName)')"
serving_revision="$(printf '%s' "$service_status" | python3 -c '
import json, sys
status = json.load(sys.stdin)["status"]
traffic = status["traffic"]
active = [item for item in traffic if item.get("percent", 0)]
if len(active) != 1 or active[0].get("percent") != 100:
    raise SystemExit("Migration requires one known serving revision at 100 percent")
if status.get("latestCreatedRevisionName") != active[0]["revisionName"]:
    raise SystemExit("Resolve the staged revision before preparing migration compatibility")
print(active[0]["revisionName"])
')"
[[ "$serving_revision" =~ ^ai-resume-parser-[a-z0-9-]+$ ]]
serving_image="$(gcloud run revisions describe "$serving_revision" "${common[@]}" --format='value(status.imageDigest)')"
[[ "$serving_image" == "${region}-docker.pkg.dev/${project}/cloud-run-source-deploy/"*@sha256:* ]]
[[ "${serving_image##*@}" =~ ^sha256:[a-f0-9]{64}$ ]]
serving_port="$(gcloud run revisions describe "$serving_revision" "${common[@]}" --format='value(spec.containers[0].ports[0].containerPort)')"
[[ "$serving_port" =~ ^[0-9]+$ ]]
gcloud run deploy ai-resume-parser "${common[@]}" --image "$serving_image" \
  --no-traffic --tag migration-compatible --command python \
  --args="-m,uvicorn,app.main:app,--host,0.0.0.0,--port,${serving_port}" \
  --update-env-vars AUTO_DB_MIGRATE=false
compatibility_file="$(mktemp)"
trap 'rm -f "$compatibility_file" "$compatibility_file.verified"' EXIT
gcloud run services describe ai-resume-parser "${common[@]}" --format='json(status)' > "$compatibility_file"
COMPATIBILITY_FILE="$compatibility_file" python3 - <<'PY'
import json, os, urllib.request
with open(os.environ['COMPATIBILITY_FILE']) as source:
    status = json.load(source)['status']
tagged = next(item for item in status['traffic'] if item.get('tag') == 'migration-compatible')
if (status['latestReadyRevisionName'] != status['latestCreatedRevisionName']
        or tagged['revisionName'] != status['latestReadyRevisionName']):
    raise SystemExit('Migration-compatible revision is not ready')
with urllib.request.urlopen(tagged['url'] + '/api/health', timeout=30) as response:
    result = json.load(response)
if result.get('ok') is not True:
    raise SystemExit('Migration-compatible HTTP health failed')
with open(os.environ['COMPATIBILITY_FILE'] + '.verified', 'w') as verified:
    verified.write(tagged['revisionName'])
print('Exact serving image is healthy without startup schema migration.')
PY
compatible_revision="$(cat "$compatibility_file.verified")"
[[ "$compatible_revision" =~ ^ai-resume-parser-[a-z0-9-]+$ ]]
gcloud run services update-traffic ai-resume-parser "${common[@]}" --to-revisions "${compatible_revision}=100"
rm -f "$compatibility_file" "$compatibility_file.verified"

gcloud run jobs deploy hirewiz-schema-migration "${common[@]}" --image "$image" \
  --service-account "hirewiz-api@${project}.iam.gserviceaccount.com" \
  --set-env-vars "SERVICE_ROLE=migration,APP_ENV=production,APP_RELEASE=${release},DB_POOL_SIZE=1,DB_MAX_OVERFLOW=0" \
  --set-secrets DATABASE_URL=hirewiz-database-url:latest \
  --tasks 1 --parallelism 1 --max-retries 0 --task-timeout 600s --cpu 1 --memory 512Mi
gcloud run jobs execute hirewiz-schema-migration "${common[@]}" --wait

analysis_url="$(gcloud run services describe hirewiz-analysis-worker "${common[@]}" --format='value(status.url)')"
worker_env="^|^SERVICE_ROLE=worker|WORKER_LABEL=employer-worker|APP_ENV=production|APP_RELEASE=${release}|LOG_FORMAT=json|ANALYSIS_TASKS_MODE=cloud_tasks|GOOGLE_CLOUD_PROJECT=${project}|ANALYSIS_TASKS_LOCATION=${region}|ANALYSIS_TASKS_QUEUE=hirewiz-analysis|ANALYSIS_TASKS_SERVICE_ACCOUNT=hirewiz-tasks@${project}.iam.gserviceaccount.com|ANALYSIS_WORKER_URL=${analysis_url}|WORKER_ALLOWED_TOPICS=employer.search,employer.refresh,employer.apply,employer.artifact-delete|EMPLOYER_DISCOVERY_ENABLED=true|EMPLOYER_AUTO_SUBMIT_ENABLED=false|EMPLOYER_ARTIFACT_BUCKET=${project}-hirewiz-application-artifacts|DB_POOL_SIZE=2|DB_MAX_OVERFLOW=0|LIFECYCLE_EMAILS_ENABLED=false"
gcloud run deploy hirewiz-employer-worker "${common[@]}" --image "$image" \
  --service-account "hirewiz-employer-worker@${project}.iam.gserviceaccount.com" \
  --no-allow-unauthenticated --concurrency 4 --timeout 900 --max-instances 2 --cpu 1 --memory 1Gi \
  --set-env-vars "$worker_env" \
  --set-secrets DATABASE_URL=hirewiz-database-url:latest,ANALYSIS_TASK_TOKEN=hirewiz-analysis-task-token:latest,JWT_SECRET=hirewiz-worker-jwt-bootstrap:latest
gcloud run services add-iam-policy-binding hirewiz-employer-worker "${common[@]}" \
  --member "serviceAccount:hirewiz-tasks@${project}.iam.gserviceaccount.com" --role roles/run.invoker
employer_url="$(gcloud run services describe hirewiz-employer-worker "${common[@]}" --format='value(status.url)')"
shared="EMPLOYER_SEARCH_TASKS_QUEUE=hirewiz-employer-search,EMPLOYER_INGESTION_TASKS_QUEUE=hirewiz-employer-ingestion,EMPLOYER_APPLICATION_TASKS_QUEUE=hirewiz-employer-application,EMPLOYER_SEARCH_WORKER_URL=${employer_url},EMPLOYER_INGESTION_WORKER_URL=${employer_url},EMPLOYER_APPLICATION_WORKER_URL=${employer_url}"
gcloud run services update hirewiz-employer-worker "${common[@]}" --update-env-vars "$shared"
gcloud run deploy hirewiz-analysis-worker "${common[@]}" --image "$image" \
  --no-allow-unauthenticated --timeout 900 --max-instances 2 \
  --update-env-vars "SERVICE_ROLE=worker,APP_ENV=production,WORKER_ALLOWED_TOPICS=analysis.run,APP_RELEASE=${release},EMPLOYER_DISCOVERY_ENABLED=true,EMPLOYER_AUTO_SUBMIT_ENABLED=false,ANALYSIS_TASKS_MODE=cloud_tasks,GOOGLE_CLOUD_PROJECT=${project},ANALYSIS_TASKS_LOCATION=${region},ANALYSIS_TASKS_QUEUE=hirewiz-analysis,ANALYSIS_TASKS_SERVICE_ACCOUNT=hirewiz-tasks@${project}.iam.gserviceaccount.com,ANALYSIS_WORKER_URL=${analysis_url},DB_POOL_SIZE=2,DB_MAX_OVERFLOW=0,${shared}" \
  --update-secrets ANALYSIS_TASK_TOKEN=hirewiz-analysis-task-token:latest

# Existing non-secret settings and secret references are retained on the API.
gcloud run deploy ai-resume-parser "${common[@]}" --image "$image" --no-traffic --tag release-candidate --min 1 \
  --command '' --args '' \
  --update-env-vars "SERVICE_ROLE=api,APP_ENV=production,AUTO_DB_MIGRATE=false,APP_RELEASE=${release},EMPLOYER_DISCOVERY_ENABLED=true,EMPLOYER_AUTO_SUBMIT_ENABLED=false,ANALYSIS_TASKS_MODE=cloud_tasks,GOOGLE_CLOUD_PROJECT=${project},ANALYSIS_TASKS_LOCATION=${region},ANALYSIS_TASKS_QUEUE=hirewiz-analysis,ANALYSIS_TASKS_SERVICE_ACCOUNT=hirewiz-tasks@${project}.iam.gserviceaccount.com,ANALYSIS_WORKER_URL=${analysis_url},EMPLOYER_SEARCH_CREDITS_PER_JOB=1,EMPLOYER_APPLY_CREDITS_PER_JOB=5,EMPLOYER_ARTIFACT_BUCKET=${project}-hirewiz-application-artifacts,DB_POOL_SIZE=4,DB_MAX_OVERFLOW=2,${shared}" \
  --update-secrets ANALYSIS_TASK_TOKEN=hirewiz-analysis-task-token:latest
candidate_file="$(mktemp)"
trap 'rm -f "$candidate_file" "$candidate_file.verified"' EXIT
gcloud run services describe ai-resume-parser "${common[@]}" --format=json > "$candidate_file"
EXPECTED_RELEASE="$release" CANDIDATE_FILE="$candidate_file" python3 - <<'PY'
import json, os, urllib.request
with open(os.environ['CANDIDATE_FILE']) as source:
    service = json.load(source)
if service['status']['latestReadyRevisionName'] != service['status']['latestCreatedRevisionName']:
    raise SystemExit('Candidate revision is not ready')
tagged = next(item for item in service['status']['traffic'] if item.get('tag') == 'release-candidate')
with urllib.request.urlopen(tagged['url'] + '/api/health', timeout=30) as response:
    result = json.load(response)
if (result.get('ok') is not True or result.get('release') != os.environ['EXPECTED_RELEASE']
        or result.get('revision') != tagged['revisionName']):
    raise SystemExit('Candidate HTTP health does not match the release')
print('Candidate HTTP health and immutable release identity verified.')
with open(os.environ['CANDIDATE_FILE'] + '.verified', 'w') as verified:
    verified.write(tagged['revisionName'])
PY
verified_revision="$(cat "$candidate_file.verified")"
[[ "$verified_revision" =~ ^ai-resume-parser-[a-z0-9-]+$ ]]
gcloud run services update-traffic ai-resume-parser "${common[@]}" --to-revisions "${verified_revision}=100"
gcloud run services update-traffic ai-resume-parser "${common[@]}" --remove-tags migration-compatible
rm -f "$candidate_file.verified"
