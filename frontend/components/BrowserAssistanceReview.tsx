"use client";

import Link from "next/link";
import { signIn } from "next-auth/react";
import { useEffect, useRef, useState } from "react";
import { accountMutationHeaders } from "../lib/accountTransportClient";
import { BROWSER_CLIENT_TIMEOUT_MS } from "../lib/browserTransportTimeouts";
import { apiDownload } from "../lib/api";
import { isHostedReceiptReview, isNativeContext, isUploadPlan, uploadedStorage, type NativeContext, type UploadPlan } from "../lib/browserHostedNetworkReview";
import { Button } from "./ui/Button";

type Device = { id: string; state: string; key_sha256: string; extension_id: string; expires_at_ms: number };
type Control = { id: string; label: string; type: "text" | "tel" | "combobox" | "checkbox" | "button";
  required?: boolean; options?: { value: string; label: string }[] };
type Field = Control & { value: string; expected_before: string; value_label?: string; expected_before_label?: string };
type Hosted = { rows: { index: number; current_role: boolean }[]; controls: Control[]; values: Record<string, string>; draft_answers?: Record<string, string> };
type Payload = {
  application_id: string; target_url: string; package_digest: string;
  artifact: { filename: string; media_type: string; size_bytes: number; sha256: string;
    artifact_id?: string; resume_id?: number; resume_version_id?: string | null; resume_choice?: "original" | "tailored" | "custom" };
  context_digest: string; reviewed_answers_sha256: string;
  document_id?: string; frame_id?: number; field_mapping?: Control[];
  review_protocol_version?: 2 | 3 | 4;
  reviewed_answers?: Record<string, string>;
  hosted?: Hosted;
  attachment?: { input_id: "resume"; expected_empty: true };
  upload_plan?: UploadPlan; native_context?: NativeContext;
  receipt_contract?: "greenhouse_hosted_browser_https_200_v2"; expected_application?: Record<string, unknown>; expected_application_sha256?: string;
  fields: Field[];
  manual_fields: { id: string; label: string; required: boolean }[];
  answers?: (Control & { value:string })[]; assisted_values?: Record<string,string>;
  candidate_id?:number; company?:string; job?:{posting_id:string;external_id:string;title:string;content_sha256:string};
  upload?:{review_id:string;action_id:string;filename:string;status:"portal_reports_uploaded";native_upload?:Record<string,unknown>;upload_plan_sha256?:string};
  upload_continuity?:{review_id:string;action_id:string;artifact_sha256:string;document_id:string;target_url:string};
  submit_control?:{form_id:"application-form";type:"submit";label:"Submit application"}; pricing?:Record<string,unknown>; admission?:Record<string,unknown>;
  disclosures: string[]; allowed_actions: ["fill"] | ["attach"] | ["upload_resume"] | ["submit"]; fee_credits: 0 | 20;
};
type Review = { id: string; state: string; expires_at_ms: number; payload?: Payload; review_digest: string; filled_count: number;
  review_protocol_version?: 2 | 3 | 4; attachment_status?: "selected_upload_unconfirmed" | "uploaded_native_verified" | "unknown"; submission_status?: "unknown" | "not_launched" | "confirmed" | "verified_not_submitted"; employer_confirmed?:boolean; fee_credits?:20 };
type Props = { deviceId: string | null; reviewId: string | null; applicationId: string | null };
type Operation = "device" | "confirm" | "revoke" | "review" | "revise" | "approve" | "cancel";
const UUID = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/;
const SHA256 = /^[a-f0-9]{64}$/;
const IDENTIFIER = /^[a-zA-Z0-9_-]{1,128}$/;
const BASE_FIELD_LABELS: Record<string, string> = { "First Name": "first_name", "Last Name": "last_name", Email: "email" };
const CUSTOM_FIELD_LABELS = new Set(["LinkedIn Profile", "Website", "Total Years of Experience", "Current Location"]);
const CHOICE_FIELD_LABELS = new Set(["Current Career Stage", "Gender", "Current Industry", "Current Designation"]);
// Exact options observed for this known phone country-code selector.
const COUNTRY_OPTIONS = [{"value":"us","label":"United States +1"},{"value":"af","label":"Afghanistan +93"},{"value":"ax","label":"Åland Islands +358"},{"value":"al","label":"Albania +355"},{"value":"dz","label":"Algeria +213"},{"value":"as","label":"American Samoa +1"},{"value":"ad","label":"Andorra +376"},{"value":"ao","label":"Angola +244"},{"value":"ai","label":"Anguilla +1"},{"value":"ag","label":"Antigua & Barbuda +1"},{"value":"ar","label":"Argentina +54"},{"value":"am","label":"Armenia +374"},{"value":"aw","label":"Aruba +297"},{"value":"ac","label":"Ascension Island +247"},{"value":"au","label":"Australia +61"},{"value":"at","label":"Austria +43"},{"value":"az","label":"Azerbaijan +994"},{"value":"bs","label":"Bahamas +1"},{"value":"bh","label":"Bahrain +973"},{"value":"bd","label":"Bangladesh +880"},{"value":"bb","label":"Barbados +1"},{"value":"by","label":"Belarus +375"},{"value":"be","label":"Belgium +32"},{"value":"bz","label":"Belize +501"},{"value":"bj","label":"Benin +229"},{"value":"bm","label":"Bermuda +1"},{"value":"bt","label":"Bhutan +975"},{"value":"bo","label":"Bolivia +591"},{"value":"ba","label":"Bosnia & Herzegovina +387"},{"value":"bw","label":"Botswana +267"},{"value":"br","label":"Brazil +55"},{"value":"io","label":"British Indian Ocean Territory +246"},{"value":"vg","label":"British Virgin Islands +1"},{"value":"bn","label":"Brunei +673"},{"value":"bg","label":"Bulgaria +359"},{"value":"bf","label":"Burkina Faso +226"},{"value":"bi","label":"Burundi +257"},{"value":"kh","label":"Cambodia +855"},{"value":"cm","label":"Cameroon +237"},{"value":"ca","label":"Canada +1"},{"value":"cv","label":"Cape Verde +238"},{"value":"bq","label":"Caribbean Netherlands +599"},{"value":"ky","label":"Cayman Islands +1"},{"value":"cf","label":"Central African Republic +236"},{"value":"td","label":"Chad +235"},{"value":"cl","label":"Chile +56"},{"value":"cn","label":"China +86"},{"value":"cx","label":"Christmas Island +61"},{"value":"cc","label":"Cocos (Keeling) Islands +61"},{"value":"co","label":"Colombia +57"},{"value":"km","label":"Comoros +269"},{"value":"cg","label":"Congo - Brazzaville +242"},{"value":"cd","label":"Congo - Kinshasa +243"},{"value":"ck","label":"Cook Islands +682"},{"value":"cr","label":"Costa Rica +506"},{"value":"ci","label":"Côte d’Ivoire +225"},{"value":"hr","label":"Croatia +385"},{"value":"cu","label":"Cuba +53"},{"value":"cw","label":"Curaçao +599"},{"value":"cy","label":"Cyprus +357"},{"value":"cz","label":"Czechia +420"},{"value":"dk","label":"Denmark +45"},{"value":"dj","label":"Djibouti +253"},{"value":"dm","label":"Dominica +1"},{"value":"do","label":"Dominican Republic +1"},{"value":"ec","label":"Ecuador +593"},{"value":"eg","label":"Egypt +20"},{"value":"sv","label":"El Salvador +503"},{"value":"gq","label":"Equatorial Guinea +240"},{"value":"er","label":"Eritrea +291"},{"value":"ee","label":"Estonia +372"},{"value":"sz","label":"Eswatini +268"},{"value":"et","label":"Ethiopia +251"},{"value":"fk","label":"Falkland Islands +500"},{"value":"fo","label":"Faroe Islands +298"},{"value":"fj","label":"Fiji +679"},{"value":"fi","label":"Finland +358"},{"value":"fr","label":"France +33"},{"value":"gf","label":"French Guiana +594"},{"value":"pf","label":"French Polynesia +689"},{"value":"ga","label":"Gabon +241"},{"value":"gm","label":"Gambia +220"},{"value":"ge","label":"Georgia +995"},{"value":"de","label":"Germany +49"},{"value":"gh","label":"Ghana +233"},{"value":"gi","label":"Gibraltar +350"},{"value":"gr","label":"Greece +30"},{"value":"gl","label":"Greenland +299"},{"value":"gd","label":"Grenada +1"},{"value":"gp","label":"Guadeloupe +590"},{"value":"gu","label":"Guam +1"},{"value":"gt","label":"Guatemala +502"},{"value":"gg","label":"Guernsey +44"},{"value":"gn","label":"Guinea +224"},{"value":"gw","label":"Guinea-Bissau +245"},{"value":"gy","label":"Guyana +592"},{"value":"ht","label":"Haiti +509"},{"value":"hn","label":"Honduras +504"},{"value":"hk","label":"Hong Kong SAR China +852"},{"value":"hu","label":"Hungary +36"},{"value":"is","label":"Iceland +354"},{"value":"in","label":"India +91"},{"value":"id","label":"Indonesia +62"},{"value":"ir","label":"Iran +98"},{"value":"iq","label":"Iraq +964"},{"value":"ie","label":"Ireland +353"},{"value":"im","label":"Isle of Man +44"},{"value":"il","label":"Israel +972"},{"value":"it","label":"Italy +39"},{"value":"jm","label":"Jamaica +1"},{"value":"jp","label":"Japan +81"},{"value":"je","label":"Jersey +44"},{"value":"jo","label":"Jordan +962"},{"value":"kz","label":"Kazakhstan +7"},{"value":"ke","label":"Kenya +254"},{"value":"ki","label":"Kiribati +686"},{"value":"xk","label":"Kosovo +383"},{"value":"kw","label":"Kuwait +965"},{"value":"kg","label":"Kyrgyzstan +996"},{"value":"la","label":"Laos +856"},{"value":"lv","label":"Latvia +371"},{"value":"lb","label":"Lebanon +961"},{"value":"ls","label":"Lesotho +266"},{"value":"lr","label":"Liberia +231"},{"value":"ly","label":"Libya +218"},{"value":"li","label":"Liechtenstein +423"},{"value":"lt","label":"Lithuania +370"},{"value":"lu","label":"Luxembourg +352"},{"value":"mo","label":"Macao SAR China +853"},{"value":"mg","label":"Madagascar +261"},{"value":"mw","label":"Malawi +265"},{"value":"my","label":"Malaysia +60"},{"value":"mv","label":"Maldives +960"},{"value":"ml","label":"Mali +223"},{"value":"mt","label":"Malta +356"},{"value":"mh","label":"Marshall Islands +692"},{"value":"mq","label":"Martinique +596"},{"value":"mr","label":"Mauritania +222"},{"value":"mu","label":"Mauritius +230"},{"value":"yt","label":"Mayotte +262"},{"value":"mx","label":"Mexico +52"},{"value":"fm","label":"Micronesia +691"},{"value":"md","label":"Moldova +373"},{"value":"mc","label":"Monaco +377"},{"value":"mn","label":"Mongolia +976"},{"value":"me","label":"Montenegro +382"},{"value":"ms","label":"Montserrat +1"},{"value":"ma","label":"Morocco +212"},{"value":"mz","label":"Mozambique +258"},{"value":"mm","label":"Myanmar (Burma) +95"},{"value":"na","label":"Namibia +264"},{"value":"nr","label":"Nauru +674"},{"value":"np","label":"Nepal +977"},{"value":"nl","label":"Netherlands +31"},{"value":"nc","label":"New Caledonia +687"},{"value":"nz","label":"New Zealand +64"},{"value":"ni","label":"Nicaragua +505"},{"value":"ne","label":"Niger +227"},{"value":"ng","label":"Nigeria +234"},{"value":"nu","label":"Niue +683"},{"value":"nf","label":"Norfolk Island +672"},{"value":"kp","label":"North Korea +850"},{"value":"mk","label":"North Macedonia +389"},{"value":"mp","label":"Northern Mariana Islands +1"},{"value":"no","label":"Norway +47"},{"value":"om","label":"Oman +968"},{"value":"pk","label":"Pakistan +92"},{"value":"pw","label":"Palau +680"},{"value":"ps","label":"Palestinian Territories +970"},{"value":"pa","label":"Panama +507"},{"value":"pg","label":"Papua New Guinea +675"},{"value":"py","label":"Paraguay +595"},{"value":"pe","label":"Peru +51"},{"value":"ph","label":"Philippines +63"},{"value":"pl","label":"Poland +48"},{"value":"pt","label":"Portugal +351"},{"value":"pr","label":"Puerto Rico +1"},{"value":"qa","label":"Qatar +974"},{"value":"re","label":"Réunion +262"},{"value":"ro","label":"Romania +40"},{"value":"ru","label":"Russia +7"},{"value":"rw","label":"Rwanda +250"},{"value":"ws","label":"Samoa +685"},{"value":"sm","label":"San Marino +378"},{"value":"st","label":"São Tomé & Príncipe +239"},{"value":"sa","label":"Saudi Arabia +966"},{"value":"sn","label":"Senegal +221"},{"value":"rs","label":"Serbia +381"},{"value":"sc","label":"Seychelles +248"},{"value":"sl","label":"Sierra Leone +232"},{"value":"sg","label":"Singapore +65"},{"value":"sx","label":"Sint Maarten +1"},{"value":"sk","label":"Slovakia +421"},{"value":"si","label":"Slovenia +386"},{"value":"sb","label":"Solomon Islands +677"},{"value":"so","label":"Somalia +252"},{"value":"za","label":"South Africa +27"},{"value":"kr","label":"South Korea +82"},{"value":"ss","label":"South Sudan +211"},{"value":"es","label":"Spain +34"},{"value":"lk","label":"Sri Lanka +94"},{"value":"bl","label":"St. Barthélemy +590"},{"value":"sh","label":"St. Helena +290"},{"value":"kn","label":"St. Kitts & Nevis +1"},{"value":"lc","label":"St. Lucia +1"},{"value":"mf","label":"St. Martin +590"},{"value":"pm","label":"St. Pierre & Miquelon +508"},{"value":"vc","label":"St. Vincent & Grenadines +1"},{"value":"sd","label":"Sudan +249"},{"value":"sr","label":"Suriname +597"},{"value":"sj","label":"Svalbard & Jan Mayen +47"},{"value":"se","label":"Sweden +46"},{"value":"ch","label":"Switzerland +41"},{"value":"sy","label":"Syria +963"},{"value":"tw","label":"Taiwan +886"},{"value":"tj","label":"Tajikistan +992"},{"value":"tz","label":"Tanzania +255"},{"value":"th","label":"Thailand +66"},{"value":"tl","label":"Timor-Leste +670"},{"value":"tg","label":"Togo +228"},{"value":"tk","label":"Tokelau +690"},{"value":"to","label":"Tonga +676"},{"value":"tt","label":"Trinidad & Tobago +1"},{"value":"tn","label":"Tunisia +216"},{"value":"tr","label":"Turkey +90"},{"value":"tm","label":"Turkmenistan +993"},{"value":"tc","label":"Turks & Caicos Islands +1"},{"value":"tv","label":"Tuvalu +688"},{"value":"vi","label":"U.S. Virgin Islands +1"},{"value":"ug","label":"Uganda +256"},{"value":"ua","label":"Ukraine +380"},{"value":"ae","label":"United Arab Emirates +971"},{"value":"gb","label":"United Kingdom +44"},{"value":"uy","label":"Uruguay +598"},{"value":"uz","label":"Uzbekistan +998"},{"value":"vu","label":"Vanuatu +678"},{"value":"va","label":"Vatican City +39"},{"value":"ve","label":"Venezuela +58"},{"value":"vn","label":"Vietnam +84"},{"value":"wf","label":"Wallis & Futuna +681"},{"value":"eh","label":"Western Sahara +212"},{"value":"ye","label":"Yemen +967"},{"value":"zm","label":"Zambia +260"},{"value":"zw","label":"Zimbabwe +263"}];
const MONTH_OPTIONS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"].map((value) => ({ value, label: value }));
const DEVICE_STATES = new Set(["pending", "active", "revoked"]);
const REVIEW_STATES = new Set(["draft", "approved", "running", "filled", "unknown", "cancelled", "expired"]);
const APPROVABLE_STATES = new Set(["draft"]);
const fileTypes = new Set(["application/pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"]);
const object = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === "object" && !Array.isArray(value);
const text = (value: unknown, max = 255): value is string => typeof value === "string" && value.length <= max;
const integer = (value: unknown): value is number => typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
const canonical = (value: unknown): string => JSON.stringify(value, (_key, entry) => object(entry) ? Object.fromEntries(Object.entries(entry).sort(([left], [right]) => left.localeCompare(right))) : entry);
const same = (left: unknown, right: unknown): boolean => canonical(left) === canonical(right);
const answerIdentity = (answers: Record<string, string>): string => JSON.stringify(Object.entries(answers).sort(([left], [right]) => left.localeCompare(right)));
function hostedControls(rows: Hosted["rows"]): Control[] {
  const controls: Control[] = [{ id: "country", label: "Country", type: "combobox", required: true, options: COUNTRY_OPTIONS }];
  for (const { index, current_role } of rows) {
    const parts: [string, string, Control["type"]][] = [["company-name", "Company name", "text"], ["title", "Title", "text"],
      ["start-date-month", "Start date month", "combobox"], ["start-date-year", "Start date year", "text"]];
    if (!current_role) parts.push(["end-date-month", "End date month", "combobox"], ["end-date-year", "End date year", "text"]);
    parts.push(["current-role", "Current role", "checkbox"]);
    for (const [name, label, type] of parts) controls.push({ id: `${name}-${index}${type === "checkbox" ? "_1" : ""}`, label, type,
      required: type !== "checkbox", ...(type === "combobox" ? { options: MONTH_OPTIONS } : {}) });
  }
  if (rows.length < 5) controls.push({ id: `employment-add-${rows.length}`, label: "Add another", type: "button", required: false });
  return controls;
}
function controlIdentity(control: Control): string {
  return JSON.stringify([control.id, control.label, control.type, control.required, control.options?.map(({ value, label }) => [value, label])]);
}
function exactOptions(value: unknown, expected: Control["options"]): boolean {
  return expected === undefined ? value === undefined : Array.isArray(value) && value.length === expected.length
    && value.every((option, index) => object(option) && Object.keys(option).sort().join(",") === "label,value"
      && option.value === expected[index].value && option.label === expected[index].label);
}
function validHostedAnswers(hosted: Hosted, answers: unknown): answers is Record<string, string> {
  if (!object(answers) || Object.keys(answers).length > 37) return false;
  for (const [id, value] of Object.entries(answers)) {
    const control = hosted.controls.find((entry) => entry.id === id);
    if (!control || !text(value) || !value || value.trim() !== value) return false;
    if (control.type === "combobox" && !control.options?.some((option) => option.value === value)) return false;
    if (control.type === "checkbox" && value !== "true" && value !== "false") return false;
    if (control.type === "button" && value !== String(hosted.rows.length + 1)) return false;
    if (control.type === "text" && id.includes("-year-") && !/^[1-9][0-9]{3}$/.test(value)) return false;
  }
  const checked = answers as Record<string, string>;
  const merged = { ...hosted.values, ...checked };
  return hosted.rows.every(({ index, current_role }) => {
    const role = checked[`current-role-${index}_1`] ?? String(current_role);
    if (role === "true") return !Object.hasOwn(answers, `end-date-month-${index}`) && !Object.hasOwn(answers, `end-date-year-${index}`);
    const startMonth = merged[`start-date-month-${index}`], startYear = merged[`start-date-year-${index}`];
    const endMonth = merged[`end-date-month-${index}`], endYear = merged[`end-date-year-${index}`];
    return ![startMonth, startYear, endMonth, endYear].every(Boolean)
      || (/^[1-9][0-9]{3}$/.test(startYear) && /^[1-9][0-9]{3}$/.test(endYear)
        && MONTH_OPTIONS.some(({ value }) => value === startMonth) && MONTH_OPTIONS.some(({ value }) => value === endMonth)
        && Number(startYear) * 12 + MONTH_OPTIONS.findIndex(({ value }) => value === startMonth) <= Number(endYear) * 12 + MONTH_OPTIONS.findIndex(({ value }) => value === endMonth));
  });
}
function isHosted(value: unknown): value is Hosted {
  if (!object(value) || Object.keys(value).some((key) => !["rows", "controls", "values", "draft_answers"].includes(key))
    || !Array.isArray(value.rows) || !value.rows.length || value.rows.length > 5
    || value.rows.some((row, index) => !object(row) || Object.keys(row).sort().join(",") !== "current_role,index" || row.index !== index || typeof row.current_role !== "boolean")) return false;
  const expected = hostedControls(value.rows as Hosted["rows"]);
  if (!Array.isArray(value.controls) || value.controls.length !== expected.length || value.controls.some((control, index) => !object(control)
    || Object.keys(control).sort().join(",") !== Object.keys(expected[index]).sort().join(",")
    || !exactOptions(control.options, expected[index].options) || controlIdentity(control as Control) !== controlIdentity(expected[index])) || !object(value.values)
    || Object.keys(value.values).sort().join(",") !== expected.map(({ id }) => id).sort().join(",")) return false;
  const values = value.values;
  if (expected.some((control) => {
    const current = values[control.id];
    if (!text(current)) return true;
    if (control.type === "combobox") return current !== "" && !control.options?.some(({ value }) => value === current);
    if (control.type === "checkbox") return current !== String((value.rows as Hosted["rows"])[Number(control.id.split("-").at(-1)!.split("_")[0])].current_role);
    return control.type === "button" && current !== String((value.rows as Hosted["rows"]).length);
  })) return false;
  return value.draft_answers === undefined || validHostedAnswers(value as Hosted, value.draft_answers);
}
function hostedFields(hosted: Hosted, answers: Record<string, string>): Field[] {
  return hosted.controls.filter(({ id }) => Object.hasOwn(answers, id) && answers[id] !== hosted.values[id]).map((control) => ({
    ...control, value: answers[control.id], expected_before: hosted.values[control.id], ...(control.type === "combobox" ? {
      value_label: control.options!.find(({ value }) => value === answers[control.id])!.label,
      expected_before_label: control.options!.find(({ value }) => value === hosted.values[control.id])?.label ?? "",
    } : {}),
  }));
}
function destination(value: unknown): value is string {
  if (!text(value, 2048)) return false;
  try { const url = new URL(value); return url.origin === "https://job-boards.greenhouse.io" && !url.search && !url.hash && /^\/razorpaysoftwareprivatelimited\/jobs\/\d+$/.test(url.pathname) && value === url.href; } catch { return false; }
}
function isDevice(value: unknown, id: string): value is Device {
  return object(value) && value.id === id && text(value.state) && DEVICE_STATES.has(value.state)
    && text(value.key_sha256) && SHA256.test(value.key_sha256) && text(value.extension_id, 128) && value.extension_id.length > 0 && integer(value.expires_at_ms);
}
function isFinalPayload(payload:Record<string,unknown>): boolean {
  if (!isHostedReceiptReview(payload) || payload.review_protocol_version!==4 || payload.fee_credits!==20 || !same(payload.allowed_actions,["submit"])
    || !integer(payload.candidate_id) || payload.candidate_id<1 || !text(payload.company) || !payload.company
    || !object(payload.job) || !text(payload.job.posting_id) || !text(payload.job.external_id) || !/^[0-9]+$/.test(payload.job.external_id)
    || !text(payload.target_url) || payload.target_url.split("/").at(-1)!==payload.job.external_id
    || !text(payload.job.title) || !payload.job.title || !text(payload.job.content_sha256) || !SHA256.test(payload.job.content_sha256)
    || !object(payload.artifact) || !text(payload.artifact.artifact_id) || !IDENTIFIER.test(payload.artifact.artifact_id)
    || !integer(payload.artifact.resume_id) || payload.artifact.resume_id<1 || !["original","custom","tailored"].includes(payload.artifact.resume_choice as string)
    || (payload.artifact.resume_choice==="tailored" ? !text(payload.artifact.resume_version_id) || !IDENTIFIER.test(payload.artifact.resume_version_id) : payload.artifact.resume_version_id!==null)
    || !object(payload.upload) || !text(payload.upload.review_id) || !UUID.test(payload.upload.review_id) || !text(payload.upload.action_id) || !UUID.test(payload.upload.action_id)
    || payload.upload.status!=="portal_reports_uploaded" || payload.upload.filename!==payload.artifact.filename
    || !same(payload.upload_continuity,{review_id:payload.upload.review_id,action_id:payload.upload.action_id,artifact_sha256:payload.artifact.sha256,document_id:payload.document_id,target_url:payload.target_url})
    || !same(payload.submit_control,{form_id:"application-form",type:"submit",label:"Submit application"})
    || !object(payload.pricing) || payload.pricing.unit_price!==20 || !object(payload.admission)
    || !Array.isArray(payload.fields) || payload.fields.length || !Array.isArray(payload.manual_fields) || payload.manual_fields.length
    || !Array.isArray(payload.field_mapping) || payload.field_mapping.length<3 || payload.field_mapping.length>64
    || !Array.isArray(payload.answers) || payload.answers.length<3 || payload.answers.length>64 || !object(payload.reviewed_answers) || !object(payload.assisted_values)) return false;
  const mapping=payload.field_mapping.filter((field)=>object(field)&&field.type!=="button");
  if (mapping.length!==payload.answers.length || new Set(mapping.map((field)=>field.id)).size!==mapping.length
    || payload.answers.some((field,index)=>!object(field) || !same({...field,value:undefined},{...mapping[index],value:undefined}) || !text(field.value)
      || field.required && !field.value || field.type==="combobox" && (!Array.isArray(field.options) || !field.options.some((option)=>object(option)&&option.value===field.value))
      || field.type==="checkbox" && !["true","false"].includes(field.value))) return false;
  return same(payload.reviewed_answers,Object.fromEntries(payload.answers.map((field)=>[field.id,field.value])))
    && payload.answers.every((field)=>payload.assisted_values && (payload.assisted_values as Record<string,unknown>)[field.id]===field.value)
    && Array.isArray(payload.disclosures) && payload.disclosures.length>0 && payload.disclosures.length<=32 && payload.disclosures.every((entry)=>text(entry,2048));
}
function isReview(value: unknown, id: string): value is Review {
  if (!object(value) || value.id !== id || !text(value.state) || !REVIEW_STATES.has(value.state) || !integer(value.expires_at_ms)
    || !integer(value.filled_count) || value.filled_count > 64 || !text(value.review_digest) || !SHA256.test(value.review_digest)) return false;
  if (value.review_protocol_version !== undefined && ![2, 3, 4].includes(value.review_protocol_version as number)) return false;
  if (value.review_protocol_version === 3 && (value.filled_count > 1 || (value.state === "filled" && value.filled_count !== 1)
    || (value.state === "filled" ? !["selected_upload_unconfirmed","uploaded_native_verified"].includes(value.attachment_status as string) : value.attachment_status !== "unknown"))) return false;
  if (value.review_protocol_version===4 && (value.fee_credits!==20 || !["unknown","not_launched","confirmed","verified_not_submitted"].includes(value.submission_status as string)
    || value.employer_confirmed!==(value.submission_status==="confirmed") || value.filled_count!==(value.submission_status==="confirmed"?1:0)
    || value.submission_status==="confirmed" && value.state!=="filled")) return false;
  if (["filled", "unknown", "cancelled", "expired"].includes(value.state) && (value.payload == null || (object(value.payload) && !Object.keys(value.payload).length))) return value.filled_count <= (value.review_protocol_version === 2 ? 64 : [3,4].includes(value.review_protocol_version as number) ? 1 : 7);
  if (!object(value.payload)) return false;
  const payload = value.payload;
  if (!text(payload.application_id, 128) || !IDENTIFIER.test(payload.application_id) || !destination(payload.target_url)
    || !text(payload.package_digest) || !SHA256.test(payload.package_digest) || !text(payload.context_digest) || !SHA256.test(payload.context_digest)
    || !text(payload.reviewed_answers_sha256) || !SHA256.test(payload.reviewed_answers_sha256) || payload.fee_credits !== (payload.review_protocol_version===4 ? 20 : 0)
    || !Array.isArray(payload.allowed_actions) || payload.allowed_actions.length !== 1 || !object(payload.artifact)) return false;
  const version = payload.review_protocol_version ?? 1;
  if (version !== 4 && [payload.receipt_contract, payload.expected_application, payload.expected_application_sha256].some(field => field !== undefined)) return false;
  if (version===4) return value.review_protocol_version===4
    && text(payload.artifact.filename,512) && Boolean(payload.artifact.filename) && text(payload.artifact.media_type) && fileTypes.has(payload.artifact.media_type)
    && integer(payload.artifact.size_bytes) && payload.artifact.size_bytes>0 && payload.artifact.size_bytes<=10*1024*1024
    && text(payload.artifact.sha256) && SHA256.test(payload.artifact.sha256) && isFinalPayload(payload);
  if (![1, 2, 3].includes(version as number) || payload.allowed_actions[0] !== (version === 3 ? payload.upload_plan ? "upload_resume" : "attach" : "fill")) return false;
  if (payload.upload_plan !== undefined ? version !== 3 || !isUploadPlan(payload.upload_plan) || !isNativeContext(payload.native_context)
    || !text(payload.document_id, 160) || !payload.document_id || payload.frame_id !== 0 : payload.native_context !== undefined || value.attachment_status === "uploaded_native_verified") return false;
  if (value.filled_count > (version === 2 ? 64 : version === 3 ? 1 : 7)
    || (payload.hosted !== undefined && (version !== 2 || !isHosted(payload.hosted)))) return false;
  const hosted = payload.hosted as Hosted | undefined;
  if ((value.review_protocol_version !== undefined && value.review_protocol_version !== version) || (version === 3 && value.review_protocol_version !== 3)) return false;
  const artifact = payload.artifact;
  if (!text(artifact.filename, 512) || !artifact.filename.trim() || !text(artifact.media_type) || !fileTypes.has(artifact.media_type)
    || !integer(artifact.size_bytes) || !artifact.size_bytes || artifact.size_bytes > 10 * 1024 * 1024 || !text(artifact.sha256) || !SHA256.test(artifact.sha256)) return false;
  if (version === 3 && (!/^[\x20-\x7e]{1,240}$/.test(artifact.filename) || !text(artifact.artifact_id, 128) || !IDENTIFIER.test(artifact.artifact_id)
    || !integer(artifact.resume_id) || artifact.resume_id < 1 || !["original", "tailored", "custom"].includes(artifact.resume_choice as string)
    || (artifact.resume_choice === "tailored" ? !text(artifact.resume_version_id, 128) || !IDENTIFIER.test(artifact.resume_version_id) : artifact.resume_version_id !== null)
    || !object(payload.attachment) || Object.keys(payload.attachment).sort().join(",") !== "expected_empty,input_id"
    || payload.attachment.input_id !== "resume" || payload.attachment.expected_empty !== true
    || !object(payload.reviewed_answers) || Object.keys(payload.reviewed_answers).length !== 0)) return false;
  if (!Array.isArray(payload.fields) || (version === 3 ? payload.fields.length !== 0 : (!payload.fields.length && (!hosted || value.state !== "draft")) || payload.fields.length > (version === 2 ? 64 : 7))
    || payload.fields.some((field) => {
      if (!object(field) || !text(field.id, 128) || !text(field.label, 256) || !text(field.value) || !field.value.trim() || !text(field.expected_before)) return true;
      const hostedControl = hosted?.controls.find(({ id }) => id === field.id);
      if (hostedControl) {
        const expected = hostedFields(hosted!, hosted!.draft_answers ?? {}).find(({ id }) => id === field.id);
        return !expected || !exactOptions(field.options, expected.options) || controlIdentity(field as Field) !== controlIdentity(expected)
          || field.value !== expected.value || field.expected_before !== expected.expected_before
          || field.value_label !== expected.value_label || field.expected_before_label !== expected.expected_before_label;
      }
      if (field.type === "text") return Object.hasOwn(BASE_FIELD_LABELS, field.label) ? field.id !== BASE_FIELD_LABELS[field.label] : !CUSTOM_FIELD_LABELS.has(field.label) || !/^question_\d+$/.test(field.id);
      if (version !== 2) return true;
      if (field.type === "tel") return field.id !== "phone" || field.label !== "Phone";
      if (field.type !== "combobox" || !CHOICE_FIELD_LABELS.has(field.label) || !/^question_\d{1,20}$/.test(field.id)
        || !Array.isArray(field.options) || !field.options.length || field.options.length > 64
        || field.options.some((option) => !object(option) || !text(option.value) || !option.value || !text(option.label) || !option.label)
        || new Set(field.options.map((option) => option.value)).size !== field.options.length
        || new Set(field.options.map((option) => option.label)).size !== field.options.length) return true;
      return !field.options.some((option) => option.value === field.value && option.label === field.value_label)
        || (field.expected_before === "" ? field.expected_before_label !== "" : !field.options.some((option) => option.value === field.expected_before && option.label === field.expected_before_label));
    }) || new Set(payload.fields.map((field) => field.id)).size !== payload.fields.length
    || new Set(payload.fields.filter((field) => !hosted?.controls.some(({ id }) => id === field.id)).map((field) => field.label)).size
      !== payload.fields.filter((field) => !hosted?.controls.some(({ id }) => id === field.id)).length
    || (hosted && !same(payload.fields.filter((field) => hosted.controls.some(({ id }) => id === field.id)).map((field) => field.id), hostedFields(hosted, hosted.draft_answers ?? {}).map(({ id }) => id)))) return false;
  if (hosted && (!object(payload.reviewed_answers) || !same(payload.reviewed_answers, Object.fromEntries(payload.fields.map(({ id, value }) => [id, value]))))) return false;
  return Array.isArray(payload.manual_fields) && payload.manual_fields.length <= 256 && payload.manual_fields.every((field) => object(field)
    && text(field.id, 128) && text(field.label, 256) && typeof field.required === "boolean")
    && Array.isArray(payload.disclosures) && payload.disclosures.length <= 32 && payload.disclosures.every((disclosure) => text(disclosure, 2048));
}
function payloadIdentity(payload: Payload): string {
  return canonical([payload.application_id, payload.target_url, payload.package_digest, payload.artifact.filename,
    payload.artifact.media_type, payload.artifact.size_bytes, payload.artifact.sha256, payload.context_digest, payload.reviewed_answers_sha256,
    payload.document_id, payload.frame_id, payload.field_mapping,
    payload.review_protocol_version ?? 1, payload.attachment,payload.upload_plan,payload.native_context,payload.receipt_contract,payload.expected_application,payload.expected_application_sha256, payload.reviewed_answers, payload.answers, payload.candidate_id,payload.company,payload.job,payload.upload,payload.upload_continuity,payload.submit_control,payload.pricing,payload.admission, payload.hosted && [payload.hosted.rows,
      payload.hosted.controls.map(controlIdentity), payload.hosted.values, payload.hosted.draft_answers],
    payload.artifact.artifact_id, payload.artifact.resume_id, payload.artifact.resume_version_id, payload.artifact.resume_choice,
    payload.fields.map(({ id, label, type, value, expected_before, options, value_label, expected_before_label }) => [id, label, type, value, expected_before, options, value_label, expected_before_label]),
    payload.manual_fields.map(({ id, label, required }) => [id, label, required]), payload.disclosures, payload.allowed_actions, payload.fee_credits]);
}
function revisedReviewMatches(previous: Review, result: Review, answers: Record<string, string>): boolean {
  const payload = previous.payload, hosted = payload?.hosted;
  if (!payload || !hosted || !result.payload || result.state !== "draft" || result.expires_at_ms !== previous.expires_at_ms
    || result.review_digest === previous.review_digest || result.filled_count !== previous.filled_count) return false;
  const fields = [...payload.fields.filter(({ id }) => !hosted.controls.some((control) => control.id === id)), ...hostedFields(hosted, answers)];
  const expected: Payload = { ...payload, fields, hosted: { ...hosted, draft_answers: answers },
    reviewed_answers: Object.fromEntries(fields.map(({ id, value }) => [id, value])), reviewed_answers_sha256: result.payload.reviewed_answers_sha256,
    manual_fields: [...payload.manual_fields.filter(({ id }) => !hosted.controls.some((control) => control.id === id)),
      ...hosted.controls.filter(({ id, required }) => required && !hosted.values[id] && !Object.hasOwn(answers, id)).map(({ id, label, required }) => ({ id, label, required: required! }))] };
  return payloadIdentity(expected) === payloadIdentity(result.payload);
}
function hostedLabel(control: Control): string {
  if (control.id === "country") return "Country (phone country code)";
  if (control.type === "button") return `Add another employment row (${Number(control.id.split("-").at(-1)) + 1} total)`;
  if (!/^(company-name|title|start-date-month|start-date-year|end-date-month|end-date-year|current-role)-[0-4](?:_1)?$/.test(control.id)) return control.label;
  return `${control.label} · employment ${Number(control.id.split("-").at(-1)!.split("_")[0]) + 1}`;
}

export default function BrowserAssistanceReview({ deviceId, reviewId, applicationId }: Props) {
  const [device, setDevice] = useState<Device | null>(null);
  const [review, setReview] = useState<Review | null>(null);
  const [accepted, setAccepted] = useState(false);
  const [downloaded, setDownloaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [expired, setExpired] = useState(false);
  const [signedOut, setSignedOut] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [requiresReload, setRequiresReload] = useState(false);
  const [hostedAnswers, setHostedAnswers] = useState<Record<string, string>>({});
  const active = useRef<AbortController | null>(null);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; active.current?.abort(); }; }, []);
  const expiry = review && !["filled", "unknown", "cancelled", "expired"].includes(review.state) ? Math.min(review.expires_at_ms, review.payload?.upload_plan ? review.payload.upload_plan.expires_at_ms - 10000 : Infinity) : device?.state === "pending" ? device.expires_at_ms : null;
  useEffect(() => {
    if (expiry === null) return;
    const timer = setTimeout(() => setExpired(true), Math.min(Math.max(0, expiry - Date.now()), 2_147_483_647));
    return () => clearTimeout(timer);
  }, [expiry]);
  const validDevice = Boolean(deviceId && UUID.test(deviceId));
  const validReview = Boolean(reviewId && UUID.test(reviewId));
  const validApplication = Boolean(applicationId && IDENTIFIER.test(applicationId));
  const attachmentReview = (review?.review_protocol_version ?? review?.payload?.review_protocol_version) === 3;
  const submitReview = (review?.review_protocol_version ?? review?.payload?.review_protocol_version) === 4;
  const nativeUploadReview = attachmentReview && Boolean(review?.payload?.upload_plan);
  const uploadedVerified = review?.attachment_status === "uploaded_native_verified";
  const uploadedStatus = "Resume upload verified by the direct browser HTTPS storage acknowledgement. No application was submitted and no application credits were charged. Final submission requires a separate complete review.";
  const storage = review?.payload?.upload?.native_upload ? uploadedStorage(review.payload.upload.native_upload.uploaded_url) : null;
  const fileReviewed = attachmentReview || submitReview || downloaded;
  const hosted = review?.payload?.hosted;
  const hostedDirty = Boolean(hosted && answerIdentity(hostedAnswers) !== answerIdentity(hosted.draft_answers ?? {}));
  const hostedValid = Boolean(hosted && validHostedAnswers(hosted, hostedAnswers));
  const hasActions = attachmentReview || submitReview || Boolean(review?.payload?.fields.length);
  const invalid = Boolean((deviceId && !validDevice) || (reviewId && !validReview) || (applicationId && !validApplication) || (deviceId && reviewId));
  const returnUrl = validDevice ? `/browser-companion?device=${deviceId}` : validReview ? `/browser-companion?review=${reviewId}`
    : validApplication ? `/browser-companion?application=${encodeURIComponent(applicationId!)}` : "/browser-companion";

  async function post(operation: Operation, body: object, controller: AbortController): Promise<unknown> {
    const headers = await accountMutationHeaders();
    controller.signal.throwIfAborted();
    const response = await fetch(`/api/browser-assistance/candidate/${operation}`, { method: "POST", redirect: "error", cache: "no-store",
      credentials: "same-origin", signal: AbortSignal.any([controller.signal, AbortSignal.timeout(BROWSER_CLIENT_TIMEOUT_MS)]), headers, body: JSON.stringify(body) });
    if (!response.ok) {
      if (response.status === 401) { setSignedOut(true); throw new Error("Sign in with your HireWiz Google account to review this request."); }
      throw new Error(response.status === 403 ? "This request is unavailable for your account. Start again from your own companion."
        : response.status === 409 || response.status === 410 ? "This request changed or expired. Load its current status before taking another action."
        : "This browser assistance request could not be verified. Load its current status; no automatic retry will occur.");
    }
    return response.json();
  }
  async function perform(operation: Operation) {
    const isDeviceOperation = ["device", "confirm", "revoke"].includes(operation);
    const identity = isDeviceOperation ? deviceId : reviewId;
    if (invalid || !identity || active.current || (isDeviceOperation ? !validDevice : !validReview)) return;
    if ((operation === "confirm" || operation === "approve" || operation === "revise") && (expired || (expiry !== null && expiry <= Date.now()))) {
      setAccepted(false); setExpired(true); setError("This review expired. Load the current status from your companion."); return;
    }
    if ((operation === "confirm" || operation === "approve") && !accepted) return;
    if (operation === "confirm" && device?.state !== "pending") return;
    if (operation === "approve" && (!review || !APPROVABLE_STATES.has(review.state) || !fileReviewed || hostedDirty || !hasActions)) return;
    if (operation === "revise" && (!review || review.state !== "draft" || !hosted || !hostedDirty || !hostedValid)) return;
    if ((operation === "confirm" || operation === "approve" || operation === "revise") && requiresReload) return;
    const controller = new AbortController(); active.current = controller;
    setBusy(true); setError(null); setNotice(null); setSignedOut(false);
    if (operation === "device" || operation === "review") { setAccepted(false); setDownloaded(false); setExpired(false); setDevice(null); setReview(null); setHostedAnswers({}); }
    try {
      const body = operation === "confirm" ? { identity, confirmed: true, key_sha256: device!.key_sha256 }
        : operation === "approve" ? { identity, confirmed: true, review_digest: review!.review_digest }
        : operation === "revise" ? { identity, review_digest: review!.review_digest, hosted_answers: hostedAnswers }
        : operation === "cancel" || operation === "revoke" ? { identity, confirmed: true } : { identity };
      const result = await post(operation, body, controller);
      if (!mounted.current || controller.signal.aborted) return;
      if (isDeviceOperation) {
        if (!isDevice(result, identity)) throw new Error("The device response changed. Load the current status; no automatic retry will occur.");
        if ((operation === "confirm" && result.state !== "active") || (operation === "revoke" && result.state !== "revoked")
          || ((operation === "confirm" || operation === "revoke") && result.key_sha256 !== device?.key_sha256)) throw new Error("The confirmed device key or state changed. Load the current status; no automatic retry will occur.");
        setDevice(result); setAccepted(false);
        if (operation === "confirm") setNotice("Device confirmation saved. Return to your companion. You will approve each application separately.");
        if (operation === "revoke") setNotice("Device access revoked. Already disclosed answers cannot be recalled.");
      } else {
        if (!isReview(result, identity) || (validApplication && result.payload && Object.keys(result.payload).length && result.payload.application_id !== applicationId)) throw new Error("The application review response changed. Load the current status; no automatic retry will occur.");
        if (operation === "approve" && (!review || result.review_digest !== review.review_digest || !["approved", "running", "filled"].includes(result.state)
          || (result.payload && review.payload && payloadIdentity(result.payload) !== payloadIdentity(review.payload)))) throw new Error("The approved review changed. Load the current status; no automatic retry will occur.");
        if (operation === "revise" && (!review || !revisedReviewMatches(review, result, hostedAnswers))) throw new Error("The saved answer review changed. Load the current status; no automatic retry will occur.");
        setReview({ ...result, payload: object(result.payload) && Object.keys(result.payload).length ? result.payload : undefined }); setAccepted(false);
        setHostedAnswers(result.payload?.hosted?.draft_answers ?? {});
        if (operation === "revise") setNotice("Exact answers saved for this review. Review the updated answers below and approve them separately before your companion fills anything.");
        if (operation === "approve") setNotice(submitReview ? "Final submission approved and 20 credits reserved. Return to the companion for the single approved Submit application click. An uncertain outcome will never be resent automatically." : nativeUploadReview ? "Exact resume upload approved. Return to your companion for the single approved upload to the reviewed storage recipient and object key. The resume may be disclosed immediately. No final submission or application-credit charge is authorized." : attachmentReview ? "Attachment assistance approved. Return to your companion to fetch and select this exact saved file. Selection may upload it immediately. Employer upload remains unconfirmed; submit the application yourself."
          : result.payload?.review_protocol_version === 2 ? "Form assistance approved. Return to your companion to fill the exact reviewed answers and choices. Attach the file and submit yourself." : "Text assistance approved. Return to your companion to fill the reviewed answers. Attach the file and submit yourself.");
        if (operation === "cancel") setNotice(attachmentReview ? "Further attachment assistance cancelled. File bytes already disclosed may remain with the employer. An already authorized selection may finish within ten seconds."
          : "Further assistance cancelled. Answers already filled or disclosed may remain with the employer. A field already authorized may finish within ten seconds; future fields are stopped.");
      }
      setRequiresReload(false);
    } catch (failure) {
      if (mounted.current && !controller.signal.aborted) { setError(failure instanceof Error ? failure.message : "The request outcome is unavailable. Load its current status; no automatic retry will occur."); setAccepted(false); if (!["device", "review"].includes(operation)) setRequiresReload(true); }
    } finally {
      if (active.current === controller) active.current = null;
      if (mounted.current && !controller.signal.aborted) setBusy(false);
    }
  }
  async function download() {
    if (!review?.payload || review.payload.review_protocol_version === 3 || busy) return;
    setBusy(true); setError(null);
    try { await apiDownload(`/v1/employer-jobs/applications/${encodeURIComponent(review.payload.application_id)}/artifact`, review.payload.artifact.filename); if (mounted.current) setDownloaded(true); }
    catch (failure) { if (mounted.current) { setDownloaded(false); setError(failure instanceof Error ? failure.message : "The exact review file could not be downloaded."); } }
    finally { if (mounted.current) setBusy(false); }
  }
  async function copyApplication() {
    if (!validApplication) return;
    try { await navigator.clipboard.writeText(applicationId!); if (mounted.current) setCopied(true); }
    catch { if (mounted.current) setError("Copy the application ID shown below into your companion."); }
  }
  function updateHostedAnswer(id: string, value: string) {
    setHostedAnswers((previous) => {
      const next = { ...previous };
      if (value === "") delete next[id]; else next[id] = value;
      if (id.startsWith("current-role-") && value === "true") {
        const index = id.split("-").at(-1)!.split("_")[0];
        delete next[`end-date-month-${index}`]; delete next[`end-date-year-${index}`];
      }
      return next;
    });
    setAccepted(false); setNotice(null);
  }

  return <section className="surface space-y-6 p-5 sm:p-8" aria-busy={busy}>
    <div><p className="eyebrow">Razorpay / Greenhouse · Candidate-reviewed pilot</p><h1 className="mt-3 font-display text-3xl sm:text-4xl">{validDevice ? "Connect this browser" : validReview ? submitReview ? "Review final submission" : attachmentReview ? "Review your exact attachment" : "Review your exact answers" : "Prepare your browser companion"}</h1>
      <p className="mt-4 text-sm leading-7 text-muted-foreground">This pilot supports Razorpay’s Greenhouse portal. Use your signed-in HireWiz account to approve a device and each listed application action separately. Enter your country and employment answers yourself; unsupported controls remain manual. Where final submission assistance is available, it requires its own review and approval.</p></div>
    {invalid ? <p role="alert" className="text-sm text-coral">Open a valid device or application review from your companion.</p> : validDevice ? <div className="space-y-5">
      <Button variant="secondary" disabled={busy} onClick={() => void perform("device")}>{busy ? "Loading…" : device ? "Load current device status" : "Review device connection"}</Button>
      {device ? <div className="space-y-4"><p className="text-sm">Device status: <span className="font-semibold">{device.state}</span></p><dl className="space-y-3 rounded-xl border border-border p-4 text-xs"><div><dt className="font-semibold">Device key fingerprint</dt><dd className="mt-1 break-all font-mono">{device.key_sha256}</dd></div><div><dt className="font-semibold">Companion extension</dt><dd className="mt-1 break-all font-mono">{device.extension_id}</dd></div></dl>
        {device.state === "pending" ? <><label className="flex items-start gap-3 text-sm leading-6"><input type="checkbox" className="mt-1 h-4 w-4 shrink-0 accent-primary" checked={accepted} disabled={busy || expired || requiresReload} onChange={(event) => setAccepted(event.target.checked)} />I checked this key fingerprint against my companion and want to connect this device to my HireWiz account.</label><Button disabled={busy || expired || requiresReload || !accepted} onClick={() => void perform("confirm")}>Confirm this device</Button></> : null}
        {!["revoked", "expired"].includes(device.state) ? <Button variant="danger" size="sm" disabled={busy} onClick={() => void perform("revoke")}>Revoke this device</Button> : null}</div> : null}
    </div> : validReview ? <div className="space-y-5">
      <Button variant="secondary" disabled={busy} onClick={() => void perform("review")}>{busy ? "Loading…" : review ? "Load current review status" : "Load exact application review"}</Button>
      {review && review.payload && submitReview ? <div className="space-y-5">
        <p className="text-sm">Final submission status: <strong>{review.submission_status}</strong>. {review.employer_confirmed ? "Employer receipt independently verified. 20 credits charged." : "Employer receipt unverified."}</p>
        <div className="rounded-xl border border-border p-4"><h2 className="font-semibold">Candidate, company and opening</h2><p className="mt-2 text-sm">Candidate #{review.payload.candidate_id} · {review.payload.company} · {review.payload.job?.title}</p><p className="text-xs">Opening {review.payload.job?.external_id} · Application {review.payload.application_id}</p><a className="mt-2 block break-all text-sm text-primary underline" href={review.payload.target_url} target="_blank" rel="noopener noreferrer">{review.payload.target_url}</a></div>
        <div className="rounded-xl border border-border p-4"><h2 className="font-semibold">Exact previously approved resume</h2><p className="mt-2 text-sm">{review.payload.artifact.filename} · {review.payload.artifact.resume_choice} resume #{review.payload.artifact.resume_id}{review.payload.artifact.resume_version_id ? ` · version ${review.payload.artifact.resume_version_id}` : ""}</p><p className="text-xs">{review.payload.artifact.size_bytes} bytes · {review.payload.artifact.media_type}</p><p className="mt-2 break-all font-mono text-xs">SHA-256: {review.payload.artifact.sha256}</p><p className="mt-2 text-sm">{review.payload.receipt_contract ? "The separately approved resume upload has a verified direct browser HTTPS storage acknowledgement. Storage acceptance does not establish final application receipt." : "The portal reports this filename uploaded after your separately approved exact-file selection. This is browser-observed upload state."}</p></div>
        {review.payload.receipt_contract && storage ? <div className="rounded-xl border border-border p-4"><h2 className="font-semibold">Reviewed employer transport</h2><p className="mt-2 text-sm">Only the backend-verified complete direct employer HTTPS 200 exchange for this exact serialized application can confirm receipt and settle 20 credits. A page message, thank-you page or HTTP 202 cannot do so.</p><dl className="mt-3 space-y-2 break-all text-xs"><div><dt>Resume storage origin</dt><dd>{storage.origin}</dd></div><div><dt>Resume storage object key</dt><dd>{storage.key}</dd></div><div><dt>Application SHA-256</dt><dd>{review.payload.expected_application_sha256}</dd></div><div><dt>Upload plan SHA-256</dt><dd>{review.payload.upload?.upload_plan_sha256}</dd></div><div><dt>Reviewed time zone</dt><dd>{review.payload.native_context?.time_zone}</dd></div></dl></div> : null}
        <div><h2 className="font-semibold">Every reviewed answer</h2><dl className="mt-3 divide-y divide-border rounded-xl border border-border px-4">{review.payload.answers?.map((answer)=><div key={answer.id} className="py-3"><dt className="text-sm font-semibold">{hostedLabel(answer)}</dt><dd className="mt-1 whitespace-pre-wrap break-words text-sm">{answer.type==="combobox" ? answer.options?.find((option)=>option.value===answer.value)?.label : answer.type==="checkbox" ? answer.value==="true" ? "Yes" : "No" : answer.value || "Empty"}</dd></div>)}</dl></div>
        <div className="rounded-xl border border-border p-4"><h2 className="font-semibold">20-credit final submission</h2><ul className="mt-3 list-disc space-y-2 pl-5 text-sm leading-6">{review.payload.disclosures.map((entry,index)=><li key={index}>{entry}</li>)}</ul></div>
        {review.state==="draft" ? <><label className="flex items-start gap-3 text-sm leading-6"><input type="checkbox" className="mt-1 h-4 w-4 shrink-0 accent-primary" checked={accepted} disabled={busy || expired || requiresReload} onChange={(event)=>setAccepted(event.target.checked)} />I reviewed every answer, candidate, opening, employer destination and exact resume above. I authorize one final Submit application click and the 20-credit reservation policy. Information may be disclosed immediately; cancellation cannot undo a click already authorized.</label><Button disabled={busy || expired || requiresReload || !accepted} onClick={()=>void perform("approve")}>Approve final submission · reserve 20 credits</Button></> : null}
        {!["cancelled","expired","filled"].includes(review.state) ? <Button variant="danger" disabled={busy} onClick={()=>void perform("cancel")}>Cancel future submission actions</Button> : null}
        {["unknown","running"].includes(review.state) ? <p role="status" className="text-sm leading-6">The attempt may have reached the employer. No verified receipt is available; 20 credits remain held. Inspect status from the companion without another submit click. Do not resend automatically.</p> : null}
      </div> : review && review.payload ? <div className="space-y-5"><p className="text-sm">Assistance status: <span className="font-semibold">{review.state}</span>{attachmentReview ? " · attachment only" : ` · ${review.filled_count} answers filled`}</p>
        <div className="rounded-xl border border-border p-4"><h2 className="font-semibold">Exact destination and file</h2><a href={review.payload.target_url} target="_blank" rel="noopener noreferrer" className="mt-2 block break-all text-sm text-primary underline underline-offset-4">{review.payload.target_url}</a><p className="mt-3 break-words text-sm font-semibold">{review.payload.artifact.filename}</p><p className="mt-1 text-xs text-muted-foreground">{(review.payload.artifact.size_bytes / 1024).toFixed(1)} KB · {review.payload.artifact.media_type}</p><p className="mt-3 break-all font-mono text-xs">SHA-256: {review.payload.artifact.sha256}</p>
          {attachmentReview ? <dl className="mt-3 space-y-2 break-words text-xs"><div><dt className="font-semibold">Saved resume source</dt><dd>{review.payload.artifact.resume_choice} resume #{review.payload.artifact.resume_id}{review.payload.artifact.resume_version_id ? ` · version ${review.payload.artifact.resume_version_id}` : ""}</dd></div><div><dt className="font-semibold">Sealed artifact</dt><dd>{review.payload.artifact.artifact_id}</dd></div><div><dt className="font-semibold">Portal file input</dt><dd>Resume/CV · resume · currently empty</dd></div></dl>
            : <Button variant="secondary" size="sm" className="mt-3" disabled={busy} onClick={() => void download()}>Download exact review file</Button>}</div>
        {review.payload.upload_plan ? <div className="rounded-xl border border-border p-4"><h2 className="font-semibold">Exact resume upload recipient</h2><dl className="mt-3 space-y-3 break-all text-xs"><div><dt className="font-semibold">Employer storage origin</dt><dd>{review.payload.upload_plan.storage_origin}</dd></div><div><dt className="font-semibold">Storage object key</dt><dd>{review.payload.upload_plan.storage_key}</dd></div><div><dt className="font-semibold">Storage permission expires</dt><dd>{new Date(review.payload.upload_plan.expires_at_ms).toISOString()}</dd></div>{([ ["Upload policy SHA-256", review.payload.upload_plan.policy_sha256], ["Upload fields SHA-256", review.payload.upload_plan.fields_sha256], ["Presign response SHA-256", review.payload.upload_plan.presign_response_sha256], ["Resume component SHA-256", review.payload.upload_plan.resume_component_sha256] ] as const).map(([label, value]) => <div key={label}><dt className="font-semibold">{label}</dt><dd>{value}</dd></div>)}</dl><p className="mt-3 text-sm leading-6">This sends the exact saved resume once to this storage origin and object key. The companion constructs the exact upload bytes once from this resume and the approved upload fields. Its complete contents may be disclosed immediately. No final application submission or application-credit charge is authorized. Approval closes ten seconds before the storage permission expires.</p></div> : null}
        {hosted && review.state === "draft" ? <fieldset disabled={busy || expired || requiresReload} className="space-y-4 rounded-xl border border-border p-4"><legend className="px-1 font-semibold">Country and employment answers</legend>
          <p className="text-sm leading-6">Enter only the exact values you want filled. Country is the phone country-code selector, not citizenship or work authorization. Employment answers come from you. Leave an entry blank to keep its current value.</p>
          <p className="text-sm leading-6">Changing Current role or adding another employment row ends this attempt. Inspect the portal again and create a fresh review before filling newly visible controls.</p>
          {hosted.controls.map((control) => {
            const index = Number(control.id.split("-").at(-1)!.split("_")[0]);
            const endDisabled = control.id.startsWith("end-date-") && (hostedAnswers[`current-role-${index}_1`] ?? String(hosted.rows[index].current_role)) === "true";
            const current = control.type === "combobox" ? control.options!.find(({ value }) => value === hosted.values[control.id])?.label ?? "Empty" : hosted.values[control.id] || "Empty";
            return <div key={control.id} className="space-y-2"><label className="block text-sm font-semibold" htmlFor={`hosted-${control.id}`}>{hostedLabel(control)}</label>
              {control.type === "button" ? <input id={`hosted-${control.id}`} type="checkbox" className="h-4 w-4 accent-primary" checked={Boolean(hostedAnswers[control.id])} onChange={(event) => updateHostedAnswer(control.id, event.target.checked ? String(hosted.rows.length + 1) : "")} />
                : control.type === "combobox" || control.type === "checkbox" ? <select id={`hosted-${control.id}`} className="block w-full rounded-lg border border-border bg-background p-2 text-sm" value={hostedAnswers[control.id] ?? ""} disabled={endDisabled} onChange={(event) => updateHostedAnswer(control.id, event.target.value)}>
                  <option value="">Keep current value</option>{control.type === "checkbox" ? <><option value="true">Yes, this is my current role</option><option value="false">No, this is not my current role</option></> : control.options!.map(({ value, label }) => <option key={value} value={value}>{label}</option>)}</select>
                  : <input id={`hosted-${control.id}`} type="text" className="block w-full rounded-lg border border-border bg-background p-2 text-sm" maxLength={control.id.includes("-year-") ? 4 : 255} inputMode={control.id.includes("-year-") ? "numeric" : undefined} value={hostedAnswers[control.id] ?? ""} disabled={endDisabled} onChange={(event) => updateHostedAnswer(control.id, event.target.value)} />}
              <p className="text-xs text-muted-foreground">Current value: {current}</p></div>;
          })}
          {hostedDirty && !hostedValid ? <p role="alert" className="text-sm text-coral">Use exact listed choices, four-digit years and an end date on or after the start date. Remove leading or trailing spaces.</p> : null}
          <Button variant="secondary" disabled={!hostedDirty || !hostedValid} onClick={() => void perform("revise")}>Save exact answers</Button>
          <p className="text-xs leading-6 text-muted-foreground">Saving changes only this review draft. It does not fill the portal or approve these answers. Approval uses the updated review and its original expiry.</p>
        </fieldset> : null}
        {!attachmentReview ? <div><h2 className="font-semibold">Answers to fill</h2>{review.payload.fields.length ? <dl className="mt-3 divide-y divide-border rounded-xl border border-border px-4">{review.payload.fields.map((field) => <div key={field.id} className="py-3"><dt className="text-sm font-semibold">{hosted?.controls.some(({ id }) => id === field.id) ? hostedLabel(field) : field.label}</dt><dd className="mt-1 whitespace-pre-wrap break-words text-sm">{field.type === "combobox" ? field.value_label : field.type === "button" ? `Add row ${field.value}` : field.type === "checkbox" ? field.value === "true" ? "Yes, this is my current role" : "No, this is not my current role" : field.value}</dd><dd className="mt-2 whitespace-pre-wrap break-words text-xs text-muted-foreground">Current value: {(field.type === "combobox" ? field.expected_before_label : field.expected_before) || "Empty"}</dd></div>)}</dl> : <p className="mt-3 text-sm leading-6">No changed answers to fill. Save at least one change before approval is available.</p>}</div> : null}
        <div className="rounded-xl border border-border bg-surface p-4"><h2 className="font-semibold">Before you approve</h2><p className="mt-2 text-sm leading-6">{nativeUploadReview ? "After approval your companion may fetch and upload only the exact saved resume to the reviewed employer storage origin and object key, then update this opening’s Resume/CV control after direct storage acknowledgement. Cancelling cannot recall disclosed bytes; an already authorized upload may finish within its ten-second lease." : attachmentReview ? "Your companion may fetch this exact saved file only after you approve. Selecting it can immediately disclose or upload its bytes to the employer or its upload service before final submission. Cancelling cannot recall earlier disclosure; an already authorized selection may finish within ten seconds."
          : "Filling can disclose or autosave answers before submission. Cancelling stops future fields. A field already authorized may finish within ten seconds, and earlier callbacks cannot be recalled."}</p>{review.payload.disclosures.length ? <ul className="mt-3 list-disc space-y-2 pl-5 text-sm leading-6">{review.payload.disclosures.map((disclosure, index) => <li key={index}>{disclosure}</li>)}</ul> : null}<p className="mt-3 text-sm leading-6">{nativeUploadReview ? "This authorizes one resume upload only. Storage acceptance does not establish application receipt. Final submission needs a separate complete review and approval." : attachmentReview ? "This authorizes file selection only. Employer upload is unconfirmed, no application receipt is established, and you must submit the application yourself." : "Selecting a resume file manually may upload its bytes immediately. HireWiz does not attach files or submit from this flow."}</p><p className="mt-3 text-sm font-semibold">{nativeUploadReview ? "0 application credits charged for this resume upload." : attachmentReview ? "0 application credits charged for this attachment assistance." : "0 application credits charged for this fill-only assistance."}</p></div>
        <div><h2 className="font-semibold">Complete these on the employer portal</h2><ul className="mt-3 list-disc space-y-2 pl-5 text-sm leading-6">{review.payload.manual_fields.filter((field) => field.required).map((field, index) => <li key={`${field.id}:${index}`} className="break-words">{field.label || field.id || "Required manual control"}</li>)}<li>{attachmentReview ? "Check the selected file and any upload status, complete the remaining fields, resolve CAPTCHA or login, and submit the application yourself." : "Attach the reviewed resume, resolve any CAPTCHA or login, and submit the application yourself."}</li></ul></div>
        {APPROVABLE_STATES.has(review.state) ? <><label className="flex items-start gap-3 text-sm leading-6"><input type="checkbox" className="mt-1 h-4 w-4 shrink-0 accent-primary" checked={accepted} disabled={busy || expired || !fileReviewed || requiresReload || hostedDirty || !hasActions} onChange={(event) => setAccepted(event.target.checked)} />{nativeUploadReview ? "I reviewed the exact saved file, resume source, employer storage origin, object key and hashes above. I authorize one upload of only this resume, which discloses its bytes immediately. This does not authorize final submission or an application-credit charge." : attachmentReview ? "I reviewed the exact saved file, resume source and employer destination above. I authorize my companion to fetch and select only this file in the empty Resume/CV input, which may upload it immediately. Final submission remains my responsibility." : "I reviewed the exact file, destination and answers above, including pre-submit disclosure. I authorize filling only; attachment and final submission remain my responsibility."}</label>{!fileReviewed ? <p className="text-xs text-muted-foreground">Download and review the exact file before approving these answers.</p> : null}{hostedDirty ? <p className="text-xs text-muted-foreground">Save your changed answers and review them before approving.</p> : null}<Button disabled={busy || expired || !fileReviewed || requiresReload || !accepted || hostedDirty || !hasActions} onClick={() => void perform("approve")}>{nativeUploadReview ? "Approve exact resume upload" : attachmentReview ? "Approve exact attachment assistance" : review.payload.review_protocol_version === 2 ? "Approve reviewed form assistance" : "Approve reviewed text assistance"}</Button></> : null}
        {!["cancelled", "expired", "filled"].includes(review.state) ? <Button variant="danger" disabled={busy} onClick={() => void perform("cancel")}>Cancel further assistance</Button> : null}
        {review.state === "unknown" ? <p role="status" className="text-sm leading-6 text-muted-foreground">{attachmentReview ? "The file selection or upload outcome is uncertain. Inspect the employer portal. No automatic retry will occur." : "A previous fill outcome is uncertain. Inspect the employer portal before continuing. No automatic retry will occur."}</p> : null}
        {review.state === "filled" ? <p role="status" className="text-sm leading-6 text-muted-foreground">{attachmentReview ? uploadedVerified ? uploadedStatus : "Resume selected locally. Employer upload is unconfirmed. No application receipt or fee is established. Check the portal and submit the application yourself." : "Reviewed answers filled only. HireWiz has not submitted this application. Finish the remaining fields, attachment and submission yourself."}</p> : null}
      </div> : review && submitReview ? <p role="status" className="text-sm leading-6 text-muted-foreground">{review.submission_status==="confirmed" ? "Employer receipt independently verified. 20 application credits charged. No further submit action is available." : review.submission_status==="verified_not_submitted" ? "Independent reconciliation verified that this application was not submitted. The 20-credit hold was returned." : review.submission_status==="unknown" ? "The submission outcome is uncertain and no verified employer receipt is available. 20 credits remain held for reconciliation. Inspect the portal without resubmitting; no automatic retry will occur." : "This final submission review ended before a send claim. No final click is authorized."}</p> : review ? <p role="status" className="text-sm leading-6 text-muted-foreground">{review.state === "filled" ? attachmentReview ? uploadedVerified ? uploadedStatus : "Resume selected locally. Employer upload is unconfirmed. No application receipt or fee is established. Check the portal and submit the application yourself." : "Reviewed answers filled only. Finish the remaining fields, attachment and final submission yourself." : review.state === "unknown" ? attachmentReview ? "The file selection or upload outcome is uncertain. Inspect the employer portal; no automatic retry will occur." : "A previous fill outcome is uncertain. Inspect the employer portal before continuing; no automatic retry will occur." : review.state === "cancelled" ? attachmentReview ? "Further attachment assistance cancelled. File bytes already disclosed may remain with the employer." : "Further assistance cancelled. Already disclosed answers may remain with the employer." : "This review expired. Start a fresh request from your companion."}</p> : null}
    </div> : <div className="space-y-4"><div className="rounded-xl border border-border p-4"><h2 className="font-semibold">Install the Chrome pilot</h2><p className="mt-2 text-sm leading-7">On your laptop or desktop, download and unzip the companion. Open Chrome&apos;s Extensions page, turn on Developer mode, choose Load unpacked, and select the extracted hirewiz-browser-companion folder. Pin HireWiz to your toolbar. To update an existing unpacked install, replace its files and reload that same extension; keep its browser storage. If a prior connection is uncertain, choose Inspect uncertain connection and follow only the recovery action it offers. The pilot asks for HireWiz and Greenhouse access when you choose to connect.</p><a className="mt-3 inline-block text-sm font-semibold text-primary underline underline-offset-4" href="/downloads/hirewiz-browser-companion-0.8.7.zip" download>Download HireWiz companion 0.8.7</a><p className="mt-3 text-xs leading-6 text-muted-foreground">Chrome desktop only. On mobile, use the company portal manually. Supported answers require a fill review. Attachment requires a separate exact-file review. Final submission requires another complete review and a 20-credit reservation when enabled.</p></div><ol className="list-decimal space-y-3 pl-5 text-sm leading-7"><li>Open your saved application and the matching Razorpay employer portal.</li><li>Open the HireWiz companion in Chrome and connect this device.</li><li>Inspect the portal and return here to review the exact file and listed action.</li><li>Approve each listed action separately. Check the portal, then request a final submission review when available.</li></ol>{validApplication ? <div className="rounded-xl border border-border p-4"><p className="text-sm font-semibold">Application ID for your companion</p><code className="mt-2 block break-all text-xs">{applicationId}</code><Button variant="secondary" size="sm" className="mt-2" onClick={() => void copyApplication()}>Copy application ID</Button>{copied ? <p role="status" className="mt-2 text-xs text-primary">Application ID copied.</p> : null}</div> : null}<p className="text-sm leading-6 text-muted-foreground">Open a device connection or application review from the installed companion. This page does not start an employer action by itself.</p></div>}
    {expired ? <p role="status" className="text-sm leading-6 text-muted-foreground">This review expired. Load the current status or start a fresh request from your companion.</p> : null}
    {notice ? <p role="status" className="text-sm leading-6 text-primary">{notice}</p> : null}
    {error ? <p role="alert" className="text-sm leading-6 text-coral">{error}</p> : null}
    {signedOut ? <Button variant="secondary" onClick={() => void signIn("google", { callbackUrl: returnUrl })}>Sign in with Google</Button> : null}
    <Link href="/employer-jobs" className="inline-block text-sm font-semibold text-primary underline underline-offset-4">Return to job search</Link>
  </section>;
}
