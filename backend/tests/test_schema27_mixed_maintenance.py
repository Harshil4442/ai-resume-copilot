"""Published metadata and genuine current-reader hooks, owned local ports only."""
from __future__ import annotations

import copy
import json
import subprocess
from dataclasses import asdict, replace
from types import SimpleNamespace

import pytest
from infra.gcp import schema27_forward_diagnostics as diagnostics
from infra.gcp import schema27_mixed_document_proof as documents
from infra.gcp import schema27_mixed_identity_profile as identities
from infra.gcp import schema27_mixed_maintenance as maintenance
from test_document_source_v5_release import probe as probe
from test_schema26_app_successor import no_network as no_network
from test_schema27_api_identity_operator import controlled as controlled
from test_schema27_api_identity_promotion import handoff as handoff
from test_schema27_mixed_app_successor import profile as profile


@pytest.fixture
def published(profile, monkeypatch):
    private, boundary = profile
    public = identities.public_export(private)
    monkeypatch.setattr(identities.PublishedProfile, "verify_custody", lambda self: self.validate())
    return public, private, boundary


def test_export_contains_only_allowed_ids_digests_and_delta(profile):
    private, _ = profile
    public = identities.public_export(private)
    raw = json.dumps(asdict(public))
    assert identities.PublishedProfile.from_dict(json.loads(raw)) == public
    assert "/Users/" not in raw and "journal_location" not in raw and "bindings" not in raw
    assert "actual_policies" not in raw and "members" not in raw
    assert public.api.uid != public.shared.uid and public.native_approval is False
    for name in identities.readback.DOCUMENTS:
        actual = copy.deepcopy(private.promotion.handoff["callers"][name])
        actual["etag"] = "fresh-native-etag"
        actual["bindings"].reverse()
        for binding in actual["bindings"]:
            binding["members"].reverse()
        legacy = public.legacy_policy_component(name, actual)
        assert legacy["etag"] == actual["etag"]
        assert "serviceAccount:" + public.api.email not in legacy["bindings"][0]["members"]


def test_published_profile_must_be_an_exact_committed_blob(profile, tmp_path, monkeypatch):
    private, _ = profile
    root = tmp_path / "owned-publication"
    root.mkdir()
    def git(*args):
        return subprocess.run(["git", "-c", "user.name=Owned Fixture", "-c", "user.email=owned@invalid.test", *args],
                              cwd=root, check=True, capture_output=True, timeout=10).stdout.decode().strip()
    git("init", "-q")
    (root / "owned.txt").write_text("synthetic source\n")
    git("add", "owned.txt")
    git("commit", "-qm", "owned source")
    public = identities.public_export(private)
    public = replace(public, promotion_source=public.promotion_source | {"release": git("rev-parse", "HEAD")})
    path = root / identities.PUBLICATION
    path.parent.mkdir(parents=True)
    raw = identities.rolling.canonical(asdict(public)) + b"\n"
    path.write_bytes(raw)
    git("add", identities.PUBLICATION)
    git("commit", "-qm", "owned reviewed metadata")
    monkeypatch.setattr(identities, "ROOT", root)
    assert identities.published() == public
    path.write_bytes(raw + b" ")
    with pytest.raises(identities.rolling.RollingDenied, match="published_uncommitted_profile"):
        identities.published()


def test_fixed_identity_metadata_reads_use_same_transport_and_never_accept_another_route(published, monkeypatch):
    public, _, boundary = published
    calls = []
    def request(uri, **kwargs):
        calls.append((uri, kwargs))
        assert kwargs == {"method": "GET", "body": None, "headers": {}, "redirections": 0}
        if "/keys?" in uri:
            body = {"keys": []}
        else:
            email = uri.rsplit("/", 1)[-1]
            observed = boundary.identities[email]
            body = {"email": email, "uniqueId": observed["uid"], "disabled": False}
        return SimpleNamespace(status=200), json.dumps(body).encode()
    transport = SimpleNamespace(request=request)
    client = object.__new__(documents.Clients)
    client.identity_profile, client.read_only_release = public, True
    client.http, client.read_budget = SimpleNamespace(_transport=transport), diagnostics.ReadBudget()
    assert public.api.matches(client.identity(public.api.email))
    assert client.http._transport is transport and len(calls) == 2
    with pytest.raises(identities.rolling.RollingDenied, match="principal_scope"):
        client.identity("foreign@invalid.test")
    with pytest.raises(identities.rolling.RollingDenied, match="principal_scope"):
        client._profile_metadata("https://secretmanager.googleapis.com/v1/projects/other/secrets/payment:access")
    assert len(calls) == 2


def test_homogeneous_new_api_reader_and_mutator_preserve_default_shared_profile(published, tmp_path, monkeypatch):
    from infra.gcp import document_application_context as apps
    from infra.gcp import document_refresh_transition as transition
    from test_schema27_api_identity_readback import native_service

    public, _, _ = published
    raw, revisions = native_service(identities.API, 1, public.api.email)
    raw["uid"] = public.service_uids[identities.API]
    release = "b" * 40
    for value in (raw["template"], *revisions.values()):
        value["labels"] = {"hirewiz-source": release}
    resource = raw["name"]
    clients = SimpleNamespace(revision=lambda name: copy.deepcopy(revisions[name.removeprefix(resource + "/revisions/")]))
    context = object.__new__(maintenance.Applications)
    context.identity_profile, context.uids = public, {name: public.service_uids[name] for name in apps.CALLERS}
    pin = SimpleNamespace(image=raw["template"]["containers"][0]["image"])
    context.registry, context.provenance = {release: pin}, lambda _: None
    assert context.serving(clients, raw, pin.image, identities.API)["uid"] == revisions[raw["latestReadyRevision"]]["uid"]
    with pytest.raises(apps.ApplicationDenied, match="caller_identity_changed"):
        apps.serving(clients, raw, pin.image, identities.API, public.service_uids[identities.API])
    original = copy.deepcopy(raw)
    mutated = transition.mutate_template(raw, identities.API, "close", "a" * 32, "unused", {}, {}, identity_profile=public)
    assert mutated["serviceAccount"] == public.api.email and raw == original
    assert transition.environment(mutated)["DATABASE_URL"] == transition.environment(raw["template"])["DATABASE_URL"]
    with pytest.raises(transition.TransitionDenied, match="caller_identity_changed"):
        transition.mutate_template(raw, identities.API, "close", "a" * 32, "unused", {}, {})


def test_unknown_or_mixed_alias_refuses_before_maintenance_can_move_traffic(published):
    from infra.gcp import document_application_context as apps
    from test_schema27_api_identity_readback import native_service

    public, _, _ = published
    raw, revisions = native_service(identities.API, 1, public.api.email)
    raw["uid"] = public.service_uids[identities.API]
    release = "b" * 40
    raw["template"]["labels"] = {"hirewiz-source": release}
    for row in revisions.values():
        row["labels"] = {"hirewiz-source": release}
        row["serviceAccount"] = public.shared.email
    context = object.__new__(maintenance.Applications)
    context.identity_profile, context.uids = public, {name: public.service_uids[name] for name in apps.CALLERS}
    pin = SimpleNamespace(image=raw["template"]["containers"][0]["image"])
    context.registry, context.provenance = {release: pin}, lambda _: None
    clients = SimpleNamespace(revision=lambda name: copy.deepcopy(revisions[name.rsplit("/", 1)[-1]]))
    with pytest.raises(identities.rolling.RollingDenied, match="maintenance_transition_in_progress_or_unknown_alias"):
        context.serving(clients, raw, pin.image, identities.API)


@pytest.mark.parametrize("failure", [None, "crm503", "crm403", "etag"])
def test_native_shaped_preflight_keeps_actual_snapshots_and_joins_latest_etag(published, monkeypatch, failure):
    from infra.gcp import document_source_candidate_v5 as v5
    from test_schema27_forward_diagnostics import BadStatusCodeError

    public, private, boundary = published
    calls, policies, checked = [], [], []
    actual_policy = copy.deepcopy(private.promotion.handoff["callers"]["hirewiz-document-inspector"])
    original = copy.deepcopy(actual_policy)
    worker = {"name": documents.prior.PARENT + "/services/hirewiz-document-inspector",
              "template": {"containers": [{"image": documents.prior.REPOSITORY + "@sha256:" + "a" * 64,
                  "env": [{"name": "DOCUMENT_WORKER_POLICY_SHA256", "value": v5.POLICY_SHA256},
                          {"name": v5.TRANSPORT_ENV, "value": v5.TRANSPORT_MODE}]}]}}
    def request(uri, **kwargs):
        calls.append(uri)
        if "/keys?" in uri:
            value = {"keys": []}
        else:
            email = uri.rsplit("/", 1)[-1]
            observed = boundary.identities[email]
            value = {"email": email, "uniqueId": observed["uid"], "disabled": False}
        return SimpleNamespace(status=200), json.dumps(value).encode()
    def policy_read(request):
        assert request.resource == worker["name"] and request.options_requestedPolicyVersion == 3
        policies.append(request.resource)
        return copy.deepcopy(actual_policy | ({"etag": "raced-etag"} if failure == "etag" and len(policies) == 2 else {}))
    crm_calls = []
    def project_policy(request):
        crm_calls.append(request.resource)
        if failure == "crm403" or failure == "crm503" and len(crm_calls) == 1:
            raise BadStatusCodeError(403 if failure == "crm403" else 503)
        return {"bindings": [], "etag": "actual-project-etag"}
    class Messages:
        def __getattr__(self, name):
            if name == "IamProjectsServiceAccountsKeysListRequest":
                class Keys:
                    KeyTypesValueValuesEnum = SimpleNamespace(USER_MANAGED="USER_MANAGED")
                    def __new__(cls, **kwargs):
                        return SimpleNamespace(**kwargs)
                return Keys
            return lambda **kwargs: SimpleNamespace(**kwargs)
    client = object.__new__(documents.Clients)
    client.identity_profile, client.read_only_release = public, True
    client.http = SimpleNamespace(_transport=SimpleNamespace(request=request))
    client.authorization, client.read_budget = None, diagnostics.ReadBudget()
    client.errors, client.data = SimpleNamespace(HttpError=BadStatusCodeError), lambda value: value
    client.cm = client.rm = client.im = Messages()
    client.crm = SimpleNamespace(projects=SimpleNamespace(Get=lambda _: {
        "projectId": documents.prior.PROJECT, "projectNumber": documents.prior.NUMBER}, GetIamPolicy=project_policy))
    client.run = SimpleNamespace(projects_locations_services=SimpleNamespace(GetIamPolicy=policy_read))
    client.iam = SimpleNamespace(projects_serviceAccounts=SimpleNamespace(Get=lambda req: {
        "name": req.name, "email": req.name.rsplit("/", 1)[-1], "uniqueId": "3" * 21},
        GetIamPolicy=lambda _: {"bindings": []}), projects_serviceAccounts_keys=SimpleNamespace(List=lambda _: {"keys": []}))
    client.service = lambda name: copy.deepcopy(worker)
    monkeypatch.setattr(documents.prior, "validate_runtime", lambda *args, **kwargs: checked.append(("runtime", args[2])))
    monkeypatch.setattr(documents.prior, "validate_worker_snapshot", lambda _clients, snapshot, **kwargs:
                        checked.append(("worker", snapshot["service_iam"])))
    monkeypatch.setattr(diagnostics.time, "sleep", lambda _: None)
    if failure in {"crm403", "etag"}:
        with pytest.raises((diagnostics.ForwardDenied, identities.rolling.RollingDenied)) as failed:
            client.preflight()
        if failure == "crm403":
            report = diagnostics.diagnostic(failed.value, "refresh")
            assert report["http_status"] == 403 and report["operation"] == "crm_project_policy_read"
            assert len(crm_calls) == 1 and not checked
        else:
            assert len(policies) == 2 and client.authorization is None
    else:
        actual = client.preflight()
        assert actual["service_iam"] == original and actual_policy == original
        assert all(value == public.legacy_policy_component("hirewiz-document-inspector", original) for _, value in checked)
        assert client.authorization["actual_inspector_policy"] == original
        assert client.authorization["actual_etag"] == original["etag"]
        assert len(crm_calls) == (2 if failure == "crm503" else 1)
    assert set(crm_calls) == {documents.prior.PROJECT}


@pytest.mark.parametrize("lost_at", [None, 1, 4, 7])
def test_current_v5_transition_retains_new_api_and_private_forward_receipts(
    published, probe, monkeypatch, tmp_path, lost_at,
):
    import test_document_refresh_transition as lifecycle
    from infra.gcp import document_refresh_transition as transition
    from infra.gcp import document_source_candidate_v5 as v5
    from test_document_source_revision import accepted

    public, private, principal_boundary = published
    probe.build = probe.build | {"kind": v5.DEFINITIONS_KIND, "fixed_renderer_base": lifecycle.OLD_V2}
    http = lifecycle.Http(lost_at=lost_at)
    pins = dict(zip(transition.PINS, (lifecycle.OLD_V2, v5.POLICY_SHA256,
        documents.scanner.courier.RENDERER, documents.scanner.courier.FONT), strict=True))
    source_release = "b" * 40
    for name, value in http.services.items():
        template = value["template"]
        if name == transition.WORKER:
            template["containers"][0]["image"] = lifecycle.OLD_V2
            template["containers"][0]["env"].append({"name": v5.TRANSPORT_ENV, "value": v5.TRANSPORT_MODE})
        else:
            value["uid"] = public.service_uids[name]
            template["serviceAccount"] = public.api.email if name == identities.API else maintenance.applications.ACCOUNTS[name]
            template["labels"] = {"hirewiz-source": source_release}
            template["containers"][0]["env"].append({"name": "APP_RELEASE", "value": source_release})
        for entry in template["containers"][0]["env"]:
            if entry["name"] in pins:
                entry["value"] = pins[entry["name"]]
        http.revisions[value["latestReadyRevision"]] = copy.deepcopy(template)
    fresh = replace(accepted(probe.source, probe.build, probe), image=lifecycle.NEW, contract_version=5)
    monkeypatch.setattr(probe, "verify_successor", lambda *_: fresh)
    monkeypatch.setattr(probe, "verify_current_successor", lambda image, _metadata: replace(fresh, image=image))
    def service(name):
        _, raw = http.request("https://run.googleapis.com/v2/" + name, method="GET", redirections=0)
        return json.loads(raw)
    clients = SimpleNamespace(service=service, revision=service, identity=principal_boundary.identity,
        preflight=lambda: {"service": copy.deepcopy(http.services[transition.WORKER])},
        document_policy=lambda name: copy.deepcopy(private.promotion.handoff["callers"][name]), close=lambda: None)
    context = object.__new__(maintenance.Applications)
    context.identity_profile, context.clients = public, clients
    context.uids = {name: public.service_uids[name] for name in transition.CALLERS}
    context.registry = {source_release: maintenance.applications.Source(source_release, "sha256:" + "c" * 64,
        "11111111-1111-4111-8111-111111111111", 123)}
    context.provenance = lambda _: None
    context.probe = SimpleNamespace(Clients=lambda: clients)
    context.last_authorization = None
    folder = tmp_path / "forward-transition"
    before = copy.deepcopy(http.services)
    if lost_at:
        with pytest.raises((transition.TransitionDenied, lifecycle.transition.TransitionDenied), match="outcome_unknown"):
            transition.transition(lifecycle.NEW, probe.build, tmp_path / "native", folder,
                                  http=http, probe=probe, application_context=context)
        assert len(http.writes) == lost_at
        with pytest.raises(transition.TransitionDenied, match="existing_journal"):
            transition.transition(lifecycle.NEW, probe.build, tmp_path / "native", folder,
                                  http=http, probe=probe, application_context=context)
        assert len(http.writes) == lost_at
        return
    result = transition.transition(lifecycle.NEW, probe.build, tmp_path / "native", folder,
                                   http=http, probe=probe, application_context=context)
    assert result["admission_restored"] is True
    assert len(http.writes) == 10
    for name in transition.CALLERS:
        assert http.services[name]["template"]["serviceAccount"] == before[name]["template"]["serviceAccount"]
        assert transition.environment(http.services[name]["template"])["DATABASE_URL"] == transition.environment(before[name]["template"])["DATABASE_URL"]
    for filename in ("forward-caller-authorization.json", "forward-caller-authorization-final.json"):
        receipt = json.loads((folder / filename).read_bytes())
        assert receipt["kind"] == documents.KIND and receipt["actual_policies"]
        assert receipt["binary_corpus_component"]["scope"] == "retained_document_binary_and_corpus"
