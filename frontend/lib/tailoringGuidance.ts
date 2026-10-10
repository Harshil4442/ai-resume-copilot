import type { components } from "./generated/api";

type Guidance = components["schemas"]["TailoringFailureGuidance"];
type Reason = Guidance["reason_code"];
type Copy = { title: string; nextStep: string };

// Product copy is static. Never render provider messages, source text or an
// unrecognized server value as an explanation or navigation destination.
const fallback: Copy = {
  title: "Could not verify safe, useful changes for this role.",
  nextStep: "Keep your original resume, choose a custom file, or review your source and approved evidence before requesting another version.",
};
const messages: Record<Reason, Copy> = {
  source_required: { title: "Your original resume file is needed.", nextStep: "Upload and select the source resume before requesting a tailored version. You can also choose a custom resume." },
  editable_source_required: { title: "This source has no safely editable text.", nextStep: "Upload an editable DOCX or supported LaTeX source project, edit the file yourself, or keep the original resume." },
  source_unreadable: { title: "The source file could not be read safely.", nextStep: "Open the original file to check it, then upload a readable PDF, DOCX or supported LaTeX project. You can choose a custom resume instead." },
  source_too_large: { title: "The source exceeds the supported editing limits.", nextStep: "Use a smaller source file or a supported source project. Keep your original or choose a custom resume for this application." },
  source_protected: { title: "This file cannot be edited while retaining its protections.", nextStep: "Keep the signed or protected original, or choose a separate editable source or custom resume. Editing a signed file would invalidate its signature." },
  source_format_unsupported: { title: "This file type is not supported for source-preserving tailoring.", nextStep: "Upload a supported PDF, DOCX or LaTeX source project, or choose your original/custom resume for the application." },
  font_unsupported: { title: "The proposed wording needs characters the original font cannot preserve.", nextStep: "Edit the source with a supported, readable font and upload it again, or keep your original/custom resume. Existing typography will not be replaced automatically." },
  replacement_does_not_fit: { title: "The proposed wording does not fit the original layout.", nextStep: "Use shorter wording in an editable source, or keep your original/custom resume. We will not shrink fonts or rebuild your layout to force this change." },
  source_layout_changed: { title: "The proposed changes would alter protected layout or content.", nextStep: "Edit a shorter passage in your source and check the result yourself, or keep your original/custom resume." },
  source_span_unavailable: { title: "A proposed edit could not be matched safely to the source.", nextStep: "Edit that passage in the original source yourself, or upload a fresh editable source. You can keep the original/custom resume." },
  source_links_protected: { title: "A proposed change would alter an existing resume link.", nextStep: "Keep the link label and clickable text unchanged when editing your source, or choose the original/custom resume." },
  evidence_changes_unsupported: { title: "The proposed changes could not be supported by approved evidence.", nextStep: "Review and correct your evidence before requesting new wording. Keep dates, numbers and claims accurate, or use your original/custom resume." },
  tex_dependency_unsupported: { title: "The LaTeX project has missing or unsupported dependencies.", nextStep: "Include the required supported source/assets files in your project, or upload your original compiled PDF/custom resume." },
  tex_compile_failed: { title: "The LaTeX project could not be compiled and verified safely.", nextStep: "Check the source, assets, supported fonts/packages and text length, or use the original compiled PDF/custom resume." },
  tex_worker_unavailable: { title: "The isolated LaTeX compiler is unavailable.", nextStep: "Use your original compiled PDF or a custom PDF/DOCX for now. Your source will not be compiled on the web server." },
  tex_artifact_invalid: { title: "The compiled resume could not be verified.", nextStep: "Use your original/custom resume. Do not apply with an unverified generated file." },
  no_supported_changes: { title: "No safe, useful changes were found for this role.", nextStep: "You can keep your original resume unchanged, save a source snapshot, or choose a custom file. Review your evidence before requesting new wording." },
  validation_failed: fallback,
};

export function getTailoringGuidance(value: unknown): Copy {
  if (!value || typeof value !== "object") return fallback;
  const code = (value as { reason_code?: unknown }).reason_code;
  return typeof code === "string" && Object.hasOwn(messages, code) ? messages[code as Reason] : fallback;
}
