"""The parent read budget includes measured local custody work, and stays finite."""
from types import SimpleNamespace

import pytest
from infra.gcp import schema27_patched_engine_recovery as recovery


@pytest.mark.parametrize('read_only', [False, True])
def test_parent_reads_survive_local_custody_but_stop_at_native_stage_boundary(monkeypatch, read_only):
    diagnostics = recovery.patched.diagnostics
    clock = [100.0]
    monkeypatch.setattr(diagnostics, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    default = diagnostics.ReadBudget()
    profile = object.__new__(recovery.identities.Profile)
    observed = {}
    def client(selected_profile, *, read_only_release, read_budget):
        observed.update(profile=selected_profile, read_only=read_only_release, budget=read_budget)
        return SimpleNamespace(read_budget=read_budget)
    monkeypatch.setattr(recovery.patched, 'Clients', client)
    parent = recovery.ParentProbe(profile).Clients(read_only_release=read_only)
    assert observed['profile'] is profile and observed['read_only'] is read_only
    assert parent.read_budget.deadline == 1900.0

    # Simulate actual local custody that already measured beyond the old 120s.
    clock[0] = 237.5
    with pytest.raises(diagnostics.ForwardDenied, match='read_deadline'):
        default.read('iam_account_read', lambda: pytest.fail('old default must stay expired'))
    reads = []
    def fresh_read():
        reads.append(clock[0])
        return {'etag': 'fresh-at-' + str(clock[0])}
    assert parent.read_budget.read('iam_account_read', fresh_read) == {'etag': 'fresh-at-237.5'}
    assert reads == [237.5]
    clock[0] = 1899.0
    assert parent.read_budget.read('iam_account_read', fresh_read) == {'etag': 'fresh-at-1899.0'}
    clock[0] = 1900.0
    with pytest.raises(diagnostics.ForwardDenied, match='read_deadline'):
        parent.read_budget.read('iam_account_read', lambda: pytest.fail('expired stage must not issue a read'))
