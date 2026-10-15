"""Pure error-only startup witness guards; no daemon/container/app execution."""
import ast
import copy
import json
import math
import time
import unittest
from contextvars import ContextVar
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'backend/scripts/document_test_image.py'


class StartupWireTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tree = ast.parse(SCRIPT.read_bytes())
        constants = {'CLAMD_FAILURE_LINES', 'CLAMD_FAILURE_PHASES', 'CLAMD_FAILURE_CLASSES',
            'CLAMD_STARTUP_INDEX_SHA', 'CLAMD_STARTUP_STATUSES', 'CLAMD_STARTUP_SHAPES',
            '_FAILURE_STAGES', '_FAILURE_CASES', '_FAILURE', 'ERROR', 'CLAMD_STARTUP_DIAGNOSTIC_PROGRAM'}
        definitions = {'_ClamdFailure', '_ClamdStartupObservation', '_clamd_diagnostic_pairs',
            '_clamd_diagnostic_values', '_clamd_diagnostic_object', '_clamd_startup_values',
            '_clamd_startup_object', '_note_clamd_failure', '_failure_report', '_progress',
            '_diagnose_clamd_startup'}
        nodes = []
        for node in cls.tree.body:
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in definitions:
                nodes.append(node)
            elif isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in constants
                                                      for t in node.targets):
                nodes.append(node)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id in constants:
                nodes.append(node)
        cls.scope = {'ContextVar': ContextVar, 'NamedTuple': NamedTuple, 'json': json,
            'DocumentImageError': ValueError, 'math': math, 'time': time, 'Path': Path}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), 'pure_startup_wire', 'exec'), cls.scope)

    def setUp(self):
        token = self.scope['_FAILURE'].set({'stage': 'case_validation', 'case': 'clamd_two_clean',
                                         'exit_code': 1, 'completed_cases': 8})
        self.addCleanup(self.scope['_FAILURE'].reset, token)
        self.original = self.core()
        self.scope['_note_clamd_failure'](self.encoded(self.original), 1, case='clamd_two_clean')
        self.calls = []
        self.scope['container'] = self.fake_container
        self.result = 1, self.encoded(self.value())

    def core(self):
        return {'version': 1, 'status': 'native_clamd_proof_unavailable', 'phase': 'source_scan',
            'chain': [{'class': 'InspectionUnavailable', 'frames': [{'function': '_diagnostics', 'line': 623}]}],
            'truncated': False}

    def value(self):
        return {**self.core(), 'startup_log_witness': {'version': 1, 'status': 'observed_prefix',
            'snapshot_final': False, 'records': [{'stream': 'log', 'ordinal': 0, 'line_bytes': 25,
                'line_sha256': 'a' * 64, 'shape': 'printable_ascii', 'public_template_indexes': [1]}],
            'truncated': False, 'public_template_index_sha256': self.scope['CLAMD_STARTUP_INDEX_SHA']}}

    def encoded(self, value):
        return (json.dumps(value, separators=(',', ':')) + '\n').encode()

    def fake_container(self, *args):
        self.calls.append(args)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    def diagnose(self):
        self.scope['_diagnose_clamd_startup'](['owned-docker'], 'owned-image', 'linux/amd64',
            Path('/owned/clamd_two_clean.json'), case='clamd_two_clean')
        return self.scope['_failure_report']()

    def test_error_only_derivative_same_binding_once_and_original_retained(self):
        report = self.diagnose()
        self.assertEqual(report['clamd_failure'], self.original)
        projection = report['clamd_startup_diagnostic']
        self.assertEqual(projection['startup_log_witness'], self.value()['startup_log_witness'])
        self.assertEqual(projection['status'], 'error_receipt_observed')
        self.assertEqual((projection['original_container_calls'], projection['diagnostic_container_calls'],
                          projection['combined_case_container_calls']), (1, 1, 2))
        self.assertTrue(projection['original_failure_retained'])
        self.assertFalse(projection['acceptance_evidence'])
        self.assertEqual(self.calls, [(['owned-docker'], 'owned-image', 'linux/amd64',
            ['-c', self.scope['CLAMD_STARTUP_DIAGNOSTIC_PROGRAM']],
            [(Path('/owned/clamd_two_clean.json'), '/tmp/request.json')])])
        self.diagnose()
        self.assertEqual(len(self.calls), 1)

    def test_derivative_success_exception_and_malformed_do_not_replace_failure_or_retry(self):
        for result, expected in (( (0, b'unexported success output'), 'returned_success'),
                                 (RuntimeError('unexported'), 'unavailable'),
                                 ((1, b'unexported'), 'unavailable'), ((True, b''), 'unavailable')):
            self.scope['_FAILURE'].get().pop('clamd_startup_observation', None)
            self.calls.clear()
            self.result = result
            report = self.diagnose()
            self.assertEqual(report['clamd_failure'], self.original)
            self.assertEqual(report['exit_code'], 1)
            self.assertEqual(report['clamd_startup_diagnostic']['status'], expected)
            self.assertNotIn('startup_log_witness', report['clamd_startup_diagnostic'])
            self.assertNotIn('unexported', json.dumps(report))
            self.diagnose()
            self.assertEqual(len(self.calls), 1)

    def test_only_exact_original_first_case_source_unknown_refusal_admitted(self):
        stored = self.scope['_FAILURE'].get()
        original = stored['clamd_failure']
        for field, value in (('case', 'clamd_source_eicar'), ('exit_code', 0), ('exit_code', True),
                             ('stage', 'case_execution')):
            previous = stored[field]
            stored[field] = value
            self.diagnose()
            stored[field] = previous
        stored['clamd_failure'] = self.scope['_ClamdFailure']('clamd_two_clean', 'candidate_scan', original.chain, False)
        self.diagnose()
        stored['clamd_failure'] = self.scope['_ClamdFailure']('clamd_two_clean', 'source_scan',
            (('InspectionUnavailable', (('_diagnostics', 625),)),), False)
        self.diagnose()
        self.assertEqual(self.calls, [])
        self.assertNotIn('clamd_startup_observation', stored)

    def test_exact_schema_bounds_and_typed_index_refusals(self):
        mutations = [lambda w: w.update(extra='unexported'), lambda w: w.update(version=True),
            lambda w: w.update(snapshot_final=True), lambda w: w.update(status='unexported'),
            lambda w: w.update(public_template_index_sha256='b' * 64),
            lambda w: w.update(records=w['records'] * 5),
            lambda w: w['records'][0].update(extra='unexported'),
            lambda w: w['records'][0].update(stream='stderr'),
            lambda w: w['records'][0].update(ordinal=True),
            lambda w: w['records'][0].update(ordinal=1024),
            lambda w: w['records'][0].update(line_bytes=2049),
            lambda w: w['records'][0].update(line_sha256='unexported'),
            lambda w: w['records'][0].update(shape='unexported'),
            lambda w: w['records'][0].update(public_template_indexes=[True]),
            lambda w: w['records'][0].update(public_template_indexes=[104]),
            lambda w: w['records'][0].update(public_template_indexes=[1, 1]),
            lambda w: w['records'][0].update(public_template_indexes=list(range(9)))]
        for mutate in mutations:
            value = self.value()
            mutate(value['startup_log_witness'])
            with self.assertRaises(ValueError):
                self.scope['_clamd_startup_values'](value['startup_log_witness'])
        value = self.value()
        value['startup_log_witness']['records'] = []
        with self.assertRaises(ValueError):
            self.scope['_clamd_startup_values'](value['startup_log_witness'])

    def test_canonical_EOF_duplicate_trailing_bytes_and_byte_cap_refused(self):
        valid = self.encoded(self.value())
        for raw in (valid[:-1], valid + b'\n', b' ' + valid, valid + b'unexported', b' ' * 8193 + b'\n',
                    valid.replace(b'"version":1', b'"version":1,"version":1', 1)):
            self.scope['_FAILURE'].get().pop('clamd_startup_observation', None)
            self.result = 1, raw
            report = self.diagnose()
            self.assertEqual(report['clamd_failure'], self.original)
            self.assertEqual(report['clamd_startup_diagnostic']['status'], 'unavailable')

    def test_progress_clears_stale_and_old_original_schema_remains_strict(self):
        self.diagnose()
        self.scope['_progress']('case_request', case='clamd_source_eicar')
        self.assertNotIn('clamd_startup_observation', self.scope['_FAILURE'].get())
        self.assertNotIn('clamd_startup_diagnostic', self.scope['_failure_report']())
        self.scope['_note_clamd_failure'](self.encoded(self.value()), 1, case='clamd_two_clean')
        self.assertNotIn('clamd_failure', self.scope['_failure_report']())

    def test_report_revalidates_storage_before_projection(self):
        self.diagnose()
        stored = self.scope['_FAILURE'].get()
        observation = stored['clamd_startup_observation']
        for malformed in (observation._replace(case='clean_pdf'), observation._replace(container_calls=2),
                          observation._replace(elapsed=float('nan')), observation._replace(exit_code=True)):
            stored['clamd_startup_observation'] = malformed
            self.assertNotIn('clamd_startup_diagnostic', self.scope['_failure_report']())
        stored['clamd_startup_observation'] = observation._replace(values=('unexported', (), False, None))
        self.assertEqual(self.scope['_failure_report']()['clamd_startup_diagnostic']['status'], 'unavailable')

    def test_uninstrumented_acceptance_and_derivative_limits_structure(self):
        programs = {node.targets[0].id: ast.literal_eval(node.value) for node in self.tree.body
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id in {'CLAMD_NATIVE_PROGRAM', 'CLAMD_STARTUP_DIAGNOSTIC_PROGRAM'}}
        original = ast.parse(programs['CLAMD_NATIVE_PROGRAM'])
        derivative = ast.parse(programs['CLAMD_STARTUP_DIAGNOSTIC_PROGRAM'])
        original_main = next(node for node in original.body if isinstance(node, ast.FunctionDef) and node.name == 'main')
        diagnostic_main = next(node for node in derivative.body if isinstance(node, ast.FunctionDef) and node.name == 'main')
        instructions = [ast.unparse(node) for node in diagnostic_main.body]
        deadline = instructions.index('deadline = time.monotonic() + 50')
        install = instructions.index('install(processor)')
        self.assertEqual(install, deadline + 1)
        unchanged = copy.deepcopy(diagnostic_main)
        del unchanged.body[install]
        self.assertEqual(ast.dump(original_main, include_attributes=False), ast.dump(unchanged, include_attributes=False))
        self.assertFalse(any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == 'install'
                             for n in ast.walk(original)))
        check = next(node for node in self.tree.body if isinstance(node, ast.FunctionDef) and node.name == 'check')
        self.assertEqual(sum(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            and n.func.id == '_diagnose_clamd_startup' for n in ast.walk(check)), 1)
        # The derivative is followed by unconditional original refusal, never validation/success substitution.
        branch = next(n for n in ast.walk(check) if isinstance(n, ast.If)
            and any(isinstance(x, ast.Expr) and isinstance(x.value, ast.Call) and isinstance(x.value.func, ast.Name)
                    and x.value.func.id == '_diagnose_clamd_startup' for x in n.body))
        self.assertEqual(ast.unparse(branch.test), 'code')
        self.assertIsInstance(branch.body[-1], ast.Raise)


if __name__ == '__main__':
    unittest.main()
