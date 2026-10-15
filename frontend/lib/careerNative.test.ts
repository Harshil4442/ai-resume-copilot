import { describe, expect, it } from "vitest";
import { getSourcePreservingContent } from "./career";

describe("native resume review contract", () => {
  it.each(["tex", "texzip"])("retains %s before and after edits for review", (source_format) => {
    const content = { format_preservation: "source", source_format, source_edits: [{ unit_id: "native:1", original_text: "Developed Python services", replacement_text: "Built Python services", reason: "Clearer action wording", evidence_ids: ["approved"] }] };
    expect(getSourcePreservingContent(content)).toEqual(content);
    expect(getSourcePreservingContent({ ...content, source_edits: [] })?.source_edits).toEqual([]);
  });
  it("refuses unknown formats and malformed evidence references", () => {
    expect(getSourcePreservingContent({ format_preservation: "source", source_format: "html", source_edits: [] })).toBeNull();
    expect(getSourcePreservingContent({ format_preservation: "source", source_format: "tex", source_edits: [{ unit_id: "x", original_text: "a", replacement_text: "b", reason: "c", evidence_ids: [1] }] })).toBeNull();
  });
});
