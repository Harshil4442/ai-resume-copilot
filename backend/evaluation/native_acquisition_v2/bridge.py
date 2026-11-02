"""Pure consistency checks for proposed native40 definitions-only bridge data.

No callback/JSON can attest a provider or fund/claim/send a request. This module
is not imported by the current engine, channel or Job. Its plans remain inert
until separately qualified provenance, capture and integration work exists.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import tarfile
import zlib
from collections import Counter
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

PROJECT = "ai-resume-parser-482412"
REPOSITORY = "us-central1-docker.pkg.dev/" + PROJECT + "/cloud-run-source-deploy/"
INSPECTOR = REPOSITORY + "hirewiz-document-inspector@sha256:"
SCHEMA = "native-definitions-equivalence-metadata-v1"
KIND = "native_cloud_build_fixed_renderer_definitions_v1"
REFRESH_TEMPLATE_SHA256 = "8618952dc7f999d7e8138a421b5de0a59f9b518b5a8250da13cc407485509b5b"
# Exact reviewed helper bytes, never a caller-selected recipe version. These
# still require genuine Git/Build provenance before any native use.
DEFINITION_HELPER_VERSIONS = {
    "14fe5261476c196411e3ae7e701a95f483d017c7b01366edf3dd0fb5d09d68ed": 1,
    "082a2c68248cd6193994589ccbfed850f4a65b713da7ecfc24e16d068cc5ba06": 1,
    "3090f162a7ef8cfe43f592afcdd4488ab2c7adf83f6a06a931708df685c4af06": 2,
}
DOCKER_BUILDER = "gcr.io/cloud-builders/docker@sha256:40c2fb4fcd0ad51376eef166c2e7b2b40a3508d5776e2bf33db3783ab39d0f2e"
POLICY = "22a012f2fb7b6d3b696d8772563e9363566b9f03fb83385cfeffbc84a26459eb"
PATH = r"[A-Za-z0-9_-][A-Za-z0-9._-]*(?:/[A-Za-z0-9_-][A-Za-z0-9._-]*){0,7}"
MAX_METADATA = 2 * 1024**2
MAX_SOURCE_ARCHIVE = 4 * 1024**2
MAX_CLOSURE = 256 * 1024**2
Hash = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Image = Annotated[str, Field(pattern="^" + re.escape(REPOSITORY) + r"[a-z0-9-]+@sha256:[a-f0-9]{64}$")]
InspectorImage = Annotated[str, Field(pattern="^" + re.escape(INSPECTOR) + r"[a-f0-9]{64}$")]


def need(value: object, code: str) -> None:
    if not value:
        raise ValueError("definitions_bridge_" + code)


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


class Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")


class Artifact(Closed):
    path: str = Field(pattern="^" + PATH + "$", max_length=512)
    size: int = Field(strict=True, ge=1, le=MAX_SOURCE_ARCHIVE)
    sha256: Hash


class MaterialIdentity(Closed):
    """Same approved material claims; source/provider verification is separate."""
    app_sha256: Hash
    app_files: int = Field(strict=True, gt=0, le=4096)
    original_entry_path: Literal["backend/scripts/native_acquisition_job.py"]
    original_entry_sha256: Hash
    compiler_image: Image
    compiler_policy_sha256: Hash
    inspector_policy_sha256: Literal["22a012f2fb7b6d3b696d8772563e9363566b9f03fb83385cfeffbc84a26459eb"]
    source_sha256: Hash
    parser_sha256: Hash
    runtime_sha256: Hash
    fonts_sha256: Hash
    scanner_binary_sha256: Hash
    compiler_runtime_sha256: Hash
    execution_configuration_sha256: Hash
    non_definition_tree_sha256: Hash
    renderer_version: str = Field(min_length=1, max_length=200)
    scanner_version: Literal["1.4.3"]


class OciEvidence(Closed):
    manifest: Artifact
    configuration: Artifact


class GenerationArtifact(Closed):
    bucket: Literal["ai-resume-parser-482412_cloudbuild"]
    name: str = Field(pattern=r"^hirewiz-document-refresh-source/[a-f0-9]{32}/source\.tar\.gz$")
    generation: str = Field(pattern=r"^[1-9][0-9]{0,20}$")
    artifact: Artifact


class RecipeSource(Closed):
    """Raw helper custody bound to the Build's source labels, not Git proof."""
    source_commit: str = Field(pattern=r"^[a-f0-9]{40}$")
    qualification_sha256: Hash
    helper_path: Literal["infra/gcp/document_source_candidate.py"]
    helper: Artifact


class DerivativeEdge(Closed):
    kind: Literal["native_cloud_build_fixed_renderer_definitions_v1"]
    parent_image: InspectorImage
    image: InspectorImage
    build_id: str = Field(pattern=r"^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$")
    nonce: str = Field(pattern=r"^[a-f0-9]{32}$")
    source_commit: str = Field(pattern=r"^[a-f0-9]{40}$")
    qualification_sha256: Hash
    raw_build: Artifact
    source_archive: GenerationArtifact
    recipe: Artifact
    recipe_source: RecipeSource
    material_inventory: Artifact
    oci: OciEvidence
    layer_inventories: list[Artifact] = Field(min_length=2, max_length=2)


class Case(Closed):
    case_id: str = Field(pattern=r"^synth-[0-9]{3}$")
    source_format: Literal["pdf", "docx", "tex", "texzip"]
    source_sha256: Hash
    source_size: int = Field(strict=True, ge=1, le=5 * 1024**2)
    compiled_pdf_sha256: Hash | None = None
    compiled_pdf_size: int | None = Field(default=None, strict=True, ge=1, le=5 * 1024**2)
    extracted_text_sha256: Hash
    page_sha256: list[Hash] = Field(min_length=1, max_length=1024)

    @model_validator(mode="after")
    def pdf_identity(self) -> Case:
        need((self.source_format in {"tex", "texzip"}) == (self.compiled_pdf_sha256 is not None)
             and (self.compiled_pdf_sha256 is None) == (self.compiled_pdf_size is None), "compiled_pdf_identity")
        return self


class RendererOrigin(Closed):
    """Mandatory retained raw origin references, never a synthesized approval."""
    record: Artifact
    execution: Artifact
    inventory: Artifact
    pages: Artifact


class OriginalEvidence(Closed):
    review_lock: Artifact
    review_evidence: Artifact
    development_plan: Artifact
    capture_registry: Artifact
    runner_receipt: Artifact
    ci_metadata: Artifact
    runner_build: Artifact
    inspector_build: Artifact
    inspector_source_archive: GenerationArtifact
    inspector_recipe_source: RecipeSource | None = None
    renderer_origin: RendererOrigin
    inspector_image: InspectorImage
    material: MaterialIdentity
    material_inventory: Artifact
    oci: OciEvidence
    cases: list[Case] = Field(min_length=40, max_length=40)

    @model_validator(mode="after")
    def population(self) -> OriginalEvidence:
        need(len({case.case_id for case in self.cases}) == 40
             and Counter(case.source_format for case in self.cases) == dict.fromkeys(("pdf", "docx", "tex", "texzip"), 10), "original40")
        need(sum(len(case.page_sha256) for case in self.cases) <= 1024, "original_page_bound")
        return self


class CurrentAcceptance(Closed):
    """Purpose is a source-bound route, not an age-bypass boolean."""
    purpose: Literal["new_candidate_and_promotion", "already_serving"]
    profile: Literal["source_v4_17_cases"]
    image: InspectorImage
    material: MaterialIdentity
    definition_issued_at: datetime
    native_record: Artifact
    execution: Artifact
    inventory: Artifact
    service: Artifact
    local_candidate_proof: Artifact | None = None


class BridgeEvidence(Closed):
    schema_version: Literal["native-definitions-equivalence-metadata-v1"]
    original: OriginalEvidence
    refresh_template: Artifact
    chain: list[DerivativeEdge] = Field(min_length=1, max_length=32)
    current: CurrentAcceptance
    files: list[Artifact] = Field(min_length=1, max_length=512)

    @model_validator(mode="after")
    def bounded(self) -> BridgeEvidence:
        need(len({row.path for row in self.files}) == len(self.files)
             and sum(row.size for row in self.files) <= MAX_CLOSURE, "metadata_closure_bound")
        return self


class ExpenseQuote(Closed):
    currency: Literal["INR"]
    native_per_request_minor: int = Field(strict=True, gt=0, le=1_000_000)
    native_reserved_minor: int = Field(strict=True, gt=0, le=1_000_000_000)
    render_reserved_minor: int = Field(strict=True, gt=0, le=1_000_000_000)
    runner_reserved_minor: int = Field(strict=True, gt=0, le=1_000_000_000)
    provenance_io_reserved_minor: int = Field(strict=True, gt=0, le=1_000_000_000)
    build_acceptance_reserved_minor: int = Field(strict=True, ge=0, le=1_000_000_000)
    build_acceptance_mode: Literal["retained_current_proof", "new_work_reserved"]
    total_reserved_minor: int = Field(strict=True, gt=0, le=1_000_000_000)

    @model_validator(mode="after")
    def whole60(self) -> ExpenseQuote:
        need(self.native_reserved_minor == 60 * self.native_per_request_minor, "whole60_reserve")
        need(self.build_acceptance_mode == "retained_current_proof" or self.build_acceptance_reserved_minor > 0,
             "new_build_acceptance_not_free")
        need(self.total_reserved_minor >= sum((self.native_reserved_minor, self.render_reserved_minor,
             self.runner_reserved_minor, self.provenance_io_reserved_minor, self.build_acceptance_reserved_minor)), "whole_cost_quote")
        return self


class BridgePermit(Closed):
    schema_version: Literal["native40-definitions-bridge-permit-v1"]
    purpose: Literal["unreviewed_native40_rescan_no_model_no_heldout"]
    epoch_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{7,79}$")
    namespace: str = Field(pattern=r"^gs://[a-z0-9][a-z0-9._-]{2,221}/quality-native-equivalence/[a-z0-9][a-z0-9-]{7,79}/$")
    bridge_evidence_sha256: Hash
    original_review_lock_sha256: Hash
    inspector_image: InspectorImage
    valid_from: datetime
    expires_at: datetime
    request_limit: int = Field(strict=True, ge=60, le=60)
    quote: ExpenseQuote
    funding_evidence: Artifact


class Inert(Closed):
    native_approval: Literal[False] = False
    processing_authorized: Literal[False] = False
    authorizes_retail: Literal[False] = False
    authorizes_scan: Literal[False] = False
    actual_native_cost_minor: None = None
    reserved_expense_released: Literal[False] = False
    requires_native_provenance_verification: Literal[True] = True
    funding_consumed: Literal[False] = False
    claims_created: Literal[0] = 0
    requests_sent: Literal[0] = 0
    memory_qualified: Literal[False] = False
    equivalence_proven: Literal[False] = False


class MetadataReport(Inert):
    state: Literal["consistent_unverified_metadata"] = "consistent_unverified_metadata"
    evidence_sha256: Hash
    original_review_lock_sha256: Hash
    current_image: Image
    material_sha256: Hash
    chain_edges: int = Field(strict=True, ge=1, le=32)


class ScanIntent(Closed):
    case_id: str = Field(pattern=r"^synth-[0-9]{3}$")
    phase: Literal["bridge_source", "bridge_compiled_pdf"]
    source_format: Literal["pdf", "docx", "tex", "texzip"]
    source_sha256: Hash
    source_size: int = Field(strict=True, ge=1, le=5 * 1024**2)
    original_source_sha256: Hash
    original_review_lock: Artifact
    original_renderer_origin: Artifact
    current_image: Image
    current_profile_sha256: Hash
    bridge_evidence_sha256: Hash
    replay_authorized: Literal[False] = False


class ScanPlan(Inert):
    state: Literal["planned_unverified_metadata"] = "planned_unverified_metadata"
    namespace: str
    permit_sha256: Hash
    evidence_sha256: Hash
    original_case_count: Literal[40] = 40
    maximum_http_requests: Literal[60] = 60
    new_compilations: Literal[0] = 0
    intents: list[ScanIntent] = Field(min_length=60, max_length=60)
    total_reserved_minor: int = Field(strict=True, gt=0)


def _json(raw: bytes) -> dict[str, Any]:
    def pairs(rows):
        need(len(rows) == len({key for key, _ in rows}), "duplicate_json_key")
        return dict(rows)
    try:
        value = json.loads(raw, object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        need(type(value) is dict, "json_object")
        pending = [(value, 0)]
        while pending:
            node, depth = pending.pop()
            need(depth <= 16, "json_depth")
            if isinstance(node, dict):
                pending.extend((item, depth + 1) for item in node.values())
            elif isinstance(node, list):
                pending.extend((item, depth + 1) for item in node)
        return value
    except (ValueError, TypeError, RecursionError):
        raise ValueError("definitions_bridge_invalid_json") from None


class _Closure:
    def __init__(self, files: list[Artifact], reader: Callable[[Artifact], bytes]):
        self.files = {row.path: row for row in files}
        self.reader = reader
        self.used: set[str] = set()

    def read(self, ref: Artifact, maximum: int = MAX_METADATA) -> bytes:
        need(self.files.get(ref.path) == ref and ref.size <= maximum, "unbound_metadata_reference")
        raw = self.reader(ref)
        need(type(raw) is bytes and len(raw) == ref.size and hashlib.sha256(raw).hexdigest() == ref.sha256,
             "metadata_bytes_changed")
        self.used.add(ref.path)
        return raw

    def json(self, ref: Artifact) -> dict[str, Any]:
        return _json(self.read(ref))


def _material(closure: _Closure, ref: Artifact, expected: MaterialIdentity) -> None:
    value = closure.json(ref)
    need(set(value) == {"schema_version", "identity", "files", "execution_configuration"}
         and value["schema_version"] == "native-material-inventory-v1"
         and MaterialIdentity.model_validate(value["identity"]) == expected, "material_identity_changed")
    maps = value["files"]
    groups = {"source": "source_sha256", "parser": "parser_sha256", "runtime": "runtime_sha256",
        "fonts": "fonts_sha256", "scanner_binary": "scanner_binary_sha256", "compiler_runtime": "compiler_runtime_sha256"}
    need(type(maps) is dict and set(maps) == set(groups), "incomplete_material_inventory")
    for group, field in groups.items():
        rows = maps[group]
        need(type(rows) is dict and 1 <= len(rows) <= 4096
             and all(type(key) is str and 0 < len(key) <= 512 and type(sha) is str
                     and re.fullmatch(r"[a-f0-9]{64}", sha) for key, sha in rows.items())
             and digest(rows) == getattr(expected, field), "material_files_changed")
    need(digest(value["execution_configuration"]) == expected.execution_configuration_sha256, "configuration_changed")


def _oci(closure: _Closure, evidence: OciEvidence, image: str) -> tuple[dict, dict]:
    need(evidence.manifest.sha256 == image.split("@sha256:", 1)[1], "oci_image_digest")
    manifest, config = closure.json(evidence.manifest), closure.json(evidence.configuration)
    need(manifest.get("schemaVersion") == 2 and manifest.get("config", {}).get("digest")
         == "sha256:" + evidence.configuration.sha256
         and manifest["config"].get("size") == evidence.configuration.size
         and type(manifest.get("layers")) is list and 1 <= len(manifest["layers"]) <= 256,
         "oci_configuration_binding")
    layers = manifest["layers"]
    need(all(type(row) is dict and re.fullmatch(r"sha256:[a-f0-9]{64}", row.get("digest", ""))
             and type(row.get("size")) is int and row["size"] > 0 for row in layers)
         and config.get("architecture") == "amd64" and config.get("os") == "linux"
         and config.get("rootfs", {}).get("type") == "layers"
         and len(config["rootfs"].get("diff_ids", [])) == len(layers)
         and all(re.fullmatch(r"sha256:[a-f0-9]{64}", row) for row in config["rootfs"]["diff_ids"]), "oci_layers")
    return manifest, config


def _execution_configuration(config: dict) -> dict:
    value = dict(config.get("config", {}))
    labels = dict(value.pop("Labels", {}))
    for key in ("hirewiz.document-source.commit", "hirewiz.document-source.qualification"):
        labels.pop(key, None)
    value["Labels"] = labels
    return value


def _step_arguments(build: dict) -> list[dict]:
    steps = build.get("steps", [])
    need(type(steps) is list and len(steps) == 2 and all(type(row) is dict and not any(row.get(key)
         for key in ("entrypoint", "script", "env", "secretEnv", "dir", "volumes", "automapSubstitutions", "allowFailure"))
         for row in steps), "build_execution_override")
    return [{"name": row.get("name"), "args": row.get("args")} for row in steps]


def _recipe_version(closure: _Closure, source: RecipeSource | None, commit: str, qualification: str) -> int:
    need(source is not None, "recipe_source_missing")
    assert source is not None
    need(source.source_commit == commit and source.qualification_sha256 == qualification,
         "recipe_source_changed")
    helper = closure.read(source.helper)
    version = DEFINITION_HELPER_VERSIONS.get(hashlib.sha256(helper).hexdigest())
    need(version is not None, "recipe_helper_unreviewed")
    assert version is not None
    return version


def _rendered_recipe(template: bytes, parent: str, *, version: int) -> bytes:
    parents = re.findall(rb"^FROM ([^ ]+)(?: AS definitions)?$", template, re.M)
    need(len(parents) == 2 and parents[0] == parents[1], "template_parent")
    recipe = template.replace(parents[0], parent.encode())
    need(type(version) is int and version in {1, 2}, "recipe_version_unreviewed")
    if version == 1:
        return recipe
    command = b"    && freshclam --stdout --datadir=/var/lib/clamav\n"
    need(recipe.count(command) == 1, "definition_update_command_changed")
    return recipe.replace(command, b"    && chown -R 100:101 /var/lib/clamav \\\n"
        b"    && find /var/lib/clamav -type d -exec chmod 0755 {} + \\\n"
        b"    && find /var/lib/clamav -type f -exec chmod 0644 {} + \\\n"
        b"    && freshclam --stdout --datadir=/var/lib/clamav \\\n"
        b"    && chown -R 0:0 /var/lib/clamav \\\n"
        b"    && find /var/lib/clamav -type d -exec chmod 0555 {} + \\\n"
        b"    && find /var/lib/clamav -type f -exec chmod 0444 {} +\n")


def _archive_entries(raw: bytes, limit: int) -> dict[str, bytes]:
    try:
        decoder = zlib.decompressobj(wbits=31)
        unpacked = decoder.decompress(raw, limit + 1)
        need(decoder.eof and not decoder.unused_data and not decoder.unconsumed_tail and len(unpacked) <= limit,
             "source_archive_bound")
        with tarfile.open(fileobj=io.BytesIO(unpacked), mode="r:") as archive:
            members = archive.getmembers()
            need(1 <= len(members) <= 128 and sum(row.size for row in members) <= limit, "source_archive_members")
            result = {}
            for row in members:
                name = row.name.removeprefix("./")
                need(row.isfile() and not row.pax_headers and re.fullmatch(PATH, name)
                     and name not in result and 0 < row.size <= limit, "source_archive_member_type")
                stream = archive.extractfile(row)
                need(stream is not None, "source_archive_member_missing")
                assert stream is not None
                result[name] = stream.read(row.size + 1)
                need(len(result[name]) == row.size, "source_archive_member_size")
            return result
    except (zlib.error, tarfile.TarError, OSError):
        raise ValueError("definitions_bridge_source_archive_invalid") from None


def _recipe(closure: _Closure, edge: DerivativeEdge, template: bytes) -> None:
    version = _recipe_version(closure, edge.recipe_source, edge.source_commit, edge.qualification_sha256)
    wanted = _rendered_recipe(template, edge.parent_image, version=version)
    need(closure.read(edge.recipe, 16384) == wanted, "rendered_recipe_changed")
    raw = closure.read(edge.source_archive.artifact, 256 * 1024)
    need(_archive_entries(raw, 65536) == {"Dockerfile": wanted}, "source_archive_recipe")


def _anchor_build(closure: _Closure, original: OriginalEvidence, template: bytes) -> None:
    build, archive = closure.json(original.inspector_build), original.inspector_source_archive
    source = {"bucket": archive.bucket, "object": archive.name, "generation": archive.generation}
    images, tags = build.get("results", {}).get("images", []), build.get("tags", [])
    need(build.get("projectId") == PROJECT and build.get("status") == "SUCCESS"
         and re.fullmatch(r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}", build.get("id", ""))
         and build.get("source", {}).get("storageSource") == source
         and build.get("sourceProvenance", {}).get("resolvedStorageSource") == source
         and len(images) == 1 and images[0].get("digest") == "sha256:" + original.inspector_image.split("@sha256:")[1]
         and type(tags) is list and 3 <= len(tags) <= 4
         and tags[0] in {KIND, "native_cloud_build_source_fixed_renderer_v1"}
         and re.fullmatch(r"[a-f0-9]{40}", tags[1]) and re.fullmatch(r"[a-f0-9]{64}", tags[2]), "original_inspector_build")
    nonce = archive.name.split("/")[1]
    files = _archive_entries(closure.read(archive.artifact, MAX_SOURCE_ARCHIVE), MAX_SOURCE_ARCHIVE)
    args = ["build", "--pull=false", "--platform=linux/amd64", "--build-arg", "DOCUMENT_DEFINITIONS_NONCE=" + nonce]
    if tags[0] == KIND:
        need(len(tags) == 4 and re.fullmatch(r"[a-f0-9]{64}", tags[3]), "original_inspector_parent")
        version = _recipe_version(closure, original.inspector_recipe_source, tags[1], tags[2])
        need(files == {"Dockerfile": _rendered_recipe(template, INSPECTOR + tags[3], version=version)}, "original_inspector_recipe")
        tag = REPOSITORY + "hirewiz-document-inspector:source-definitions-" + nonce
    else:
        need(len(tags) == 3 and original.inspector_recipe_source is None, "original_full_source_tags")
        material = closure.json(original.material_inventory)["files"]["source"]
        policy_path = "infra/document-processing/policy.source-fixed-renderer.json"
        need(set(files) == set(material) | {policy_path}
             and all(hashlib.sha256(files[name]).hexdigest() == sha for name, sha in material.items())
             and hashlib.sha256(files[policy_path]).hexdigest() == original.material.inspector_policy_sha256
             and _json(files[policy_path]).get("source_sha256") == material, "original_materialized_source")
        args += ["--file", "infra/document-processing/source-fixed-renderer.Dockerfile"]
        for name, sha in sorted(material.items()):
            args += ["--label", "hirewiz.document-test.source." + name + "=" + sha]
        tag = REPOSITORY + "hirewiz-document-inspector:source-revision-" + nonce
    args += ["--label", "hirewiz.document-source.commit=" + tags[1],
             "--label", "hirewiz.document-source.qualification=" + tags[2], "--tag", tag, "."]
    need(images[0].get("name") == tag and build.get("serviceAccount")
         == "projects/" + PROJECT + "/serviceAccounts/hirewiz-builder-20261014@" + PROJECT + ".iam.gserviceaccount.com"
         and _step_arguments(build)
         == [{"name": DOCKER_BUILDER, "args": args}, {"name": DOCKER_BUILDER, "args": ["push", tag]}], "original_inspector_build_steps")


def _build(closure: _Closure, edge: DerivativeEdge) -> None:
    raw = closure.json(edge.raw_build)
    tag = REPOSITORY + "hirewiz-document-inspector:source-definitions-" + edge.nonce
    source = {"bucket": edge.source_archive.bucket, "object": edge.source_archive.name,
              "generation": edge.source_archive.generation}
    args = ["build", "--pull=false", "--platform=linux/amd64", "--build-arg", "DOCUMENT_DEFINITIONS_NONCE=" + edge.nonce,
        "--label", "hirewiz.document-source.commit=" + edge.source_commit,
        "--label", "hirewiz.document-source.qualification=" + edge.qualification_sha256, "--tag", tag, "."]
    images = raw.get("results", {}).get("images", [])
    need(edge.source_archive.name == "hirewiz-document-refresh-source/" + edge.nonce + "/source.tar.gz"
         and raw.get("id") == edge.build_id and raw.get("projectId") == PROJECT and raw.get("status") == "SUCCESS"
         and raw.get("source", {}).get("storageSource") == source
         and raw.get("sourceProvenance", {}).get("resolvedStorageSource") == source
         and raw.get("serviceAccount") == "projects/" + PROJECT + "/serviceAccounts/hirewiz-builder-20261014@" + PROJECT + ".iam.gserviceaccount.com"
         and _step_arguments(raw)
         == [{"name": DOCKER_BUILDER, "args": args}, {"name": DOCKER_BUILDER, "args": ["push", tag]}]
         and raw.get("tags") == [KIND, edge.source_commit, edge.qualification_sha256, edge.parent_image.split("@sha256:")[1]]
         and len(images) == 1 and images[0].get("name") == tag
         and images[0].get("digest") == "sha256:" + edge.image.split("@sha256:")[1],
         "build_parent_source_or_result_changed")


def _original(closure: _Closure, original: OriginalEvidence, template: bytes) -> None:
    _anchor_build(closure, original, template)
    lock, runner = closure.json(original.review_lock), closure.json(original.runner_receipt)
    review = closure.json(original.review_evidence)
    plan, ci, build, registry = (closure.json(ref) for ref in
        (original.development_plan, original.ci_metadata, original.runner_build, original.capture_registry))
    need(lock.get("schema_version") == "native-development-review-lock-v2"
         and lock.get("state") == "locked_before_held_out_native_capture"
         and lock.get("runner_receipt", {}).get("sha256") == original.runner_receipt.sha256
         and lock.get("ci_metadata", {}).get("sha256") == original.ci_metadata.sha256
         and lock.get("build_metadata", {}).get("sha256") == original.runner_build.sha256
         and lock.get("development_plan", {}).get("sha256") == original.development_plan.sha256
         and lock.get("capture_registry", {}).get("sha256") == original.capture_registry.sha256, "original_lock_references")
    need(lock.get("review_evidence", {}).get("sha256") == original.review_evidence.sha256
         and review.get("state") == "completed_native_development_review"
         and review.get("reviewed_at") == lock.get("reviewed_at")
         and review.get("reviewed_by") == lock.get("reviewed_by")
         and review.get("development_plan_sha256") == original.development_plan.sha256
         and review.get("capture_registry_sha256") == original.capture_registry.sha256
         and review.get("native_renderer_origin_refs") == original.renderer_origin.model_dump(mode="json"),
         "renderer_origin_not_sealed_by_original_review")
    source_rows = lock.get("files", [])
    for kind, wanted in (("source", [case.source_sha256 for case in original.cases]),
                         ("compiled_pdf", [case.compiled_pdf_sha256 for case in original.cases if case.compiled_pdf_sha256]),
                         ("text", [case.extracted_text_sha256 for case in original.cases]),
                         ("page", [sha for case in original.cases for sha in case.page_sha256])):
        need(Counter(row.get("sha256") for row in source_rows if row.get("kind") == kind) == Counter(wanted),
             "original_lock_case_artifacts")
    material = original.material
    need(runner.get("schema_version") == "native-quality-qualified-runner-v1"
         and runner.get("qualification_contract") == "hirewiz_schema26_app_rolling_v3"
         and runner.get("app_sha256") == material.app_sha256 and runner.get("app_files") == material.app_files
         and runner.get("entry_path") == material.original_entry_path
         and runner.get("entry_sha256") == material.original_entry_sha256
         and runner.get("repository") == "https://github.com/Harshil4442/ai-resume-copilot.git"
         and re.fullmatch(r"[a-f0-9]{40}", runner.get("release", ""))
         and re.fullmatch(re.escape(REPOSITORY) + r"hirewiz@sha256:[a-f0-9]{64}", runner.get("image", ""))
         and runner.get("ci_metadata_sha256") == digest(ci) and runner.get("build_metadata_sha256") == digest(build),
         "original_runner_material")
    jobs = ci.get("jobs", [])
    need(ci.get("headSha") == runner.get("release") and ci.get("workflowName") == "CI"
         and ci.get("conclusion") == "success" and ci.get("status") == "completed"
         and {row.get("name") for row in jobs} == {"backend", "frontend", "security-and-container", "browser-companion", "cold-browser-journey"}
         and len(jobs) == 5 and all(row.get("conclusion") == "success" and row.get("status") == "completed" for row in jobs), "original_ci")
    steps = next(row for row in jobs if row["name"] == "backend").get("steps", [])
    for name in ("Qualify schema26 app v3 compatibility", "Qualify schema26 app v3 native protocol",
                 "Qualify retained actual-source schema25 app v2"):
        matching = [step for step in steps if step.get("name") == name]
        need(len(matching) == 1 and matching[0].get("status") == "completed"
             and matching[0].get("conclusion") == "success", "original_ci_qualification")
    tag = runner.get("image", "").split("@", 1)[0] + ":" + runner.get("release", "")
    images = build.get("results", {}).get("images", [])
    build_steps = _step_arguments(build)
    allowed_steps = [
        [{"name": "gcr.io/cloud-builders/docker", "args": args},
         {"name": "gcr.io/cloud-builders/docker", "args": ["push", tag]}]
        for args in (["build", "--tag", tag, "."], ["build", "--tag=" + tag, "."])
    ]
    need(build.get("id") == runner.get("build_id") and build.get("status") == "SUCCESS" and build.get("projectId") == PROJECT
         and build.get("sourceProvenance", {}).get("resolvedGitSource") == {"url": "https://github.com/Harshil4442/ai-resume-copilot.git", "revision": runner.get("release"), "dir": "."}
         and build_steps in allowed_steps
         and len(images) == 1 and images[0].get("name") == tag
         and images[0].get("digest") == runner.get("image", "").split("@")[-1], "original_build")
    selected = plan.get("selected_cases", [])
    need(plan.get("schema_version") == "development-native-service-acquisition-v1" and plan.get("split") == "development"
         and len(selected) == 40 and [row.get("case_id") for row in selected] == [case.case_id for case in original.cases], "original_plan")
    for row, case in zip(selected, original.cases, strict=True):
        need(row.get("source_format") == case.source_format and row.get("source", {}).get("sha256") == case.source_sha256,
             "original_case_changed")
    captures = registry.get("captures", {})
    need(registry.get("custody") == "operator_reviewed_native_capture_v1"
         and set(captures) == {case.case_id for case in original.cases}, "original_capture_registry")
    for case in original.cases:
        capture = captures[case.case_id]
        need(capture.get("source_sha256") == case.source_sha256, "original_capture_source")
        if case.compiled_pdf_sha256:
            need(capture.get("compilation", {}).get("source_sha256") == case.source_sha256
                 and capture["compilation"].get("pdf", {}).get("sha256") == case.compiled_pdf_sha256, "original_capture_pdf")
    origin = original.renderer_origin
    record, execution, inventory, pages = (closure.json(ref) for ref in (origin.record, origin.execution, origin.inventory, origin.pages))
    need(record.get("schema_version") == "native-renderer-origin-v1" and record.get("image") == original.inspector_image
         and record.get("execution_sha256") == origin.execution.sha256 and record.get("inventory_sha256") == origin.inventory.sha256
         and record.get("page_manifest_sha256") == origin.pages.sha256
         and record.get("inspector_build_sha256") == original.inspector_build.sha256
         and record.get("inspector_source_archive_sha256") == original.inspector_source_archive.artifact.sha256
         and inventory.get("material_sha256") == digest(material.model_dump(mode="json"))
         and inventory.get("source_sha256") == material.source_sha256 and inventory.get("fonts_sha256") == material.fonts_sha256,
         "missing_or_incomplete_original_renderer_origin")
    containers = execution.get("template", {}).get("containers", [])
    need(type(execution.get("uid")) is str and execution["uid"] and type(execution.get("succeededCount")) is int
         and execution["succeededCount"] == 1
         and len(containers) == 1 and containers[0].get("image") == original.inspector_image
         and record.get("execution_name") == execution.get("name"), "original_renderer_execution")
    need(set(pages) == {case.case_id for case in original.cases}, "original_rendered40")
    for case in original.cases:
        need(pages[case.case_id] == {"source_sha256": case.source_sha256,
             "artifact_sha256": case.compiled_pdf_sha256 or case.source_sha256,
             "extracted_text_sha256": case.extracted_text_sha256, "page_sha256": case.page_sha256}, "original_renderer_case_material")


def _acceptance(closure: _Closure, current: CurrentAcceptance, terminal: DerivativeEdge, now: datetime) -> None:
    need(now.tzinfo is not None and current.definition_issued_at.tzinfo is not None, "acceptance_timezone")
    age = now - current.definition_issued_at
    limit = timedelta(hours=24) if current.purpose == "new_candidate_and_promotion" else timedelta(days=3)
    need(timedelta(0) <= age < limit, "current_acceptance_age")
    native, execution, inventory, service = (closure.json(ref) for ref in
        (current.native_record, current.execution, current.inventory, current.service))
    need(native.get("contract_version") == 4 and native.get("required_case_count") == 17
         and native.get("build_id") == terminal.build_id
         and native.get("source_archive_sha256") == terminal.source_archive.artifact.sha256
         and native.get("source_generation") == terminal.source_archive.generation
         and native.get("acceptance_purpose") == current.purpose
         and native.get("verifier") == ("SourceProbe.verify_successor" if current.purpose == "new_candidate_and_promotion"
                                         else "SourceProbe.verify_current_successor")
         and native.get("image") == current.image and native.get("policy_sha256") == current.material.inspector_policy_sha256
         and native.get("execution_sha256") == current.execution.sha256
         and native.get("inventory_sha256") == current.inventory.sha256
         and native.get("definition_issued_at") == current.definition_issued_at.isoformat()
         and inventory.get("material_sha256") == digest(current.material.model_dump(mode="json")), "current_native_references")
    containers = execution.get("template", {}).get("containers", [])
    need(type(execution.get("succeededCount")) is int and execution["succeededCount"] == 1
         and type(execution.get("uid")) is str and execution["uid"]
         and len(containers) == 1 and containers[0].get("image") == current.image
         and native.get("execution_name") == execution.get("name"), "current_native_execution")
    serving = service.get("template", {}).get("containers", [])
    need(len(serving) == 1 and serving[0].get("image") == current.image and type(service.get("uid")) is str
         and service["uid"] and service.get("reconciling", False) is False
         and service.get("terminalCondition", {}).get("state") == "CONDITION_SUCCEEDED", "current_profile_service")
    if current.purpose == "new_candidate_and_promotion":
        need(current.local_candidate_proof is not None, "new_candidate_proof_missing")
        assert current.local_candidate_proof is not None
        proof = closure.json(current.local_candidate_proof)
        need(proof.get("image") == current.image and proof.get("definition_issued_at") == current.definition_issued_at.isoformat()
             and proof.get("material_sha256") == digest(current.material.model_dump(mode="json")), "new_candidate_proof_changed")
    else:
        need(current.local_candidate_proof is None and native.get("acceptance_purpose") == "already_serving",
             "serving_is_not_candidate_promotion")


def validate_metadata(evidence: BridgeEvidence, read_metadata: Callable[[Artifact], bytes], *, now: datetime) -> MetadataReport:
    """Consistency only; never replaces full native40/60 or page-equivalence proof."""
    evidence = BridgeEvidence.model_validate(evidence)
    closure = _Closure(evidence.files, read_metadata)
    original, current = evidence.original, evidence.current
    need(original.material == current.material and original.inspector_image != current.image, "same_approved_material_required")
    template = closure.read(evidence.refresh_template, 16384)
    need(hashlib.sha256(template).hexdigest() == REFRESH_TEMPLATE_SHA256, "fixed_refresh_template")
    _original(closure, original, template)
    _material(closure, original.material_inventory, original.material)
    previous, previous_oci = original.inspector_image, _oci(closure, original.oci, original.inspector_image)
    need(digest(_execution_configuration(previous_oci[1])) == original.material.execution_configuration_sha256,
         "original_oci_configuration_material")
    images = {previous}
    builds = {closure.json(original.inspector_build)["id"], closure.json(original.runner_build)["id"]}
    generations = {(original.inspector_source_archive.bucket, original.inspector_source_archive.name,
                    original.inspector_source_archive.generation)}
    for edge in evidence.chain:
        need(edge.parent_image == previous and edge.image not in images and edge.build_id not in builds
             and (edge.source_archive.bucket, edge.source_archive.name, edge.source_archive.generation) not in generations,
             "chain_not_contiguous_or_unique")
        _build(closure, edge)
        _recipe(closure, edge, template)
        _material(closure, edge.material_inventory, original.material)
        parent_manifest, parent_config = previous_oci
        child_manifest, child_config = _oci(closure, edge.oci, edge.image)
        parent_execution, child_execution = (_execution_configuration(config) for config in (parent_config, child_config))
        need(child_manifest["layers"][:-2] == parent_manifest["layers"]
             and child_config["rootfs"]["diff_ids"][:-2] == parent_config["rootfs"]["diff_ids"]
             and parent_execution == child_execution
             and child_config.get("config", {}).get("Labels", {}).get("hirewiz.document-source.commit") == edge.source_commit
             and child_config["config"]["Labels"].get("hirewiz.document-source.qualification") == edge.qualification_sha256,
             "oci_parent_or_configuration_changed")
        for index, ref in enumerate(edge.layer_inventories):
            delta = closure.json(ref)
            paths = delta.get("changed_paths", [])
            need(delta.get("schema_version") == "oci-layer-path-inventory-v1"
                 and delta.get("layer_digest") == child_manifest["layers"][-2 + index]["digest"]
                 and delta.get("diff_id") == child_config["rootfs"]["diff_ids"][-2 + index]
                 and delta.get("parent_image") == edge.parent_image and delta.get("image") == edge.image
                 and delta.get("non_definition_tree_sha256") == original.material.non_definition_tree_sha256
                 and type(paths) is list and 1 <= len(paths) <= 128
                 and all(type(path) is str and re.fullmatch(r"/var/lib/clamav(?:/[A-Za-z0-9_-][A-Za-z0-9._-]*)?", path) for path in paths),
                 "outside_definition_material_changed")
        images.add(edge.image)
        builds.add(edge.build_id)
        generations.add((edge.source_archive.bucket, edge.source_archive.name, edge.source_archive.generation))
        previous, previous_oci = edge.image, (child_manifest, child_config)
    need(previous == current.image, "current_not_chain_terminal")
    _acceptance(closure, current, evidence.chain[-1], now)
    need(closure.used == set(closure.files), "unused_or_missing_metadata_artifact")
    return MetadataReport(evidence_sha256=digest(evidence.model_dump(mode="json")),
        original_review_lock_sha256=original.review_lock.sha256, current_image=current.image,
        material_sha256=digest(original.material.model_dump(mode="json")), chain_edges=len(evidence.chain))


def scan_plan(evidence: BridgeEvidence, permit: BridgePermit, read_metadata: Callable[[Artifact], bytes], *, now: datetime) -> ScanPlan:
    """Sixty inert intents; no source byte/case selection, funding or claim use."""
    evidence, permit = BridgeEvidence.model_validate(evidence), BridgePermit.model_validate(permit)
    report = validate_metadata(evidence, read_metadata, now=now)
    raw_lock = _Closure(evidence.files, read_metadata).json(evidence.original.review_lock)
    locked_at = datetime.fromisoformat(raw_lock.get("locked_at", ""))
    need(permit.valid_from.tzinfo is not None and permit.expires_at.tzinfo is not None
         and locked_at.tzinfo is not None and locked_at <= permit.valid_from
         and permit.valid_from <= now < permit.expires_at <= permit.valid_from + timedelta(days=1)
         and permit.expires_at <= evidence.current.definition_issued_at + timedelta(days=3), "permit_window")
    need(permit.namespace.endswith("/quality-native-equivalence/" + permit.epoch_id + "/")
         and permit.bridge_evidence_sha256 == report.evidence_sha256
         and permit.original_review_lock_sha256 == report.original_review_lock_sha256
         and permit.inspector_image == report.current_image, "separate_bridge_permit_binding")
    funding_raw = read_metadata(permit.funding_evidence)
    need(type(funding_raw) is bytes and len(funding_raw) == permit.funding_evidence.size
         and hashlib.sha256(funding_raw).hexdigest() == permit.funding_evidence.sha256, "funding_bytes")
    funding = _json(funding_raw)
    need(funding == {"schema_version": "native40-bridge-funding-metadata-v1", "purpose": permit.purpose,
        "epoch_id": permit.epoch_id, "namespace": permit.namespace, "bridge_evidence_sha256": report.evidence_sha256,
        "request_limit": 60, "quote": permit.quote.model_dump(mode="json")}, "whole_bridge_funding_metadata")
    intents = []
    for case in evidence.original.cases:
        common = {"case_id": case.case_id, "original_source_sha256": case.source_sha256,
            "original_review_lock": evidence.original.review_lock,
            "original_renderer_origin": evidence.original.renderer_origin.record,
            "current_image": report.current_image, "current_profile_sha256": digest(evidence.current.model_dump(mode="json")),
            "bridge_evidence_sha256": report.evidence_sha256}
        intents.append(ScanIntent(**common, phase="bridge_source", source_format=case.source_format,
            source_sha256=case.source_sha256, source_size=case.source_size))
        if case.compiled_pdf_sha256 is not None:
            assert case.compiled_pdf_size is not None
            intents.append(ScanIntent(**common, phase="bridge_compiled_pdf", source_format="pdf",
                source_sha256=case.compiled_pdf_sha256, source_size=case.compiled_pdf_size))
    need(len(intents) == 60, "complete60_scan_plan")
    return ScanPlan(namespace=permit.namespace, permit_sha256=digest(permit.model_dump(mode="json")),
        evidence_sha256=report.evidence_sha256, intents=intents, total_reserved_minor=permit.quote.total_reserved_minor)
