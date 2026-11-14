"""Finite V2 source and read-only provenance checks; no processing authority.

The native entry never imports this operator-side module. A source fingerprint,
an offline receipt, and fake-port tests cannot attest an image or fund a run.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib
import importlib.abc
import json
import os
import socket
import stat
import subprocess
import sys
import sysconfig
import tempfile
import types
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / "backend"
if __package__ in {None, ""}:
    sys.path.insert(0, str(BACKEND))

PREFIX = "backend/evaluation/native_acquisition_v2/"
INITIALIZERS = ("backend/evaluation/__init__.py", PREFIX + "__init__.py")
PACKAGE_PATHS = (*INITIALIZERS, *(PREFIX + name + ".py" for name in
                  ("engine", "channel", "job", "qualification", "isolation", "bridge")))
SUITES = tuple("backend/tests/test_native_acquisition_v2_" + name + ".py" for name in ("engine", "channel", "source"))
RUNBOOK = "docs/runbooks/NATIVE_QUALITY_HELD_OUT_V2.md"
COMPONENT_PATHS = (*PACKAGE_PATHS, *SUITES, RUNBOOK)
SOURCE_ASSETS = tuple("backend/evals/assets/courier_prime/" + name for name in (
    "CourierPrime-Regular.ttf", "CourierPrime-Bold.ttf", "CourierPrime-Italic.ttf", "CourierPrime-BoldItalic.ttf",
    "OFL.txt", "provenance.json"))
IMAGE_SEEDS = (*INITIALIZERS, *(PREFIX + name + ".py" for name in ("engine", "channel", "job")))
RUNTIME_BYTE_INPUTS = ("backend/evaluation/development40_transport_v2.py",)
CONTROLLER_ONLY_IMPORTS = {
    PREFIX + "isolation.py": ("evaluation.development40_qualification_v2",),
}
CONTROLLER_ONLY_PATHS = tuple("backend/" + module.replace(".", "/") + ".py"
    for modules in CONTROLLER_ONLY_IMPORTS.values() for module in modules)
LITERAL_INPUTS = {
    "backend/scripts/qualify_native_quality_acquisition.py": ("APP_SHA256", "ENTRY", "ENTRY_SHA256", "HISTORICAL_RELEASE"),
    "backend/scripts/schema25_app_release_contract_v3.py": ("CANDIDATE_APP_SHA256", "CANDIDATE_APP_FILES",
        "COMPATIBILITY_STEP", "PROTOCOL_STEP", "RETAINED_STEP"),
}
# These are current APPLICATION/operator inputs, never literal_image_inputs or
# a reason to reinterpret the retained producer's fixed runtime/source receipt.
APPLICATION_CONTRACTS = {
    "hirewiz_schema26_app_rolling_v3": ("backend/scripts/schema25_app_release_contract_v3.py",
        "infra/gcp/schema26_app_rolling_v3.py"),
    "hirewiz_schema27_app_v1": ("backend/scripts/schema27_app_release_contract.py",
        "infra/gcp/schema27_native_migration.py"),
}
ENTRY = PREFIX + "job.py"
VERSION = "held-out-native-source-v2"
MAX_SOURCE = 512 * 1024
PORT_PATH = "backend/scripts/qualify_native_quality_acquisition.py"
PORT_SHA256 = 'b2eb3ee6474c012eb600ee80820db17ceb02b1e009f9caeddb4550c074d6b44a'
_PORTS: types.ModuleType | None = None
_CHILD_ROOT: Path | None = None


def need(value: object, code: str) -> None:
    if not value:
        raise ValueError("held_out_source_" + code)


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _dependency_roots():
    prefix = Path(sys.prefix).resolve()
    result = set()
    for key, value in sysconfig.get_paths().items():
        path = Path(value)
        if key in {"purelib", "platlib"} and path.is_absolute() and path.resolve().is_relative_to(prefix):
            result.add((path, path.resolve()))
            result.add((path.resolve(), path.resolve()))
    return tuple(result)


_DEPENDENCY_ROOTS = _dependency_roots()


def _project_source(path: Path) -> bool:
    resolved = path.resolve()
    if not (path.is_relative_to(ROOT) or resolved.is_relative_to(ROOT)):
        return False
    return not any(path.is_relative_to(raw) and resolved.is_relative_to(physical)
                   for raw, physical in _DEPENDENCY_ROOTS)


def _bootstrap_entries():
    """Only Git metadata is read before the fixed helper's source is trusted."""
    environment = {"PATH": "/usr/bin:/bin", "GIT_NO_LAZY_FETCH": "1", "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"}
    result = subprocess.run(["/usr/bin/git", "-c", "core.fsmonitor=false", "-C", str(ROOT),
        "ls-tree", "-r", "-z", "--full-tree", "HEAD"], env=environment,
        stdin=subprocess.DEVNULL, capture_output=True, timeout=10, check=True)
    need(len(result.stdout) <= 16 * 1024**2, "bootstrap_tree_bound")
    entries = {}
    for row in result.stdout.split(b"\0"):
        if row:
            metadata, name = row.split(b"\t", 1)
            entries[name.decode()] = tuple(metadata.split())
    return entries


def _bootstrap_source(path: Path, entries) -> bytes:
    relative = str(path.relative_to(ROOT))
    approved = relative.startswith(("backend/app/", "backend/scripts/", "backend/alembic/", "infra/gcp/",
                                   "infra/document-processing/", "infra/native-tex/"))
    approved = approved or relative in {*PACKAGE_PATHS, *CONTROLLER_ONLY_PATHS}
    mode, kind, expected = entries.get(relative, (None, None, None))
    need(approved and path.suffix == ".py" and path.resolve() == path and not path.is_symlink()
         and path.is_file() and path.stat().st_size <= 4 * 1024**2
         and mode in {b"100644", b"100755"} and kind == b"blob", "bootstrap_import_refused")
    raw = path.read_bytes()
    need(hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw,
        usedforsecurity=False).hexdigest().encode() == expected, "bootstrap_import_changed")
    return raw


def _bootstrap_child(entries) -> Path:
    cache = Path(sys.pycache_prefix or "/")
    need(sys.flags.isolated and sys.dont_write_bytecode and sys.pycache_prefix
         and cache.is_absolute() and not cache.exists() and not cache.is_symlink()
         and cache.parent.resolve() == cache.parent and cache.parent.stat().st_uid == os.getuid()
         and stat.S_IMODE(cache.parent.stat().st_mode) == 0o700, "private_cache_required")
    target = Path(sys.argv[sys.argv.index("--root") + 1]).resolve()

    def audit(event, args):
        if event == "exec":
            filename = getattr(args[0], "co_filename", "")
            if filename and Path(filename).is_absolute() and _project_source(Path(filename)):
                _bootstrap_source(Path(filename), entries)
    sys.addaudithook(audit)
    finders = tuple(sys.meta_path)

    class ImportOrigins(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            for finder in finders:
                spec = finder.find_spec(fullname, path, target)
                if spec is None:
                    continue
                origin = spec.origin
                if origin and Path(origin).is_absolute() and _project_source(Path(origin)):
                    _bootstrap_source(Path(origin), entries)
                for location in spec.submodule_search_locations or ():
                    directory = Path(location)
                    need(not _project_source(directory) or directory.resolve() == directory,
                         "bootstrap_namespace_link")
                return spec
            return None
    sys.meta_path[:] = [ImportOrigins()]

    def deny(*args, **kwargs):
        raise ValueError("held_out_source_child_IO_denied")
    for attribute in ("create_connection", "getaddrinfo", "gethostbyname"):
        setattr(socket, attribute, deny)
    for attribute in ("connect", "connect_ex", "listen"):
        setattr(socket.socket, attribute, deny)

    def deny_bind(*args, **kwargs):
        # Requests' IPv6 capability check catches OSError; allocate no socket.
        raise OSError("held-out source child binding denied")
    cast(Any, socket.socket).bind = deny_bind
    return target


def _ports():
    """Load only the pinned committed helper, never its import-loader bytecode.

    The parent source-only path has the same before-execution check as the
    child. Future old-helper changes require an explicit reviewed pin renewal.
    """
    global _PORTS, _CHILD_ROOT
    need(not any((ROOT / name).exists() or (ROOT / name).is_symlink() for name in (
        "backend/__init__.py", "backend/scripts/__init__.py", "infra/__init__.py", "infra/gcp/__init__.py")),
        "undeclared_package_ancestor")
    entries = _bootstrap_entries()
    path = ROOT / PORT_PATH
    raw = _bootstrap_source(path, entries)
    need(hashlib.sha256(raw).hexdigest() == PORT_SHA256, "bootstrap_helper_changed")
    if _PORTS is None:
        child = _bootstrap_child(entries) if "--source-child" in sys.argv else None
        name = "scripts.qualify_native_quality_acquisition"
        module = types.ModuleType(name)
        module.__file__, module.__package__ = str(path), "scripts"
        sys.modules[name] = module
        exec(compile(raw, str(path), "exec"), module.__dict__)
        # No namespace loader may run first, including a flat scripts.pyc.
        namespace = types.ModuleType("scripts")
        namespace.__path__ = [str(BACKEND / "scripts")]
        namespace.__package__ = "scripts"
        sys.modules["scripts"] = namespace
        namespace.__dict__["qualify_native_quality_acquisition"] = module
        _PORTS = module
        if child is not None:
            import requests  # noqa: F401 -- initialize only under socket/origin denial
            module._child_guards(child)
            _CHILD_ROOT = child
    # Retain the explicit import edge for the declared controller source graph;
    # its module was installed only after the source pin passed above.
    from scripts import qualify_native_quality_acquisition as q

    filename = q.__file__
    need(type(filename) is str, "foreign_ports")
    assert isinstance(filename, str)
    need(q is _PORTS and Path(filename) == path and path.resolve() == path, "foreign_ports")
    return q


def package_members(root: Path) -> dict[str, str]:
    """Exact local source membership; no corpus, imports or provider calls."""
    need(root.is_absolute() and root.resolve() == root, "root")
    members = list((root / PREFIX).rglob("*"))
    need(not any(path.is_symlink() for path in members), "package_link")
    actual = {str(path.relative_to(root)) for path in members
              if path.is_file() and "__pycache__" not in path.parts}
    need(actual == set(PACKAGE_PATHS) - {INITIALIZERS[0]}, "package_members")
    result = {}
    for name in PACKAGE_PATHS:
        path = root / name
        need(path.is_file() and not path.is_symlink() and path.resolve() == path
             and 0 < path.stat().st_size <= MAX_SOURCE, "package_path")
        raw = path.read_bytes()
        if name in INITIALIZERS:
            body = ast.parse(raw).body
            need(len(body) == 1 and isinstance(body[0], ast.Expr)
                 and isinstance(body[0].value, ast.Constant)
                 and type(body[0].value.value) is str, "initializer_must_be_docstring_only")
        result[name] = hashlib.sha256(raw).hexdigest()
    need(not (root / "backend/__init__.py").exists(), "backend_ancestor")
    return result


def _import_fence(root: Path, closure: dict[str, str]) -> None:
    """Resolve each local import in new modules against the declared closure.

    Existing helpers are already byte-bound by the immutable neutral inventory;
    the source child additionally checks every reached local module origin.
    """
    def candidates(module: str) -> list[str]:
        if module.startswith(("app.", "scripts.", "evaluation.")) or module in {"app", "scripts", "evaluation"}:
            base = "backend/" + module.replace(".", "/")
        elif module.startswith("infra.") or module == "infra":
            base = module.replace(".", "/")
        else:
            return []
        return [path for path in (base + ".py", base + "/__init__.py") if (root / path).is_file()]

    for name in PACKAGE_PATHS:
        tree = ast.parse((root / name).read_bytes())
        for node in ast.walk(tree):
            modules = []
            bases: list[str] = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
                bases = modules
            elif isinstance(node, ast.ImportFrom):
                need(all(alias.name != "*" for alias in node.names), "wildcard_import")
                module = node.module or ""
                if node.level:
                    parts = Path(name).relative_to("backend").with_suffix("").parts[:-1]
                    need(node.level <= len(parts), "relative_import")
                    module = ".".join((*parts[:len(parts) + 1 - node.level], *(module.split(".") if module else ())))
                modules = [module, *(module + "." + alias.name for alias in node.names)]
                bases = [module]
            for module in modules:
                need(module != "tests" and not module.startswith("tests."), "runtime_test_import")
                for path in candidates(module):
                    need(path in closure and hashlib.sha256((root / path).read_bytes()).hexdigest() == closure[path],
                         "undeclared_local_import")
                if module.startswith("evaluation.") and (module in bases or candidates(module)):
                    need(module == "evaluation.native_acquisition_v2" or any(
                        module == path.removeprefix("backend/").removesuffix(".py").replace("/", ".")
                        for path in PACKAGE_PATHS) or module in CONTROLLER_ONLY_IMPORTS.get(name, ()),
                        "undeclared_evaluation_import")


def _committed(root: Path, name: str, head: str, ports) -> bytes:
    path = root / name
    need(path.is_file() and not path.is_symlink() and path.resolve() == path
         and path.stat().st_size <= 4 * 1024**2, "dependency_path")
    raw = path.read_bytes()
    need(raw == ports.git(root, "show", head + ":" + name), "dependency_not_committed")
    return raw


def _imports(root: Path, seeds: tuple[str, ...], head: str, ports) -> dict[str, str]:
    """Conservative transitive import closure, not the unrelated V1 snapshot.

    Include conditional/function-local imports and implicit initializers. The
    source child checks actual reached origins against this finite declaration.
    """
    pending, result = list(seeds), {}

    def paths(module: str) -> list[str]:
        bases = ["backend/" + module.replace(".", "/"), module.replace(".", "/")]
        if "." not in module:
            bases.extend(("infra/gcp/" + module, "backend/scripts/" + module))
        return [name for base in bases for name in (base + ".py", base + "/__init__.py") if (root / name).is_file()]

    while pending:
        name = pending.pop()
        if name in result:
            continue
        raw = _committed(root, name, head, ports)
        result[name] = hashlib.sha256(raw).hexdigest()
        need(len(result) <= 1024, "import_closure_bound")
        for parent in Path(name).parents:
            initializer = str(parent / "__init__.py")
            if str(parent) != "." and (root / initializer).is_file() and initializer != name:
                pending.append(initializer)
        for node in ast.walk(ast.parse(raw)):
            modules = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if node.level:
                    parts = Path(name).with_suffix("").parts[:-1]
                    if parts and parts[0] == "backend":
                        parts = parts[1:]
                    need(node.level <= len(parts), "relative_import")
                    module = ".".join((*parts[:len(parts) + 1 - node.level], *(module.split(".") if module else ())))
                modules = [module, *(module + "." + alias.name for alias in node.names if alias.name != "*")]
            for module in modules:
                pending.extend(paths(module))
    return result


def _literal_inputs(root: Path, head: str, ports) -> dict[str, dict[str, Any]]:
    """Exactly the constants the unchanged engine reads as data, not functions."""
    result = {}
    for name, required in LITERAL_INPUTS.items():
        values = {}
        for node in ast.parse(_committed(root, name, head, ports)).body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id in required:
                        need(target.id not in values, "duplicate_literal_input")
                        values[target.id] = ast.literal_eval(node.value)
        need(set(values) == set(required), "missing_literal_input")
        result[name] = values
    return result


def source_identity(root: Path) -> dict[str, Any]:
    q = _ports()
    need(root.is_absolute() and root.resolve() == root, "root")
    need(not q.git(root, "status", "--porcelain", "--untracked-files=all"), "clean_source")
    head = q.git(root, "rev-parse", "HEAD").decode().strip()
    package = package_members(root)
    # Keep the unchanged V1 check and historical reference metadata distinct.
    # Its broad464 snapshot is not a claim about V2's reached image imports.
    try:
        legacy = q.source_identity(root)
        reference = {"state": "legacy_compatibility_check_only", "runtime_authority": False,
            "historical_release": q.HISTORICAL_RELEASE, "files": len(legacy["execution_dependencies"]),
            "snapshot_sha256": digest(legacy)}
    except ValueError:
        legacy = {"component_paths": {}, "proof_inputs": {}, "current_proof_inputs": {}}
        reference = {"state": "legacy_guard_refused", "runtime_authority": False}
    components = {}
    for name in COMPONENT_PATHS:
        raw = _committed(root, name, head, q)
        need(0 < len(raw) <= MAX_SOURCE, "component_bound")
        components[name] = hashlib.sha256(raw).hexdigest()
    assets = {}
    for name in SOURCE_ASSETS:
        raw = _committed(root, name, head, q)
        need(raw, "source_asset")
        assets[name] = hashlib.sha256(raw).hexdigest()
    runtime_imports = _imports(root, IMAGE_SEEDS, head, q)
    need(PREFIX + "bridge.py" in runtime_imports, "bridge_not_reached_by_runtime")
    need(not (set(RUNTIME_BYTE_INPUTS) | set(CONTROLLER_ONLY_PATHS)) & set(runtime_imports),
         "runtime_data_or_controller_import")
    runtime_bytes = {name: hashlib.sha256(_committed(root, name, head, q)).hexdigest()
                     for name in RUNTIME_BYTE_INPUTS}
    execution = {**runtime_imports, **runtime_bytes, **assets,
        **{name: hashlib.sha256(_committed(root, name, head, q)).hexdigest()
           for name in ("Dockerfile", "backend/pyproject.toml", "backend/uv.lock")}}
    controller = _imports(root, (PREFIX + "qualification.py", PREFIX + "isolation.py"), head, q)
    need(set(CONTROLLER_ONLY_PATHS) <= set(controller), "controller_dependency_not_reached")
    need(not set(RUNTIME_BYTE_INPUTS) & set(controller), "runtime_byte_input_import")
    literals = _literal_inputs(root, head, q)
    old_proof = {name: value for name, value in legacy["component_paths"].items()
                 if name.startswith("backend/tests/") or Path(name).suffix != ".py"}
    proof = {**legacy["proof_inputs"], **legacy["current_proof_inputs"], **old_proof,
        PREFIX + "bridge.py": components[PREFIX + "bridge.py"],
        **{name: components[name] for name in (*SUITES, RUNBOOK)},
        **{name: execution[name] for name in ("backend/pyproject.toml", "backend/uv.lock")},
        ".github/workflows/ci.yml": hashlib.sha256(_committed(root, ".github/workflows/ci.yml", head, q)).hexdigest()}
    _import_fence(root, {**execution, **controller})
    body = {"schema_version": VERSION, "component_paths": components, "execution_dependencies": execution,
        "controller_dependencies": controller, "runtime_byte_inputs": runtime_bytes,
        "literal_image_inputs": literals, "legacy_reference": reference,
        "proof_inputs": proof, "source_assets": assets,
        "absent_package_ancestors": list(q.ABSENT_ANCESTORS), "full_ci_suites": list(SUITES)}
    need(q.git(root, "rev-parse", "HEAD").decode().strip() == head
         and not q.git(root, "status", "--porcelain", "--untracked-files=all"), "source_changed")
    return {**body, "controller_sha256": digest(body),
        "runtime_dependencies_sha256": digest({"files": execution, "literal_data": literals}),
        "proof_inputs_sha256": digest(proof), "component_sha256": digest(package),
        "entry_sha256": package[ENTRY]}


def current_application_identity(root: Path, *, family: str) -> dict[str, Any]:
    """Source-bound current application/operator role, never native-runner proof.

    The retained producer's engine, bridge, literal_image_inputs and runtime
    dependency calculation remain unchanged. A future current producer requires
    its own versioned entry, real source/CI/Build and native QA qualification.
    """
    need(type(family) is str and family in APPLICATION_CONTRACTS, "application_family")
    need(root.is_absolute() and root.resolve() == root, "application_root")
    q = _ports()
    source = q.current_application_source(root, family=family)
    need(source["role"] == "current_application_operator_source" and source["family"] == family
         and all(source[name] is False for name in ("native_runner_eligible", "retained_producer_proof",
             "processing_authorized", "native_approval")) and source["source_only"] is True,
         "application_role_required")
    head = source["release"]
    contract, observer = APPLICATION_CONTRACTS[family]
    inputs = {PORT_PATH: (("CURRENT_APPLICATION_FAMILY", "CURRENT_APPLICATION_APP_SHA256",
                         "CURRENT_APPLICATION_APP_FILES") if family == "hirewiz_schema27_app_v1" else ("APP_SHA256",)),
        contract: ("CONTRACT", "CANDIDATE_APP_SHA256", "CANDIDATE_APP_FILES",
                   "COMPATIBILITY_STEP", "PROTOCOL_STEP", "RETAINED_STEP")}
    literals = {}
    for name, required in inputs.items():
        values = {}
        for node in ast.parse(_committed(root, name, head, q)).body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id in required:
                        need(target.id not in values, "duplicate_application_literal")
                        values[target.id] = ast.literal_eval(node.value)
        need(set(values) == set(required), "missing_application_literal")
        literals[name] = values
    app = literals[contract]
    need(app["CONTRACT"] == family and app["CANDIDATE_APP_SHA256"] == source["app_sha256"]
         and app["CANDIDATE_APP_FILES"] == source["app_files"], "application_literal_changed")
    if family == "hirewiz_schema27_app_v1":
        current = literals[PORT_PATH]
        need((current["CURRENT_APPLICATION_FAMILY"], current["CURRENT_APPLICATION_APP_SHA256"],
              current["CURRENT_APPLICATION_APP_FILES"]) == (family, source["app_sha256"], source["app_files"]),
             "application_port_literal_changed")
    controller = _imports(root, (PORT_PATH, contract, observer), head, q)
    need(q.git(root, "rev-parse", "HEAD").decode().strip() == head
         and q.current_application_source(root, family=family) == source, "application_source_changed")
    body = {"schema_version": "current-application-operator-source-v1", "role": source["role"],
        "family": family, "application_source": source, "controller_dependencies": controller,
        "literal_application_inputs": literals, "native_runner_eligible": False,
        "retained_producer_proof": False, "source_only": True, "native_approval": False,
        "processing_authorized": False, "authorizes_retail": False, "provider_calls": 0}
    return {**body, "application_operator_sha256": digest(body)}


def _runtime_modules(closure: dict[str, str]) -> None:
    for path in PACKAGE_PATHS:
        module_name = path.removeprefix("backend/").removesuffix(".py").replace("/", ".")
        module_name = module_name.removesuffix(".__init__")
        module = importlib.import_module(module_name)
        filename = module.__file__
        need(type(filename) is str, "runtime_module_file")
        assert isinstance(filename, str)
        actual = Path(filename).resolve()
        need(actual == ROOT / path and hashlib.sha256(actual.read_bytes()).hexdigest() == closure[path],
             "runtime_module_provenance")


def _source_child(root: Path) -> dict[str, Any]:
    q = _ports()
    need(_CHILD_ROOT == root, "source_child_bootstrap_required")
    own = source_identity(ROOT)
    actual = source_identity(root)
    need(all(actual[name] == own[name] for name in ("runtime_dependencies_sha256", "component_sha256", "entry_sha256")),
         "controller_runner_image_inputs_changed")
    declared = {**own["execution_dependencies"], **own["controller_dependencies"]}
    # Runtime source data is hash-bound, but may not execute in the source
    # child merely because its bytes are part of the image identity.
    allowed = set(declared) - set(RUNTIME_BYTE_INPUTS)

    def audit(event, args):
        if event == "exec":
            name = getattr(args[0], "co_filename", "")
            if name and Path(name).is_absolute() and _project_source(Path(name)):
                need(Path(name).resolve() == Path(name), "linked_reached_source")
                need(str(Path(name).relative_to(ROOT)) in allowed, "undeclared_reached_source")
    sys.addaudithook(audit)
    _runtime_modules(declared)
    # Use the low-level source guard/fingerprint, not V1's broad equality gate
    # or its entry/receipt. Candidate proof metadata retains its own old hashes.
    native, contract, release = q._source_modules()
    head = q.git(root, "rev-parse", "HEAD").decode().strip()
    native.rolling.release.source_guard.checked_files(root, head)
    fingerprint = contract.source_fingerprint(root, release.prior_app_files(root, contract.PRIOR_RELEASE))
    app = actual["literal_image_inputs"]["backend/scripts/schema25_app_release_contract_v3.py"]
    for module in list(sys.modules.values()):
        name = getattr(module, "__file__", None)
        if name and Path(name).is_absolute() and _project_source(Path(name)):
            need(Path(name).resolve() == Path(name), "linked_module_provenance")
            relative = str(Path(name).relative_to(ROOT))
            need(relative in allowed and hashlib.sha256(Path(name).read_bytes()).hexdigest() == declared[relative],
                 "module_provenance")
    need(source_identity(ROOT) == own and source_identity(root) == actual, "source_child_changed")
    return {"release": head, "qualification_sha256": fingerprint,
        "app_sha256": app["CANDIDATE_APP_SHA256"], "app_files": app["CANDIDATE_APP_FILES"],
        "entry_sha256": actual["entry_sha256"], "runtime_dependencies_sha256": actual["runtime_dependencies_sha256"],
        "proof_inputs_sha256": actual["proof_inputs_sha256"], "component_sha256": actual["component_sha256"]}


def runner_source(root: Path) -> dict[str, Any]:
    """No credentials, source from another checkout is never executed."""
    with tempfile.TemporaryDirectory(prefix="hirewiz-heldout-source-") as directory:
        private = Path(directory).resolve()
        environment = {"PATH": "/usr/bin:/bin", "HOME": str(private), "XDG_CONFIG_HOME": str(private),
            "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1",
            "CLOUDSDK_CORE_DISABLE_FILE_LOGGING": "true", "CLOUDSDK_CORE_LOG_HTTP": "false"}
        command = [sys.executable, "-I", "-B", "-X", "pycache_prefix=" + str(private / "cache"),
                   str(Path(__file__).resolve()), "--source-child", "--root", str(root)]
        result = subprocess.run(command, env=environment, cwd=private, stdin=subprocess.DEVNULL,
            capture_output=True, timeout=45, check=False)
        need(result.returncode == 0 and 0 < len(result.stdout) <= 65536 and not result.stderr, "source_child_refused")
        value = json.loads(result.stdout)
        need(type(value) is dict and set(value) == {"release", "qualification_sha256", "app_sha256", "app_files",
            "entry_sha256", "runtime_dependencies_sha256", "proof_inputs_sha256", "component_sha256"}, "source_child_shape")
        return value


def _observer(root: Path):
    native, _, _ = _ports()._source_modules()
    return native.Source26Boundary(root)


def verify_qualified_runner(plan, raw: bytes, *, runner_root: Path, controller_ci_run_id: int) -> dict[str, Any]:
    """Fresh read-only native metadata; offline data never bypasses these calls."""
    from evaluation.native_acquisition_v2 import isolation

    q = _ports()
    receipt = isolation.bound_runner(plan, raw)
    need(os.environ.get("CLOUDSDK_CORE_DISABLE_FILE_LOGGING") == "true"
         and os.environ.get("CLOUDSDK_CORE_LOG_HTTP") == "false", "safe_cloud_logging")
    need(type(controller_ci_run_id) is int and controller_ci_run_id > 0, "controller_ci")
    identity = source_identity(ROOT)
    need(plan.controller_sha256 == identity["controller_sha256"], "controller_plan")
    observed = runner_source(runner_root)
    fields = set(observed)
    need(observed == {name: getattr(receipt, name) for name in fields}
         and all(observed[name] == identity[name] for name in ("entry_sha256", "runtime_dependencies_sha256",
             "component_sha256")), "actual_runner_source")
    native, contract, _ = q._source_modules()
    observer = _observer(runner_root)
    observer.verify_source_image(release=receipt.release, image_digest=receipt.image.split("@", 1)[1],
        build_id=receipt.build_id, ci_run_id=receipt.ci_run_id)
    ci = q._successful_ci(observer, runner_root, receipt.release, receipt.ci_run_id)
    jobs = [row for row in ci["jobs"] if row.get("name") == "backend"]
    names = (contract.COMPATIBILITY_STEP, contract.PROTOCOL_STEP, contract.RETAINED_STEP)
    steps = [step for job in jobs for step in job.get("steps", []) if step.get("name") in names]
    need(len(jobs) == 1 and len(steps) == len(names) and {step.get("name") for step in steps} == set(names)
         and all(step.get("status") == "completed" and step.get("conclusion") == "success" for step in steps), "qualification_steps")
    build = observer._cloud("builds", "describe", receipt.build_id, "--region", native.rolling.release.REGION)
    need(digest(ci) == receipt.ci_metadata_sha256 and digest(build) == receipt.build_metadata_sha256, "native_metadata_custody")
    head = q.git(ROOT, "rev-parse", "HEAD").decode().strip()
    q._successful_ci(_observer(ROOT), ROOT, head, controller_ci_run_id)
    need(source_identity(ROOT) == identity and runner_source(runner_root) == observed
         and q.git(ROOT, "rev-parse", "HEAD").decode().strip() == head, "verification_source_changed")
    return {"runner_receipt_sha256": hashlib.sha256(raw).hexdigest(), "controller_head": head,
        "controller_sha256": identity["controller_sha256"], "runner_release": receipt.release,
        "fresh_source_CI_Build_verified": True, "memory_qualified": False,
        "native_approval": False, "processing_authorized": False, "authorizes_retail": False}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--source-child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--application-family", choices=tuple(APPLICATION_CONTRACTS),
        help="Current application/operator source only; no native producer or QA qualification")
    args = parser.parse_args(argv)
    try:
        need(not (args.source_child and args.application_family), "application_role_conflict")
        if args.source_child:
            result = _source_child(args.root)
        elif args.application_family:
            need(args.root == ROOT, "application_controller_root")
            result = current_application_identity(ROOT, family=args.application_family)
        else:
            need(args.root == ROOT, "controller_root")
            result = {**source_identity(ROOT), "source_only": True, "provider_calls": 0,
                "native_approval": False, "processing_authorized": False, "authorizes_retail": False}
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception:
        print(json.dumps({"state": "held_out_source_refused", "native_approval": False,
            "processing_authorized": False, "authorizes_retail": False}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
