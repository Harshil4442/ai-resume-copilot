"""Deployment contracts supply libpq's root bundle without relaxing verify-full."""
from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest
from backend.tests.test_fresh_monetary_release import fresh, release
from backend.tests.test_fresh_monetary_release import source as source
from backend.tests.test_fresh_monetary_release import target as target
from backend.tests.test_rolling_release import FixtureBoundary, rolling

ROOT_CERT = "/etc/ssl/certs/ca-certificates.crt"


def _tex(monkeypatch):
    monkeypatch.setattr(release.native_tex, "ACCEPTED_ORIGIN",
                        "https://hirewiz-native-tex-synthetic-uc.a.run.app", raising=False)
    monkeypatch.setattr(release.native_tex, "ACCEPTED_IMAGE",
                        release.native_tex.REPOSITORY + "@sha256:" + "f" * 64, raising=False)


@pytest.mark.parametrize("service", release.SERVICES)
def test_fresh_api_and_workers_have_explicit_libpq_trust_and_unchanged_secret(source, target, monkeypatch, service):
    _tex(monkeypatch)
    env, secrets, removed = release.runtime_environment(fresh.runtime_plan(source, target), service)
    assert env["PGSSLROOTCERT"] == ROOT_CERT
    assert secrets["DATABASE_URL"] == target.runtime_secret
    assert "PGSSLMODE" not in env and "PGSSLROOTCERT" not in secrets and "PGSSLROOTCERT" not in removed
    assert all(env[name] == "false" for name in ("AUTO_DB_MIGRATE", "EMPLOYER_AUTO_SUBMIT_ENABLED", "RAZORPAY_CHECKOUT_ENABLED"))


def test_both_migration_entrypoints_pass_root_bundle_without_touching_dsn(source, target, monkeypatch):
    calls = []
    def cloud(*args):
        calls.append(args)
        return {"status": {"succeededCount": 1, "completionTime": "synthetic"}}
    disposed = []
    engine = SimpleNamespace(dispose=lambda: disposed.append(True))
    inventory = SimpleNamespace(expected_system_identifier_sha256="a" * 64,
                                expected_database_oid=16395, database_namespace="public")
    ordinary = release.NativeBoundary(release.Path(__file__).resolve().parents[2])
    monkeypatch.setattr(ordinary, "_database_context", lambda *args: (
        None, (engine, inventory, None, None), {"replacement_migration": "synthetic_migrator"}, None))
    monkeypatch.setattr(ordinary, "_cloud", cloud)
    ordinary.migrate(source, "20261008_0009", "20261009_0010")
    native = fresh.FreshNativeBoundary(ordinary.root)
    monkeypatch.setattr(native, "verify_fresh_database_phase", lambda *args, **kwargs: None)
    module = SimpleNamespace(neon_identity=SimpleNamespace(FRESH_CLUSTER="a" * 64,
        FRESH_DATABASE_OID=16395, NAMESPACE="public"))
    monkeypatch.setattr(native, "_fresh_connection", lambda *args: (
        module, SimpleNamespace(actor="synthetic_migrator", actor_oid=24578), engine))
    monkeypatch.setattr(native, "_cloud", cloud)
    native.migrate_fresh(source, target, "20261008_0009", "20261009_0010")
    deployments = [args for args in calls if args[:3] == ("run", "jobs", "deploy")]
    assert len(deployments) == 2 and len(disposed) == 2
    for args, reference in zip(deployments, (source.migration_secret, target.migration_secret), strict=True):
        value = args[args.index("--set-env-vars") + 1]
        env = dict(item.split("=", 1) for item in value.removeprefix("^|^").split("|"))
        assert env["PGSSLROOTCERT"] == ROOT_CERT and "PGSSLMODE" not in env
        assert args[args.index("--set-secrets") + 1] == "DATABASE_URL=" + reference
        assert args[args.index("--max-retries") + 1] == "0"


@pytest.mark.parametrize("service", release.SERVICES)
def test_rolling_stage_installs_root_bundle_and_preserves_secret_identity(service):
    fixture = FixtureBoundary()
    current = fixture.service(service)
    before = copy.deepcopy(current)
    body = rolling.stage_body(fixture.plan, fixture.plan.pin(service), current)
    env = rolling.environment(body["template"])
    assert current == before and "PGSSLROOTCERT" not in rolling.environment(current["template"])
    assert env["PGSSLROOTCERT"] == {"name": "PGSSLROOTCERT", "value": ROOT_CERT}
    assert env["DATABASE_URL"] == rolling.environment(current["template"])["DATABASE_URL"]
    assert "PGSSLMODE" not in env


def test_rolling_reconciliation_rejects_changed_root_bundle(tmp_path):
    tmp_path.chmod(0o700)
    fixture = FixtureBoundary()
    journal = rolling.Journal(tmp_path, fixture.plan)
    fixture.pause = True
    rolling.advance(fixture.plan, fixture, journal)
    current = fixture.states[release.SERVICES[0]]["template"]
    rolling.environment(current)["PGSSLROOTCERT"]["value"] = "/foreign/unreviewed.crt"
    for operation in fixture.operations.values():
        operation["done"] = True
    with pytest.raises(rolling.RollingDenied):
        rolling.reconcile(fixture.plan, fixture, journal)
    assert len(fixture.writes) == 1


@pytest.mark.parametrize("relative", ["Dockerfile", "backend/Dockerfile"])
def test_direct_image_default_matches_explicit_deployment_bundle(relative):
    dockerfile = release.Path(__file__).resolve().parents[2] / relative
    assert "PGSSLROOTCERT=" + ROOT_CERT + " \\\n" in dockerfile.read_text()
    assert release.SAFE_ENV["PGSSLROOTCERT"] == ROOT_CERT
