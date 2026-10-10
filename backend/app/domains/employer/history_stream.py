"""Resumable literal saved-item indexing without retaining resume or job text.

Each invocation bounds returned source bytes and parser/reduction operations.
PostgreSQL's native JSON text/TOAST conversion is separate database work; this
module does not claim that slicing a legacy JSON column bounds that conversion.
Normal paid admission never invokes that conversion once history is ready.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
import re

from sqlalchemy import text

from . import models

VERSION = "employer-history-stream-v1"
FIXED_ITEM_WORK = 256
BLOCK_BYTES = 256
CAPTURE_CHARS = 256  # All archived identity fields fit; text is hashed only.
_ITEM_FIELDS = {
    "charged_credits",
    "owner_id",
    "user_id",
    "search_id",
    "opening_key",
    "identity_recipe",
    "identity_recipe_version",
    "canonical_identity_snapshot",
}
_POSTING_FIELDS = {
    "id",
    "source_id",
    "external_id",
    "platform",
    "opening_key",
    "owner_id",
    "user_id",
    "search_id",
    "region",
    "board_token",
    "employer_key",
    "requisition_id",
    "identity_recipe",
    "identity_recipe_version",
    "canonical_identity_snapshot",
}


def _hash(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _start_hash() -> dict:
    return {"chain": _hash(b"literal-string-v1"), "tail": "", "bytes": 0}


def _append_hash(state: dict, value: str) -> None:
    raw = bytes.fromhex(state["tail"]) + value.encode("utf-8")
    state["bytes"] += len(value.encode("utf-8"))
    while len(raw) >= BLOCK_BYTES:
        state["chain"] = _hash(bytes.fromhex(state["chain"]) + raw[:BLOCK_BYTES])
        raw = raw[BLOCK_BYTES:]
    state["tail"] = raw.hex()


def _finish_hash(state: dict) -> str:
    return _hash(
        bytes.fromhex(state["chain"])
        + bytes.fromhex(state["tail"])
        + state["bytes"].to_bytes(16, "big")
    )


def _node(db, progress, identity):
    return db.get(
        models.EmployerSearchHistoryNode,
        (progress.search_id, progress.stream_generation, identity),
    )


def _state(node) -> dict:
    return dict(node.hash_checkpoint or {})


def _checkpoint(progress) -> dict:
    return deepcopy(
        progress.parser_checkpoint
        or {
            "version": VERSION,
            "next_node": 1,
            "container": None,
            "root": None,
            "lex": None,
            "reduce": None,
            "finish_item": None,
            "done": False,
            "invalid": None,
        }
    )


def _invalid(cp, reason: str) -> None:
    cp["invalid"] = cp.get("invalid") or reason


def _create(db, progress, cp, kind):
    parent = _node(db, progress, cp["container"]) if cp["container"] else None
    state = _state(parent) if parent else {}
    member = (
        state.pop("pending_key", None) if parent and parent.kind == "object" else None
    )
    index = parent.child_count if parent else 0
    node = models.EmployerSearchHistoryNode(
        search_id=progress.search_id,
        generation=progress.stream_generation,
        node_id=cp["next_node"],
        parent_node_id=parent.node_id if parent else None,
        member_key_hash=member["digest"] if member else None,
        member_key=member["value"]
        if member and member["value"] is not None and len(member["value"]) <= 64
        else None,
        member_index=index,
        kind=kind,
        child_count=0,
        hash_checkpoint={},
        parser_expect="key_or_end"
        if kind == "object"
        else "value_or_end"
        if kind == "array"
        else "scalar",
    )
    cp["next_node"] += 1
    if parent:
        parent.child_count += 1
        parent.parser_expect = "comma_or_end"
        parent.hash_checkpoint = state
    else:
        if cp["root"] is not None:
            _invalid(cp, "snapshot_multiple_roots")
        cp["root"] = node.node_id
        if kind != "array":
            _invalid(cp, "snapshot_items_not_array")
    db.add(node)
    db.flush([node])
    return node


def _start_string(db, progress, cp, *, key=False):
    node = None if key else _create(db, progress, cp, "string")
    cp["lex"] = {
        "kind": "string",
        "node": node.node_id if node else None,
        "key": key,
        "capture": "",
        "truncated": False,
        "escape": False,
        "unicode": None,
        "surrogate": None,
        "hash": _start_hash(),
        "retain": key or node.member_key in _ITEM_FIELDS | _POSTING_FIELDS,
    }


def _string_char(cp, lex, char):
    try:
        _append_hash(lex["hash"], char)
    except UnicodeError:
        _invalid(cp, "snapshot_unicode_invalid")
        _append_hash(lex["hash"], "\ufffd")
    if lex["retain"] and not lex["truncated"]:
        if len(lex["capture"]) + len(char) <= CAPTURE_CHARS:
            lex["capture"] += char
        else:
            lex["capture"] = ""
            lex["truncated"] = True


def _finish_string(db, progress, cp, lex):
    if lex["surrogate"] is not None:
        _invalid(cp, "snapshot_unicode_invalid")
    value = None if lex["truncated"] or not lex["retain"] else lex["capture"]
    digest = _finish_hash(lex["hash"])
    if lex["key"]:
        parent = _node(db, progress, cp["container"])
        duplicate = (
            db.query(models.EmployerSearchHistoryNode.node_id)
            .filter_by(
                search_id=progress.search_id,
                generation=progress.stream_generation,
                parent_node_id=parent.node_id,
                member_key_hash=digest,
            )
            .limit(1)
            .first()
        )
        if duplicate:
            _invalid(cp, "snapshot_duplicate_object_key")
        state = _state(parent)
        state["pending_key"] = {"digest": digest, "value": value}
        parent.hash_checkpoint = state
        parent.parser_expect = "colon"
    else:
        node = _node(db, progress, lex["node"])
        node.content_digest = digest
        node.small_value = {"value": value, "truncated": lex["truncated"]}
        _scalar_done(db, progress, cp, node)
    cp["lex"] = None


def _scalar_done(db, progress, cp, node):
    if node.parent_node_id == cp["root"]:
        _invalid(cp, "snapshot_item_invalid")
        cp["finish_item"] = node.node_id
    if node.parent_node_id is None:
        cp["done"] = True


def _finish_primitive(db, progress, cp, lex):
    token = lex["capture"]
    if lex["truncated"]:
        # Generated archived contracts have bounded SQL-integer charges and
        # finite Python-scored numbers. Preserve other enormous literal tokens
        # by digest; they never become a retained amount/identity field.
        value, kind = None, "number"
        digest = _finish_hash(lex["hash"])
    else:
        try:
            value = json.loads(token)
        except (ValueError, RecursionError):
            _invalid(cp, "snapshot_json_invalid")
            value = None
        kind = (
            "boolean" if type(value) is bool else "null" if value is None else "number"
        )
        if type(value) is float and not math.isfinite(value):
            _invalid(cp, "snapshot_nonfinite_number")
        digest = _hash(
            (
                kind + ":" + json.dumps(value, separators=(",", ":"), allow_nan=True)
            ).encode()
        )
    node = _node(db, progress, lex["node"])
    node.kind = kind
    node.content_digest = digest
    node.small_value = {"value": value, "truncated": lex["truncated"]}
    _scalar_done(db, progress, cp, node)
    cp["lex"] = None


def _children(db, progress, parent, names):
    # Match the full key-index prefix. LIMIT alone cannot bound scans through
    # arbitrary unknown sibling keys in a large literal object.
    digests = []
    for name in sorted(names):
        state = _start_hash()
        _append_hash(state, name)
        digests.append(_finish_hash(state))
    nodes = (
        db.query(models.EmployerSearchHistoryNode)
        .filter(
            models.EmployerSearchHistoryNode.search_id == progress.search_id,
            models.EmployerSearchHistoryNode.generation == progress.stream_generation,
            models.EmployerSearchHistoryNode.parent_node_id == parent,
            models.EmployerSearchHistoryNode.member_key_hash.in_(digests),
            models.EmployerSearchHistoryNode.member_key.in_(names),
        )
        .limit(len(names) + 1)
        .all()
    )
    if len(nodes) > len(names):
        raise ValueError("Duplicate saved snapshot fields exceeded fixed extraction")
    return {node.member_key: node for node in nodes}


def _retained(node, field):
    marker = {"untrusted_embedded_value": True}
    # Presence of these undefined recipe fields alone requires review. Their
    # text adds no authority and must not inflate the bounded facts row.
    if field in {
        "identity_recipe",
        "identity_recipe_version",
        "canonical_identity_snapshot",
        "region",
        "board_token",
        "employer_key",
        "requisition_id",
    }:
        return marker
    if node.kind in {"object", "array"}:
        return marker
    scalar = node.small_value or {}
    if scalar.get("truncated"):
        return {"oversized_archived_scalar": True}
    value = scalar.get("value")
    limits = {"id": 64, "source_id": 64, "platform": 40, "search_id": 64}
    if field in limits and (
        type(value) is not str
        or not 0 < len(value) <= limits[field]
        or re.fullmatch(r"[A-Za-z0-9_.:-]+", value) is None
    ):
        return marker
    if field == "external_id" and (type(value) is not str or not 0 < len(value) <= 160):
        return marker
    if field == "opening_key" and (
        type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None
    ):
        return marker
    if field in {"owner_id", "user_id", "charged_credits"} and (
        type(value) is not int or not -(2**31) <= value < 2**31
    ):
        # These archived leaves correspond to native signed Integer columns.
        # Malformed arbitrary-precision JSON numbers cannot inflate facts or
        # displace valid native paid/user evidence.
        return marker
    return value


def _finish_item(db, progress, cp, node):
    if cp["invalid"]:
        return
    if node.kind != "object":
        _invalid(cp, "snapshot_item_invalid")
        return
    item_nodes = _children(db, progress, node.node_id, _ITEM_FIELDS | {"posting"})
    posting_node = item_nodes.pop("posting", None)
    if posting_node is None or posting_node.kind != "object":
        _invalid(cp, "snapshot_item_invalid")
        return
    posting_nodes = _children(db, progress, posting_node.node_id, _POSTING_FIELDS)
    posting = {key: _retained(value, key) for key, value in posting_nodes.items()}
    identity = posting.get("id")
    if type(identity) is not str or not 0 < len(identity) <= 64:
        _invalid(cp, "snapshot_posting_id_invalid")
        return
    facts = {key: _retained(value, key) for key, value in item_nodes.items()}
    facts["posting"] = posting
    key = (progress.search_id, progress.stream_generation, identity)
    seen = db.get(models.EmployerSearchHistorySeen, key)
    if seen is None:
        seen = models.EmployerSearchHistorySeen(
            search_id=progress.search_id,
            generation=progress.stream_generation,
            posting_id=identity,
            first_ordinal=node.member_index,
            occurrence_count=1,
            content_digest=node.content_digest,
            facts=facts,
            opening_key=posting.get("opening_key")
            if type(posting.get("opening_key")) is str
            and len(posting["opening_key"]) == 64
            else None,
            conflicting=False,
        )
        db.add(seen)
    else:
        seen.occurrence_count += 1
        if seen.content_digest != node.content_digest:
            seen.conflicting = True
            seen.static_reason = "duplicate_items_conflict"
    progress.next_ordinal = max(progress.next_ordinal, node.member_index + 1)


def _reduce(db, progress, cp, work_left):
    node = _node(db, progress, cp["reduce"])
    state = _state(node)
    state.setdefault("chain", _hash((node.kind + "-children-v1").encode()))
    state.setdefault("cursor", None)
    query = db.query(models.EmployerSearchHistoryNode).filter_by(
        search_id=progress.search_id,
        generation=progress.stream_generation,
        parent_node_id=node.node_id,
    )
    if node.kind == "object":
        if state["cursor"] is not None:
            query = query.filter(
                models.EmployerSearchHistoryNode.member_key_hash > state["cursor"]
            )
        query = query.order_by(models.EmployerSearchHistoryNode.member_key_hash)
    else:
        if state["cursor"] is not None:
            query = query.filter(
                models.EmployerSearchHistoryNode.member_index > state["cursor"]
            )
        query = query.order_by(models.EmployerSearchHistoryNode.member_index)
    maximum = min(64, max(1, work_left // 8))
    children = query.limit(maximum + 1).all()
    for child in children[:maximum]:
        if child.content_digest is None:
            _invalid(cp, "snapshot_unfinished_child")
            child_digest = _hash(b"invalid-child")
        else:
            child_digest = child.content_digest
        member = (
            child.member_key_hash
            if node.kind == "object"
            else child.member_index.to_bytes(16, "big").hex()
        )
        state["chain"] = _hash(
            bytes.fromhex(state["chain"])
            + bytes.fromhex(member)
            + bytes.fromhex(child_digest)
        )
        state["cursor"] = (
            child.member_key_hash if node.kind == "object" else child.member_index
        )
    node.hash_checkpoint = state
    used = max(1, len(children[:maximum]) * 8)
    if len(children) <= maximum:
        node.content_digest = _hash(
            bytes.fromhex(state["chain"]) + node.child_count.to_bytes(16, "big")
        )
        cp["reduce"] = None
        cp["container"] = node.parent_node_id
        if node.parent_node_id == cp["root"]:
            cp["finish_item"] = node.node_id
        if node.parent_node_id is None:
            cp["done"] = True
    return used


def advance_snapshot(db, progress, *, byte_budget=16_384, work_budget=32_768):
    """Advance a persisted parser, retaining unfinished history as unfinished.

    The source revision is checked by the caller before and after this work.
    Byte offsets refer to UTF8 bytes, not PostgreSQL character offsets.
    """
    if byte_budget < 32 or work_budget < FIXED_ITEM_WORK + 16:
        raise ValueError("History chunks require 32 bytes and 272 work units")
    cp = _checkpoint(progress)
    work, consumed = 0, 0

    def finish_pending():
        nonlocal work
        if cp.get("finish_item") is not None and work + FIXED_ITEM_WORK <= work_budget:
            _finish_item(db, progress, cp, _node(db, progress, cp["finish_item"]))
            cp["finish_item"] = None
            work += FIXED_ITEM_WORK

    finish_pending()
    while cp["reduce"] is not None and work + 16 <= work_budget:
        work += _reduce(db, progress, cp, work_budget - work)
        finish_pending()
    if cp["reduce"] is not None or cp.get("finish_item") is not None:
        progress.parser_checkpoint = cp
        return {"bytes": 0, "work": work, "complete": False}
    dialect = db.get_bind().dialect.name
    expression = (
        "substring(convert_to(CAST(items AS TEXT),'UTF8') FROM :offset FOR :size)"
        if dialect == "postgresql"
        else "substr(CAST(items AS BLOB), :offset, :size)"
    )
    raw = db.execute(
        text(
            f"SELECT {expression} FROM employer_searches WHERE id=:search AND user_id=:owner"
        ),
        {
            "offset": progress.byte_offset + 1,
            "size": byte_budget,
            "search": progress.search_id,
            "owner": progress.user_id,
        },
    ).scalar_one_or_none()
    raw = bytes(raw or b"")
    assert len(raw) <= byte_budget
    try:
        value = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        if error.reason == "unexpected end of data" and error.end == len(raw):
            value = raw[: error.start].decode("utf-8")
        else:
            _invalid(cp, "snapshot_unicode_invalid")
            value = raw.decode("utf-8", errors="replace")
    index = 0
    while (
        index < len(value)
        and work + 16 <= work_budget
        and cp["reduce"] is None
        and cp.get("finish_item") is None
    ):
        char = value[index]
        if cp["lex"]:
            lex = cp["lex"]
            if lex["kind"] == "string":
                if lex["unicode"] is not None:
                    lex["unicode"] += char
                    if len(lex["unicode"]) == 4:
                        try:
                            code = int(lex["unicode"], 16)
                        except ValueError:
                            _invalid(cp, "snapshot_json_invalid")
                            code = 0xFFFD
                        lex["unicode"] = None
                        if 0xD800 <= code <= 0xDBFF:
                            if lex["surrogate"] is not None:
                                _invalid(cp, "snapshot_unicode_invalid")
                            lex["surrogate"] = code
                        elif 0xDC00 <= code <= 0xDFFF and lex["surrogate"] is not None:
                            _string_char(
                                cp,
                                lex,
                                chr(
                                    0x10000
                                    + ((lex["surrogate"] - 0xD800) << 10)
                                    + code
                                    - 0xDC00
                                ),
                            )
                            lex["surrogate"] = None
                        else:
                            if lex["surrogate"] is not None:
                                _invalid(cp, "snapshot_unicode_invalid")
                                lex["surrogate"] = None
                            _string_char(cp, lex, chr(code))
                elif lex["escape"]:
                    lex["escape"] = False
                    if char == "u":
                        lex["unicode"] = ""
                    else:
                        escaped = {
                            '"': '"',
                            "\\": "\\",
                            "/": "/",
                            "b": "\b",
                            "f": "\f",
                            "n": "\n",
                            "r": "\r",
                            "t": "\t",
                        }.get(char)
                        if escaped is None:
                            _invalid(cp, "snapshot_json_invalid")
                        else:
                            _string_char(cp, lex, escaped)
                elif char == "\\":
                    lex["escape"] = True
                elif char == '"':
                    _finish_string(db, progress, cp, lex)
                else:
                    if ord(char) < 32:
                        _invalid(cp, "snapshot_json_invalid")
                    if lex["surrogate"] is not None:
                        _invalid(cp, "snapshot_unicode_invalid")
                        lex["surrogate"] = None
                    _string_char(cp, lex, char)
            elif char in " \r\n\t,]}":
                _finish_primitive(db, progress, cp, lex)
                work += 8
                continue
            else:
                _append_hash(lex["hash"], char)
                if not lex["truncated"]:
                    if len(lex["capture"]) < CAPTURE_CHARS:
                        lex["capture"] += char
                    else:
                        lex["capture"] = ""
                        lex["truncated"] = True
        elif char not in " \r\n\t":
            parent = _node(db, progress, cp["container"]) if cp["container"] else None
            expected = (
                parent.parser_expect if parent else "done" if cp["done"] else "root"
            )
            if expected == "colon":
                if char != ":":
                    _invalid(cp, "snapshot_json_invalid")
                parent.parser_expect = "value"
            elif expected == "comma_or_end":
                closing = "}" if parent.kind == "object" else "]"
                if char == closing:
                    cp["reduce"] = parent.node_id
                elif char == ",":
                    parent.parser_expect = "key" if parent.kind == "object" else "value"
                else:
                    _invalid(cp, "snapshot_json_invalid")
            elif expected in {"key", "key_or_end"}:
                if char == '"':
                    _start_string(db, progress, cp, key=True)
                elif char == "}" and expected == "key_or_end":
                    cp["reduce"] = parent.node_id
                else:
                    _invalid(cp, "snapshot_json_invalid")
            elif expected == "value_or_end" and char == "]":
                cp["reduce"] = parent.node_id
            elif expected in {"root", "value", "value_or_end"}:
                if char in "{[":
                    node = _create(
                        db, progress, cp, "object" if char == "{" else "array"
                    )
                    cp["container"] = node.node_id
                    work += 8
                elif char == '"':
                    _start_string(db, progress, cp)
                    work += 8
                elif char in "-0123456789tfn":
                    node = _create(db, progress, cp, "number")
                    cp["lex"] = {
                        "kind": "primitive",
                        "node": node.node_id,
                        "capture": char,
                        "truncated": False,
                        "hash": _start_hash(),
                    }
                    _append_hash(cp["lex"]["hash"], char)
                    work += 8
                else:
                    _invalid(cp, "snapshot_json_invalid")
            else:
                _invalid(cp, "snapshot_trailing_data")
        index += 1
        consumed += len(char.encode("utf-8"))
        work += 1
    progress.byte_offset += consumed
    while cp["reduce"] is not None and work + 16 <= work_budget:
        work += _reduce(db, progress, cp, work_budget - work)
        finish_pending()
    finish_pending()
    if not raw and not cp["done"] and cp["reduce"] is None:
        _invalid(cp, "snapshot_json_incomplete")
        cp["done"] = True
    if (
        cp["done"]
        and cp["reduce"] is None
        and cp.get("finish_item") is None
        and progress.byte_offset >= progress.source_size_bytes
    ):
        progress.state = "unresolved" if cp["invalid"] else "complete"
        progress.unresolved_count = int(bool(cp["invalid"]))
        root = _node(db, progress, cp["root"]) if cp["root"] else None
        progress.source_sha256 = root.content_digest if root else None
    progress.parser_checkpoint = cp
    return {
        "bytes": consumed,
        "returned_bytes": len(raw),
        "work": work,
        "complete": progress.state in {"complete", "unresolved"},
    }
