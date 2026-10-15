"""Pure procfs short-read/deadline guards; no native/app/provider imports."""
import ast
import copy
import math
import os
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
PROCESSOR = ROOT / "infra/document-processing/processor.py"


class Reader:
    def __init__(self, chunks, clock, advances=None, refusal_at=None):
        self.chunks = list(chunks)
        self.clock = clock
        self.advances = list(advances or [])
        self.refusal_at = refusal_at
        self.requests = []
        self.closed = []
        self.eof = False

    def open(self, path, flags):
        if path != "/proc/424242/maps" or flags != os.O_RDONLY | os.O_NOFOLLOW:
            raise AssertionError("unowned synthetic descriptor")
        return 9

    def read(self, descriptor, count):
        if descriptor != 9:
            raise AssertionError("unowned synthetic descriptor")
        self.requests.append(count)
        if self.refusal_at == len(self.requests):
            raise OSError("synthetic read refusal")
        if self.advances:
            self.clock.value += self.advances.pop(0)
        if not self.chunks:
            self.eof = True
            return b""
        value = self.chunks.pop(0)
        if len(value) > count:
            self.chunks.insert(0, value[count:])
        return value[:count]

    def close(self, descriptor):
        self.closed.append(descriptor)


class MapsEOFTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tree = ast.parse(PROCESSOR.read_bytes())
        cls.owned = next(n for n in cls.tree.body if isinstance(n, ast.ClassDef) and n.name == "OwnedClamd")
        cls.helper = next(n for n in cls.tree.body if isinstance(n, ast.FunctionDef) and n.name == "_read_clamd_maps")
        cls.remaining = next(n for n in cls.owned.body if isinstance(n, ast.FunctionDef) and n.name == "_remaining")
        cls.start = next(n for n in cls.owned.body if isinstance(n, ast.FunctionDef) and n.name == "_start")

    def namespace(self, chunks, *, advances=None, refusal_at=None, expired=False, old_single_read=False):
        clock = SimpleNamespace(value=10.0 if expired else 0.0)
        reader = Reader(chunks, clock, advances, refusal_at)
        scope = {"os": SimpleNamespace(open=reader.open, read=reader.read, close=reader.close,
            O_RDONLY=os.O_RDONLY, O_NOFOLLOW=os.O_NOFOLLOW), "InspectionUnavailable": ValueError,
            "math": math, "time": SimpleNamespace(monotonic=lambda: clock.value)}
        owned = ast.ClassDef(name="OwnedClamd", bases=[], keywords=[], body=[copy.deepcopy(self.remaining)], decorator_list=[])
        helper = copy.deepcopy(self.helper)
        start = copy.deepcopy(self.start)
        begin = next(i for i, node in enumerate(start.body) if isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Attribute)
            and node.value.func.attr == "open" and isinstance(node.value.args[0], ast.BinOp)
            and isinstance(node.value.args[0].right, ast.Constant) and node.value.args[0].right.value == "/maps")
        tail = ast.parse("def maps_tail(self, deadline, manifest):\n    pass\n").body[0]
        tail.body = start.body[begin:]
        if old_single_read:
            block = next(n for n in tail.body if isinstance(n, ast.Try))
            # Exact old source implementation, supplied through the same actual caller/guard.
            block.body = ast.parse(
                'raw = os.read(descriptor, 1048577)\n'
                'if len(raw) > 1048576:\n    raise InspectionUnavailable()\n'
                'maps = raw.decode("ascii", errors="strict")\n').body
        tree = ast.fix_missing_locations(ast.Module(body=[owned, helper, tail], type_ignores=[]))
        exec(compile(tree, "owned_maps_guard_only", "exec"), scope)
        return scope, reader

    def tail(self, chunks, **options):
        scope, reader = self.namespace(chunks, **options)
        scope["maps_tail"](SimpleNamespace(child=SimpleNamespace(pid=424242),
            _remaining=scope["OwnedClamd"]._remaining), 10.0,
            {"files": [{}, {}, {"path": "/usr/lib/public/libclamav.so.12"}]})
        return reader

    def test_old_short_read_reproducer_and_complete_EOF_guard(self):
        chunks = [b"0001-0002 r--p 0 00:00 1 /usr/lib/public/other.so\n",
                  b"0002-0003 r-xp 0 00:00 2 /usr/lib/public/libclamav.so.12\n"]
        old, reader = self.namespace(chunks, old_single_read=True)
        with self.assertRaises(ValueError):
            old["maps_tail"](SimpleNamespace(child=SimpleNamespace(pid=424242),
                _remaining=old["OwnedClamd"]._remaining), 10.0,
                {"files": [{}, {}, {"path": "/usr/lib/public/libclamav.so.12"}]})
        self.assertEqual(len(reader.requests), 1)
        self.assertFalse(reader.eof)
        self.assertEqual(reader.closed, [9])
        reader = self.tail(chunks)
        self.assertEqual(len(reader.requests), 3)
        self.assertTrue(reader.eof)
        self.assertEqual(reader.closed, [9])

    def test_record_split_across_short_reads_is_joined_before_comparison(self):
        reader = self.tail([b"0001-0002 r-xp 0 00:00 2 /usr/lib/public/libcla",
                            b"mav.so.", b"12", b"\n"])
        self.assertTrue(reader.eof)
        self.assertEqual(reader.closed, [9])

    def test_exact_library_equality_still_refuses_missing_wrong_and_multiple(self):
        chunks = [b"0001-0002 r-xp 0 00:00 2 /usr/lib/public/libclamav.so.12\n",
                  b"0003-0004 r-xp 0 00:00 3 /usr/lib/public/libclamav.so.other\n"]
        old, reader = self.namespace(chunks, old_single_read=True)
        old["maps_tail"](SimpleNamespace(child=SimpleNamespace(pid=424242),
            _remaining=old["OwnedClamd"]._remaining), 10.0,
            {"files": [{}, {}, {"path": "/usr/lib/public/libclamav.so.12"}]})
        self.assertFalse(reader.eof)  # The old first prefix wrongly admits the set.
        self.assertEqual(reader.closed, [9])
        scope, reader = self.namespace(chunks)
        with self.assertRaises(ValueError):
            scope["maps_tail"](SimpleNamespace(child=SimpleNamespace(pid=424242),
                _remaining=scope["OwnedClamd"]._remaining), 10.0,
                {"files": [{}, {}, {"path": "/usr/lib/public/libclamav.so.12"}]})
        self.assertTrue(reader.eof)
        self.assertEqual(reader.closed, [9])
        for content in (b"", b"0001-0002 r-xp 0 00:00 2 /usr/lib/public/libclamav.so.other\n",
                        b"0001-0002 r-xp 0 00:00 2 /usr/lib/public/libclamav.so.12\n"
                        b"0003-0004 r-xp 0 00:00 3 /usr/lib/public/libclamav.so.other\n"):
            scope, reader = self.namespace([content] if content else [])
            with self.assertRaises(ValueError):
                scope["maps_tail"](SimpleNamespace(child=SimpleNamespace(pid=424242),
                    _remaining=scope["OwnedClamd"]._remaining), 10.0,
                    {"files": [{}, {}, {"path": "/usr/lib/public/libclamav.so.12"}]})
            self.assertTrue(reader.eof)
            self.assertEqual(reader.closed, [9])

    def test_exact_cap_requires_EOF_and_cap_plus_one_refuses(self):
        scope, reader = self.namespace([b"a" * 1048576])
        self.assertEqual(len(scope["_read_clamd_maps"](9, 10.0)), 1048576)
        self.assertTrue(reader.eof)
        self.assertEqual(reader.requests[-1], 1)
        self.assertTrue(all(0 < n <= 65536 for n in reader.requests))
        scope, reader = self.namespace([b"a" * 1048577])
        with self.assertRaises(ValueError):
            scope["_read_clamd_maps"](9, 10.0)
        self.assertFalse(reader.eof)
        self.assertEqual(reader.requests[-1], 1)

    def test_expired_between_reads_and_after_EOF_refuse_with_descriptor_cleanup(self):
        for options, chunks, calls in (({"expired": True}, [b"x"], 0),
                                      ({"advances": [10.0]}, [b"x", b"y"], 1),
                                      ({"advances": [10.0]}, [], 1)):
            scope, reader = self.namespace(chunks, **options)
            with self.assertRaises(ValueError):
                scope["maps_tail"](SimpleNamespace(child=SimpleNamespace(pid=424242),
                    _remaining=scope["OwnedClamd"]._remaining), 10.0,
                    {"files": [{}, {}, {"path": "/usr/lib/public/libclamav.so.12"}]})
            self.assertEqual(len(reader.requests), calls)
            self.assertEqual(reader.closed, [9])

    def test_non_ASCII_complete_read_refuses_with_descriptor_cleanup(self):
        scope, reader = self.namespace([b"\xff"])
        with self.assertRaises(UnicodeError):
            scope["maps_tail"](SimpleNamespace(child=SimpleNamespace(pid=424242),
                _remaining=scope["OwnedClamd"]._remaining), 10.0,
                {"files": [{}, {}, {"path": "/usr/lib/public/libclamav.so.12"}]})
        self.assertTrue(reader.eof)
        self.assertEqual(reader.closed, [9])

    def test_read_error_closes_descriptor_and_does_not_invent_EOF(self):
        scope, reader = self.namespace([b"first", b"second"], refusal_at=2)
        with self.assertRaises(OSError):
            scope["maps_tail"](SimpleNamespace(child=SimpleNamespace(pid=424242),
                _remaining=scope["OwnedClamd"]._remaining), 10.0,
                {"files": [{}, {}, {"path": "/usr/lib/public/libclamav.so.12"}]})
        self.assertFalse(reader.eof)
        self.assertEqual(reader.closed, [9])


if __name__ == "__main__":
    unittest.main()
