"""One finite shared-window diagnostic across ordinary authenticated read journeys."""
from __future__ import annotations

import asyncio
import math
import time
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from scripts.stage5_capacity import (
    DASHBOARD_PATH,
    FRESH_WINDOW_COOLDOWN_SECONDS,
    FRESH_WINDOW_MODE,
    expected_capacity_refusal,
    summarize,
    wait_for_fresh_window,
)
from scripts.stage5_skill_roi import BASELINE_PREFLIGHT, checked_workspace, workspace_path

VERSION = "stage5-owned-first-minute-v1"
WINDOW_SECONDS = 60
RESPONSE_GRACE_SECONDS = 10
MINIMUM_SECONDS = FRESH_WINDOW_COOLDOWN_SECONDS + WINDOW_SECONDS + RESPONSE_GRACE_SECONDS
MAX_INFLIGHT = 8
ADMIN_LIMIT = 40
MAX_CONTROL_READS = 8
UPLOAD_ACTORS = (2, 3)
READ_CONTROLS = {
    **BASELINE_PREFLIGHT, "foreign_workspace_status": 404, "foreign_order_status": 404,
    "foreign_upload_status": 404, "owned_pending_order_valid": True, "owned_queued_upload_valid": True,
}


@dataclass(frozen=True)
class Read:
    journey: str
    actor: int
    second: int


def order_id(actor: int) -> str:
    return f"stage5-order-{actor}"


def upload_id(actor: int) -> str:
    return f"rup_{actor:032x}"


def route(read: Read) -> str:
    if read.journey == "dashboard":
        return DASHBOARD_PATH
    if read.journey == "workspace":
        return workspace_path(read.actor)
    if read.journey == "payment":
        return "/api/v1/credits/orders/" + order_id(read.actor)
    if read.journey == "upload":
        return "/api/resume/uploads/" + upload_id(read.actor)
    raise ValueError("Unknown first-minute read")


def schedule(buyers_percent: int) -> tuple[Read, ...]:
    if buyers_percent not in (20, 25):
        raise ValueError("Unknown synthetic buyer cohort")
    divisor = 5 if buyers_percent == 20 else 4
    rows = (
        *(Read("dashboard", actor, second) for second in (0, 15, 35, 55) for actor in (1, 5, 8)),
        *(Read("workspace", actor, second) for second in (0, 5, 15, 35, 55) for actor in (1, 6, 8)),
        *(Read("payment", actor, second) for second in (0, 5, 15, 35) for actor in range(divisor, 9, divisor)),
        *(Read("upload", actor, second) for second in (5, 15, 35) for actor in UPLOAD_ACTORS),
    )
    return tuple(sorted(rows, key=lambda item: (item.second, item.journey, item.actor)))


def seed_pending(engine, plan) -> dict[str, int]:
    from sqlalchemy import text
    from sqlalchemy.orm import Session

    from app.domains.credits.schemas import PurchaseQuoteView
    from app.domains.resume_uploads.models import ResumeUpload
    from app.models import PaymentOrder

    first_wave = [item for item in schedule(plan.buyers_percent) if item.journey == "payment" and item.second == 0]
    now = datetime.now(UTC)
    with Session(engine) as db:
        if tuple(db.execute(text("SELECT current_database(), current_schema()")).one()) != ("stage5_owned", "stage5_owned"):
            raise RuntimeError("First-minute seed requires the owned database and schema")
        for item in first_wave:
            identity = order_id(item.actor)
            quote = PurchaseQuoteView(quote_id=f"{identity}-quote", sku="starter_pass", name="Synthetic pass",
                catalog_version="stage5-synthetic", terms_version="stage5-synthetic", amount_minor=7900, credits=40,
                created_at=now, expires_at=now + timedelta(minutes=15), pass_expires_at=now + timedelta(days=30),
                server_time=now)
            db.add(PaymentOrder(public_id=identity, user_id=item.actor, provider_mode="test",
                sku=quote.sku, catalog_version=quote.catalog_version, entitlement_kind="credit_pass",
                entitlement_quantity=quote.credits, gross_amount_minor=quote.amount_minor,
                billing_country_confirmed_at=now.replace(tzinfo=None), status="client_confirmed",
                cost_policy_snapshot={"unified_purchase_quote": quote.model_dump(mode="json")}))
        for actor in UPLOAD_ACTORS:
            identity = upload_id(actor)
            db.add(ResumeUpload(id=identity, user_id=actor, idempotency_sha256=f"{actor:064x}",
                request_sha256="b" * 64, source_sha256="c" * 64, size_bytes=32, source_format="pdf",
                media_type="application/pdf", original_filename="synthetic.pdf", quarantine_bucket="synthetic-quarantine",
                quarantine_name=f"quarantine/{identity}/source", clean_bucket="synthetic-clean",
                clean_name=f"clean/{identity}/source", state="queued",
                expires_at=now + timedelta(minutes=15), upload_grant_expires_at=now + timedelta(minutes=5)))
        db.commit()
    return {"first_minute_pending_payment_orders": len(first_wave), "first_minute_queued_uploads": len(UPLOAD_ACTORS)}


def owned_response(read: Read, body: object, buyers_percent: int) -> bool:
    if type(body) is not dict:
        return False
    if read.journey == "workspace":
        return checked_workspace(body, read.actor) is not None
    if read.journey == "payment":
        quote = body.get("accepted_quote")
        return (body.get("schema_version") == "unified-credits-v1" and body.get("order_id") == order_id(read.actor)
                and body.get("status") == "pending" and body.get("payment_status") == "client_confirmed"
                and body.get("provider_order_id") is None and body.get("fulfilled") is False
                and body.get("checkout_available") is False and body.get("amount_minor") == 7900
                and body.get("refunded_amount_minor") == 0 and body.get("currency") == "INR"
                and type(quote) is dict and quote.get("quote_id") == f"{order_id(read.actor)}-quote"
                and quote.get("credits") == 40)
    if read.journey == "upload":
        return body == {"upload_id": upload_id(read.actor), "state": "queued", "error_code": None, "resume": None}
    if read.journey == "dashboard":
        from tests.fixtures.stage5_native import check_body

        return check_body(DASHBOARD_PATH, body, user_id=read.actor,
            buyer=read.actor % (5 if buyers_percent == 20 else 4) == 0, heavy=read.actor == 8)
    return False


def control_reads(client, tokens, buyers_percent: int) -> dict:
    pending_order = next(item for item in schedule(buyers_percent) if item.journey == "payment")
    pending_upload = Read("upload", UPLOAD_ACTORS[0], 5)

    def get(path, user):
        return client.get(path, headers={"Authorization": "Bearer " + tokens[user]})

    def succeeds(read):
        response = get(route(read), read.actor)
        return response.status_code == 200 and owned_response(read, response.json(), buyers_percent)

    return {"foreign_workspace_status": get(workspace_path(2), 1).status_code,
        "foreign_order_status": get(route(pending_order), 1).status_code,
        "foreign_upload_status": get(route(pending_upload), 1).status_code,
        "owned_pending_order_valid": succeeds(pending_order),
        "owned_queued_upload_valid": succeeds(pending_upload)}


def sample(storage, counters):
    # INFO is local fixture instrumentation and adds one Redis command per sample.
    return {**counters.snapshot(),
        "redis_commands_including_sampling": storage.storage.info("stats")["total_commands_processed"]}


async def measure(client, plan, tokens, storage, counters, *, clock=time.perf_counter, sleep=asyncio.sleep) -> dict:
    start = clock()
    cooldown = await wait_for_fresh_window(deadline=start + plan.measurement_seconds, clock=clock, sleep=sleep)
    if cooldown is None or clock() + WINDOW_SECONDS + RESPONSE_GRACE_SECONDS > start + plan.measurement_seconds:
        return {"state": "not_started_deadline", "requests": []}
    origin = clock()
    deadline = origin + WINDOW_SECONDS + RESPONSE_GRACE_SECONDS
    semaphore = asyncio.Semaphore(MAX_INFLIGHT)
    schedule_rows = schedule(plan.buyers_percent)
    per_stream = {(read.journey, read.actor): asyncio.Lock() for read in schedule_rows}
    blocked: set[tuple[str, int]] = set()
    active = peak = 0
    before = sample(storage, counters)
    samples = [{"second": 0, "cumulative": before}]

    async def snapshot_window():
        for second in range(1, WINDOW_SECONDS + 1):
            await sleep(max(0, origin + second - clock()))
            samples.append({"second": second, "cumulative": sample(storage, counters)})

    async def one(read: Read) -> dict:
        nonlocal active, peak
        due = origin + read.second
        await sleep(max(0, due - clock()))
        # The payment UI stops automatic reads on admission refusal; upload
        # respects Retry-After=60, so the remaining ticks also fall outside this window.
        row = {"journey": read.journey, "actor": read.actor, "second": read.second,
            "status": "suppressed_after_capacity", "bytes": 0, "sent": False, "completion_second": None,
            "contract_valid": False, "capacity_refusal_valid": False, "retry_after": None}
        key = (read.journey, read.actor)
        async with per_stream[key]:
            async with semaphore:
                sent = clock()
                if key not in blocked and sent < origin + WINDOW_SECONDS:
                    row.update(status="transport_error", sent=True)
                    active += 1
                    peak = max(peak, active)
                    try:
                        response = await asyncio.wait_for(client.get(route(read), headers={
                            "Authorization": "Bearer " + tokens[read.actor]}), timeout=deadline - sent)
                        row.update(status=response.status_code, bytes=len(response.content),
                            retry_after=response.headers.get("retry-after"))
                        if response.status_code == 200:
                            cache = {part.strip().lower() for part in response.headers.get("cache-control", "").split(",")}
                            row["contract_valid"] = {"private", "no-store"}.issubset(cache) and owned_response(
                                read, response.json(), plan.buyers_percent)
                        elif response.status_code == 429:
                            row["capacity_refusal_valid"] = expected_capacity_refusal(
                                FRESH_WINDOW_MODE, response.status_code, response.json(), response.headers)
                            if read.journey in ("payment", "upload") and row["capacity_refusal_valid"]:
                                blocked.add(key)
                    except ValueError:
                        row["status"] = "response_invalid"
                    except TimeoutError:
                        row["status"] = "deadline"
                    except Exception:
                        row["status"] = "transport_error"
                    finally:
                        active -= 1
                elif key not in blocked:
                    row["status"] = "deadline"
        done = clock()
        row.update(queue_ms=max(0, sent - due) * 1000, http_ms=max(0, done - sent) * 1000,
            arrival_to_completion_ms=max(0, done - due) * 1000,
            completion_second=int(done - origin) if row["sent"] else None)
        return row

    rows, _ = await asyncio.gather(asyncio.gather(*(one(read) for read in schedule_rows)), snapshot_window())
    after = sample(storage, counters)
    delta = {key: after.get(key, 0) - before.get(key, 0) for key in after}
    by_journey = {}
    for name in ("dashboard", "workspace", "payment", "upload"):
        selected = [row for row in rows if row["journey"] == name]
        sent = [row for row in selected if row["sent"]]
        by_journey[name] = {**summarize(sent), "planned_ticks": len(selected),
            "completed_http": sum(type(row["status"]) is int for row in sent),
            "suppressed_after_capacity": sum(row["status"] == "suppressed_after_capacity" for row in selected)}
    return {"state": "measured", "cooldown_seconds": cooldown, "actual_peak_inflight_http": peak,
        "planned_ticks": len(schedule_rows), "observed_requests": sum(row["sent"] for row in rows),
        "measurement_seconds": clock() - start, "wire_and_sql": delta, "samples": samples,
        "journeys": by_journey, "requests": rows}


def validate(result: dict, preflight: dict, buyers_percent: int) -> dict:
    failures: Counter[str] = Counter()
    rows = result.get("requests", [])
    expected = schedule(buyers_percent)
    if preflight != READ_CONTROLS:
        failures["preflight_boundary_or_contract"] += 1
    if result.get("state") != "measured":
        failures["window_not_measured"] += 1
    if [(row.get("journey"), row.get("actor"), row.get("second")) for row in rows] != [
            (item.journey, item.actor, item.second) for item in expected]:
        failures["request_denominator"] += 1
    elif result["planned_ticks"] != len(expected) or result["observed_requests"] != sum(row["sent"] for row in rows):
        failures["request_denominator"] += 1
    cooldown = result.get("cooldown_seconds")
    if not isinstance(cooldown, (int, float)) or isinstance(cooldown, bool) or not math.isfinite(cooldown) or cooldown < FRESH_WINDOW_COOLDOWN_SECONDS:
        failures["natural_cooldown"] += 1
    if not 1 <= result.get("actual_peak_inflight_http", 0) <= MAX_INFLIGHT:
        failures["concurrency"] += 1
    refused, completed = 0, 0
    paused: set[tuple[str, int]] = set()
    for row in rows:
        key = (row["journey"], row["actor"])
        if row["status"] == "suppressed_after_capacity":
            if key not in paused or row["sent"]:
                failures["unexpected_poll_suppression"] += 1
        elif key in paused or not row["sent"]:
            failures["unexpected_request_after_capacity_or_deadline"] += 1
        elif row["status"] == 200 and row["contract_valid"] is True:
            completed += 1
        elif row["status"] == 429 and row["capacity_refusal_valid"] is True and row["retry_after"] == "60":
            refused += 1
            if row["journey"] in ("payment", "upload"):
                paused.add(key)
        else:
            failures["unexpected_http_outcome"] += 1
    wire = result.get("wire_and_sql", {})
    # Concurrent requests can consume their first List and be refused before a second.
    if (wire.get("synthetic_admin_list_databases") != ADMIN_LIMIT or not 0 < completed <= ADMIN_LIMIT // 2
        or not refused or wire.get("synthetic_admin_get_database", 0) or wire.get("blocked_external_attempts", 0)
        or any(wire.get("sql_" + verb, 0) for verb in ("INSERT", "UPDATE", "DELETE"))):
        failures["shared_governor_or_readonly"] += 1
    samples = result.get("samples", [])
    observations = [value.get("cumulative", {}) for value in samples]
    monotonic = all(all(current.get(key, 0) >= value for key, value in previous.items())
                    for previous, current in zip(observations, observations[1:], strict=False))
    if ([value.get("second") for value in samples] != list(range(WINDOW_SECONDS + 1)) or not monotonic
        or any(type(sample.get("redis_commands_including_sampling")) is not int for sample in observations)):
        failures["second_by_second_observation"] += 1
    return {"passed": not failures, "failures": dict(failures), "production_capacity_qualified": False}
