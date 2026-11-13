"""SQL-only UI eligibility projection; never a native artifact or approval gate.

Matches getSourcePreservingContent's JSON-shape check plus nonempty edits. Full
file/package validation still runs at the existing explicit action boundaries.
All SQL fragments are closed source constants; candidate data is never SQL text.
"""
from sqlalchemy import Boolean, literal_column


def source_preserving_edits(dialect: str):
    column = "resume_versions.structured_content"
    if dialect == "postgresql":
        content = f"CAST({column} AS jsonb)"
        edits = f"CASE WHEN jsonb_typeof({content}->'source_edits')='array' THEN {content}->'source_edits' ELSE '[]'::jsonb END"
        invalid = " OR ".join(f"jsonb_typeof(e.value->'{key}') IS DISTINCT FROM 'string'" for key in
                              ("unit_id", "original_text", "replacement_text", "reason"))
        expression = f"""COALESCE((
            {content}->>'format_preservation'='source'
            AND {content}->>'source_format' IN ('pdf','docx','tex','texzip')
            AND jsonb_array_length({edits})>0
            AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements({edits}) AS e(value)
                WHERE jsonb_typeof(e.value) IS DISTINCT FROM 'object' OR {invalid}
                OR jsonb_typeof(e.value->'evidence_ids') IS DISTINCT FROM 'array'
                OR EXISTS (SELECT 1 FROM jsonb_array_elements(CASE WHEN jsonb_typeof(e.value->'evidence_ids')='array'
                    THEN e.value->'evidence_ids' ELSE '[]'::jsonb END) AS ids(value)
                    WHERE jsonb_typeof(ids.value) IS DISTINCT FROM 'string'))
            AND (NOT ({content} ? 'evidence_needed') OR (
                jsonb_typeof({content}->'evidence_needed')='array'
                AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(CASE WHEN jsonb_typeof({content}->'evidence_needed')='array'
                    THEN {content}->'evidence_needed' ELSE '[]'::jsonb END) AS needed(value)
                    WHERE jsonb_typeof(needed.value) IS DISTINCT FROM 'string')))
        ),FALSE)"""
    elif dialect == "sqlite":
        edits = f"CASE WHEN json_type({column},'$.source_edits')='array' THEN json_extract({column},'$.source_edits') ELSE '[]' END"
        obj = "CASE WHEN e.type='object' THEN e.value ELSE '{}' END"
        invalid = " OR ".join(f"json_type({obj},'$.{key}') IS NOT 'text'" for key in
                              ("unit_id", "original_text", "replacement_text", "reason"))
        expression = f"""COALESCE((
            json_extract({column},'$.format_preservation')='source'
            AND json_extract({column},'$.source_format') IN ('pdf','docx','tex','texzip')
            AND json_array_length({edits})>0
            AND NOT EXISTS (SELECT 1 FROM json_each({edits}) e WHERE e.type IS NOT 'object' OR {invalid}
                OR json_type({obj},'$.evidence_ids') IS NOT 'array'
                OR EXISTS (SELECT 1 FROM json_each(CASE WHEN json_type({obj},'$.evidence_ids')='array'
                    THEN json_extract({obj},'$.evidence_ids') ELSE '[]' END) ids WHERE ids.type IS NOT 'text'))
            AND (json_type({column},'$.evidence_needed') IS NULL OR (
                json_type({column},'$.evidence_needed')='array'
                AND NOT EXISTS (SELECT 1 FROM json_each(CASE WHEN json_type({column},'$.evidence_needed')='array'
                    THEN json_extract({column},'$.evidence_needed') ELSE '[]' END) needed WHERE needed.type IS NOT 'text')))
        ),FALSE)"""
    else:
        raise ValueError("Version metadata projection requires PostgreSQL or owned SQLite fixtures")
    return literal_column(expression, type_=Boolean()).label("source_preserving_edits")
