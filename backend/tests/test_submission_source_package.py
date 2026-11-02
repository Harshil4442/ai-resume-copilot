"""Finite Docker COPY/context source proof only; never an image build or execution."""
from __future__ import annotations

import ast
import fnmatch
import hashlib
import json
import posixpath
import shlex
from pathlib import Path, PurePosixPath

import pytest

ROOT = Path(__file__).resolve().parents[2]


def declared_sources():
    tree = ast.parse((ROOT / "backend/app/domains/credits/submission.py").read_bytes())
    values = ast.literal_eval(next(node.value for node in tree.body if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "DELIVERY_SOURCES" for target in node.targets)))
    assert type(values) is tuple and len(set(values)) == len(values)
    return values


def context_allows(path, ignore):
    """Evaluate this fixed literal-parent/child allowlist, conservatively.

    This is a source control, not a general Dockerignore implementation. Match
    each ancestor too, so an excluded directory excludes every descendant.
    """
    parts = PurePosixPath(path).parts
    candidates = ["/".join(parts[:i]) for i in range(1, len(parts) + 1)]
    allowed = True
    for raw in ignore.splitlines():
        rule = raw.strip()
        if not rule or rule.startswith("#"):
            continue
        include = rule.startswith("!")
        pattern = rule.removeprefix("!").strip("/")
        assert pattern and not pattern.startswith("../")
        if any(fnmatch.fnmatchcase(candidate, pattern) for candidate in candidates):
            allowed = include
    return allowed


def image_sources(dockerfile, declared):
    """Map only declared files through the actual finite context COPY forms."""
    result, workdir = {}, "/"
    for raw in dockerfile.splitlines():
        line = raw.strip()
        if line.startswith("WORKDIR "):
            workdir = posixpath.normpath(posixpath.join(workdir, line.removeprefix("WORKDIR ")))
        if not line.startswith("COPY "):
            continue
        value = line.removeprefix("COPY ")
        if value.startswith("--from="):
            # Existing uv executable copy cannot overwrite any /app source.
            assert shlex.split(value)[-1] == "/bin/"
            continue
        args = json.loads(value) if value.startswith("[") else shlex.split(value)
        assert len(args) >= 2 and all(type(item) is str for item in args)
        sources, target = args[:-1], args[-1]
        destination = posixpath.normpath(posixpath.join(workdir, target))
        for source in sources:
            clean = source.rstrip("/")
            assert clean and not clean.startswith("/") and ".." not in PurePosixPath(clean).parts
            assert not any(char in clean for char in "*?[")
            if clean == "backend":
                assert source == "backend/" and target == "./" and workdir == "/app/backend"
                for name in declared:
                    if name.startswith("backend/"):
                        result[name] = destination + "/" + name.removeprefix("backend/")
            else:
                if clean not in declared:
                    assert clean in {"backend/pyproject.toml", "backend/uv.lock"}
                    continue
                result[clean] = destination + "/" + PurePosixPath(clean).name if target.endswith("/") else destination
    return result


def verify_payload(dockerfile, ignore):
    declared = declared_sources()
    mapped = image_sources(dockerfile, declared)
    assert set(mapped) == set(declared), "A declared delivery input is missing from COPY"
    for name in declared:
        assert mapped[name] == "/app/" + name, "A declared input has the wrong container path"
        assert context_allows(name, ignore), "A declared input is excluded from the build context"
        path = ROOT / name
        assert path.is_file() and not path.is_symlink(), "A declared input must be an exact regular source file"
        assert path.resolve().is_relative_to(ROOT.resolve())
        assert all(not parent.is_symlink() for parent in path.parents if parent.is_relative_to(ROOT))
    return declared


def test_every_current_delivery_input_is_copied_at_its_exact_container_path():
    declared = verify_payload((ROOT / "Dockerfile").read_text(), (ROOT / ".dockerignore").read_text())
    assert "backend/app/domains/credits/canary.py" in declared
    client = [name for name in declared if not name.startswith("backend/")]
    assert len(client) == 25
    # The context may admit only those literal client files, never whole trees.
    ignores = (ROOT / ".dockerignore").read_text()
    assert sorted(line[1:] for line in ignores.splitlines() if line.startswith("!") and "." in PurePosixPath(line).name) == sorted(client)


def test_actual_delivery_digest_reads_the_dry_copy_payload_at_container_paths(tmp_path):
    from app.domains.credits import finance
    from app.domains.credits import submission_contract as contract

    declared = verify_payload((ROOT / "Dockerfile").read_text(), (ROOT / ".dockerignore").read_text())
    mapped = image_sources((ROOT / "Dockerfile").read_text(), declared)
    for name, target in mapped.items():
        destination = tmp_path / target.lstrip("/")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((ROOT / name).read_bytes())

    def actual_digest(root):
        path = root / "backend/app/domains/credits/submission.py"
        tree = ast.parse(path.read_bytes())
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "delivery_contract")
        namespace = {"__file__": str(path), "DELIVERY_SOURCES": declared,
            "Path": Path, "hashlib": hashlib, "contract": contract, "finance": finance}
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec"), namespace)
        return namespace["delivery_contract"]()

    # Execute only the original pure hash function, never Docker/the image/app.
    assert actual_digest(tmp_path / "app") == actual_digest(ROOT)


def test_actual_canary_helper_byte_drift_changes_the_delivery_contract(monkeypatch):
    from app.domains.credits import finance
    from app.domains.credits import submission_contract as contract

    path = ROOT / "backend/app/domains/credits/submission.py"
    tree = ast.parse(path.read_bytes())
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "delivery_contract")
    namespace = {"__file__": str(path), "DELIVERY_SOURCES": declared_sources(),
        "Path": Path, "hashlib": hashlib, "contract": contract, "finance": finance}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec"), namespace)
    original = Path.read_bytes
    target = ROOT / "backend/app/domains/credits/canary.py"
    before = namespace["delivery_contract"]()
    reads = []

    def changed(current):
        if current == target:
            reads.append(current)
            return original(current) + b"\n# different admission implementation\n"
        return original(current)

    monkeypatch.setattr(Path, "read_bytes", changed)
    assert namespace["delivery_contract"]() != before and reads


@pytest.mark.parametrize("name", ["browser-companion/extension/submission-billing.js",
    "frontend/lib/browserSubmissionBilling.ts", "frontend/lib/browserHostedNetworkReview.ts",
    "frontend/components/BrowserAssistanceReview.tsx"])
def test_missing_or_misdirected_client_copy_cannot_pass_source_packaging(name):
    source = (ROOT / "Dockerfile").read_text()
    with pytest.raises(AssertionError):
        verify_payload(source.replace(json.dumps(name) + ", ", ""), (ROOT / ".dockerignore").read_text())
    with pytest.raises(AssertionError):
        verify_payload(source.replace("/app/frontend/", "/app/wrong/"), (ROOT / ".dockerignore").read_text())


def test_a_later_context_exclusion_cannot_silently_remove_qualified_inputs():
    with pytest.raises(AssertionError, match="excluded"):
        verify_payload((ROOT / "Dockerfile").read_text(), (ROOT / ".dockerignore").read_text() + "\nfrontend\n")


@pytest.mark.parametrize("name", ["frontend/.env.local", "frontend/lib/private.key",
    "frontend/lib/credentials.json", "frontend/lib/unrelated.ts", "frontend/node_modules/pkg/index.js",
    "browser-companion/.env", "browser-companion/extension/private.key",
    "browser-companion/extension/unrelated.js", "browser-companion/node_modules/pkg/index.js"])
def test_inert_source_allowlist_does_not_open_unrelated_or_private_client_files(name):
    assert not context_allows(name, (ROOT / ".dockerignore").read_text())
