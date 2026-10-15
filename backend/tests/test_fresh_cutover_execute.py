"""Focused synthetic coordinator/refusal tests. No Docker/SDK/SQL operations."""

from __future__ import annotations

import importlib
import json
import os
import sys
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(os.environ.get("HIREWIZ_ACTION_TEST_ROOT", str(Path(__file__).resolve().parents[2])))
sys.path[:0] = [str(Path(__file__).parent), str(ROOT / "infra/gcp"), str(ROOT / "backend")]
execute = importlib.import_module("fresh_cutover_execute")
docker = importlib.import_module("fresh_cutover_docker")
local = importlib.import_module("local_neon_source_delete")
registry = importlib.import_module("fresh_cutover_registry")
native = execute.native
fresh = execute.fresh


def values():
    return {
        "release": "a" * 40,
        "image_digest": "sha256:" + "b" * 64,
        "build_id": "c" * 8 + "-" + "c" * 4 + "-" + "c" * 4 + "-" + "c" * 4 + "-" + "c" * 12,
        "ci_run_id": 1,
        "backup_uri": "gs://ai-resume-parser-482412-hirewiz-application-artifacts/releases/synthetic.dump",
        "inventory_sha256": "d" * 64,
        "model_primary": "gemini-3.6-flash",
        "role_environment": dict(registry.ROLES),
        "bootstrap_password": "synthetic-bootstrap-password",
        "operation": "execute",
    }


def test_decode_accepts_original_or_exact_resume_fields_only():
    original = values()
    assert execute.decode_input(json.dumps(original).encode()) == original
    resumed = {**original, execute.RESUME_FIELD: dict(execute.source_sql.RESUME_VALUES)}
    assert execute.decode_input(json.dumps(resumed).encode()) == resumed
    for malformed in ({**resumed, "resume_from_any_phase": True},
                      {**original, execute.RESUME_FIELD: None},
                      {**original, execute.RESUME_FIELD: {**execute.source_sql.RESUME_VALUES, "owner_oid": 2080769}}):
        with pytest.raises((native.release.ReleaseDenied, execute.source_sql.SourceActionDenied)):
            execute.decode_input(json.dumps(malformed).encode())


def source():
    return execute.plan(
        values(),
        {
            "expense": "hirewiz-expense-policy-20261014:1",
            "model": "hirewiz-model-cost-policy-20261014:1",
        },
    )


def test_absent_source_slots_are_explicit_and_final_plan_refuses_until_native_issuance():
    initial = source()
    initial.validate()
    assert (
        initial.runtime_secret
        == initial.migration_secret
        == initial.observer_secret
        == initial.model_key_secret
        == ""
    )
    legacy = native.release.Plan(
        **{key: getattr(initial, key) for key in native.release.Plan.__dataclass_fields__}
    )
    with pytest.raises(native.release.ReleaseDenied):
        legacy.validate()
    with pytest.raises(
        native.release.ReleaseDenied, match="fresh_source_native_issuance_not_complete"
    ):
        fresh.runtime_plan(initial, execute.TARGET)
    final = replace(
        initial,
        observer_secret="hirewiz-source-observer-db-20261014:2",
        model_key_secret="hirewiz-model-isolated-20261014:1",
    )
    result = fresh.runtime_plan(final, execute.TARGET)
    assert type(result) is native.release.Plan and result.database_owner_oid == 16391
    result.validate()


def test_registry_expected_state_is_not_provider_queue_or_backup_proof():
    raw, digest = registry.expected_registry(
        "a" * 40, "sha256:" + "b" * 64, datetime.now(UTC).isoformat()
    )
    value = json.loads(raw)
    assert (
        value["provider_fencing_verified"]
        is value["queue_admission_closed"]
        is value["protected_backup_verified"]
        is False
    )
    assert value["retired_role_mode"] == "deleted" and value["retired_role_oid"] == 16387
    assert execute.writes.hashlib.sha256(raw).hexdigest() == digest
    with pytest.raises(ValueError):
        registry.expected_registry(
            "unresolved", "sha256:" + "b" * 64, datetime.now(UTC).isoformat()
        )


def test_target_cannot_overlap_real_source_observer():
    final = replace(source(), observer_secret=execute.TARGET.observer_secret)
    with pytest.raises(
        native.release.ReleaseDenied, match="source_and_fresh_secret_classes_overlap"
    ):
        execute.TARGET.validate(final)


class Connection:
    @contextmanager
    def begin(self):
        yield self

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def exec_driver_sql(self, _):
        pass

    def execute(self, *_):
        return SimpleNamespace(one=lambda: (0, 0, 0))


def rebind(monkeypatch, *, password="new-password", absence=(0, 0, 0)):
    old = source()
    old = replace(old, database_owner_oid=99991)
    boundary = object.__new__(native.NativeFreshFenceBoundary)
    boundary.scope = native.BoundScope(old, execute.TARGET, (), (), None, (), ("neondb_owner",))
    boundary.retired_since = None
    boundary._source_isolated_at = datetime.now(UTC) - timedelta(seconds=1)
    boundary._source_old_passwords = ("old-source",)
    boundary._setup_passwords = {
        "runtime": "old-runtime",
        "migration": "old-migration",
        "observer": "old-target-observer",
    }
    calls = []

    class Engine:
        def connect(self):
            calls.append("authenticate")
            return Connection()

        def dispose(self):
            calls.append("dispose")

    target = SimpleNamespace(
        dump_environment=lambda: {"PGPASSWORD": password}, engine=lambda: Engine()
    )
    neon = SimpleNamespace(
        collect_source_release_target=lambda *a, **k: ("native-binding", target),
        text=lambda x: x,
        verify_transport=lambda *a: calls.append("transport"),
        verify_native_target=lambda *a: calls.append(("identity", a[2])),
    )
    helper = SimpleNamespace(_native_session_ids=lambda *a, **k: calls.append("session"))
    monkeypatch.setattr(
        native.NativeFreshFenceBoundary,
        "_module",
        lambda self, path, name: neon if path.endswith("neon_direct_identity.py") else helper,
    )
    monkeypatch.setattr(
        native.NativeFreshFenceBoundary,
        "describe_release_secret",
        lambda *a: {"state": "ENABLED", "createTime": datetime.now(UTC).isoformat()},
    )
    monkeypatch.setattr(
        Connection,
        "execute",
        lambda _self, query, *_args: SimpleNamespace(
            one=lambda: (True, 0, 0) if "bool_and" in query else absence
        ),
    )
    candidate = replace(old, observer_secret="hirewiz-source-observer-db-20261014:2")
    return boundary, candidate, calls


def test_source_observer_copied_cross_alias_password_is_denied_before_auth(monkeypatch):
    boundary, candidate, calls = rebind(monkeypatch, password="old-migration")
    with pytest.raises(
        native.release.ReleaseDenied, match="fence_source_observer_reuses_precut_password"
    ):
        boundary.bind_source_observer_after_isolation(candidate)
    assert calls == [] and not boundary.scope.source.observer_secret


def test_source_observer_rebind_cannot_change_owner_or_scope(monkeypatch):
    boundary, candidate, calls = rebind(monkeypatch)
    with pytest.raises(
        native.release.ReleaseDenied, match="fence_source_observer_plan_or_name_drift"
    ):
        boundary.bind_source_observer_after_isolation(replace(candidate, database_owner_oid=99992))
    assert calls == []


def test_source_observer_rebind_requires_native_old_role_absence(monkeypatch):
    boundary, candidate, calls = rebind(monkeypatch, absence=(1, 0, 0))
    with pytest.raises(
        native.release.ReleaseDenied, match="fence_source_observer_old_sql_authority_still_present"
    ):
        boundary.bind_source_observer_after_isolation(candidate)
    assert (
        "authenticate" in calls
        and calls[-1] == "dispose"
        and not boundary.scope.source.observer_secret
    )


def test_source_observer_positive_auth_binds_only_ref_without_full_fence_or_time_reset(monkeypatch):
    boundary, candidate, calls = rebind(monkeypatch)
    cut = boundary._source_isolated_at
    boundary.bind_source_observer_after_isolation(candidate)
    assert calls == ["authenticate", "transport", ("identity", 16391), "session", "dispose"]
    assert boundary.scope.source == candidate and boundary._source_old_passwords == ("old-source",)
    assert boundary.retired_since is None and boundary._source_isolated_at == cut
    assert boundary._setup_passwords["migration"] == "old-migration"


def test_docker_route_requires_init_isolated_amd64_and_opaque_managers_only():
    nonce = "a" * 32
    image = (
        "us-central1-docker.pkg.dev/ai-resume-parser-482412/cloud-run-source-deploy/hirewiz-release-operator@sha256:"
        + "b" * 64
    )
    argv = docker.run_argv(
        image,
        "c" * 40,
        "hirewiz-cutover-" + nonce,
        "hirewiz-cutover-data-" + nonce,
        nonce,
        Path("/synthetic/inputs"),
    )
    assert "--init" in argv and argv[argv.index("--platform") + 1] == "linux/amd64"
    assert (
        "--privileged" not in argv
        and "host" not in argv
        and "/var/run/docker.sock" not in " ".join(argv)
    )
    assert "--read-only" in argv and "--cap-drop" in argv
    assert all("readonly" in arg for arg in argv if "type=bind" in arg)
    assert not any("PASSWORD=" in arg or "TOKEN=" in arg for arg in argv)
    with pytest.raises(execute.writes.WriteDenied):
        docker.run_argv(
            image, "c" * 40, "unrelated", "hirewiz-cutover-data-" + nonce, nonce, Path("/synthetic")
        )


def test_resume_mount_is_exact_prior_readonly_volume_and_cannot_overlap_current():
    resume = execute.source_sql.PreparedOwnerResume.from_payload(dict(execute.source_sql.RESUME_VALUES))
    nonce = "a" * 32
    image = resume.prior_operator_image
    argv = docker.run_argv(image, "c" * 40, "hirewiz-cutover-" + nonce,
                          "hirewiz-cutover-data-" + nonce, nonce, Path("/synthetic/inputs"), resume)
    assert "type=volume,src=" + resume.volume + ",dst=/operator-prior,readonly" in argv
    assert argv.count("type=volume,src=" + resume.volume + ",dst=/operator-prior,readonly") == 1
    assert not any("TOKEN=" in arg or "PASSWORD=" in arg for arg in argv)
    with pytest.raises(execute.writes.WriteDenied, match="docker_resume_current_prior_volume_overlap"):
        docker.run_argv(image, "c" * 40, "hirewiz-cutover-" + resume.prior_nonce,
                        resume.volume, resume.prior_nonce, Path("/synthetic/inputs"), resume)


@pytest.mark.parametrize("drift", [None, "label", "driver", "options", "live"])
def test_native_prior_volume_and_container_absence_are_observed_before_resume(monkeypatch, drift):
    resume = execute.source_sql.PreparedOwnerResume.from_payload(dict(execute.source_sql.RESUME_VALUES))
    row = {"Name": resume.volume, "Driver": "local", "Scope": "local", "Options": None,
           "Labels": {docker.LABEL: resume.prior_nonce}}
    if drift == "label":
        row["Labels"][docker.LABEL] = "f" * 32
    elif drift == "driver":
        row["Driver"] = "unrelated"
    elif drift == "options":
        row["Options"] = {"device": "/unrelated"}
    calls = []

    def command(args, _deadline):
        calls.append(args)
        return (json.dumps([row]).encode() if args[:2] == ["volume", "inspect"]
                else b"f" * 64 if drift == "live" else b"")

    monkeypatch.setattr(docker, "command", command)
    if drift:
        with pytest.raises(execute.writes.WriteDenied):
            docker.verify_resume_volume(resume, 9999999999)
    else:
        docker.verify_resume_volume(resume, 9999999999)
        assert calls[1][0] == "ps"
        assert "label=" + docker.LABEL + "=" + resume.prior_nonce in calls[1]
    assert all(args[0] in {"volume", "ps"} for args in calls)


def test_fixed_terminal_projection_never_emits_extra_credential_or_accepts_goal_completion():
    event = {
        "event": "gcp_cutover_complete",
        "release": "a" * 40,
        "joined_revisions": {name: name + "-synthetic" for name in native.release.SERVICES},
        "gcp_traffic_promoted": True,
        "goal_complete": False,
        "queue_admission": "closed",
        "checkout_enabled": False,
        "frontend_deployment_verified": False,
        "requested_write_calls": 25,
        "exact_http_request_count_known": False,
    }
    assert docker.terminal_projection(event, "a" * 40) == event
    with pytest.raises(execute.writes.WriteDenied):
        docker.terminal_projection({**event, "password": "synthetic"}, "a" * 40)
    with pytest.raises(execute.writes.WriteDenied):
        docker.terminal_projection({**event, "goal_complete": True}, "a" * 40)


class LocalRoute:
    def __init__(self, actor=local.ACTOR):
        self.commands = 0
        self.calls = []
        self.actor = actor
        self.owner = local.ROLE

    def completed(self, _):
        pass

    def call(self, method, path, **kwargs):
        self.commands += 1
        self.calls.append((method, path, kwargs))
        if path == "/users/me":
            return {"id": self.actor}
        if path == "/projects/" + local.PROJECT:
            return {"project": {"id": local.PROJECT, "org_id": local.ORG}}
        if path.endswith("/endpoints"):
            return {
                "endpoints": [
                    {
                        "id": local.ENDPOINT,
                        "project_id": local.PROJECT,
                        "branch_id": local.BRANCH,
                        "host": local.HOST,
                        "type": "read_write",
                        "disabled": False,
                    }
                ]
            }
        if path.endswith("/roles/" + local.ROLE):
            return {"role": {"name": local.ROLE, "branch_id": local.BRANCH}}
        if path.endswith("/databases/neondb"):
            if method == "PATCH":
                self.owner = local.OWNER
            return {
                "database": {"name": "neondb", "branch_id": local.BRANCH, "owner_name": self.owner}
            }
        if path.endswith("/roles"):
            return {"roles": [{"name": local.OWNER, "branch_id": local.BRANCH}]}
        raise AssertionError("unmapped synthetic route")


def test_local_neon_actor_drift_prevents_any_mutation():
    route = LocalRoute("different-native-actor")
    with pytest.raises(RuntimeError, match="local_neon_current_actor_drift"):
        local.perform(route)
    assert all(method == "GET" for method, _, _ in route.calls)


def test_local_neon_owner_metadata_and_delete_are_fixed_and_not_sql_fence_proof():
    route = LocalRoute()
    result = local.perform(route)
    mutations = [(method, path) for method, path, _ in route.calls if method != "GET"]
    assert mutations == [
        ("PATCH", local.PREFIX + "/databases/neondb"),
        ("DELETE", local.PREFIX + "/roles/neondb_owner"),
    ]
    assert result["source_sql_absence_verified"] is result["native_fence_complete"] is False


@pytest.mark.parametrize("resume_mode", [False, True])
def test_real_coordinator_barrier_refusal_stops_before_password_rotation_restore_and_promotion(
    monkeypatch, tmp_path, resume_mode
):
    log = []
    boundary = object.__new__(native.NativeFreshFenceBoundary)
    boundary._source_old_passwords = ("old-source",)
    boundary._setup_passwords = {"runtime": "old-target"}
    for name in (
        "verify_identity_and_policy",
        "verify_admission_closed",
        "verify_document_preparation",
        "bind_authorities_once",
        "begin_source_observer_isolation",
        "bind_source_observer_after_isolation",
        "establish_retirement_cut",
        "bind_replacements_after_isolation",
    ):
        monkeypatch.setattr(
            native.NativeFreshFenceBoundary, name, lambda *_a, _name=name, **_k: log.append(_name)
        )
    monkeypatch.setattr(execute.LinuxExecutionFiles, "validate", lambda _: None)
    monkeypatch.setenv("HIREWIZ_OPERATOR_COMMIT", "a" * 40)
    monkeypatch.setattr(execute, "configure_registry", lambda *a: None)
    monkeypatch.setattr(execute, "WORK", tmp_path)

    class DB:
        @contextmanager
        def transaction(self):
            yield self

        def execute(self, _):
            pass

        def close(self):
            pass

    monkeypatch.setattr(execute.writes.transfer.BoundDatabase, "connect", lambda _: DB())
    monkeypatch.setattr(execute.writes.transfer, "_empty", lambda _: None)
    accounts = tuple(
        SimpleNamespace(email=name, uid="123456") for name in execute.writes.NEW_ACCOUNTS
    )

    class Writer:
        def create_accounts(self):
            log.append("create_accounts")
            return accounts

        def __getattr__(self, name):
            return lambda *_a, **_k: log.append(name)

    monkeypatch.setattr(execute.writes, "OfficialWrites", lambda *a: Writer())
    owner = execute.source_sql.PreparedOwner(2080768 if resume_mode else 99991)
    for name, result in [
        ("original_owner", SimpleNamespace(connect=lambda: DB())),
        ("preflight_source", {}),
        ("prepare_owner", owner),
        ("resume_prepared_owner", owner),
        ("transition_source", object()),
        ("observer_target", object()),
        ("verify_provider_delete_ready", None),
        ("verify_source_quiescent", None),
    ]:
        def operation(*_a, _result=result, _name=name, **_k):
            log.append(_name)
            return _result
        monkeypatch.setattr(execute.source_sql, name, operation)
    monkeypatch.setattr(
        execute.writes,
        "publish_source_observer",
        lambda *a: "hirewiz-source-observer-db-20261014:1",
    )
    monkeypatch.setattr(
        execute.writes,
        "rotate_empty_target",
        lambda *a: pytest.fail("rotation passed failed local barrier"),
    )
    monkeypatch.setattr(
        execute.fresh,
        "stage_fresh_release",
        lambda *a, **k: pytest.fail("restore passed failed local barrier"),
    )

    def refused():
        raise RuntimeError("synthetic local refusal")

    events = []
    value = values()
    if resume_mode:
        value[execute.RESUME_FIELD] = dict(execute.source_sql.RESUME_VALUES)
    with pytest.raises(RuntimeError, match="synthetic local refusal"):
        execute.execute(value, boundary, events.append, refused)
    assert events[-1]["event"] == "local_neon_delete_required"
    assert "establish_retirement_cut" not in log and "bind_replacements_after_isolation" not in log
    assert log.index("begin_source_observer_isolation") < log.index("create_accounts")
    assert log.index("delete_old_model_keys") < log.index("create_accounts")
    assert log.index("verify_document_preparation") < log.index("disable_old_principals")
    if resume_mode:
        assert "prepare_owner" not in log
        assert log.index("verify_source_quiescent") < log.index("bind_authorities_once")
        assert log.index("bind_authorities_once") < log.index("verify_prepared_owner_resume_pristine")
        assert log.index("verify_prepared_owner_resume_pristine") < log.index("resume_prepared_owner")
        assert log.index("resume_prepared_owner") < log.index("disable_old_principals")
    else:
        assert "resume_prepared_owner" not in log
        assert "verify_prepared_owner_resume_pristine" not in log
        assert log.index("prepare_owner") < log.index("bind_authorities_once")


@pytest.mark.parametrize("row", [(False, 0, 0), (True, 1, 0), (True, 0, 1)])
def test_source_owned_role_requires_native_nologin_no_membership_and_no_sessions(row):
    connection = SimpleNamespace(execute=lambda *_: SimpleNamespace(one=lambda: row))
    with pytest.raises(native.release.ReleaseDenied, match="fresh_source_owned_role_not_closed"):
        fresh._source_owner_closed(connection, 99991, lambda text: text)


def test_source_password_bank_survives_rebind_and_all_target_auths_before_clear(monkeypatch):
    boundary, candidate, _calls = rebind(monkeypatch)
    boundary.bind_source_observer_after_isolation(candidate)
    boundary._source_old_passwords += ("old-temporary-observer",)
    boundary._setup_passwords = dict(
        zip(native.SETUP_REFERENCES, boundary._setup_passwords.values(), strict=True)
    )
    target = native.release.FreshTargetPlan(*(row[2] + ":2" for row in execute.writes.TARGET_ROLES))
    observed = []
    reuse = [True]

    def collect(_owner, _ref, *, alias, **_kw):
        password = (
            "old-temporary-observer"
            if reuse[0] and alias == "replacement_runtime"
            else "distinct-" + alias
        )
        return alias, SimpleNamespace(dump_environment=lambda: {"PGPASSWORD": password})

    neon = SimpleNamespace(collect_fresh_release_target=collect, FRESH_DATABASE_OWNER_OID=16391)
    monkeypatch.setattr(native.NativeFreshFenceBoundary, "_module", lambda *_: neon)

    def authenticate(self, _neon, binding, _target):
        assert self._source_old_passwords == ("old-source", "old-temporary-observer")
        assert self._setup_passwords
        observed.append(binding)

    monkeypatch.setattr(
        native.NativeFreshFenceBoundary, "_authenticate_empty_replacement", authenticate
    )
    with pytest.raises(
        native.release.ReleaseDenied, match="fence_replacement_reuses_precut_password"
    ):
        boundary._bind_distinct_replacement_passwords(target)
    assert observed == [] and boundary._source_old_passwords
    reuse[0] = False
    boundary._bind_distinct_replacement_passwords(target)
    assert observed == list(native.SETUP_REFERENCES)
    assert boundary._source_old_passwords == () and boundary._setup_passwords == {}
