"""Pure negative controls; these fixtures never count as native acceptance."""
from __future__ import annotations

import ast
import copy
from pathlib import Path
from types import SimpleNamespace

import pytest
import test_document_refresh_probe as historical
from infra.gcp import document_candidate as candidate
from infra.gcp import document_candidate_payload as payload
from infra.gcp import document_candidate_probe as p
from infra.gcp import document_candidate_receipt as receipt

IMAGE, NONCE = historical.IMAGE, historical.NONCE


def value():
    result = historical.payload()
    result.update(event="hirewiz_document_candidate_required_v3", version=3,
                  policy_sha256=candidate.POLICY_SHA256)
    result["identity_preflight"].update(runtime_hashes=p.source_contract(), courier_bundle_verified=True)
    last = copy.deepcopy(result["cases"][-1])
    last.update(case="unsupported_source_font_refusal", reason="unsupported_source_font")
    result["cases"].append(last)
    return result


def inventory():
    result = value()
    result.update(event="hirewiz_document_candidate_inventory_v1", version=1,
                  cases=[], all_required_passed=False)
    return result


def plan(mode="corpus"):
    return {"image": IMAGE, "job_nonce": NONCE, "mode": mode}


def job(mode="corpus"):
    result = historical.job()
    result.update(p.desired(IMAGE, NONCE, mode))
    return result


def completed(mode="corpus"):
    j, ex = historical.completed(job(mode))
    ex["startTime"] = j["createTime"]
    return j, ex


def test_v2_bytes_and_policy_remain_frozen():
    import hashlib

    assert hashlib.sha256((p.ROOT / "infra/gcp/document_refresh_payload.txt").read_bytes()).hexdigest() == p.prior.PAYLOAD_SHA
    assert hashlib.sha256((p.ROOT / "infra/gcp/document_refresh_receipt.txt").read_bytes()).hexdigest() == p.prior.PARSER_SHA
    assert len(p.prior.runtime_hashes()) == 12
    assert p.prior.POLICY != candidate.POLICY_SHA256


def test_current_twenty_candidate_runtime_files_and_courier_are_bound():
    runtime = p.source_contract()
    assert len(runtime) == 20
    assert sum(name.endswith(".ttf") for name in runtime) == 4
    assert runtime == payload.EXPECTED_RUNTIME_HASHES
    assert "courier_prime_fonts.verify_bundle()" in payload.PREFLIGHT_PROGRAM
    assert len(payload.CASE_NAMES) == 11


def test_payload_can_only_replace_the_image_literal():
    result = p.rendered_payload(IMAGE)
    ast.parse(result)
    assert IMAGE in result and p.PLACEHOLDER not in result
    assert "--inventory" in result
    for bad in (p.PLACEHOLDER, IMAGE + ":latest", "https://example.test", IMAGE.replace("hirewiz-document-inspector", "other")):
        with pytest.raises(p.prior.NativeProofDenied):
            p.rendered_payload(bad)


def test_inventory_does_not_assert_corpus_acceptance():
    result = p.validate_inventory(inventory(), IMAGE, p.source_contract())
    assert set(result) == {"renderer_version", "font_manifest_sha256"}
    with pytest.raises(ValueError):
        receipt.validate(inventory(), IMAGE, candidate.POLICY_SHA256, p.source_contract())


@pytest.mark.parametrize("change", ["font_bytes", "courier", "writable", "cleanup", "bool_flag", "claim_corpus", "wrong_policy", "missing_field"])
def test_inventory_requires_actual_runtime_and_owned_cleanup(change):
    v = inventory()
    if change == "font_bytes":
        v["identity_preflight"]["runtime_hashes"]["/usr/share/fonts/truetype/hirewiz-courier-prime/CourierPrime-Regular.ttf"] = "0" * 64
    elif change == "courier":
        v["identity_preflight"]["courier_bundle_verified"] = False
    elif change == "writable":
        v["identity_preflight"]["font_manifest_process_writable"] = True
    elif change == "cleanup":
        v["identity_preflight"]["diagnostic"]["cleanup_code"] = 1
    elif change == "bool_flag":
        v["activation_flags"]["checkout_enabled"] = 0
    elif change == "claim_corpus":
        v["all_required_passed"] = True
    elif change == "wrong_policy":
        v["policy_sha256"] = p.prior.POLICY
    else:
        del v["identity_preflight"]["courier_bundle_verified"]
    with pytest.raises(p.prior.NativeProofDenied):
        p.validate_inventory(v, IMAGE, p.source_contract())


def test_candidate_corpus_requires_all_eleven_cases():
    assert receipt.validate(value(), IMAGE, candidate.POLICY_SHA256, p.source_contract()) is True
    with pytest.raises(ValueError):
        receipt.validate(historical.payload(), IMAGE, candidate.POLICY_SHA256, p.source_contract())


@pytest.mark.parametrize("change", ["missing_refusal", "substitution_accepted", "wrong_refusal", "failed_cleanup", "corrupt_layout", "false_scan", "missing_courier", "wrong_runtime"])
def test_candidate_corpus_rejects_incomplete_or_unsafe_claims(change):
    v = value()
    if change == "missing_refusal":
        v["cases"].pop()
    elif change == "substitution_accepted":
        v["cases"][-1]["diagnostic"].update(processor="clean", launcher_code=0)
    elif change == "wrong_refusal":
        v["cases"][-1]["reason"] = "hyperlink_changed"
    elif change == "failed_cleanup":
        v["cases"][-1]["diagnostic"]["cleanup_code"] = 1
    elif change == "corrupt_layout":
        v["cases"][-3]["layout"]["outside_changed_regions_pixel_delta"] = 1
    elif change == "false_scan":
        v["cases"][-3]["candidate_scan_equal"] = False
    elif change == "missing_courier":
        v["identity_preflight"]["courier_bundle_verified"] = False
    else:
        v["identity_preflight"]["runtime_hashes"] = p.prior.runtime_hashes()
    with pytest.raises(ValueError):
        receipt.validate(v, IMAGE, candidate.POLICY_SHA256, p.source_contract())


@pytest.mark.parametrize("mode", ["inventory", "corpus"])
def test_exact_secretless_singleton_job_shape(mode):
    j, ex = completed(mode)
    p.validate_job(j, plan(mode))
    p.validate_execution(ex, j, plan(mode))
    native = p.v1_job(p.desired(IMAGE, NONCE, mode))
    spec = native["spec"]["template"]["spec"]["template"]["spec"]
    assert spec["maxRetries"] == 0
    assert spec["containers"][0]["sandboxLauncher"] is True
    assert set(x["name"] for x in spec["containers"][0]["env"]) == {"DOCUMENT_WORKER_IMAGE_DIGEST", "DOCUMENT_WORKER_POLICY_SHA256"}


@pytest.mark.parametrize("change", ["secret", "retry", "second_execution", "runtime_user", "sandbox", "image", "payload", "policy"])
def test_changed_native_job_or_execution_is_rejected(change):
    j, ex = completed()
    task = j["template"]["template"]
    if change == "secret":
        task["containers"][0]["env"].append({"name": "SECRET", "valueSource": {"secretKeyRef": {"secret": "x", "version": "1"}}})
    elif change == "retry":
        ex["retriedCount"] = 1
    elif change == "second_execution":
        j["executionCount"] = 2
    elif change == "runtime_user":
        task["serviceAccount"] = "other@example.invalid"
    elif change == "sandbox":
        task["containers"][0]["sandboxLauncher"] = False
    elif change == "image":
        task["containers"][0]["image"] = historical.NEXT
    elif change == "payload":
        task["containers"][0]["args"][-1] += "\nprint('unreviewed')"
    else:
        task["containers"][0]["env"][-1]["value"] = p.prior.POLICY
    with pytest.raises(p.prior.NativeProofDenied):
        p.validate_job(j, plan())
        p.validate_execution(ex, j, plan())


def test_native_events_are_bound_and_unique():
    j, ex = completed()
    v = value()
    e = historical.event(j, ex, v)
    client = SimpleNamespace(entries=lambda *_: {"entries": [e]})
    assert p.native_payload(client, j, ex, "corpus") == v
    client.entries = lambda *_: {"entries": [e, e]}
    with pytest.raises(p.prior.NativeProofDenied, match="uniqueness"):
        p.native_payload(client, j, ex, "corpus")
    e["resource"]["labels"]["job_name"] = "other-job"
    client.entries = lambda *_: {"entries": [e]}
    with pytest.raises(p.prior.NativeProofDenied, match="scope"):
        p.native_payload(client, j, ex, "corpus")


def test_candidate_source_and_v2_source_have_separate_fixtures():
    source = Path(payload.__file__).read_text()
    assert 'family="Courier Prime"' in source
    assert 'docx_layout_corpus(image)[0]' in source
    assert '"unsupported_source_font_refusal"' in source
    assert 'font.name = "Cambria"' in (p.ROOT / "infra/gcp/document_refresh_payload.txt").read_text()
