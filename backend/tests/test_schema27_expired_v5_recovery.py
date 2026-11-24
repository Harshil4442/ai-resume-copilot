"""Expired structural custody never substitutes for fresh V5 native acceptance."""
from __future__ import annotations

import copy
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from infra.gcp import document_refresh_transition as transition
from infra.gcp import document_source_candidate_v5 as v5
from infra.gcp import document_source_release as source
from infra.gcp import document_worker as worker
from infra.gcp import schema27_expired_v5_recovery as recovery
from infra.gcp import schema27_mixed_document_proof as documents
from infra.gcp import schema27_mixed_identity_profile as identities
from infra.gcp import schema27_mixed_maintenance as maintenance
from test_document_failed_v4_inventory_recovery import observed as observed
from test_document_source_v5_release import probe as probe
from test_schema26_app_successor import no_network as no_network
from test_schema27_api_identity_operator import controlled as controlled
from test_schema27_api_identity_promotion import handoff as handoff
from test_schema27_mixed_app_successor import profile as profile


@pytest.mark.parametrize("age", [timedelta(days=2), timedelta(days=4)])
def test_only_genuine_expired_v5_structure_can_enter_recovery(profile, probe, monkeypatch, age):
    private, _ = profile
    image, build = probe.build["immutable_image"], probe.build
    metadata = worker.render(image, probe.source.commit, source_v5=True)["service"]
    metadata.update(uid=probe.SERVICE_UID, labels={"hirewiz-refresh-build": build["build_id"],
        "hirewiz-refresh-nonce": build["nonce"],
        "hirewiz-refresh-job": "hirewiz-doc-six-" + probe.handles(build)["corpus"]["nonce"][:12]})
    stamp = datetime.now(UTC) - age
    monkeypatch.setattr(probe, "build_identity", lambda **_: build)
    monkeypatch.setattr(probe, "clients", lambda: SimpleNamespace(preflight=lambda: {"service": copy.deepcopy(metadata)},
        close=lambda: None))
    monkeypatch.setattr(probe, "_verified_evidence", lambda _: ({"name": "job", "uid": "job-uid"},
        {"name": "execution", "uid": "execution-uid"}, {"inventory": "verified"}, {"cases": "verified"}, stamp))
    monkeypatch.setattr(documents, "current_probe", lambda actual, selected_image, actual_metadata:
        probe if actual == private and selected_image == image and actual_metadata == metadata else None)
    if age < timedelta(days=3):
        with pytest.raises(identities.rolling.RollingDenied, match="structural_predecessor_required"):
            recovery.predecessor(private, image, metadata)
        return
    observed = recovery.predecessor(private, image, metadata)
    assert type(observed) is recovery.ExpiredV5Predecessor
    assert observed.contract_version == 5 and observed.required_case_count == 17
    assert observed.worker_sha256 == source.corpus.sha(metadata)
    assert observed.report()["processing_authorized"] is False
    with pytest.raises(transition.TransitionDenied, match="current_acceptance_required"):
        transition.require_current_acceptance(observed, image, probe)
    with pytest.raises(probe.NativeProofDenied, match="definitions_stale"):
        probe._verify_current_source(image, metadata)


def test_published_or_wrong_version_cannot_select_expired_private_recovery(profile, probe, monkeypatch):
    private, _ = profile
    with pytest.raises(identities.rolling.RollingDenied, match="private_profile_required"):
        recovery.predecessor(identities.public_export(private), probe.build["immutable_image"], {})
    monkeypatch.setattr(documents, "current_probe", lambda *_: SimpleNamespace(_v5=False))
    with pytest.raises(identities.rolling.RollingDenied, match="parent_profile"):
        recovery.predecessor(private, probe.build["immutable_image"], {})
    class Impostor(recovery.Applications):
        pass
    assert not recovery.selected(object.__new__(Impostor))
    assert not recovery.selected(SimpleNamespace(expired_v5=True))


def test_historical_component_is_complete_plain_data_never_acceptance(probe, monkeypatch):
    stamp = datetime.now(UTC) - timedelta(days=4)
    monkeypatch.setattr(probe, "_verified_evidence", lambda _: ({"name": "job", "uid": "job-uid"},
        {"name": "execution", "uid": "execution-uid"}, {}, {"verified": "native-corpus"}, stamp))
    component = recovery.historical_component(probe, object())
    assert type(component) is dict and set(component) == set(documents.prior.NativeAcceptance.__dataclass_fields__)
    assert component["definition_expires_at"] == (stamp + timedelta(days=3)).isoformat()
    assert component["contract_version"] == 5 and component["required_case_count"] == 17
    with pytest.raises(transition.TransitionDenied, match="current_acceptance_required"):
        transition.require_current_acceptance(component, probe.build["immutable_image"], probe)


@pytest.mark.parametrize("failure", [None, "old_acceptance", "wrong_parent", "lost_ack"])
def test_expired_recovery_requires_new_17_cases_and_keeps_admission_closed(profile, probe, monkeypatch, tmp_path, failure):
    import test_document_refresh_transition as lifecycle
    from test_document_source_revision import accepted

    private, principal_boundary = profile
    probe.build = probe.build | {"kind": v5.DEFINITIONS_KIND, "fixed_renderer_base": lifecycle.OLD_V2}
    http = lifecycle.Http(lost_at=1 if failure == "lost_ack" else None)
    pins = dict(zip(transition.PINS, (lifecycle.OLD_V2, v5.POLICY_SHA256,
        source.courier.RENDERER, source.courier.FONT), strict=True))
    release = "b" * 40
    uids = dict(maintenance.applications.UIDS)
    for name, value in http.services.items():
        template = value["template"]
        if name == transition.WORKER:
            template["containers"][0]["image"] = lifecycle.OLD_V2
            template["containers"][0]["env"].append({"name": v5.TRANSPORT_ENV, "value": v5.TRANSPORT_MODE})
        else:
            value["uid"] = uids[name]
            template["serviceAccount"] = private.transition.new_api.email if name == identities.API else maintenance.applications.ACCOUNTS[name]
            template["labels"] = {"hirewiz-source": release}
            template["containers"][0]["env"].append({"name": "APP_RELEASE", "value": release})
        for entry in template["containers"][0]["env"]:
            if entry["name"] in pins:
                entry["value"] = pins[entry["name"]]
        http.revisions[value["latestReadyRevision"]] = copy.deepcopy(template)
    old = recovery.ExpiredV5Predecessor(lifecycle.OLD_V2, "old-build", "f" * 40, "e" * 64,
        v5.POLICY_SHA256, probe.build["source_sha256"], source.courier.RENDERER, source.courier.FONT,
        "2026-11-21T00:00:00+00:00", "a" * 64, "b" * 64, "c" * 64, 5, 17)
    fresh = replace(accepted(probe.source, probe.build, probe), image=lifecycle.NEW, contract_version=5)
    monkeypatch.setattr(probe, "verify_successor", lambda *_: old if failure == "old_acceptance" else fresh)
    monkeypatch.setattr(probe, "verify_current_successor", lambda image, _metadata: replace(fresh, image=image))
    def service(name):
        _, raw = http.request("https://run.googleapis.com/v2/" + name, method="GET", redirections=0)
        return json.loads(raw)
    clients = SimpleNamespace(service=service, revision=service, identity=principal_boundary.identity,
        preflight=lambda: {"service": copy.deepcopy(http.services[transition.WORKER])},
        document_policy=lambda name: copy.deepcopy(private.promotion.handoff["callers"][name]), close=lambda: None)
    context = object.__new__(recovery.Applications)
    context.identity_profile, context.clients, context.uids = private, clients, uids
    context.restored = SimpleNamespace(verify_custody=lambda: None)
    context.registry = {release: maintenance.applications.Source(release, "sha256:" + "c" * 64,
        "11111111-1111-4111-8111-111111111111", 123)}
    context.provenance = lambda _: None
    context.probe = SimpleNamespace(Clients=lambda: clients, verify_expired_current_source_predecessor=lambda *_:
        replace(old, contract_version=4) if failure == "wrong_parent" else old)
    context.last_authorization = None
    directory = tmp_path / "expired-transition"
    before = copy.deepcopy(http.services)
    if failure:
        expected = {"old_acceptance": "current_acceptance_required", "wrong_parent": "definitions_parent_changed",
                    "lost_ack": "outcome_unknown"}[failure]
        with pytest.raises((transition.TransitionDenied, lifecycle.transition.TransitionDenied), match=expected):
            transition.transition(lifecycle.NEW, probe.build, tmp_path / "native", directory,
                http=http, probe=probe, application_context=context, recover_expired_source=True)
        assert len(http.writes) == (1 if failure == "lost_ack" else 0)
        if failure == "lost_ack":
            with pytest.raises(transition.TransitionDenied, match="existing_journal"):
                transition.transition(lifecycle.NEW, probe.build, tmp_path / "native", directory,
                    http=http, probe=probe, application_context=context, recover_expired_source=True)
            assert len(http.writes) == 1
        return
    result = transition.transition(lifecycle.NEW, probe.build, tmp_path / "native", directory,
        http=http, probe=probe, application_context=context, recover_expired_source=True)
    assert result["admission_restored"] is False and result["recovery_admission_kept_closed"] is True
    assert len(http.writes) == 10
    captured = json.loads((directory / "captured-flags.json").read_bytes())
    assert any(row["value"] == "true" for values in captured.values() for row in values.values())
    for name in transition.CALLERS:
        env = transition.environment(http.services[name]["template"])
        assert all(env[key] == {"name": key, "value": "false"} for key in transition.PAUSE[name])
        assert http.services[name]["template"]["serviceAccount"] == before[name]["template"]["serviceAccount"]
        assert env["DATABASE_URL"] == transition.environment(before[name]["template"])["DATABASE_URL"]
    assert json.loads((directory / "expired-v5-admission-kept-closed.json").read_bytes())["processing_authorized"] is False


def test_expired_context_selects_definitions_only_and_preserves_original_history(probe, monkeypatch, tmp_path):
    from scripts import document_refresh as refresh

    context = object.__new__(recovery.Applications)
    seen = []
    context.before_refresh = lambda: seen.append("homogeneous_restore_rechecked")
    parent = probe.build["immutable_image"]
    context.probe = SimpleNamespace(Clients=lambda: SimpleNamespace(
        preflight=lambda: {"service": {"template": {"containers": [{"image": parent}]}}}, close=lambda: None),
        policy_for_serving=lambda *_: v5.POLICY_SHA256)
    modules = refresh.infra
    history = tmp_path / "original-history"
    def prepare(directory, actual_history, selected, **kwargs):
        assert actual_history == history and selected is None
        assert kwargs["application_context"] is context and kwargs["recover_expired_source"] is True
        assert "source_revision" not in kwargs and "source_v5" not in kwargs
        seen.append("original_history_verified")
        return None
    build = {"immutable_image": "child", "kind": v5.DEFINITIONS_KIND}
    def candidate(directory, **kwargs):
        assert kwargs == {"publish": True, "now": now, "nonce": None, "parent": parent,
                          "source_v5": True, "recover_expired_source": True, "application_context": context}
        seen.append("definitions_only")
        return {"build_identity": build}
    def promote(actual, native, transition_journal, **kwargs):
        assert actual == build and kwargs == {"application_context": context, "recover_expired_source": True}
        seen.append("stock_native_and_transition")
        return {"status": "expired_v5_scanner_refreshed_admission_closed"}
    monkeypatch.setattr(refresh, "infra", lambda name: SimpleNamespace(prepare=prepare) if name == "document_refresh_recovery"
                        else SimpleNamespace(promote=promote) if name == "document_source_release" else modules(name))
    monkeypatch.setattr(refresh, "source_definitions_candidate", candidate)
    now = datetime.now(UTC)
    result = refresh.refresh(tmp_path / "new-attempt", publish=True, now=now, history=history,
        application_context=context, recover_expired_source=True)
    assert result["status"] == "expired_v5_scanner_refreshed_admission_closed"
    assert seen == ["homogeneous_restore_rechecked", "original_history_verified", "definitions_only", "stock_native_and_transition"]


def test_private_context_requires_typed_restore_and_rechecks_current_source(profile, monkeypatch, tmp_path):
    from infra.gcp import schema27_staged_restore as restoration

    private, _ = profile
    calls = []
    prior = maintenance.applications.Source("70a1ef77c19c7f00570bc159b3db514b38d112d0", "sha256:" + "b" * 64,
        "11111111-1111-4111-8111-111111111111", 123)
    monkeypatch.setattr(restoration.VerifiedRestore, "verify_custody", lambda _: calls.append("restore_custody"))
    monkeypatch.setattr(restoration.VerifiedRestore, "verify_current", lambda _: calls.append("fresh_native_restore"))
    monkeypatch.setattr(maintenance.applications, "sources", lambda _: {})
    uids = dict(maintenance.applications.UIDS)
    for name in (identities.API, "hirewiz-analysis-worker"):
        uids[name] = private.promotion.before["services"][name]["uid"]
    with pytest.raises(identities.rolling.RollingDenied, match="owned_restore_required"):
        recovery.Applications(tmp_path, object(), object(), uids, private, SimpleNamespace(profile=private))
    assert calls == []
    restored = object.__new__(restoration.VerifiedRestore)
    restored.profile = private
    restored.application_plan = SimpleNamespace(prior_source=prior, release="3f7fd47738b336e4cbfdfdff2f6fdc13adca74c6")
    context = recovery.Applications(tmp_path, object(), object(), uids, private, restored)
    assert context.identity_profile is private and type(context.identity_profile) is identities.Profile
    assert context.registry[prior.release] == prior and calls == ["restore_custody", "fresh_native_restore"]
    context.observe = lambda: {"actual": "homogeneous"}
    assert context.before_refresh() == {"actual": "homogeneous"}
    assert calls == ["restore_custody", "fresh_native_restore"] * 2
    restored.application_plan = SimpleNamespace(prior_source=replace(prior, release="f" * 40),
        release="3f7fd47738b336e4cbfdfdff2f6fdc13adca74c6")
    with pytest.raises(identities.rolling.RollingDenied, match="restored_source_scope"):
        recovery.Applications(tmp_path, object(), object(), uids, private, restored)


@pytest.mark.parametrize("typed", [False, True])
def test_old_failed_claim_is_not_replayed_by_expired_v5_history(observed, monkeypatch, tmp_path, typed):
    from infra.gcp import document_refresh_probe as native_probe
    from infra.gcp import document_refresh_recovery as history_reader

    from scripts import document_refresh as refresh

    files, reader, *_ = observed
    history = tmp_path / "original-history"
    history.mkdir(mode=0o700)
    history_reader.retain(files, history / history_reader.FAILED_V4_ARTIFACT)
    successor = history / "original-v5-consumer"
    successor.mkdir(mode=0o700)
    (successor / "failed-v4-inventory").mkdir(mode=0o700)
    refresh.write_new(successor / "result.json", {"status": "document_refresh_serving", "admission_restored": True})
    output = tmp_path / "attempt"
    output.mkdir(mode=0o700)
    context = object.__new__(recovery.Applications) if typed else SimpleNamespace(expired_v5=True)
    context.probe = native_probe
    reader.close = lambda: None
    monkeypatch.setattr(native_probe, "Clients", lambda: reader)
    monkeypatch.setattr(refresh, "native_http", lambda: pytest.fail("Consumed claims may not be created again"))
    checks = []
    def completed(run, failure, original, *_args, **kwargs):
        assert run == successor and original == files and failure["job_uid"] == history_reader.FAILED_V4_JOB_UID
        assert kwargs == {"application_context": context}
        checks.append("historical_native_component")
        return "actual-current-image"
    monkeypatch.setattr(history_reader, "verify_completed_v5_consumer", completed)
    def joined(_client, _probe, _refresh, jobs, images, **kwargs):
        assert history_reader.FAILED_V4_JOB in jobs and kwargs["expected_image"] == "actual-current-image"
        assert kwargs["recover_expired_source"] is True and kwargs["application_context"] is context
        checks.append("current_expired_structure_and_callers")
    monkeypatch.setattr(history_reader, "joined", joined)
    options = {"refresh_module": refresh, "application_context": context, "recover_expired_source": True}
    if not typed:
        with pytest.raises(refresh.RefreshDenied, match="failed_v4_mode_refused"):
            history_reader.prepare(output, history, None, **options)
        assert checks == []
    else:
        assert history_reader.prepare(output, history, None, **options) is None
        assert checks == ["historical_native_component", "current_expired_structure_and_callers"]
    assert list(output.iterdir()) == []
