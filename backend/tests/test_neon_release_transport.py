"""Read-only release transport guards; native services use explicit inert fixtures."""

import ast
import hashlib
import importlib.util
import sys
from dataclasses import replace
from pathlib import Path

import pytest

PACKET = Path(__file__).resolve().parents[2]
ORIGINAL = PACKET


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


neon = load("private_neon_release_transport", PACKET / "backend/scripts/neon_direct_identity.py")
# Load only the allowed standalone guard's definitions. Its native source scan,
# GIT commands and migration discovery are never called in these inert tests.
load("release_preflight", ORIGINAL / "infra/gcp/release_preflight.py")
release = load("private_neon_release_sequencer", PACKET / "infra/gcp/monetary_release.py")


@pytest.fixture(autouse=True)
def inert_target_context(tmp_path, monkeypatch):
    # A synthetic fixture exercises the exact receipt parser/hash guard; it is
    # not a native provider proof and never claims server/credential readiness.
    import json

    receipt = {
        "organization_id_from_actual_billing_href": neon.ORGANIZATION,
        "project_id": neon.NEON_PROJECT,
        "branch_id": neon.BRANCH,
        "endpoint_id": neon.ENDPOINT,
        "direct_host": neon.RELEASE_DIRECT_HOST,
        "pooler_host": neon.RELEASE_POOLER_HOST,
        "database_name": neon.DATABASE,
        "postgres_major": 17,
        "source": "explicit_inert_unit_fixture",
    }
    path = tmp_path / "inert-provider-target.json"
    path.write_text(json.dumps(receipt))
    monkeypatch.setattr(neon, "RELEASE_CONTROL_RECEIPT", path)
    monkeypatch.setattr(
        neon, "RELEASE_CONTROL_RECEIPT_SHA", hashlib.sha256(path.read_bytes()).hexdigest()
    )
    return path


class Reader:
    """Synthetic metadata/value source; never a production native attestation."""

    def __init__(self, alias="observer", host=None):
        self.calls = []
        self.metadata = {
            "name": f"projects/{neon.PROJECT_NUMBER}/secrets/inert-observer-secret/versions/7",
            "state": "ENABLED",
            "etag": "inert-source-etag",
        }
        name = neon.RELEASE_ACTORS[alias][0]
        self.value = f"postgresql://{name}:inert-fixture-password@{host or neon.RELEASE_DIRECT_HOST}/neondb?sslmode=require&channel_binding=require"
        self.after = None

    def describe_release_secret(self, ref):
        self.calls.append(("metadata", ref))
        return dict(self.after if len(self.calls) > 1 and self.after is not None else self.metadata)

    def access_release_secret(self, ref):
        self.calls.append(("access", ref))
        return self.value


def target(reader=None, alias="observer", schema="20261008_0009", owner=16387):
    return neon.collect_release_target(
        reader or Reader(alias),
        "inert-observer-secret:7",
        alias=alias,
        database_owner_oid=owner,
        expected_schema=schema,
    )


@pytest.mark.parametrize("alias", tuple(neon.RELEASE_ACTORS))
@pytest.mark.parametrize("schema", sorted(neon.RELEASE_SCHEMAS))
def test_registered_readonly_target_role_and_exact_step(alias, schema):
    reader = Reader(alias, neon.RELEASE_POOLER_HOST)
    binding, t = target(reader, alias, schema)
    assert reader.calls == [
        ("metadata", "inert-observer-secret:7"),
        ("access", "inert-observer-secret:7"),
        ("metadata", "inert-observer-secret:7"),
    ]
    assert (binding.actor, binding.actor_oid) == neon.RELEASE_ACTORS[alias]
    assert binding.database_owner_oid == 16387 and binding.actor_oid != 16387
    assert neon.bound_schema(binding) == schema
    assert t._url.query["sslmode"] == "verify-full"
    assert t._url.host == neon.RELEASE_DIRECT_HOST
    assert "-c default_transaction_read_only=on" in binding.options
    assert "inert-fixture-password" not in repr(binding) + repr(t) + repr(neon._issued)
    dump = t.dump_environment()
    assert dump["PGUSER"] == binding.actor and dump["PGSSLMODE"] == "verify-full"
    assert dump["PGOPTIONS"] == neon.CAPTURE_OPTIONS
    with pytest.raises(neon.NeonIdentityDenied):
        neon._registered(replace(binding))


@pytest.mark.parametrize(
    "changes",
    [
        {"reference": "inert-observer-secret:latest"},
        {"reference": "inert-observer-secret:0"},
        {"reference": "foreign/path:7"},
        {"reference": True},
        {"alias": "old_owner"},
        {"alias": []},
        {"expected_schema": "20261008_0008"},
        {"expected_schema": []},
        {"database_owner_oid": True},
        {"database_owner_oid": 0},
        {"database_owner_oid": 4294967296},
    ],
)
def test_bad_identity_inputs_refuse_before_source_read(changes):
    reader = Reader()
    kw = {"alias": "observer", "database_owner_oid": 16387, "expected_schema": "20261008_0009"}
    ref = changes.pop("reference", "inert-observer-secret:7")
    kw.update(changes)
    with pytest.raises(neon.NeonIdentityDenied):
        neon.collect_release_target(reader, ref, **kw)
    assert reader.calls == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("name", "projects/111111111111/secrets/inert-observer-secret/versions/7"),
        ("name", f"projects/{neon.PROJECT_NUMBER}/secrets/other-secret/versions/7"),
        ("name", f"projects/{neon.PROJECT_NUMBER}/secrets/inert-observer-secret/versions/8"),
        ("state", "DISABLED"),
        ("state", "DESTROYED"),
        ("etag", ""),
        ("etag", "bad etag"),
        ("etag", True),
    ],
)
def test_native_source_identity_state_refuses_before_access(field, value):
    reader = Reader()
    reader.metadata[field] = value
    with pytest.raises(neon.NeonIdentityDenied):
        target(reader)
    assert reader.calls == [("metadata", "inert-observer-secret:7")]


@pytest.mark.parametrize(
    "mutation",
    [
        lambda r: setattr(r, "after", {**r.metadata, "etag": "changed-etag"}),
        lambda r: setattr(
            r,
            "value",
            r.value.replace(
                neon.RELEASE_DIRECT_HOST, neon.ENDPOINT + ".ap-southeast-1.aws.neon.tech"
            ),
        ),
        lambda r: setattr(
            r, "value", r.value.replace(neon.RELEASE_DIRECT_HOST, "other.example.invalid")
        ),
        lambda r: setattr(
            r, "value", r.value.replace("hirewiz_observer_20261010_r1", "neondb_owner")
        ),
        lambda r: setattr(r, "value", r.value.replace("/neondb?", "/other?")),
        lambda r: setattr(r, "value", r.value + "&options=-c%20role=neondb_owner"),
        lambda r: setattr(r, "value", r.value + "&hostaddr=203.0.113.1"),
        lambda r: setattr(r, "value", r.value + "&service=other"),
        lambda r: setattr(r, "value", r.value.replace("sslmode=require", "sslmode=disable")),
        lambda r: setattr(
            r, "value", r.value.replace("channel_binding=require", "channel_binding=disable")
        ),
        lambda r: setattr(r, "value", r.value.replace("inert-fixture-password", "")),
    ],
)
def test_source_or_dsn_drift_refuses_without_fallback_or_credential_error(mutation):
    reader = Reader()
    mutation(reader)
    with pytest.raises(neon.NeonIdentityDenied) as e:
        target(reader)
    assert "inert-fixture-password" not in str(e.value)
    assert "postgresql://" not in str(e.value)
    assert len(reader.calls) <= 3


def test_driver_is_rejected_before_dialect_sql_and_has_startup_readonly(monkeypatch):
    binding, t = target()
    engine = t.engine()
    events = []

    class Impostor:
        def close(self):
            events.append("closed")

    def physical(*args, **params):
        events.append("physical")
        assert params["options"] == neon.CAPTURE_OPTIONS
        return Impostor()

    monkeypatch.setattr(engine.dialect, "connect", physical)
    monkeypatch.setattr(engine.dialect, "initialize", lambda *_: events.append("dialect_sql"))
    with pytest.raises(neon.NeonIdentityDenied):
        engine.connect()
    engine.dispose()
    assert events == ["physical", "closed"]


class Rows:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows

    def one(self):
        assert len(self.rows) == 1
        return self.rows[0]

    def scalars(self):
        return iter(row[0] for row in self.rows)


class CatalogFixture:
    def __init__(self, binding, marker=None, owner=None, actor=None):
        self.binding = binding
        self.marker = marker or [binding.expected_schema]
        self.owner = owner if owner is not None else binding.database_owner_oid
        self.actor = actor if actor is not None else binding.actor_oid
        self.queries = []

    def execute(self, sql):
        q = str(sql)
        self.queries.append(q)
        if "pg_proc" in q:
            return Rows([(3441, 10, False, "pg_control_system", "internal")])
        if "pg_database" in q:
            return Rows(
                [
                    (
                        neon.DATABASE_OID,
                        self.owner,
                        self.actor,
                        self.binding.actor,
                        self.binding.actor,
                        neon.NAMESPACE_OID,
                        "inert-system-identity",
                    )
                ]
            )
        assert q == "SELECT version_num FROM public.alembic_version LIMIT 3"
        return Rows([(m,) for m in self.marker])


@pytest.mark.parametrize("schema", sorted(neon.RELEASE_SCHEMAS))
def test_actual_marker_and_independent_owner_are_checked_on_every_identity(schema, monkeypatch):
    binding, _ = target(schema=schema)
    monkeypatch.setattr(neon, "CLUSTER", hashlib.sha256(b"inert-system-identity").hexdigest())
    conn = CatalogFixture(binding)
    neon.verify_native_target(conn, binding, neon.DATABASE_OID)
    assert len(conn.queries) == 3
    for bad in (
        CatalogFixture(binding, marker=["other"]),
        CatalogFixture(binding, marker=[schema, schema]),
        CatalogFixture(binding, owner=binding.actor_oid),
        CatalogFixture(binding, actor=16387),
    ):
        with pytest.raises(neon.NeonIdentityDenied):
            neon.verify_native_target(bad, binding, neon.DATABASE_OID)


def test_legacy_registered_capture_remains_schema9_two_queries(monkeypatch):
    binding = neon.NeonDirectProxyBinding(
        "fixture.neon.invalid", "prefer", neon.CAPTURE_OPTIONS, "capture"
    )
    neon._issued[binding] = (neon.PROJECT, neon.REGION, neon.REVISION, neon.IMAGE)
    assert neon.bound_schema(binding) == "20261008_0009"
    monkeypatch.setattr(neon, "CLUSTER", hashlib.sha256(b"inert-system-identity").hexdigest())
    conn = CatalogFixture(binding)
    neon.verify_native_target(conn, binding, neon.DATABASE_OID)
    assert len(conn.queries) == 2
    with pytest.raises(neon.NeonIdentityDenied):
        neon.verify_release_schema(conn, binding)


def function(source, name):
    return next(
        n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == name
    )


def test_native_fence_has_no_input_override():
    with pytest.raises(
        release.ReleaseDenied, match="native_external_consumer_and_provider_fence_verifier_missing"
    ):
        release.NativeBoundary(ORIGINAL).verify_fences(None)


def plan():
    return release.Plan(
        release="a" * 40,
        image_digest="sha256:" + "b" * 64,
        build_id="11111111-1111-1111-1111-111111111111",
        ci_run_id=7,
        runtime_secret="inert-runtime-db:2",
        migration_secret="inert-migration-db:3",
        observer_secret="inert-observer-db:7",
        database_owner_oid=16387,
        expense_secret="inert-expense-policy:4",
        expense_sha256="c" * 64,
        model_policy_secret="inert-model-policy:5",
        model_policy_sha256="e" * 64,
        model_key_secret="inert-model-key:6",
        model_primary="gemini-3.6-flash",
        model_api_base=release.MODEL_API_BASE,
        runtime_service_account=f"hirewiz-runtime@{release.PROJECT}.iam.gserviceaccount.com",
        migration_service_account=f"hirewiz-migration@{release.PROJECT}.iam.gserviceaccount.com",
        backup_uri=f"gs://{release.PROJECT}-hirewiz-application-artifacts/releases/reviewed.dump",
        inventory_path=PACKET / "not-read-inert-inventory.json",
        inventory_sha256="d" * 64,
    )


def test_observer_source_and_owner_are_required_pinned_plan_fields():
    p = plan()
    p.validate()
    for change in (
        {"observer_secret": "inert-observer-db:latest"},
        {"observer_secret": p.runtime_secret},
        {"observer_secret": p.expense_secret},
        {"database_owner_oid": True},
        {"database_owner_oid": 0},
        {"database_owner_oid": 4294967296},
    ):
        with pytest.raises(release.ReleaseDenied):
            replace(p, **change).validate()


def test_all_context_consumers_have_updated_shape_without_native_execution():
    tree = ast.parse((PACKET / "infra/gcp/monetary_release.py").read_text())
    observed = 0
    for n in ast.walk(tree):
        if (
            isinstance(n, ast.Assign)
            and isinstance(n.value, ast.Call)
            and isinstance(n.value.func, ast.Attribute)
            and n.value.func.attr == "_database_context"
        ):
            unpack = n.targets[0]
            assert isinstance(unpack, ast.Tuple) and isinstance(unpack.elts[1], ast.Tuple)
            assert len(unpack.elts[1].elts) == 4
            observed += 1
    assert observed == 5


def test_target_receipt_drift_refuses_before_secret_metadata(inert_target_context):
    inert_target_context.write_text("{}")
    reader = Reader()
    with pytest.raises(neon.NeonIdentityDenied, match="control_receipt_mismatch"):
        target(reader)
    assert reader.calls == []


def test_native_reader_exception_does_not_expose_credential_details():
    reader = Reader()

    def fail(_):
        raise ValueError("inert-auth-error-sensitive-sentinel")

    reader.access_release_secret = fail
    with pytest.raises(neon.NeonIdentityDenied) as error:
        target(reader)
    assert "inert-auth-error-sensitive-sentinel" not in str(error.value)
