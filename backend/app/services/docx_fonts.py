"""Resolve fonts actually used by bounded DOCX text without font substitution.

This module only inspects already-scanned OOXML. Ambiguous selection, unsupported
generated text or missing theme/style dependencies refuse; no file is rewritten.
Installed font resolution belongs to the isolated renderer, not the API host.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from lxml import etree

from .docx_layout_validation import DocxLayoutError, _compact, _document

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
P = "{http://schemas.openxmlformats.org/package/2006/relationships}"
SLOTS = {"ascii": "asciiTheme", "hAnsi": "hAnsiTheme", "eastAsia": "eastAsiaTheme", "cs": "cstheme"}
MAX_FONTS = 32
MAX_TEXT = 200_000
FontKey = tuple[str, bool, bool]
TRANSFORMS = ("caps", "smallCaps", "vanish", "webHidden", "specVanish")
STYLE_TOGGLES = frozenset({"b", "i", "bCs", "iCs", "caps", "smallCaps", "vanish"})


def require(value) -> None:
    if not value:
        raise DocxLayoutError("unsupported_source_font")


def _xml(raw: bytes):
    require(len(raw) <= 10 * 1024 * 1024)
    tree = etree.fromstring(raw, etree.XMLParser(resolve_entities=False, no_network=True,
                                               load_dtd=False, huge_tree=False))
    require(not tree.getroottree().docinfo.doctype)
    nodes = list(tree.iter())
    require(len(nodes) <= 100_000 and all(len(list(node.iterancestors())) <= 64 for node in nodes))
    return tree


def _on(node) -> bool:
    return _boolean(node.get(W + "val", "true"))


def _boolean(value: str) -> bool:
    require(value in {"true", "false", "1", "0", "on", "off"})
    return value in {"true", "1", "on"}


def _properties(state: dict, rpr, *, style: bool = False) -> dict:
    result = dict(state)
    if rpr is None:
        return result
    fonts = rpr.find(W + "rFonts")
    if fonts is not None:
        require(fonts.get(W + "hint", "default") == "default")
        for slot, themed in SLOTS.items():
            if fonts.get(W + themed) is not None:
                result[slot] = ("theme", fonts.get(W + themed))
            elif fonts.get(W + slot) is not None:
                result[slot] = ("literal", fonts.get(W + slot))
    for name in ("b", "i", "bCs", "iCs", "cs", "rtl", *TRANSFORMS):
        node = rpr.find(W + name)
        if node is not None:
            value = _on(node)
            # OOXML style toggle properties include caps/smallCaps/vanish;
            # direct run values are absolute. Resolve the full cascade before
            # declining an effective visible transformation.
            if style and name in STYLE_TOGGLES:
                result[name] = not result.get(name, False) if value else result.get(name, False)
            else:
                result["force_cs" if name == "cs" else name] = value
    lang = rpr.find(W + "lang")
    if lang is not None:
        for name in ("val", "eastAsia", "bidi"):
            if lang.get(W + name):
                result["lang_" + name] = lang.get(W + name)
    return result


def _inactive_table_conditions(table) -> set[str]:
    """Ignore only formatting explicitly disabled by this table's own look.

    Cell/row cnfStyle overrides and inherited/active condition selection still
    require a separate resolver. Named tblLook attributes take precedence over
    its legacy mask; absent named flags remain unknown here, not assumed off.
    """
    if any(True for _ in table.iter(W + "cnfStyle")):
        return set()
    looks = table.findall(W + "tblPr/" + W + "tblLook")
    require(len(looks) <= 1)
    if not looks:
        return set()
    look = looks[0]
    bits = {"firstRow": 0x20, "lastRow": 0x40, "firstColumn": 0x80,
            "lastColumn": 0x100, "noHBand": 0x200, "noVBand": 0x400}
    named = {name: look.get(W + name) for name in bits if look.get(W + name) is not None}
    if named:
        flags = {name: _boolean(value) for name, value in named.items()}
    else:
        value = look.get(W + "val")
        if value is None:
            return set()
        require(re.fullmatch(r"[0-9a-fA-F]{4}", value))
        flags = {name: bool(int(value, 16) & bit) for name, bit in bits.items()}
    inactive = {condition for condition, flag in (("firstRow", "firstRow"), ("lastRow", "lastRow"),
                ("firstCol", "firstColumn"), ("lastCol", "lastColumn")) if flags.get(flag) is False}
    for flag, conditions in (("noHBand", ("band1Horz", "band2Horz")),
                             ("noVBand", ("band1Vert", "band2Vert"))):
        if flags.get(flag) is True:
            inactive.update(conditions)
    # Corners are certainly absent when both associated row/column options are
    # explicitly off. Leave all other corner selection ambiguous for now.
    for condition, row, column in (("nwCell", "firstRow", "firstColumn"),
                                   ("neCell", "firstRow", "lastColumn"),
                                   ("swCell", "lastRow", "firstColumn"),
                                   ("seCell", "lastRow", "lastColumn")):
        if flags.get(row) is False and flags.get(column) is False:
            inactive.add(condition)
    return inactive


def _slot(char: str, state: dict) -> str:
    code = ord(char)
    if state.get("force_cs") or state.get("rtl"):
        return "cs"
    if code < 128:
        return "ascii"
    if (0x590 <= code <= 0x8FF or 0x900 <= code <= 0xDFF
            or 0xFB1D <= code <= 0xFDFF or 0xFE70 <= code <= 0xFEFF):
        return "cs"
    if (0x1100 <= code <= 0x11FF or 0x2E80 <= code <= 0xA4CF
            or 0xAC00 <= code <= 0xD7AF or 0xF900 <= code <= 0xFAFF
            or 0xFF00 <= code <= 0xFFEF or 0x20000 <= code <= 0x3134F):
        return "eastAsia"
    # Latin, Greek, Cyrillic and ordinary typographic punctuation use hAnsi.
    require(code <= 0x58F or 0x1E00 <= code <= 0x206F or 0x20A0 <= code <= 0x214F)
    return "hAnsi"


def _theme_family(theme, token: str, slot: str, state: dict) -> str:
    require(token in {"majorAscii", "majorHAnsi", "majorEastAsia", "majorBidi",
                      "minorAscii", "minorHAnsi", "minorEastAsia", "minorBidi"})
    require(theme is not None)
    node = theme.find(".//" + A + ("majorFont" if token.startswith("major") else "minorFont"))
    require(node is not None)
    name = "latin" if token.endswith(("Ascii", "HAnsi")) else "ea" if token.endswith("EastAsia") else "cs"
    face = node.find(A + name)
    if face is not None and face.get("typeface"):
        return face.get("typeface")
    language = state.get("lang_eastAsia" if slot == "eastAsia" else "lang_bidi", "").lower()
    scripts = {"ja": "Jpan", "ko": "Hang", "ar": "Arab", "he": "Hebr", "hi": "Deva", "th": "Thai"}
    script = ("Hant" if language in {"zh-tw", "zh-hk", "zh-mo"} else "Hans") if language.startswith("zh-") else scripts.get(language.split("-")[0])
    matches = [row.get("typeface") for row in node.findall(A + "font") if row.get("script") == script]
    require(script is not None and len(matches) == 1 and matches[0])
    return matches[0]


@dataclass(frozen=True)
class FontUse:
    # One paragraph at a time keeps matching bounded and preserves run fonts.
    paragraphs: tuple[tuple[tuple[str, FontKey], ...], ...]
    required: dict[FontKey, frozenset[int]]


def document_fonts(content: bytes) -> FontUse:
    try:
        parts, document = _document(content)
        styles = _xml(parts["word/styles.xml"]) if "word/styles.xml" in parts else etree.Element(W + "styles")
        by_id = {}
        defaults = {}
        for row in styles.findall(W + "style"):
            key, kind = row.get(W + "styleId"), row.get(W + "type")
            require(key and key not in by_id and kind in {"paragraph", "character", "table", "numbering"})
            by_id[key] = row
            if row.get(W + "default") in {"1", "true", "on"}:
                require(kind not in defaults)
                defaults[kind] = key

        def chain(key, kind):
            found = []
            seen: set[str] = set()
            while key is not None:
                require(key in by_id and key not in seen and len(seen) < 32)
                seen.add(key)
                row = by_id[key]
                require(row.get(W + "type") == kind)
                found.append(row)
                base = row.find(W + "basedOn")
                key = base.get(W + "val") if base is not None else None
            return list(reversed(found))

        rels = _xml(parts["word/_rels/document.xml.rels"]) if "word/_rels/document.xml.rels" in parts else []
        related = {}
        theme = None
        for rel in rels:
            if rel.tag != P + "Relationship" or rel.get("TargetMode") == "External":
                continue
            if rel.get("Type") not in {R[1:-1] + "/" + kind for kind in ("theme", "header", "footer")}:
                continue
            path = PurePosixPath("word") / rel.get("Target", "")
            require(not path.is_absolute() and ".." not in path.parts)
            related[rel.get("Id")] = str(path)
            if rel.get("Type", "").endswith("/theme"):
                require(theme is None and str(path) in parts)
                theme = _xml(parts[str(path)])
        roots = [document]
        for tag in ("headerReference", "footerReference"):
            for row in document.iter(W + tag):
                path = related.get(row.get(R + "id"))
                require(path in parts)
                assert path is not None
                root = _xml(parts[path])
                if not any(etree.tostring(existing) == etree.tostring(root) for existing in roots[1:]):
                    roots.append(root)
        require(len(roots) <= 65)
        base = _properties({}, styles.find(W + "docDefaults/" + W + "rPrDefault/" + W + "rPr"))
        if "word/settings.xml" in parts:
            settings = _xml(parts["word/settings.xml"])
            language = settings.find(W + "themeFontLang")
            if language is not None:
                for name in ("val", "eastAsia", "bidi"):
                    if language.get(W + name):
                        base["lang_" + name] = language.get(W + name)
        paragraphs: list[tuple[tuple[str, FontKey], ...]] = []
        points: dict[FontKey, set[int]] = {}
        inactive_tables: dict[Any, set[str]] = {}
        total = 0
        for root in roots:
            # Fields, symbols, list numbering and DrawingML text need their own
            # exact font-selection/rendered-value resolver; never infer it.
            require(not any(row.tag in {W + "fldSimple", W + "instrText", W + "sym", A + "t"} for row in root.iter()))
            for paragraph in root.iter(W + "p"):
                require(len(paragraphs) < 5000)
                state = dict(base)
                tables = [node for node in paragraph.iterancestors() if node.tag == W + "tbl"]
                if tables:
                    require(len(tables) == 1)
                    table_style = tables[0].find(W + "tblPr/" + W + "tblStyle")
                    table_id = table_style.get(W + "val") if table_style is not None else defaults.get("table")
                    if tables[0] not in inactive_tables:
                        inactive_tables[tables[0]] = _inactive_table_conditions(tables[0])
                    for style in chain(table_id, "table"):
                        state = _properties(state, style.find(W + "rPr"), style=True)
                        for conditional in style.findall(W + "tblStylePr"):
                            rpr = conditional.find(W + "rPr")
                            if rpr is not None:
                                if conditional.get(W + "type") in inactive_tables[tables[0]]:
                                    continue
                                require(conditional.get(W + "type") == "wholeTable")
                                state = _properties(state, rpr, style=True)
                ppr = paragraph.find(W + "pPr")
                style_node = ppr.find(W + "pStyle") if ppr is not None else None
                paragraph_id = style_node.get(W + "val") if style_node is not None else defaults.get("paragraph")
                for style in chain(paragraph_id, "paragraph"):
                    require(style.find(W + "pPr/" + W + "numPr") is None)
                    state = _properties(state, style.find(W + "rPr"), style=True)
                require(ppr is None or ppr.find(W + "numPr") is None)
                glyphs = []
                for run in paragraph.iter(W + "r"):
                    if next((a for a in run.iterancestors() if a.tag == W + "p"), None) is not paragraph:
                        continue
                    rpr = run.find(W + "rPr")
                    selected = dict(state)
                    character = rpr.find(W + "rStyle") if rpr is not None else None
                    character_id = character.get(W + "val") if character is not None else defaults.get("character")
                    for style in chain(character_id, "character"):
                        selected = _properties(selected, style.find(W + "rPr"), style=True)
                    selected = _properties(selected, rpr)
                    text = "".join(row.text or "" for row in run.iter(W + "t"))
                    if any(not char.isspace() for char in text):
                        require(not any(selected.get(name, False) for name in TRANSFORMS))
                    selected_faces: dict[str, FontKey] = {}
                    for char in text:
                        if char.isspace():
                            continue
                        slot = _slot(char, selected)
                        if slot not in selected_faces:
                            family = selected.get(slot)
                            require(family is not None)
                            assert family is not None
                            family = _theme_family(theme, family[1], slot, selected) if family[0] == "theme" else family[1]
                            require(type(family) is str and 0 < len(family) <= 128 and family == family.strip()
                                    and not re.search(r"[\x00-\x1f\x7f\\:,=]", family))
                            complex_script = slot == "cs"
                            selected_faces[slot] = (family, bool(selected.get("bCs" if complex_script else "b", False)),
                                                   bool(selected.get("iCs" if complex_script else "i", False)))
                        key = selected_faces[slot]
                        points.setdefault(key, set()).add(ord(char))
                        require(len(points) <= MAX_FONTS)
                        for normalized in _compact(char):
                            glyphs.append((normalized, key))
                            total += 1
                        require(total <= MAX_TEXT)
                if glyphs:
                    paragraphs.append(tuple(glyphs))
        require(paragraphs)
        return FontUse(tuple(paragraphs), {key: frozenset(value) for key, value in points.items()})
    except DocxLayoutError:
        raise
    except Exception:
        raise DocxLayoutError("unsupported_source_font") from None


def verify_rendered_fonts(use: FontUse, glyphs: list[Any], faces: dict[FontKey, str]) -> None:
    """Refuse ambiguous repeated text rather than swapping font occurrences."""
    text = "".join(glyph["text"] for glyph in glyphs)
    covered: set[int] = set()
    work = 0
    declared: dict[str, tuple] = {}
    counts: Counter[str] = Counter()
    for paragraph in use.paragraphs:
        value = "".join(char for char, _key in paragraph)
        # Part/geometry binding is not available here. Identical paragraph text
        # with different run-font assignments cannot be matched safely, even if
        # every font appears somewhere in the PDF. Identical assignments may
        # repeat (including page headers), but cannot lose source occurrences.
        require(value not in declared or declared[value] == paragraph)
        declared[value] = paragraph
        counts[value] += 1
    for value, paragraph in declared.items():
        offset, matches = 0, 0
        while True:
            found = text.find(value, offset)
            if found < 0:
                break
            offset = found + len(value)
            work += len(value)
            require(work <= MAX_TEXT * 4)
            if all(glyphs[found + index]["font"] == faces[key] for index, (_char, key) in enumerate(paragraph)):
                covered.update(range(found, offset))
                matches += 1
        require(matches >= counts[value])
    require(len(covered) == len(glyphs))
