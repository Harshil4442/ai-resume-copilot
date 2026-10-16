"""Known Razorpay hosted controls absent from Greenhouse's public question feed.

These are candidate-entered review drafts, never inferred resume facts or native
application answers. Country means the phone country-code selector, not citizenship.
"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path

COUNTRIES = json.loads(Path(__file__).with_name("hosted_country_options.json").read_text())
MONTHS = [{"value": name, "label": name} for name in (
    "January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December")]
MAX_ROWS = 5
MAX_ACTIONS = 64


def mapping(rows: object) -> list[dict]:
    from . import service as s
    s.require(isinstance(rows, list) and 1 <= len(rows) <= MAX_ROWS)
    assert isinstance(rows, list)
    s.require(all(isinstance(row, dict) and set(row) == {"index", "current_role"}
                  and type(row["index"]) is int and row["index"] == index and type(row["current_role"]) is bool
                  for index, row in enumerate(rows)))
    result = [{"id": "country", "label": "Country", "type": "combobox", "required": True, "options": copy.deepcopy(COUNTRIES)}]
    for row in rows:
        index = row["index"]
        for name, label, kind in (
            ("company-name", "Company name", "text"), ("title", "Title", "text"),
            ("start-date-month", "Start date month", "combobox"), ("start-date-year", "Start date year", "text"),
            *(() if row["current_role"] else (("end-date-month", "End date month", "combobox"), ("end-date-year", "End date year", "text"))),
            ("current-role", "Current role", "checkbox"),
        ):
            field = {"id": f"{name}-{index}" + ("_1" if kind == "checkbox" else ""), "label": label,
                     "type": kind, "required": kind != "checkbox"}
            if kind == "combobox":
                field["options"] = copy.deepcopy(MONTHS)
            result.append(field)
    if len(rows) < MAX_ROWS:
        result.append({"id": f"employment-add-{len(rows)}", "label": "Add another", "type": "button", "required": False})
    return result


def controls(inspection: dict, fields: list[dict]) -> dict:
    """Bind existing values for drafting, separately from approved action values."""
    result = {"rows": copy.deepcopy(inspection["hosted_rows"]), "controls": fields,
              "values": {field["id"]: inspection["assisted_values"][field["id"]] for field in fields}}
    from . import service as s
    for field in fields:
        value = result["values"][field["id"]]
        if field["type"] == "checkbox":
            s.require(value == str(result["rows"][int(field["id"].split("-")[-1].split("_")[0])]["current_role"]).lower())
        if field["type"] == "button":
            s.require(value == str(len(result["rows"])))
    return result


def revise(payload: dict, answers: object) -> dict:
    """Candidate's account saves a draft, then approves the new exact digest."""
    from . import service as s
    s.require(payload.get("review_protocol_version") == 2 and isinstance(payload.get("hosted"), dict))
    hosted = payload["hosted"]
    fields = hosted["controls"]
    by_id = {field["id"]: field for field in fields}
    s.require(isinstance(answers, dict) and set(answers) <= set(by_id) and len(answers) <= 37)
    assert isinstance(answers, dict)
    s.require(all(isinstance(value, str) and 0 < len(value) <= 255 and value.strip() == value for value in answers.values()))
    for identity, value in answers.items():
        field = by_id[identity]
        if field["type"] == "combobox":
            s.require(value in {option["value"] for option in field["options"]})
        elif field["type"] == "checkbox":
            s.require(value in {"true", "false"})
        elif field["type"] == "button":
            s.require(value == str(len(hosted["rows"]) + 1))
        elif "-year-" in identity:
            s.require(re.fullmatch(r"[1-9][0-9]{3}", value) is not None)
    for row in hosted["rows"]:
        index = row["index"]
        role = answers.get(f"current-role-{index}_1", str(row["current_role"]).lower())
        end = (f"end-date-month-{index}", f"end-date-year-{index}")
        s.require(role != "true" or not any(key in answers for key in end))
        merged = {**hosted["values"], **answers}
        start_month, start_year = (merged.get(f"start-date-{kind}-{index}", "") for kind in ("month", "year"))
        end_month, end_year = (merged.get(key, "") for key in end)
        if role == "false" and all((start_month, start_year, end_month, end_year)):
            month_index = {option["value"]: number for number, option in enumerate(MONTHS)}
            s.require(re.fullmatch(r"[1-9][0-9]{3}", start_year) is not None and re.fullmatch(r"[1-9][0-9]{3}", end_year) is not None)
            s.require(start_month in month_index and end_month in month_index
                      and (int(start_year), month_index[start_month]) <= (int(end_year), month_index[end_month]))
    result = copy.deepcopy(payload)
    result["fields"] = [field for field in result["fields"] if field["id"] not in by_id]
    for field in fields:
        identity = field["id"]
        if identity not in answers or answers[identity] == hosted["values"][identity]:
            continue
        before = hosted["values"][identity]
        entry = {**field, "value": answers[identity], "expected_before": before}
        if field["type"] == "combobox":
            labels = {option["value"]: option["label"] for option in field["options"]}
            s.require(before == "" or before in labels)
            entry.update(value_label=labels[answers[identity]], expected_before_label=labels.get(before, ""))
        result["fields"].append(entry)
    s.require(len(result["fields"]) <= MAX_ACTIONS)
    result["reviewed_answers"] = {field["id"]: field["value"] for field in result["fields"]}
    result["reviewed_answers_sha256"] = s.digest({"context_digest": result["context_digest"], "reviewed_answers": result["reviewed_answers"]})
    result["hosted"]["draft_answers"] = copy.deepcopy(answers)
    result["manual_fields"] = [field for field in result["manual_fields"] if field["id"] not in by_id]
    result["manual_fields"].extend({key: field[key] for key in ("id", "label", "required")} for field in fields
                                 if field["required"] and not hosted["values"][field["id"]] and field["id"] not in answers)
    return result
