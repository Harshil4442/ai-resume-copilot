"""Pure AST scanner-deadline regression tests; no app/SDK/native parser imports."""
import ast
import builtins
import hashlib
import json
import math
import re
import subprocess
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace as N

REPOSITORY = Path(__file__).resolve().parents[2]
PROCESSOR = REPOSITORY / 'infra' / 'document-processing' / 'processor.py'
TREE = ast.parse(PROCESSOR.read_bytes())
NAMES = {'InspectionDenied', 'InspectionUnavailable', '_command', 'scan_original'}
NODES = [n for n in TREE.body if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in NAMES]
NOW = datetime(2026, 10, 12, 10, tzinfo=UTC)
VERSION = b'ClamAV 1.4.3/28151/Mon Oct 12 06:24:02 2026\n'
PATH = Path('/inert/original.docx')


def summary(detected=False):
    state = 'Fixture.Test-Signature FOUND' if detected else 'OK'
    return (str(PATH) + ': ' + state + '\nKnown viruses: 1\nEngine version: 1.4.3\n'
        'Scanned files: 1\nScanned directories: 0\nInfected files: ' + str(int(detected)) + '\n').encode()


def source_namespace(run, clock):
    ns = {'subprocess': N(run=run, DEVNULL=subprocess.DEVNULL), 'time': N(monotonic=lambda: clock[0]),
        'math': math, 're': re, 'datetime': datetime, 'UTC': UTC, 'timedelta': timedelta,
        'DATABASE': '/inert/definitions', 'Path': Path, 'ERROR': 'document_inspection_refused',
        'UNAVAILABLE': 'document_inspection_unavailable'}
    exec(compile(ast.Module(body=NODES, type_ignores=[]), 'pure_proposed_scanner_functions', 'exec'), ns)
    return ns


class SharedDeadlineTests(unittest.TestCase):
    def test_second_scan_inherits_remaining_budget_instead_of_renewing(self):
        clock = [100.0]
        calls = []

        def run(arguments, **kwargs):
            calls.append(kwargs['timeout'])
            if '--version' in arguments:
                clock[0] += 1
                return N(returncode=0, stdout=VERSION, stderr=b'')
            if len(calls) == 2:
                clock[0] += 20
                return N(returncode=0, stdout=summary(), stderr=b'')
            raise subprocess.TimeoutExpired(arguments, kwargs['timeout'])

        ns = source_namespace(run, clock)
        self.assertEqual(ns['scan_original'](PATH, now=NOW, deadline=150)['engine'], 'ClamAV')
        with self.assertRaises(ns['InspectionUnavailable']):
            ns['scan_original'](PATH, now=NOW, deadline=150)
        self.assertEqual(calls, [5, 30, 5, 28.0])

    def test_expired_or_invalid_deadline_does_not_spawn(self):
        calls = []
        ns = source_namespace(lambda *a, **kw: calls.append(kw), [100.0])
        for deadline in (100.0, 99.0, float('nan'), float('inf'), True):
            with self.assertRaises(ns['InspectionUnavailable']):
                ns['_command'](['fixed-scanner'], 30, deadline=deadline)
        self.assertEqual(calls, [])

    def test_late_clean_result_remains_unavailable(self):
        clock = [100.0]

        def run(*args, **kwargs):
            self.assertEqual(kwargs['timeout'], 0.25)
            clock[0] = 100.3
            return N(returncode=0, stdout=b'OK', stderr=b'')

        ns = source_namespace(run, clock)
        with self.assertRaises(ns['InspectionUnavailable']):
            ns['_command'](['fixed-scanner'], 30, deadline=100.25)

    def test_genuine_detection_still_permanently_refused(self):
        def run(arguments, **kwargs):
            return N(returncode=0, stdout=VERSION, stderr=b'') if '--version' in arguments else N(
                returncode=1, stdout=summary(True), stderr=b'')

        ns = source_namespace(run, [100.0])
        with self.assertRaises(ns['InspectionDenied']):
            ns['scan_original'](PATH, now=NOW, deadline=150)

    def test_ordinary_inspection_keeps_original_timeouts_and_mock_signature(self):
        calls = []

        def run(arguments, **kwargs):
            calls.append(kwargs['timeout'])
            return N(returncode=0, stdout=VERSION if '--version' in arguments else summary(), stderr=b'')

        ns = source_namespace(run, [100.0])
        self.assertEqual(ns['scan_original'](PATH, now=NOW)['engine'], 'ClamAV')
        self.assertEqual(calls, [5, 30])
        original = ns['_command']

        def legacy_command(arguments, timeout, *, allow_detection=False):
            return original(arguments, timeout, allow_detection=allow_detection)

        ns['_command'] = legacy_command
        self.assertEqual(ns['scan_original'](PATH, now=NOW)['engine'], 'ClamAV')

    def test_definition_and_coherent_scan_checks_are_not_bypassed(self):
        for invalid in (b'NOT A VERSION', VERSION):
            def run(arguments, _invalid=invalid, **kwargs):
                return N(returncode=0, stdout=_invalid if '--version' in arguments else b'INCOMPLETE', stderr=b'')

            ns = source_namespace(run, [100.0])
            with self.assertRaises(ns['InspectionUnavailable']):
                ns['scan_original'](PATH, now=NOW, deadline=150)
        ns = source_namespace(lambda *a, **kw: N(returncode=0, stdout=VERSION, stderr=b''), [100.0])
        with self.assertRaises(ns['InspectionUnavailable']):
            ns['scan_original'](PATH, now=NOW + timedelta(days=4), deadline=150)


    def test_layout_keeps_one_owner_and_shared_work_deadline_until_cleanup(self):
        source = b'inert-source'
        candidate = b'inert-candidate'
        owners = []
        events = []
        clock = [100.0]
        edits = []
        binding = {'candidate_size_bytes': len(candidate),
            'candidate_sha256': hashlib.sha256(candidate).hexdigest()}
        request = {'edits': edits, 'worker_image': 'inert-image', 'policy_sha256': 'inert-policy'}

        class Owner:
            def __init__(self, directory, deadline):
                self.directory, self.deadline = directory, deadline
                self.entered = self.stopped = False
                owners.append(self)

            def __enter__(self):
                self.entered = True
                events.append('enter')
                return self

            def scan(inner, path, content):
                self.assertTrue(inner.entered and not inner.stopped)
                self.assertEqual(path.parent, inner.directory)
                self.assertEqual(path.read_bytes(), content)
                expected = source if path.name == 'original.docx' else candidate
                self.assertIs(content, expected)
                events.append((path.name, inner.deadline, content))
                clock[0] += 20 if path.name == 'original.docx' else 5
                return {'inert_scan': path.name}

            def __exit__(self, kind, error, traceback):
                self.stopped = True
                events.append('cleanup')
                clock[0] += 1
                return False

        def render(actual_source, actual_candidate, actual_edits, *, image, policy, deadline):
            self.assertIs(actual_source, source)
            self.assertIs(actual_candidate, candidate)
            self.assertIs(actual_edits, edits)
            self.assertEqual(len(owners), 1)
            self.assertTrue(owners[0].stopped)
            events.append(('render', image, policy, deadline))
            return {'inert_layout': True}

        ns = self.layout_namespace(source, candidate, request, binding, Owner, render,
            lambda: clock[0])
        result = ns['validate_docx_edit'](b'inert-payload')
        self.assertEqual(len(owners), 1)
        self.assertEqual(owners[0].deadline, 150.0)
        self.assertEqual(events, ['enter', ('original.docx', 150.0, source),
            ('candidate.docx', 150.0, candidate), 'cleanup',
            ('render', 'inert-image', 'inert-policy', 150.0)])
        self.assertEqual(result['layout'], {'inert_layout': True})
        # The actual callsite must keep both scans in this one context and load
        # the renderer only after context exit. No product modules are imported.
        layout = next(n for n in TREE.body if isinstance(n, ast.FunctionDef)
            and n.name == 'validate_docx_edit')
        contexts = [n for n in ast.walk(layout) if isinstance(n, ast.With)
            and any(isinstance(item.context_expr, ast.Call)
                and isinstance(item.context_expr.func, ast.Name)
                and item.context_expr.func.id == 'OwnedClamd' for item in n.items)]
        self.assertEqual(len(contexts), 1)
        scans = [n for n in ast.walk(layout) if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute) and n.func.attr == 'scan']
        self.assertEqual(len(scans), 2)
        self.assertTrue(all(contexts[0].lineno < n.lineno < contexts[0].end_lineno for n in scans))
        renderer_import = next(n for n in ast.walk(layout) if isinstance(n, ast.ImportFrom)
            and n.module == 'docx_renderer')
        self.assertGreater(renderer_import.lineno, contexts[0].end_lineno)

    def test_either_scan_or_cleanup_refusal_prevents_rendering(self):
        source = b'inert-source'
        candidate = b'inert-candidate'
        binding = {'candidate_size_bytes': len(candidate),
            'candidate_sha256': hashlib.sha256(candidate).hexdigest()}
        request = {'edits': [], 'worker_image': 'inert-image', 'policy_sha256': 'inert-policy'}
        for refusal_call in (1, 2, 'cleanup'):
            for refusal_name in ('InspectionDenied', 'InspectionUnavailable'):
                with self.subTest(scan=refusal_call, refusal=refusal_name):
                    scans = []
                    renders = []
                    owners = []
                    namespace = {}
                    expected = 2 if refusal_call == 'cleanup' else refusal_call

                    class Owner:
                        def __init__(self, directory, deadline, _owners=owners):
                            self.directory, self.deadline = directory, deadline
                            self.stopped = False
                            _owners.append(self)

                        def __enter__(self):
                            return self

                        def scan(self, path, content, _scans=scans, _refusal_call=refusal_call,
                                 _namespace=namespace, _refusal_name=refusal_name):
                            _scans.append(self.deadline)
                            if len(_scans) == _refusal_call:
                                raise _namespace[_refusal_name]()
                            return {'inert_scan': True}

                        def __exit__(self, kind, error, traceback, _refusal_call=refusal_call,
                                     _namespace=namespace, _refusal_name=refusal_name):
                            self.stopped = True
                            if _refusal_call == 'cleanup':
                                raise _namespace[_refusal_name]()
                            return False

                    def render(*args, _renders=renders, **kwargs):
                        _renders.append(True)
                        raise AssertionError('refused input reached renderer')

                    namespace.update(self.layout_namespace(source, candidate, request,
                        binding, Owner, render, lambda: 100.0))
                    with self.assertRaises(namespace[refusal_name]):
                        namespace['validate_docx_edit'](b'inert-payload')
                    self.assertEqual(scans, [150.0] * expected)
                    self.assertEqual(len(owners), 1)
                    self.assertTrue(owners[0].stopped)
                    self.assertEqual(renders, [])

    @staticmethod
    def layout_namespace(source, candidate, request, binding, owner, render, monotonic):
        scanner = source_namespace(lambda *args, **kwargs: None, [100.0])
        modules = {
            'app.services.docx_layout_validation': N(LAYOUT_REASONS=set(),
                DocxLayoutError=type('InertDocxLayoutError', (ValueError,), {})),
            'app.services.resume_layout': N(ResumeLayoutError=type('InertResumeLayoutError',
                (ValueError,), {}), _apply_docx=lambda original, edits: candidate),
            'docx_renderer': N(validate_rendered_edit=render),
        }

        def inert_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name not in modules or level:
                raise AssertionError('unexpected import in isolated layout control')
            return modules[name]

        ns = {'__builtins__': {**vars(builtins), '__import__': inert_import},
            'time': N(monotonic=monotonic), 'tempfile': tempfile, 'Path': Path,
            'hashlib': hashlib, 'OwnedClamd': owner, 'validate_docx': lambda content: None,
            'decode_docx_request': lambda payload: (source, {'request': request, 'binding': binding}),
            'InspectionDenied': scanner['InspectionDenied'],
            'InspectionUnavailable': scanner['InspectionUnavailable'],
            'DocxLayoutDenied': type('InertDocxLayoutDenied', (ValueError,), {})}
        node = next(n for n in TREE.body if isinstance(n, ast.FunctionDef)
            and n.name == 'validate_docx_edit')
        exec(compile(ast.Module(body=[node], type_ignores=[]), 'isolated_actual_layout_function',
            'exec'), ns)
        return ns


if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(SharedDeadlineTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    report = {'scope': 'pure_AST_deadline_regressions_not_native_evidence', 'tests': result.testsRun,
        'passed': result.wasSuccessful(), 'app_imports': 0, 'SDK_imports': 0,
        'native_provider_calls': 0, 'native_scanner_or_parser_runs': 0, 'repository_edits': 0}
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if result.wasSuccessful() else 1)
