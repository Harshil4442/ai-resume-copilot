"""Forward native scanner payload with an explicit positive teardown receipt."""
from __future__ import annotations

import ast

from infra.gcp import document_source_candidate as profile
from infra.gcp import document_source_payload as v4

INVENTORY_EVENT = "hirewiz_document_source_inventory_v5"
CORPUS_EVENT = "hirewiz_document_source_required_v5"
VERSION = 5

AUXILIARY = """
def auxiliary(service, program, diagnostic):
    name = "document-" + uuid.uuid4().hex
    cleanup_required = True
    try:
        diagnostic.begin_run()
        try:
            code, raw = service._run([service.SANDBOX, "do", "--sandbox-name=" + name, "--write", "--",
                "/usr/local/bin/python3", "-I", "-c", program], timeout=55, limit=16384, allow_outcome=False)
        except BaseException as exc:
            diagnostic.end_run(error=exc)
            if isinstance(exc, service._LauncherRefused):
                cleanup_required = exc.authorized
            raise
        diagnostic.end_run(code)
        require(type(code) is int and code == 0)
        diagnostic.confirm_provider_teardown()
        cleanup_required = False
        return parse(raw, 16384)
    finally:
        if cleanup_required:
            diagnostic.begin_run(cleanup=True)
            try:
                cleanup_code, _ = service._run([service.SANDBOX, "delete", name, "--force"],
                                               timeout=5, limit=65536, cleanup=True)
            except BaseException as exc:
                diagnostic.end_run(error=exc, cleanup=True)
                raise
            else:
                diagnostic.end_run(cleanup_code, cleanup=True)
                require(type(cleanup_code) is int and cleanup_code == 0)
"""

CLEANUP_SUCCESS = """
def cleanup_success(diagnostic):
    return (diagnostic.launcher == "returned" and type(diagnostic.launcher_code) is int
            and diagnostic.launcher_code == 0 and diagnostic.cleanup == "provider_teardown"
            and diagnostic.cleanup_code is None and diagnostic.cleanup_elapsed_ms is None)
"""


def rendered(image, source, *, root=profile.ROOT):
    tree = ast.parse(v4.rendered(image, source, root=root))
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    profile.need({"auxiliary", "cleanup_success", "diagnostic_projection", "_base_main", "source_main"} <= functions.keys(),
                 "v5_base_helpers_changed")

    # Only the launcher-to-processor status tests move to the inner status.
    refused_status_tests = [node for node in ast.walk(tree) if isinstance(node, ast.Compare)
                            and ast.unparse(node) == "diagnostic.launcher_code == 1"]
    profile.need(len(refused_status_tests) == 3, "v5_base_refusal_checks_changed")
    for comparison in refused_status_tests:
        comparison.left.attr = "processor_code"

    receiver = functions["diagnostic_projection"]
    accepted_cleanups = [node for node in ast.walk(receiver) if isinstance(node, ast.Set)
                         and ast.unparse(node) == "{'not_required'}"]
    profile.need(len(accepted_cleanups) == 1 and ast.unparse(receiver.body[-1]) == "return result",
                 "v5_base_diagnostic_changed")
    accepted_cleanups[0].elts.append(ast.Constant("provider_teardown"))
    receiver.body[-1:-1] = ast.parse(
        "value = diagnostic.processor_code\n"
        "require(value is None or (type(value) is int and value in {0, 1, 2}))\n"
        "result['processor_code'] = value\n"
    ).body

    for helper in (AUXILIARY, CLEANUP_SUCCESS):
        replacement = ast.parse(helper).body[0]
        index = tree.body.index(functions[replacement.name])
        tree.body[index] = replacement

    import_service = [node for node in ast.walk(functions["_base_main"]) if isinstance(node, ast.Try)
                      and any(ast.unparse(child) == "import service" for child in node.body)]
    profile.need(len(import_service) == 1, "v5_base_service_import_changed")
    block = import_service[0].body
    index = next(i for i, node in enumerate(block) if ast.unparse(node) == "import service")
    block[index + 1:index + 1] = ast.parse(
        "require(os.environ.get(service.SANDBOX_TRANSPORT_ENV) == service.SANDBOX_TRANSPORT)"
    ).body

    events = {"SOURCE_INVENTORY_EVENT": INVENTORY_EVENT, "SOURCE_CORPUS_EVENT": CORPUS_EVENT}
    event_assignments = [
        node for node in tree.body if isinstance(node, ast.Assign) and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name) and node.targets[0].id in events
    ]
    profile.need(len(event_assignments) == 2, "v5_base_event_changed")
    for assignment in event_assignments:
        assignment.value = ast.Constant(events[assignment.targets[0].id])

    version = [node for node in functions["source_main"].body if isinstance(node, ast.Assign)
               and len(node.targets) == 1 and ast.unparse(node.targets[0]) == "report['version']"]
    profile.need(len(version) == 1 and ast.unparse(version[0].value) == "1 if inventory_only else 4",
                 "v5_base_version_changed")
    version[0].value = ast.Constant(VERSION)
    return ast.unparse(ast.fix_missing_locations(tree)) + "\n"
