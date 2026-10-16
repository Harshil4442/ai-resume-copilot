// Candidate-facing validation for the single reviewed Greenhouse hosted flow.
// Server-side native/device/lease verification remains the settlement authority.
export type NativeContext = { tab_id: number; time_zone: string };
export type UploadPlan = { body_provenance: "extension_owned_arraybuffer"; transport: "greenhouse-owned-upload-browser-https-v2"; presign_url: string; storage_origin: string; storage_key: string;
  policy_sha256: string; fields_sha256: string; presign_response_sha256: string; resume_component_sha256: string; expires_at_ms: number; presign_evidence: Record<string, unknown> };
export const HOSTED_RECEIPT = "greenhouse_hosted_browser_https_200_v2";
const PRESIGN = "https://boards.greenhouse.io/uncacheable_attributes/presigned_fields?fields%5B%5D=resume";
const HEX = /^[a-f0-9]{64}$/;
const UUID = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/;
const object = (value: unknown): value is Record<string, unknown> => Boolean(value) && typeof value === "object" && !Array.isArray(value);
const integer = (value: unknown): value is number => Number.isSafeInteger(value);
const text = (value: unknown): value is string => typeof value === "string" && value.length > 0;
const hex = (value: unknown): value is string => text(value) && HEX.test(value);
const canonical = (value: unknown): string => value == null || typeof value !== "object" ? JSON.stringify(value)
  : Array.isArray(value) ? `[${value.map(canonical).join(",")}]` : `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${canonical((value as Record<string, unknown>)[key])}`).join(",")}}`;
const same = (a: unknown, b: unknown) => canonical(a) === canonical(b);
const keys = (value: Record<string, unknown>, expected: string[]) => same(Object.keys(value).sort(), expected.toSorted());
function timeZone(value: unknown): boolean {
  if (!text(value) || !/^[A-Za-z][A-Za-z0-9_+/-]{0,99}$/.test(value)) return false;
  try { new Intl.DateTimeFormat("en", { timeZone: value }); return true; } catch { return false; }
}
function ipAddress(value: unknown): boolean {
  if (!text(value) || value.length > 64) return false;
  if (value.includes(":")) { try { return new URL(`http://[${value}]/`).hostname.startsWith("["); } catch { return false; } }
  const parts = value.split(".");
  return parts.length === 4 && parts.every(part => /^(0|[1-9][0-9]{0,2})$/.test(part) && Number(part) <= 255);
}
function storageKey(value: unknown): value is string {
  return text(value) && value.length <= 1024 && !/[\\?#%\x00-\x1f]/.test(value) && !value.includes("${")
    && value.split("/").every(part => part !== "" && part !== "." && part !== "..");
}
export function uploadedStorage(value: unknown): { origin: string; key: string } | null {
  if (!text(value)) return null;
  try {
    const url = new URL(value), key = decodeURIComponent(url.pathname.slice(1));
    const encoded = encodeURIComponent(key).replace(/[!'()*]/g, character => `%${character.charCodeAt(0).toString(16).toUpperCase()}`).replace(/%2F/g, "/");
    return isStorageOrigin(url.origin) && storageKey(key) && value === `${url.origin}/${encoded}` ? { origin: url.origin, key } : null;
  } catch { return null; }
}
export function isNativeContext(value: unknown): value is NativeContext {
  return object(value) && keys(value, ["tab_id", "time_zone"]) && integer(value.tab_id) && value.tab_id >= 0 && value.tab_id < 2 ** 31
    && timeZone(value.time_zone);
}
export function isStorageOrigin(value: unknown): value is string {
  if (!text(value)) return false;
  try { const url = new URL(value); return url.origin === value && !url.port && /^https:\/\/[a-z0-9][a-z0-9.-]{0,120}\.s3(?:\.[a-z0-9-]+)?\.amazonaws\.com$/.test(value); } catch { return false; }
}
function network(value: unknown, kind: "presign" | "upload", url: string): value is Record<string, unknown> {
  return object(value) && keys(value, ["request_id", "method", "url", "tab_id", "frame_id", "document_id", "initiator", "started_at_ms", "browser_major", "status_code",
    "https_validation", "request_body_source", "native_body_crosscheck", "request_body_sha256", "request_body_size", "completed_at_ms", "from_cache", "redirected", "ip"])
    && text(value.request_id) && value.request_id.length <= 160 && value.url === url && value.method === (kind === "presign" ? "GET" : "POST")
    && value.tab_id === -1 && value.frame_id === -1 && value.document_id === null && value.initiator === "chrome-extension://pnjmegppacleipajnifjmbcopjjffklb"
    && integer(value.started_at_ms) && integer(value.completed_at_ms) && value.started_at_ms > 0 && value.completed_at_ms >= value.started_at_ms
    && value.completed_at_ms - value.started_at_ms <= 10000 && integer(value.browser_major) && value.browser_major >= 144 && value.browser_major <= 999
    && (kind === "presign" ? value.status_code === 200 && value.request_body_sha256 === null && value.request_body_size === 0
      : [200, 201, 204].includes(value.status_code as number) && hex(value.request_body_sha256) && integer(value.request_body_size) && value.request_body_size > 0 && value.request_body_size <= 10 * 1024 * 1024 + 65536)
    && value.request_body_source === (kind === "presign" ? "none" : "extension_owned_arraybuffer")
    && (kind === "presign" ? value.native_body_crosscheck === "none" : ["raw", "form_fields"].includes(value.native_body_crosscheck as string))
    && value.https_validation === "browser_default" && value.from_cache === false && value.redirected === false
    && ipAddress(value.ip);
}
export function isUploadPlan(value: unknown): value is UploadPlan {
  return object(value) && keys(value, ["body_provenance", "transport", "presign_url", "storage_origin", "storage_key", "policy_sha256", "fields_sha256", "presign_response_sha256", "resume_component_sha256", "expires_at_ms", "presign_evidence"])
    && value.body_provenance === "extension_owned_arraybuffer" && value.transport === "greenhouse-owned-upload-browser-https-v2" && value.presign_url === PRESIGN && isStorageOrigin(value.storage_origin)
    && storageKey(value.storage_key)
    && [value.policy_sha256, value.fields_sha256, value.presign_response_sha256, value.resume_component_sha256].every(hex)
    && integer(value.expires_at_ms) && value.expires_at_ms > 0 && value.expires_at_ms <= 8640000000000000 && network(value.presign_evidence, "presign", PRESIGN)
    && value.expires_at_ms > (value.presign_evidence.completed_at_ms as number)
    && value.expires_at_ms <= (value.presign_evidence.started_at_ms as number) + 330000;
}
export function isNativeUpload(value: unknown, payload: Record<string, unknown>): value is Record<string, unknown> {
  if (!object(value) || !object(payload.artifact) || !object(payload.upload) || !isNativeContext(payload.native_context)
    || !text(payload.document_id) || payload.document_id.length > 160 || payload.frame_id !== 0
    || !keys(value, ["version", "review_id", "action_id", "review_digest", "artifact_sha256", "artifact_size_bytes", "filename", "target_url", "document_id", "tab_id", "uploaded_url", "multipart_sha256", "multipart_size", "native"])) return false;
  const artifact = payload.artifact;
  if (!text(value.uploaded_url)) return false;
  const storage = uploadedStorage(value.uploaded_url); if (!storage) return false;
  const origin = storage.origin;
  return value.version === "greenhouse_owned_upload_browser_https_v2" && text(value.review_id) && UUID.test(value.review_id) && text(value.action_id) && UUID.test(value.action_id)
    && value.review_id === payload.upload.review_id && value.action_id === payload.upload.action_id
    && hex(value.review_digest) && value.artifact_sha256 === artifact.sha256 && value.artifact_size_bytes === artifact.size_bytes && value.filename === artifact.filename
    && value.target_url === payload.target_url && value.document_id === payload.document_id && value.tab_id === payload.native_context.tab_id
    && isStorageOrigin(origin) && hex(value.multipart_sha256) && integer(value.multipart_size) && integer(artifact.size_bytes)
    && value.multipart_size > artifact.size_bytes && value.multipart_size <= artifact.size_bytes + 65536
    && network(value.native, "upload", origin + "/") && value.native.request_body_sha256 === value.multipart_sha256 && value.native.request_body_size === value.multipart_size;
}
export function isHostedReceiptReview(payload: Record<string, unknown>): boolean {
  if (payload.upload_plan !== undefined) return false;
  if (payload.receipt_contract === undefined) return payload.expected_application === undefined && payload.expected_application_sha256 === undefined && payload.native_context === undefined
    && object(payload.upload) && keys(payload.upload, ["review_id", "action_id", "filename", "status"]);
  if (payload.receipt_contract !== HOSTED_RECEIPT || !isNativeContext(payload.native_context) || !hex(payload.expected_application_sha256)
    || !object(payload.expected_application) || !object(payload.reviewed_answers) || !object(payload.upload) || !object(payload.artifact)
    || !keys(payload.upload, ["review_id", "action_id", "filename", "status", "native_upload", "upload_plan_sha256"])
    || !hex(payload.upload.upload_plan_sha256) || !isNativeUpload(payload.upload.native_upload, payload)) return false;
  const application = payload.expected_application, answers = payload.reviewed_answers, uploaded = payload.upload.native_upload;
  const known = new Set(["first_name", "last_name", "email", "phone", "country"]);
  const expected: Record<string, unknown> = { first_name: answers.first_name, last_name: answers.last_name, email: answers.email,
    answers_attributes: {}, demographic_answers: [], data_compliance: {}, attachments: {}, from_job_board_renderer: true,
    employments: [], mapped_url_token: null, appcast_click_id: null, time_zone: payload.native_context.time_zone,
    resume_url: uploaded.uploaded_url, resume_url_filename: payload.artifact.filename, ...(answers.phone ? { phone: answers.phone } : {}) };
  if (!object(application.answers_attributes) || !Array.isArray(application.employments) || !Array.isArray(payload.field_mapping)) return false;
  const custom = payload.field_mapping.filter(field => object(field) && /^question_\d+$/.test(field.id as string)) as Record<string, unknown>[];
  if (Object.keys(application.answers_attributes).length !== custom.length) return false;
  const priorities = new Set<number>();
  for (const field of custom) {
    if (!["text", "email", "combobox"].includes(field.type as string)) return false;
    known.add(field.id as string);
    const id = (field.id as string).split("_")[1], entry = application.answers_attributes[id], value = answers[field.id as string];
    if (!object(entry) || !integer(entry.priority) || entry.priority < 0 || entry.priority >= custom.length || priorities.has(entry.priority)) return false;
    priorities.add(entry.priority);
    const answer = field.type === "combobox" ? value === "0" || value === "1" ? { boolean_value: Number(value) }
      : { answer_selected_options_attributes: value ? { "0": { question_option_id: value } } : {} } : { text_value: value };
    if (!same(entry, { question_id: id, priority: entry.priority, ...answer })) return false;
  }
  expected.answers_attributes = application.answers_attributes;
  const rows = Object.keys(answers).filter(id => /^company-name-[0-4]$/.test(id)).map(id => Number(id.split("-").at(-1))).sort();
  if (!same(rows, rows.map((_, index) => index))) return false;
  for (const index of rows) {
    const ids = ["company-name", "title", "start-date-month", "start-date-year"].map(name => `${name}-${index}`);
    const role = `current-role-${index}_1`;
    if (!["true", "false"].includes(answers[role] as string)) return false;
    ids.push(role);
    if (answers[role] === "false") ids.push(`end-date-month-${index}`, `end-date-year-${index}`);
    if (ids.some(id => !Object.hasOwn(answers, id))) return false;
    for (const id of ids) known.add(id);
  }
  if (Object.keys(answers).some(id => !known.has(id))) return false;
  expected.employments = rows.map(index => ({ company_name: answers[`company-name-${index}`], title: answers[`title-${index}`],
    start_date: { month: answers[`start-date-month-${index}`] || null, year: answers[`start-date-year-${index}`] || null },
    current: answers[`current-role-${index}_1`] === "true", ...(answers[`current-role-${index}_1`] === "true" ? {}
      : { end_date: { month: answers[`end-date-month-${index}`] || null, year: answers[`end-date-year-${index}`] || null } }) }));
  return same(application, expected);
}
