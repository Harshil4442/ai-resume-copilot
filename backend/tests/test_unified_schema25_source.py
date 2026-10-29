"""Source preparation is exact and does not obtain any provider/database session."""

from pathlib import Path
from shutil import copyfile

import pytest

from scripts import unified_credit_schema25_contract as contract

ROOT = Path(__file__).resolve().parents[2]


def test_source_chain_has_only_reviewed_create_statements_and_current_pins():
    assert len(contract.verify_source(ROOT)) == 64


def test_modified_migration_requires_new_reviewed_pin(tmp_path):
    destination = tmp_path / "backend/alembic/versions"
    destination.mkdir(parents=True)
    for revision in contract.REVISIONS:
        original = next((ROOT / "backend/alembic/versions").glob(revision + "_*.py"))
        copyfile(original, destination / original.name)
    path = next(destination.glob(contract.SCHEMA + "_*.py"))
    path.write_text(path.read_text() + "\n# unexpected source edit\n")
    with pytest.raises(contract.Schema25Denied, match="source_pin_changed"):
        contract.verify_source(tmp_path)


@pytest.mark.parametrize(
    "flags",
    [
        {},
        {key: "true" for key in contract.DISABLED_FLAGS},
        {key: False for key in contract.DISABLED_FLAGS},
    ],
)
def test_flags_must_be_explicit_observed_false_strings(flags):
    with pytest.raises(contract.Schema25Denied, match="remain_closed"):
        contract.require_disabled(flags)
