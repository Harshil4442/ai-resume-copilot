"""Pure synthetic exceptions/AST protocol checks, never native daemon evidence."""
import ast
import json
import subprocess
import unittest
from contextvars import ContextVar
from pathlib import Path
from types import SimpleNamespace
from typing import NamedTuple

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'backend/scripts/document_test_image.py'
PROCESSOR = ROOT / 'infra/document-processing/processor.py'


class InspectionUnavailable(RuntimeError):
    pass


class InspectionDenied(ValueError):
    pass


class DiagnosticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tree = ast.parse(SCRIPT.read_bytes())
        constants = {'CLAMD_FAILURE_LINES', 'CLAMD_FAILURE_PHASES', 'CLAMD_FAILURE_CLASSES',
                     '_FAILURE_STAGES', '_FAILURE_CASES', '_FAILURE', 'ERROR'}
        definitions = {'_ClamdFailure', '_clamd_diagnostic_pairs', '_clamd_diagnostic_values',
                       '_clamd_diagnostic_object', '_note_clamd_failure', '_failure_report', '_progress'}
        nodes = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in definitions:
                nodes.append(node)
            elif isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in constants
                                                      for t in node.targets):
                nodes.append(node)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id in constants:
                nodes.append(node)
        cls.host = {'ContextVar': ContextVar, 'NamedTuple': NamedTuple, 'json': json,
                    'DocumentImageError': ValueError}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), 'pure_diagnostic_driver', 'exec'), cls.host)
        program = next(node.value for node in tree.body if isinstance(node, ast.Assign)
                       and any(isinstance(t, ast.Name) and t.id == 'CLAMD_NATIVE_PROGRAM' for t in node.targets))
        native_tree = ast.parse(ast.literal_eval(program))
        native_nodes = [node for node in native_tree.body if (
            isinstance(node, ast.FunctionDef) and node.name == 'failure_receipt') or (
            isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in
                {'CLAMD_FAILURE_LINES', 'CLAMD_FAILURE_PHASES', 'CLAMD_FAILURE_CLASSES', 'CLAMD_PHASE'}
                                                for t in node.targets))]
        cls.native = {'processor': SimpleNamespace(InspectionUnavailable=InspectionUnavailable,
            InspectionDenied=InspectionDenied, subprocess=SimpleNamespace(TimeoutExpired=subprocess.TimeoutExpired))}
        exec(compile(ast.Module(body=native_nodes, type_ignores=[]), 'pure_native_failure_projection', 'exec'), cls.native)

    def setUp(self):
        token = self.host['_FAILURE'].set({'stage': 'case_validation', 'case': 'clamd_two_clean',
                                         'exit_code': 1, 'completed_cases': 8})
        self.addCleanup(self.host['_FAILURE'].reset, token)
        self.native['CLAMD_PHASE'] = 'source_scan'

    def exception_at(self, function, line):
        source = '\n' * (line - 2) + 'def ' + function + '():\n    raise error from None\n'
        namespace = {'error': InspectionUnavailable('unexported synthetic marker')}
        exec(compile(source, '/opt/document/processor.py', 'exec'), namespace)
        try:
            namespace[function]()
        except InspectionUnavailable as error:
            return error
        raise AssertionError('missing synthetic exception')

    def object(self):
        return {'version': 1, 'status': 'native_clamd_proof_unavailable', 'phase': 'source_scan',
                'chain': [{'class': 'InspectionUnavailable', 'frames': [{'function': 'scan', 'line': 539}]}],
                'truncated': False}

    def encoded(self, value):
        return (json.dumps(value, separators=(',', ':')) + '\n').encode()

    def test_context_chain_preserves_cleanup_wrapper_and_original_owned_raise(self):
        original = self.exception_at('_diagnostics', 359)
        wrapped = self.exception_at('scan', 539)
        cleanup = self.exception_at('close', 572)
        wrapped.__context__ = original
        cleanup.__context__ = wrapped
        receipt = self.native['failure_receipt'](cleanup)
        self.assertEqual([row['frames'][0] for row in receipt['chain']], [
            {'function': 'close', 'line': 572}, {'function': 'scan', 'line': 539},
            {'function': '_diagnostics', 'line': 359}])
        self.assertNotIn('unexported', json.dumps(receipt))
        self.assertNotIn('/opt/', json.dumps(receipt))
        self.assertFalse(receipt['truncated'])
        self.host['_note_clamd_failure'](self.encoded(receipt), 1, case='clamd_two_clean')
        self.assertEqual(self.host['_failure_report']()['clamd_failure'], receipt)

    def test_bounded_cycle_and_context_depth(self):
        error = ValueError('unexported')
        error.__context__ = error
        receipt = self.native['failure_receipt'](error)
        self.assertTrue(receipt['truncated'])
        self.assertEqual(len(receipt['chain']), 1)
        current = ValueError()
        for _ in range(10):
            outer = RuntimeError()
            outer.__context__ = current
            current = outer
        receipt = self.native['failure_receipt'](current)
        self.assertTrue(receipt['truncated'])
        self.assertEqual(len(receipt['chain']), 8)

    def test_unknown_exception_class_and_message_never_exported(self):
        class PrivateNamedError(Exception):
            def __str__(self):
                raise AssertionError('message access forbidden')
        receipt = self.native['failure_receipt'](PrivateNamedError('unexported'))
        self.assertEqual(receipt['chain'], [{'class': 'Unexpected', 'frames': []}])
        self.assertNotIn('PrivateNamed', json.dumps(receipt))

    def test_literal_raise_tables_match_pinned_processor_AST(self):
        tree = ast.parse(PROCESSOR.read_bytes())
        selected = {'_command', '_native_file', '_installed_clamd', '_clamd_definition'}
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in selected]
        owner = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'OwnedClamd')
        functions += [node for node in owner.body if isinstance(node, ast.FunctionDef)]
        expected = {node.name: tuple(sorted(child.lineno for child in ast.walk(node) if isinstance(child, ast.Raise)))
                    for node in functions}
        expected = {name: lines for name, lines in expected.items() if lines}
        self.assertEqual(self.host['CLAMD_FAILURE_LINES'], expected)
        self.assertEqual(self.native['CLAMD_FAILURE_LINES'], expected)

    def test_exact_schema_EOF_and_safe_class_function_line_refusals(self):
        mutations = [lambda value: value.update(extra='unexported'),
            lambda value: value.update(phase='unexported'),
            lambda value: value.update(version=True),
            lambda value: value['chain'][0].update(extra='unexported'),
            lambda value: value['chain'][0].update(**{'class': 'unexported'}),
            lambda value: value['chain'][0]['frames'][0].update(function='unexported'),
            lambda value: value['chain'][0]['frames'][0].update(line=True),
            lambda value: value['chain'][0]['frames'][0].update(line=540),
            lambda value: value.update(chain=value['chain'] * 9),
            lambda value: value['chain'][0].update(frames=value['chain'][0]['frames'] * 9)]
        for mutate in mutations:
            value = self.object()
            mutate(value)
            self.host['_note_clamd_failure'](self.encoded(value), 1, case='clamd_two_clean')
            self.assertNotIn('clamd_failure', self.host['_failure_report']())
        valid = self.encoded(self.object())
        for raw in (valid[:-1], valid + b'junk', valid + b'\n', b' ' + valid,
                    valid.replace(b'"version":1', b'"version":1,"version":1'), b'x' * 8193):
            self.host['_note_clamd_failure'](raw, 1, case='clamd_two_clean')
            self.assertNotIn('clamd_failure', self.host['_failure_report']())

    def test_binding_and_progress_clear_stale_failure(self):
        raw = self.encoded(self.object())
        for code, case in ((0, 'clamd_two_clean'), (2, 'clamd_two_clean'), (1, 'clean_pdf')):
            self.host['_note_clamd_failure'](raw, code, case=case)
            self.assertNotIn('clamd_failure', self.host['_failure_report']())
        self.host['_note_clamd_failure'](raw, 1, case='clamd_two_clean')
        self.assertIn('clamd_failure', self.host['_failure_report']())
        self.host['_progress']('case_request', case='clamd_source_eicar')
        self.assertNotIn('clamd_failure', self.host['_failure_report']())


if __name__ == '__main__':
    unittest.main()
