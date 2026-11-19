"""Exact actual-policy authorization beside separately scoped binary evidence."""
from __future__ import annotations

import copy

import pytest
from infra.gcp import schema27_mixed_document_proof as documents
from infra.gcp import schema27_mixed_identity_profile as identities
from test_schema27_api_identity_operator import controlled as controlled
from test_schema27_api_identity_promotion import handoff as handoff
from test_schema27_mixed_app_successor import forward_receipt
from test_schema27_mixed_app_successor import profile as profile


def test_only_pure_legacy_policy_component_changes_and_actual_response_is_retained(profile):
    pin, _ = profile
    receipt = forward_receipt(pin)
    name = identities.readback.DOCUMENTS[0]
    actual = copy.deepcopy(receipt["actual_policies"][name])
    legacy = pin.legacy_policy_component(name, actual)
    assert actual == receipt["actual_policies"][name]
    assert legacy["etag"] == actual["etag"]
    assert "serviceAccount:" + pin.transition.new_api.email in actual["bindings"][0]["members"]
    assert "serviceAccount:" + pin.transition.new_api.email not in legacy["bindings"][0]["members"]
    assert receipt["binary_corpus_component"]["scope"] == "retained_document_binary_and_corpus"
    assert receipt["kind"] != documents.prior.NativeAcceptance.__name__


@pytest.mark.parametrize("drift", ["conditional", "extra_identity", "etag", "binary", "scope"])
def test_unreviewed_caller_delta_and_changed_component_bindings_are_rejected(profile, drift):
    pin, _ = profile
    receipt = forward_receipt(pin)
    name = identities.readback.DOCUMENTS[0]
    policy = receipt["actual_policies"][name]
    if drift == "conditional":
        new = "serviceAccount:" + pin.transition.new_api.email
        policy["bindings"][0]["members"].remove(new)
        policy["bindings"].append({"role": "roles/run.invoker", "members": [new],
                                   "condition": {"title": "unreviewed", "expression": "true"}})
        receipt["actual_policy_sha256"] = identities.rolling.sha(receipt["actual_policies"])
    elif drift == "extra_identity":
        policy["bindings"][0]["members"].append("serviceAccount:foreign@invalid.test")
        receipt["actual_policy_sha256"] = identities.rolling.sha(receipt["actual_policies"])
    elif drift == "etag":
        policy["etag"] = "new-unbound-etag"
        receipt["actual_policy_sha256"] = identities.rolling.sha(receipt["actual_policies"])
    elif drift == "binary":
        receipt["binary_corpus_component"]["acceptance"]["receipt_sha256"] = "changed"
    else:
        receipt["binary_corpus_component"]["scope"] = "full_changed_policy_acceptance"
    with pytest.raises(identities.rolling.RollingDenied):
        documents.validate_receipt(pin, receipt)
