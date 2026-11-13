"""Fresh-process pure authority imports cannot acquire SQL or network setup."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PURE_IMPORT = r'''
import importlib, importlib.abc, json, sys
blocked = []
class ForbiddenDependency(RuntimeError):
    pass
class PureBoundary(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {"app.models", "app.database", "app.domains.notifications.service"}:
            blocked.append(fullname)
            raise ForbiddenDependency("SQL/application setup is forbidden")
sys.meta_path.insert(0, PureBoundary())
def audit(event, args):
    if event in {"socket.connect", "sqlite3.connect"}:
        blocked.append(event)
        raise ForbiddenDependency("Network/database access is forbidden")
sys.addaudithook(audit)
try:
    importlib.import_module(sys.argv[1])
    assert not any(name in sys.modules for name in ("app.models", "app.database", "app.domains.notifications.service"))
    print(json.dumps({"status": "pure_import", "blocked": blocked}))
except BaseException as error:
    print(json.dumps({"status": "refused", "type": type(error).__name__, "blocked": blocked}))
    sys.exit(2)
'''


@pytest.mark.parametrize("module", ["app.domains.recovery.gcp_scoped_census",
    "app.domains.recovery.gcp_digest_census", "app.domains.notifications.mail_contracts"])
def test_fresh_pure_import_has_no_sql_configuration_orm_or_network(module):
    # A fresh interpreter gets no inherited credentials, DATABASE_URL or PG env.
    result = subprocess.run([sys.executable, "-c", PURE_IMPORT, module], cwd=ROOT,
        env={"PATH": os.defpath, "PYTHONPATH": str(ROOT)}, capture_output=True,
        text=True, timeout=20)
    assert result.returncode == 0, result.stdout
    assert json.loads(result.stdout) == {"status": "pure_import", "blocked": []}


def test_budget_contract_reexports_are_exact_objects_and_readonly_receipt(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    from app.domains.notifications import mail_budget, mail_contracts
    assert mail_budget.ProviderResult is mail_contracts.ProviderResult
    assert mail_budget.MailBudgetError is mail_contracts.MailBudgetError
    assert mail_budget._result is mail_contracts._result
    result = mail_contracts.ProviderResult("known_retryable", "a" * 64, 429)
    assert mail_budget._result(result) is result
    error = mail_budget.MailBudgetError("known_retryable", committed_result=result)
    assert str(error) == "provider_mail_known_retryable" and error.committed_result is result
    assert mail_contracts.MailBudgetError("unknown").committed_result is None
    with pytest.raises(AttributeError):
        error.committed_result = None


@pytest.mark.parametrize("outcome,status", [("sent", 200), ("sent", 299), ("known_retryable", 429),
    ("permanent", 400), ("permanent", 404), ("unknown", None), ("unknown", 500)])
def test_contract_validation_preserves_original_success_and_unknown_shapes(outcome, status):
    from app.domains.notifications.mail_contracts import ProviderResult, _result
    result = ProviderResult(outcome, "a" * 64, status)
    assert _result(result) is result


@pytest.mark.parametrize("outcome,receipt,status", [("sent", "a" * 64, None), ("sent", "a" * 64, 500),
    ("known_retryable", "a" * 64, 400), ("permanent", "a" * 64, 408), ("permanent", "a" * 64, 429),
    ("permanent", "a" * 64, 500), ("unknown", "A" * 64, None), ("unknown", "a" * 64, True),
    ("unknown", "a" * 64, 600), ("fabricated", "a" * 64, 200)])
def test_contract_validation_preserves_fixed_refusal(outcome, receipt, status):
    from app.domains.notifications.mail_contracts import MailBudgetError, ProviderResult, _result
    with pytest.raises(MailBudgetError, match="^provider_mail_unknown$") as error:
        _result(ProviderResult(outcome, receipt, status))
    assert error.value.committed_result is None


def test_existing_package_exports_resolve_original_service_objects(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    from app.domains import notifications
    from app.domains.notifications import service
    names = ["DeliverySummary", "deliver_pending_notifications", "enqueue_notification",
             "lifecycle_email_enabled", "queue_due_reminder_emails"]
    assert notifications.__all__ == names
    for name in names:
        assert getattr(notifications, name) is getattr(service, name)
    with pytest.raises(AttributeError):
        _ = notifications.unavailable_export
