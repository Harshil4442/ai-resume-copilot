"""One reviewed immutable V2 renderer profile; no mutable/global pin changes.

The accepted V1 manifest remains unchanged. This exact full-source image differs
only in seven fontconfig cache-file hashes in the installed renderer inventory;
all 68 non-cache font/config/renderer entries and the renderer version match.
The package comparison additionally records the libexpat1 package update.
These constants select an exact expected response, never arbitrary hash input.
"""
from __future__ import annotations

from typing import Any

IMAGE = 'us-central1-docker.pkg.dev/ai-resume-parser-482412/cloud-run-source-deploy/hirewiz-document-inspector@sha256:0a7e550623f0485945f9bbe52573091aabedfd612eb353e6a8bbb920e2ffa94f'
RENDERER = "LibreOffice 25.2.3.2 520(Build:2)"
FONT = "82d048a3ec9dd856d503b93d7e6f6a44a14388639ad3989046d9cf9cfa6291c1"
BASELINE_IMAGE = "us-central1-docker.pkg.dev/ai-resume-parser-482412/cloud-run-source-deploy/hirewiz-document-inspector@sha256:240f6df0df2c82d69067e162ca40870ab77069a0b80e3d8b64abcda312b5a78d"
COMPARISON_SHA256 = "853754b8be6191a3a4a5b73319cc1ccb75cd8c0134b0dc60a850ea3b15e45b2d"
NONCACHE_PROFILE_SHA256 = "9e6724e47d202c8686e03e13bd24ddae7e9d8a1ad9bf0bbdc805fca90956bc98"
BUILD = {'build_id': 'e89eecfb-81e0-4b29-97b3-dd02a04ac04e',
 'build_resource': 'projects/157225724590/locations/us-central1/builds/e89eecfb-81e0-4b29-97b3-dd02a04ac04e',
 'builder_service_account': 'hirewiz-builder-20261014@ai-resume-parser-482412.iam.gserviceaccount.com',
 'fixed_renderer_base': None,
 'immutable_image': 'us-central1-docker.pkg.dev/ai-resume-parser-482412/cloud-run-source-deploy/hirewiz-document-inspector@sha256:0a7e550623f0485945f9bbe52573091aabedfd612eb353e6a8bbb920e2ffa94f',
 'kind': 'native_cloud_build_full_source_v1',
 'nonce': '3e179673132c46318f9f291ea1d97725',
 'policy_sha256': 'd3baf46026934f48b8def219b48d348d7b03bc3b4d0cb713a6f222c7299df7f2',
 'refresh_dockerfile_sha256': '46d865812b27e3461add84751b798b63d43327b3a3e5be77a4a768a9ce36542c',
 'source_archive_sha256': '3403791f4804af965981098f8d251a97ba128e83aa07f0a016a03aa3cdafd6a6',
 'source_bucket': 'ai-resume-parser-482412_cloudbuild',
 'source_generation': '1792730522648573',
 'source_object': 'hirewiz-document-refresh-source/3e179673132c46318f9f291ea1d97725/source.tar.gz',
 'version': 1}
SOURCES = {'backend/app/services/docx_layout_validation.py': 'b1e9c9027851d11422c3de5199943416807c2e5eb7bdbcd04ec4ca38c74a03b9',
 'backend/app/services/market/security.py': 'a440f4ea26a5fb5982690da8399c75983e9133a23501d12e1973a985487200f6',
 'backend/app/services/market/skill_extractor.py': '765824e77d2e9e0bfc88c910134a5009dd3ded52eccc958407d8173039746f54',
 'backend/app/services/market/skill_taxonomy.py': 'e210d0c2b97567fae690d4710ffffb1a5a93bb5bb732badcff47cc2839296366',
 'backend/app/services/native_project.py': '9dcbddd7ed5592ba597d1e77d82ab078e291b1bfc8439db7171145b0635f984f',
 'backend/app/services/parsing.py': '8d35401fe06124c259d845cd84887cfcf5f289b179af990f4716014a3cee0793',
 'backend/app/services/pdf_streams.py': '72c43333a814a309622d7465e858118b3727ee958078775727a6560a0a7c5389',
 'backend/app/services/resume_layout.py': 'd46e28c8c0a5ff0734f84ce4bda6bc42377beb9afbc6c1d90e8f1cc215261146',
 'infra/document-processing/Dockerfile': '46d865812b27e3461add84751b798b63d43327b3a3e5be77a4a768a9ce36542c',
 'infra/document-processing/docx_renderer.py': 'b11d0b9e9ce3cd079df2d77272e19d9d29c2acc4183471590fa2d9493f0a6ce9',
 'infra/document-processing/processor.py': '208eff8d08a9104af25165f6181a48b8d7939d14f2436512b430c1f77c7a75fe',
 'infra/document-processing/service.py': '5031bf49452abce33c42f825adeb789a0af7b3dfbddd7b27593ca472dd90097e',
 'infra/document-processing/writer_owner.py': '2431048a9cf0867a7c016da912c54df54c683b9b3244309cfe36e5e4ee39b16e'}
PARTIAL_FILES = {'build-acknowledged.json': '5f5a0b3aa2282608edfaa23382617f4a1b058eb99cc391cdfb717018e60566dc',
 'build-create-started.json': 'f6564e7b8b33539151e2fc03a8a5c1ae35ad4683027d7f2e74500a3bb81d058d',
 'build-started.json': '8aef37d8c24b68b5ee861e96ceab533bda177ea1563dfda1bf2488751c2853c4',
 'native-build-identity.json': 'e27b17217a6665b5530b75f42bca7d9a395cc56b7d85326f4ad19606eac22600',
 'source-upload-acknowledged.json': '09590a6163eedd0bf11cc35f9a77926b45e04e203671dbe39324769d4db634df',
 'terminal-recoveries/hirewiz-doc-six-cebe8490883c/claim.acknowledged.json': 'a34c52390ef9a726e94e71e09c91127eb2feba4aaa9921c7700f20e31a83f26d',
 'terminal-recoveries/hirewiz-doc-six-cebe8490883c/claim.started.json': 'd4f1282321b7c9d1682e46f0836c1b5bf01a712b2d700dd86112195e4d5a0728',
 'terminal-recoveries/hirewiz-doc-six-cebe8490883c/disposition.json': '9d4dfe20eb7ff7e62ec8e920793e810152cfd9d51556e2f2ea6c822931813fee',
 'terminal-recoveries/hirewiz-doc-six-cebe8490883c/original/build-acknowledged.json': 'b0c034281083ad0b64213e0ff60b9cd122660746acaadc4a6bd3bfb8f6153efd',
 'terminal-recoveries/hirewiz-doc-six-cebe8490883c/original/build-create-started.json': '0b9d81d2caf4bd883837155f1961c8241cdfb10040ea47d3f40e5568b673b4b5',
 'terminal-recoveries/hirewiz-doc-six-cebe8490883c/original/build-started.json': 'bac6a8f661bf36ceb8363ab8e6308eceb613841c3bd527e5abbb821889b2b266',
 'terminal-recoveries/hirewiz-doc-six-cebe8490883c/original/local-native-proof.json': 'd450eef2eea8b624dd9c309bff45a01eb7001b7718747b1fcfbbca73004af364',
 'terminal-recoveries/hirewiz-doc-six-cebe8490883c/original/native-build-identity.json': '2b3ce1ebebf55929acd2c36f5a8304e750d26e946a2130c2aad8cd6d924afd6f',
 'terminal-recoveries/hirewiz-doc-six-cebe8490883c/original/native-proof/binding.json': 'de3d83dd0032e0420c79629509f16fb287a3617add67c0af487418e75a3af8d1',
 'terminal-recoveries/hirewiz-doc-six-cebe8490883c/original/native-proof/create.acknowledged.json': '9815d7063058b6055a7eb254659dc23f93fe43b2a9f6668f84c5afef1cf74a3d',
 'terminal-recoveries/hirewiz-doc-six-cebe8490883c/original/native-proof/create.started.json': 'a4f114397832b79767760a9d9bf33a46e5be86213f1fe8b82d38baa8fe0a47aa',
 'terminal-recoveries/hirewiz-doc-six-cebe8490883c/original/native-proof/run.acknowledged.json': '50996a84b32023b010ecdbdb5df08dbedd7a12a66c8142f078be1dce66e43366',
 'terminal-recoveries/hirewiz-doc-six-cebe8490883c/original/native-proof/run.started.json': '2d2f3a65a0e0d4296203a482b7ef9432710c9118ab5bdd628fabf74d559fa288',
 'terminal-recoveries/hirewiz-doc-six-cebe8490883c/original/source-upload-acknowledged.json': '58293199635214b979a3e1bb4fe96a516cc758b0602e984b5f206d105c7c460d'}
PARTIAL_MANIFEST_SHA256 = "9d20a0b548b2de03fe61939cd714e697bef42426cd526f1714c598b7d269f5b1"


def matches_build(value: Any) -> bool:
    """Existing generic build guards still run; this profile adds exact binding."""
    return (type(value) is dict and all(value.get(key) == expected for key, expected in BUILD.items())
            and value.get("source_sha256") == SOURCES)


def uses_profile(value: Any) -> bool:
    """A future definitions-only successor must prove this exact renderer parent."""
    if type(value) is not dict:
        return False
    return matches_build(value) or (value.get("kind") == "native_cloud_build"
        and value.get("fixed_renderer_base") == IMAGE and value.get("source_sha256") == SOURCES)
