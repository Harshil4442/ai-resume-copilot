"""Restore exactly three original Scheduler states after completed schema26 V3.

Consumes original maintenance and completed application custody. No new plan,
DDL, credential, queue, worker, API-template or traffic authority is provided.
Unknown state-only POST outcomes are read/reconciled and never sent twice.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import asdict, replace
from pathlib import Path

sys.path[:0] = [
    str(Path(__file__).resolve().parent),
    str(Path(__file__).resolve().parents[2] / "backend"),
    str(Path(__file__).resolve().parents[2]),
]
import rolling_release as rolling  # noqa: E402 -- trusted standalone module roots
import schema25_maintenance as maintenance  # noqa: E402 -- trusted standalone module roots
import schema26_app_rolling_v3 as app26  # noqa: E402 -- trusted standalone module roots
import schema26_maintenance_guard as guard  # noqa: E402 -- trusted standalone module roots

BINDING = "schema26.schedules.restore.binding"


def need(value, code):
    rolling.need(value, "schema26_schedule_" + code)


def schedules(plan):
    need(type(plan) is maintenance.MaintenancePlan, "original_maintenance_required")
    plan.validate()
    pins = tuple(pin for pin in plan.resources if pin.kind == "schedule")
    need(
        tuple(pin.name for pin in pins) == maintenance.SCHEDULES
        and all(pin.state == "ENABLED" for pin in pins),
        "original_enabled_schedules_required",
    )
    return pins


def restore_states(plan, journal, controls):
    """Truthful permitted transition per original once marker, never fake pins."""
    states = {}
    for pin in schedules(plan):
        pause = "schedule." + pin.name + ".pause"
        need(
            journal.read(pause + ".started")
            == {
                "target": "PAUSED",
                "replay_authorized": False,
                "plan_sha256": journal.plan_hash,
            }
            and journal.read(pause + ".checked")
            == {"state": "PAUSED", "plan_sha256": journal.plan_hash},
            "original_pause_not_observed",
        )
        marker = "schedule." + pin.name + ".restore"
        started, checked = (
            journal.read(marker + ".started"),
            journal.read(marker + ".checked"),
        )
        need(checked is None or started is not None, "orphan_restore_checked")
        if started is not None:
            need(
                started
                == {
                    "target": "ENABLED",
                    "replay_authorized": False,
                    "plan_sha256": journal.plan_hash,
                },
                "restore_intent_changed",
            )
        if checked is not None:
            need(
                checked == {"state": "ENABLED", "plan_sha256": journal.plan_hash},
                "restore_checked_changed",
            )
        row = controls.get("schedule", pin.name)
        need(
            maintenance.configuration(row) == pin.configuration_sha256,
            "schedule_configuration_changed",
        )
        state = row.get("state")
        need(
            state
            in (
                {"PAUSED"}
                if started is None
                else {"ENABLED"}
                if checked is not None
                else {"PAUSED", "ENABLED"}
            ),
            "schedule_state_changed",
        )
        states[pin.name] = state
    return states


def verify_resources(plan, application, journal, controls, actual):
    pins, states = schedules(plan), restore_states(plan, journal, controls)
    by_name = {pin.name: pin for pin in pins}
    expected = []
    for pin in application.resources:
        if pin.kind == "schedule":
            need(pin.name in by_name, "schedule_scope_changed")
            original = by_name[pin.name]
            need(
                pin.fingerprint
                == rolling.sha(
                    {"state": "PAUSED", "configuration": original.configuration_sha256}
                ),
                "paused_application_pin_changed",
            )
            expected.append(
                replace(
                    pin,
                    fingerprint=rolling.sha(
                        {
                            "state": states[pin.name],
                            "configuration": original.configuration_sha256,
                        }
                    ),
                )
            )
        else:
            expected.append(pin)
    need(
        tuple(pin.name for pin in application.resources if pin.kind == "schedule")
        == maintenance.SCHEDULES
        and actual == tuple(expected),
        "resource_or_document_state_changed",
    )


class NativeScheduleResume:
    def __init__(
        self,
        root,
        maintenance_path,
        maintenance_sha,
        maintenance_directory,
        application_path,
        application_sha,
        application_directory,
    ):
        self.root = root.resolve()
        need(
            Path(__file__).resolve()
            == self.root / "infra/gcp/schema26_schedule_resume.py"
            and Path(app26.__file__).resolve()
            == self.root / "infra/gcp/schema26_app_rolling_v3.py",
            "exact_source_required",
        )
        guard._native_client_types()
        self.plan = maintenance.load(
            app26.initial._read_pinned(maintenance_path, maintenance_sha)
        )
        self.application = app26.load(
            app26.initial._read_pinned(application_path, application_sha)
        )
        self.journal = rolling.Journal(maintenance_directory, self.plan)
        self.application_journal = rolling.Journal(
            application_directory, self.application
        )
        self.app = app26.NativeApp26Boundary(
            self.root,
            maintenance_plan_path=maintenance_path,
            maintenance_plan_sha256=maintenance_sha,
            maintenance_journal=maintenance_directory,
        )
        self.controls = maintenance.NativeControls()

    def binding(self):
        return {
            "application_plan_sha256": self.application_journal.plan_hash,
            "release": self.application.release,
            "image_digest": self.application.image_digest,
            "compatibility_sha256": self.application.compatibility_sha256,
            "operator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "schedule_pins_sha256": rolling.sha(
                [asdict(pin) for pin in schedules(self.plan)]
            ),
            "replay_authorized": False,
            "plan_sha256": self.journal.plan_hash,
        }

    def verify(self):
        app, plan, journal = self.app, self.application, self.application_journal
        need(
            type(app) is app26.NativeApp26Boundary
            and type(plan) is app26.App26V3Plan
            and type(self.journal) is rolling.Journal
            and type(journal) is rolling.Journal
            and self.journal.plan_hash == rolling.sha(asdict(self.plan))
            and journal.plan_hash == rolling.sha(asdict(plan)),
            "exact_custody_required",
        )
        schedules(self.plan)
        need(
            plan.maintenance_baseline is not None
            and plan.maintenance_baseline.maintenance_plan_sha256
            == self.journal.plan_hash,
            "maintenance_application_join_changed",
        )
        need(
            journal.read(app26.historical.ROLLBACK_INTENT) is None
            and not any(
                journal.read("rollback." + name + ".started") for name in app26.SERVICES
            ),
            "rollback_started",
        )
        app._verify_global_before_resources(plan)
        verify_resources(
            self.plan,
            plan,
            self.journal,
            self.controls,
            app._resources(plan.workers_release),
        )
        app._verify_global_after_resources(plan)
        for pin in plan.services:
            marker = "promote." + pin.service
            started, ack, checked = (
                journal.read(marker + suffix)
                for suffix in (".started", ".ack", ".checked")
            )
            need(
                started is not None
                and ack is not None
                and checked is not None
                and started.get("phase") == "promote"
                and ack.get("request_sha256") == started.get("request_sha256")
                and rolling.OPERATION.fullmatch(ack.get("operation", ""))
                and checked.get("operation_ack_retained") is True
                and checked.get("replay_authorized") is False,
                "completed_promotion_required",
            )
            operation = app.operation(ack["operation"])
            need(
                operation.get("name") == ack["operation"]
                and operation.get("done") is True
                and "error" not in operation,
                "promotion_operation_unresolved",
            )
            observed = rolling._readback(plan, app, pin, started)
            need(
                all(checked.get(key) == value for key, value in observed.items()),
                "promotion_checked_changed",
            )

    def close(self):
        failed = False
        for session in (
            self.controls.session,
            self.app._run.stock.session if self.app._run is not None else None,
        ):
            if session is not None:
                try:
                    session.close()
                except Exception:
                    failed = True
        need(not failed, "owned_transport_cleanup_unavailable")


def execute(boundary, *, reconcile=False):
    need(type(boundary) is NativeScheduleResume, "fixed_native_controller_required")
    plan, journal = boundary.plan, boundary.journal
    with journal.locked(), boundary.application_journal.locked():
        existing = journal.read(BINDING)
        if existing is None:
            need(
                not reconcile
                and not any(
                    journal.read("schedule." + p.name + ".restore." + suffix)
                    for p in schedules(plan)
                    for suffix in ("started", "checked")
                ),
                "restore_binding_required",
            )
            boundary.verify()
            body = boundary.binding()
            journal.record(
                BINDING, {k: v for k, v in body.items() if k != "plan_sha256"}
            )
            existing = journal.read(BINDING)
        need(existing == boundary.binding(), "restore_binding_changed")
        for pin in schedules(plan):
            marker = "schedule." + pin.name + ".restore"
            started, checked = (
                journal.read(marker + ".started"),
                journal.read(marker + ".checked"),
            )
            boundary.verify()
            if checked is not None:
                continue
            if reconcile and started is None:
                return {
                    "status": "schema26_existing_schedule_actions_reconciled_only",
                    "remaining_unsent": True,
                    "replay_authorized": False,
                }
            need(reconcile or started is None, "started_reconcile_only")
            maintenance.resource_action(
                pin, boundary.controls, journal, "restore", reconcile=reconcile
            )
        boundary.verify()
        return {
            "status": "schema26_schedules_restored",
            "schema": app26.schema26.SCHEMA,
            "schedules": list(maintenance.SCHEDULES),
            "replay_authorized": False,
            "api_traffic_changes": 0,
            "queue_changes": 0,
            "credential_changes": 0,
            "paid_activation": False,
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("resume", "reconcile"))
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[2]
    )
    for name in (
        "maintenance-plan",
        "maintenance-journal",
        "application-plan",
        "application-journal",
    ):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--maintenance-plan-sha256", required=True)
    parser.add_argument("--application-plan-sha256", required=True)
    args = parser.parse_args()
    boundary = None
    try:
        need(
            os.getenv("CLOUDSDK_CORE_DISABLE_FILE_LOGGING") == "true"
            and os.getenv("CLOUDSDK_CORE_LOG_HTTP") == "false",
            "private_logging_required",
        )
        boundary = NativeScheduleResume(
            args.root,
            args.maintenance_plan,
            args.maintenance_plan_sha256,
            args.maintenance_journal,
            args.application_plan,
            args.application_plan_sha256,
            args.application_journal,
        )
        print(
            json.dumps(
                execute(boundary, reconcile=args.mode == "reconcile"), sort_keys=True
            )
        )
    except Exception:
        print(
            json.dumps(
                {
                    "status": "schema26_schedule_resume_stopped_reconcile_only",
                    "replay_authorized": False,
                }
            )
        )
        raise SystemExit(1) from None
    finally:
        if boundary is not None:
            try:
                boundary.close()
            except Exception:
                print(
                    json.dumps(
                        {
                            "status": "schema26_schedule_cleanup_unresolved",
                            "replay_authorized": False,
                        }
                    )
                )
                raise SystemExit(1) from None


if __name__ == "__main__":
    main()
