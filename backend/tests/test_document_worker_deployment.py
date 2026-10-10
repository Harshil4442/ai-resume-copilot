"""Offline configuration/refusal checks; these are not Cloud Run isolation proof."""

import copy
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
IMAGE = "us-central1-docker.pkg.dev/ai-resume-parser-482412/cloud-run-source-deploy/hirewiz-document-inspector@sha256:" + "a" * 64
RELEASE = "b" * 40


@pytest.fixture
def config():
    spec = importlib.util.spec_from_file_location("document_worker_deployment", ROOT / "infra/gcp/document_worker.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def snapshots(config):
    result = config.render(IMAGE, RELEASE)
    service = copy.deepcopy(result["service"])
    name = service["name"]
    revision_name = name + "/revisions/hirewiz-document-inspector-00001-synthetic"
    service.update(uid="synthetic-service", generation="1", observedGeneration="1", reconciling=False,
                   latestReadyRevision=revision_name,
                   terminalCondition={"state": "CONDITION_SUCCEEDED"},
                   trafficStatuses=[{"type": "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST", "revision": revision_name, "percent": 100}])
    revision = copy.deepcopy(service["template"])
    revision.update(name=revision_name, uid="synthetic-revision", service=name,
                    conditions=[{"type": "Ready", "state": "CONDITION_SUCCEEDED"}])
    return {"service": service, "revision": revision,
            "service_iam": result["initial_service_iam"], "project_iam": {"version": 3, "bindings": []},
            "service_account": {"email": config.SERVICE_ACCOUNT, "disabled": False},
            "service_account_keys": {"keys": []}}


def test_default_plan_has_no_purchasable_or_enabled_wiring(config):
    value = config.plan()
    assert value["api_wiring_enabled"] is False and value["deployment_proven"] is False
    assert value["image_bound"] is False and value["native_calls"] == 0


def test_render_is_separate_secretless_launcher_not_existing_api(config):
    value = config.render(IMAGE, RELEASE)
    service = value["service"]
    assert service["name"].endswith("/services/hirewiz-document-inspector")
    assert service["template"]["serviceAccount"] != config.API_INVOKER
    assert service["ingress"] == "INGRESS_TRAFFIC_INTERNAL_ONLY"
    assert service["invokerIamDisabled"] is False
    assert service["template"]["containers"][0]["sandboxLauncher"] is True
    assert service["template"]["maxInstanceRequestConcurrency"] == 1
    assert service["template"]["scaling"] == {"minInstanceCount": 0, "maxInstanceCount": 2}
    assert value["initial_service_iam"]["bindings"] == []
    assert value["after_isolation_service_iam"]["bindings"] == [
        {"role": "roles/run.invoker", "members": ["serviceAccount:" + config.API_INVOKER]}]
    assert value["api_wiring_enabled"] is False and value["runtime_isolation_proven"] is False
    env = service["template"]["containers"][0]["env"]
    assert {entry["name"] for entry in env} == {"DOCUMENT_WORKER_IMAGE_DIGEST", "DOCUMENT_WORKER_POLICY_SHA256"}


@pytest.mark.parametrize("image,release", [("image:latest", RELEASE), (IMAGE.replace("us-central1", "europe-west1"), RELEASE),
    (IMAGE.replace("hirewiz-document-inspector", "ai-resume-parser"), RELEASE), (IMAGE, "branch"),
    (IMAGE, "B" * 40)])
def test_unbound_or_unrelated_image_and_release_refused(config, image, release):
    with pytest.raises(config.ConfigurationDenied):
        config.render(image, release)


def test_offline_matching_snapshot_does_not_become_native_or_runtime_evidence(config):
    report = config.check_snapshot(snapshots(config), IMAGE, RELEASE)
    assert report["configuration_matches"] is True
    assert report["input_authority"] == "untrusted_offline_snapshot"
    assert report["runtime_isolation_proven"] is False
    assert report["effective_iam_proven"] is False
    assert report["api_wiring_enabled"] is False


@pytest.mark.parametrize("change", [
    lambda value: value["service"].update(invokerIamDisabled=True),
    lambda value: value["service"].update(ingress="INGRESS_TRAFFIC_ALL"),
    lambda value: value["service"].update(reconciling=True),
    lambda value: value["service"].update(observedGeneration="0"),
    lambda value: value["service"]["template"].update(serviceAccount="hirewiz-api@ai-resume-parser-482412.iam.gserviceaccount.com"),
    lambda value: value["service"]["template"].update(executionEnvironment="EXECUTION_ENVIRONMENT_GEN1"),
    lambda value: value["service"]["template"].update(maxInstanceRequestConcurrency=80),
    lambda value: value["service"]["template"]["scaling"].update(minInstanceCount=1),
    lambda value: value["service"]["template"].update(timeout="300s"),
    lambda value: value["revision"]["containers"][0].update(sandboxLauncher=False),
    lambda value: value["revision"]["containers"][0].update(image=IMAGE[:-1] + "b"),
    lambda value: value["revision"]["containers"][0]["resources"]["limits"].update(memory="1Gi"),
    lambda value: value["revision"]["containers"][0].update(command=["sh", "-c", "run"]),
    lambda value: value["revision"]["containers"].append({"name": "sidecar", "image": "secret-image"}),
    lambda value: value["revision"].update(volumes=[{"name": "secrets", "secret": {"secret": "merchant"}}]),
    lambda value: value["revision"]["containers"][0].update(volumeMounts=[{"name": "credentials", "mountPath": "/credentials"}]),
    lambda value: value["revision"].update(vpcAccess={"connector": "unknown"}),
    lambda value: value["service"].update(iapEnabled=True),
    lambda value: value["service"]["trafficStatuses"][0].update(revision="unsafe-retained-revision"),
    lambda value: value["service"]["trafficStatuses"].append({"revision": "unsafe-tag", "percent": 0, "tag": "unsafe"}),
])
def test_dangerous_or_unbound_config_refused(config, change):
    value = snapshots(config)
    change(value)
    with pytest.raises(config.ConfigurationDenied):
        config.check_snapshot(value, IMAGE, RELEASE)


@pytest.mark.parametrize("entry", [
    {"name": "DATABASE_URL", "value": "synthetic-secret-never-output"},
    {"name": "GEMINI_API_KEY", "value": "synthetic-secret-never-output"},
    {"name": "DOCUMENT_WORKER_POLICY_SHA256", "valueSource": {"secretKeyRef": {"secret": "x", "version": "1"}}},
    {"name": "DOCUMENT_WORKER_IMAGE_DIGEST", "value": IMAGE},
])
def test_credential_extra_duplicate_and_reference_env_refuse_without_echo(config, entry):
    value = snapshots(config)
    value["revision"]["containers"][0]["env"].append(entry)
    with pytest.raises(config.ConfigurationDenied) as error:
        config.check_snapshot(value, IMAGE, RELEASE)
    assert "synthetic-secret-never-output" not in str(error.value)


@pytest.mark.parametrize("policy,api_phase", [
    ({"bindings": [{"role": "roles/run.invoker", "members": ["allUsers"]}]}, False),
    ({"bindings": [{"role": "custom/anything", "members": ["allAuthenticatedUsers"]}]}, False),
    ({"bindings": [{"role": "roles/run.invoker", "members": ["serviceAccount:other@example.test"]}]}, True),
    ({"bindings": [{"role": "roles/run.invoker", "members": ["serviceAccount:hirewiz-api@ai-resume-parser-482412.iam.gserviceaccount.com"], "condition": {"expression": "true"}}]}, True),
])
def test_service_iam_extra_public_or_conditional_invoker_refused(config, policy, api_phase):
    value = snapshots(config)
    value["service_iam"] = policy
    with pytest.raises(config.ConfigurationDenied):
        config.check_snapshot(value, IMAGE, RELEASE, api_phase=api_phase)


@pytest.mark.parametrize("member", ["allUsers", "allAuthenticatedUsers", "serviceAccount:hirewiz-document-inspector@ai-resume-parser-482412.iam.gserviceaccount.com"])
def test_project_public_custom_roles_or_privileged_worker_identity_refused(config, member):
    value = snapshots(config)
    value["project_iam"]["bindings"] = [{"role": "projects/p/roles/custom", "members": [member]}]
    with pytest.raises(config.ConfigurationDenied):
        config.check_snapshot(value, IMAGE, RELEASE)


def test_user_managed_worker_key_and_disabled_identity_refused(config):
    value = snapshots(config)
    value["service_account_keys"]["keys"] = [{"keyType": "USER_MANAGED", "name": "never-log-this"}]
    with pytest.raises(config.ConfigurationDenied):
        config.check_snapshot(value, IMAGE, RELEASE)
    value = snapshots(config)
    value["service_account"]["disabled"] = True
    with pytest.raises(config.ConfigurationDenied):
        config.check_snapshot(value, IMAGE, RELEASE)


def test_api_only_phase_still_does_not_enable_application(config):
    value = snapshots(config)
    value["service_iam"] = config.render(IMAGE, RELEASE)["after_isolation_service_iam"]
    report = config.check_snapshot(value, IMAGE, RELEASE, api_phase=True)
    assert report["service_iam_phase"] == "api-only"
    assert report["api_wiring_enabled"] is False


def test_duplicate_bounded_input_and_error_privacy(config):
    with pytest.raises(config.ConfigurationDenied):
        config.decode(b'{"credential":"a","credential":"b"}')
    with pytest.raises(config.ConfigurationDenied):
        config.decode(b" " * (config.MAX_INPUT_BYTES + 1))
    with pytest.raises(config.ConfigurationDenied) as error:
        config.decode(b"synthetic-secret-never-output")
    assert "synthetic-secret-never-output" not in str(error.value)
    assert json.loads(json.dumps(config.plan()))["native_calls"] == 0


def test_deep_json_within_byte_bound_has_fixed_refusal(config):
    with pytest.raises(config.ConfigurationDenied) as error:
        config.decode(b"[" * 40000 + b"0" + b"]" * 40000)
    assert str(error.value) == "document_worker_configuration_unverified"


@pytest.mark.parametrize("change", [
    lambda value: value["service"].pop("generation"),
    lambda value: value["service"].update(generation=True, observedGeneration=True),
    lambda value: value["service"].update(generation="0", observedGeneration="0"),
    lambda value: value["revision"]["conditions"].append({"type": "Ready", "state": "CONDITION_FAILED"}),
    lambda value: value["service_iam"].update(version=True),
])
def test_missing_or_ambiguous_ready_identity_refused(config, change):
    value = snapshots(config)
    change(value)
    with pytest.raises(config.ConfigurationDenied):
        config.check_snapshot(value, IMAGE, RELEASE)


@pytest.mark.parametrize("change", [
    lambda value: value["service"]["template"].update(maxInstanceRequestConcurrency=True),
    lambda value: value["revision"]["containers"][0].update(sandboxLauncher=1),
    lambda value: value["service"].update(invokerIamDisabled=0),
    lambda value: value["service"]["template"]["scaling"].update(minInstanceCount=False),
    lambda value: value["revision"]["containers"][0]["resources"].update(cpuIdle=1),
    lambda value: value["service_account"].update(disabled=0),
])
def test_json_bool_numeric_substitutes_refused(config, change):
    value = snapshots(config)
    change(value)
    with pytest.raises(config.ConfigurationDenied):
        config.check_snapshot(value, IMAGE, RELEASE)


def test_nonboolean_phase_is_not_permission(config):
    with pytest.raises(config.ConfigurationDenied):
        config.check_snapshot(snapshots(config), IMAGE, RELEASE, api_phase="false")


def test_native_default_omissions_and_bound_short_service_are_supported(config):
    value = snapshots(config)
    value["service"].pop("invokerIamDisabled")
    value["service"].pop("defaultUriDisabled")
    value["service"].pop("reconciling")
    value["service_account"].pop("disabled")
    for instance in (value["service"]["template"], value["revision"]):
        instance["scaling"].pop("minInstanceCount")
        instance["containers"][0]["resources"].pop("startupCpuBoost")
    value["revision"]["service"] = config.SERVICE
    assert config.check_snapshot(value, IMAGE, RELEASE)["configuration_matches"] is True
