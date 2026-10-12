"""Pure owned fixture regression: no image driver/app aggregates/native engines."""
import ast
import base64
import hashlib
import importlib.util
import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock
from zipfile import ZIP_DEFLATED, ZipFile

from docx import Document

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'backend/scripts/document_test_image.py'


class ClamdCorpusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tree = ast.parse(SCRIPT.read_bytes())
        nodes = [node for node in tree.body if (
            isinstance(node, ast.FunctionDef) and node.name in {'corpus', 'clamd_corpus'}) or (
            isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == 'EICAR'
                                                 for target in node.targets))]
        if len(nodes) != 3:
            raise AssertionError('unexpected owned fixture AST')
        cls.namespace = {'ROOT': ROOT, 'DocumentImageError': ValueError, 'base64': base64,
            'hashlib': hashlib, 'importlib': importlib, 'io': io, 'json': json, 'sys': sys,
            'ZipFile': ZipFile, 'ZIP_DEFLATED': ZIP_DEFLATED}
        for target in ('subprocess.Popen', 'subprocess.run', 'socket.socket'):
            patcher = mock.patch(target, side_effect=AssertionError('native/provider call forbidden'))
            patcher.start()
            cls.addClassCleanup(patcher.stop)
        exec(compile(ast.Module(body=nodes, type_ignores=[]), 'pure_owned_fixture_functions', 'exec'),
             cls.namespace)
        cls.rows = cls.namespace['clamd_corpus']()
        cls.layout = sys.modules['document_native_clamd_patch']

    def test_all_eleven_cases_generate_and_bind_exact_payloads(self):
        expected = ['clamd_two_clean', 'clamd_source_eicar', 'clamd_candidate_eicar',
            'clamd_zip_member_eicar', 'clamd_encrypted_pdf', 'clamd_encrypted_zip',
            'clamd_max_filesize', 'clamd_max_scansize', 'clamd_max_files',
            'clamd_max_recursion', 'clamd_resident_parser']
        self.assertEqual([name for name, _, _, _ in self.rows], expected)
        for name, payload, outcome, resident in self.rows:
            with self.subTest(case=name):
                self.assertLessEqual(len(payload), 7 * 1024**2)
                request = json.loads(payload)
                self.assertEqual(set(request), {'case', 'source', 'candidate', 'expected',
                    'resident_parser', 'candidate_sha256', 'candidate_size_bytes', 'edits'})
                self.assertEqual(request['case'], name)
                self.assertEqual(request['expected'], outcome)
                self.assertIs(request['resident_parser'], resident)
                source = base64.b64decode(request['source'], validate=True)
                candidate = base64.b64decode(request['candidate'], validate=True)
                self.assertTrue(0 < len(source) <= 5 * 1024**2)
                self.assertTrue(0 < len(candidate) <= 5 * 1024**2)
                self.assertIs(type(request['candidate_size_bytes']), int)
                self.assertEqual(request['candidate_size_bytes'], len(candidate))
                self.assertEqual(request['candidate_sha256'], hashlib.sha256(candidate).hexdigest())
                if not resident:
                    self.assertIsNone(request['edits'])
        source_case = json.loads(self.rows[1][1])
        candidate_case = json.loads(self.rows[2][1])
        eicar = self.namespace['EICAR']
        self.assertEqual(len(eicar), 68)
        self.assertEqual(base64.b64decode(source_case['source'], validate=True), eicar)
        self.assertEqual(base64.b64decode(candidate_case['candidate'], validate=True), eicar)

    def test_resident_edit_is_eligible_and_roundtrips_exact_candidate(self):
        name, payload, outcome, resident = self.rows[-1]
        self.assertEqual(name, 'clamd_resident_parser')
        self.assertEqual(outcome, 'clean')
        self.assertTrue(resident)
        request = json.loads(payload)
        source = base64.b64decode(request['source'], validate=True)
        candidate = base64.b64decode(request['candidate'], validate=True)
        edits = request['edits']
        self.assertEqual(len(edits), 1)
        original = 'Developed Python automation tools for reliable test workflows.'
        replacement = 'Built Python automation tools for reliable test workflows.'
        self.assertGreaterEqual(len(original), 30)
        self.assertGreaterEqual(len(original.split()), 5)
        units = self.layout.extract_source_units(source, 'docx')
        unit = next(unit for unit in units if unit['text'] == original)
        self.assertEqual(edits, [{'unit_id': unit['unit_id'], 'original_text': original,
                                  'replacement_text': replacement}])
        self.assertEqual(self.layout._apply_docx(source, edits), candidate)
        candidate_units = self.layout.extract_source_units(candidate, 'docx')
        self.assertIn(replacement, [unit['text'] for unit in candidate_units])
        self.assertNotIn(original, [unit['text'] for unit in candidate_units])
        self.assertNotEqual(source, candidate)

    def test_ordinary_eight_cases_keep_identity_only_clean_docx(self):
        ordinary = self.namespace['corpus']()
        self.assertEqual([row[0] for row in ordinary], ['clean_pdf', 'clean_docx', 'page_counter',
            'active_pdf', 'includetext', 'eicar_exact_file', 'eicar_zip_member', 'missing_definitions'])
        docx = next(content for name, _, content, _ in ordinary if name == 'clean_docx')
        paragraphs = [paragraph.text for paragraph in Document(io.BytesIO(docx)).paragraphs]
        self.assertEqual(paragraphs, ['Candidate Example', 'SKILLS', 'Python, SQL'])
        self.assertEqual(self.layout.extract_source_units(docx, 'docx'), [])
        resident_source = base64.b64decode(json.loads(self.rows[-1][1])['source'], validate=True)
        self.assertNotEqual(resident_source, docx)


if __name__ == '__main__':
    unittest.main()
