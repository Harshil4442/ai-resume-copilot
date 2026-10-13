"""Pure ZIP admission/security controls; no daemon, document parser or app imports."""
import ast
import hashlib
import importlib.util
import io
import struct
import tempfile
import time
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

ROOT = Path(__file__).resolve().parents[2]
PROCESSOR = ROOT / 'infra/document-processing/processor.py'
spec = importlib.util.spec_from_file_location('pure_zip_processor', PROCESSOR)
processor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(processor)


def archive(rows, *, method=ZIP_DEFLATED, streamed=False, zip64=False):
    class Unseekable(io.BytesIO):
        def seekable(self):
            return False

        def seek(self, *args):
            raise OSError()

    output = Unseekable() if streamed else io.BytesIO()
    with ZipFile(output, 'w', method) as zipped:
        for name, content in rows:
            if zip64:
                with zipped.open(name, 'w', force_zip64=True) as member:
                    member.write(content)
            else:
                zipped.writestr(name, content)
    return output.getvalue()


def forged_metadata(value, *, size=None, crc=None):
    value = bytearray(value)
    central = value.index(b'PK\x01\x02')
    if size is not None:
        struct.pack_into('<I', value, 22, size)
        struct.pack_into('<I', value, central + 24, size)
    if crc is not None:
        struct.pack_into('<I', value, 14, crc)
        struct.pack_into('<I', value, central + 16, crc)
    return bytes(value)


class ZipAdmissionTests(unittest.TestCase):
    def admit(self, value):
        processor._zip_admit(value, time.monotonic() + 10)

    def refused(self, value, reason):
        with self.assertRaises(processor.ZipAdmissionUnavailable) as caught:
            self.admit(value)
        self.assertEqual(caught.exception.reason, reason)

    def test_bounds_are_fixed_and_input_budget_not_increased(self):
        self.assertEqual(processor.MAX_FILE, 5 * 1024**2)
        self.assertEqual((processor.ZIP_MEMBER_LIMIT, processor.ZIP_TOTAL_LIMIT,
                          processor.ZIP_MEMBER_COUNT, processor.ZIP_DEPTH_LIMIT),
                         (6 * 1024**2, 20 * 1024**2, 500, 16))
        self.assertEqual(processor.CLAMD_SETTINGS['StreamMaxLength'], '5M')
        self.assertEqual(processor.CLAMD_SETTINGS['MaxFileSize'], '6M')

    def test_normal_docx_metadata_stored_deflated_descriptor_and_zip64(self):
        rows = [('[Content_Types].xml', b'<Types/>'), ('word/document.xml', b'<document/>'),
                ('word/media/image.png', b'plain bounded synthetic image bytes')]
        for method in (ZIP_STORED, ZIP_DEFLATED):
            for streamed, zip64 in ((False, False), (True, False), (False, True), (True, True)):
                with self.subTest(method=method, streamed=streamed, zip64=zip64):
                    self.admit(archive(rows, method=method, streamed=streamed, zip64=zip64))
        self.admit(archive([]))

    def test_original_oversize_zip_vector_is_preserved(self):
        large = archive([('large.txt', b'x' * (6 * 1024**2 + 1))])
        self.assertLess(len(large), processor.MAX_FILE)
        self.refused(large, 'member_bytes')
        self.admit(archive([('boundary.txt', b'x' * (6 * 1024**2))]))

    def test_forged_low_size_and_prefix_crc_do_not_truncate_inflation(self):
        large = archive([('large.txt', b'x' * (6 * 1024**2 + 1))])
        self.refused(forged_metadata(large, size=1, crc=zlib.crc32(b'x')), 'member_bytes')
        smaller = archive([('small.txt', b'x' * 10000)])
        self.refused(forged_metadata(smaller, size=1, crc=zlib.crc32(b'x')), 'malformed')

    def test_crc_corruption_and_truncated_or_extra_deflate_are_refused(self):
        raw = archive([('small.txt', b'known synthetic bytes' * 100)])
        self.refused(forged_metadata(raw, crc=0), 'malformed')
        central = raw.index(b'PK\x01\x02')
        broken = bytearray(raw)
        broken[30 + len('small.txt')] ^= 0xff
        self.refused(bytes(broken), 'malformed')
        # Expand declared compressed span into descriptor-less hidden bytes;
        # its range is now incoherent before any document parser can run.
        broken = bytearray(raw)
        compressed = struct.unpack_from('<I', broken, 18)[0]
        struct.pack_into('<I', broken, 18, compressed - 1)
        struct.pack_into('<I', broken, central + 20, compressed - 1)
        self.refused(bytes(broken), 'malformed')

    def test_unsigned_stream_descriptor_is_supported_without_relaxing_ranges(self):
        raw = archive([('data', b'bounded bytes')], streamed=True)
        descriptor = raw.index(b'PK\x07\x08')
        central = raw.index(b'PK\x01\x02')
        unsigned = bytearray(raw[:descriptor] + raw[descriptor + 4:])
        ending = unsigned.rindex(b'PK\x05\x06')
        struct.pack_into('<I', unsigned, ending + 16, central - 4)
        self.admit(bytes(unsigned))

    def test_refused_post_ok_scans_still_consume_two_actual_attempts(self):
        # Native exchanges/definition metadata are explicitly synthetic here;
        # the actual ZIP gate runs. This proves control flow, never native AV.
        content = archive([('large.txt', b'x' * (6 * 1024**2 + 1))])
        synthetic_metadata = {'engine': 'ClamAV', 'version': 'synthetic', 'definitions': 'synthetic'}
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            path = directory / 'original.docx'
            path.write_bytes(content)
            path.chmod(0o600)
            owner = processor.OwnedClamd(directory, time.monotonic() + 10)
            owner.child, owner.signatures = object(), [1]
            replies = []

            def exchange(chunks, *args):
                if chunks == [b'zVERSION\0']:
                    return b'synthetic header\0'
                replies.append(chunks[0])
                return b'stream: OK\0'

            with patch.object(processor, '_clamd_definition', return_value=(synthetic_metadata, 'synthetic header')), \
                    patch.object(owner, '_exchange', side_effect=exchange):
                for _ in range(3):
                    with self.assertRaises(processor.InspectionUnavailable):
                        owner.scan(path, content)
            self.assertEqual(replies, [b'zINSTREAM\0', b'zINSTREAM\0'])
            self.assertEqual(owner.instream_attempts, 2)
            self.assertEqual([row['reply'] for row in owner.protocol_bindings], ['OK', 'OK'])
            self.assertEqual(owner.scan_bindings, [])
            self.assertEqual(owner.zip_admission_failure['reason'], 'member_bytes')
            self.assertEqual(owner.zip_admission_failure['size_bytes'], len(content))

    def test_clean_source_then_refused_candidate_preserves_exact_protocol_bindings(self):
        # Explicit synthetic exchange seam; real source/candidate bytes and ZIP gate.
        source = b'%PDF-1.7\nplain synthetic source\n%%EOF'
        candidate = archive([('large.txt', b'x' * (6 * 1024**2 + 1))])
        synthetic_metadata = {'engine': 'ClamAV', 'version': 'synthetic', 'definitions': 'synthetic'}
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            original, target = directory / 'original.docx', directory / 'candidate.docx'
            for path, content in ((original, source), (target, candidate)):
                path.write_bytes(content)
                path.chmod(0o600)
            owner = processor.OwnedClamd(directory, time.monotonic() + 10)
            owner.child, owner.signatures = object(), [1]
            attempts = []

            def exchange(chunks, *args):
                if chunks == [b'zVERSION\0']:
                    return b'synthetic header\0'
                attempts.append(chunks[0])
                return b'stream: OK\0'

            with patch.object(processor, '_clamd_definition', return_value=(synthetic_metadata, 'synthetic header')), \
                    patch.object(owner, '_exchange', side_effect=exchange):
                self.assertEqual(owner.scan(original, source), synthetic_metadata)
                with self.assertRaises(processor.InspectionUnavailable):
                    owner.scan(target, candidate)
                with self.assertRaises(processor.InspectionUnavailable):
                    owner.scan(original, source)
            self.assertEqual(attempts, [b'zINSTREAM\0', b'zINSTREAM\0'])
            self.assertEqual(owner.instream_attempts, 2)
            self.assertEqual([row['reply'] for row in owner.protocol_bindings], ['OK', 'OK'])
            self.assertEqual([row['result'] for row in owner.scan_bindings], ['CL_CLEAN'])
            for row, content in zip(owner.protocol_bindings, (source, candidate), strict=True):
                self.assertEqual(row['sha256'], hashlib.sha256(content).hexdigest())
                self.assertEqual(row['size_bytes'], len(content))
                self.assertEqual(row['metadata'], synthetic_metadata)
            self.assertEqual(owner.zip_admission_failure, {
                'sha256': hashlib.sha256(candidate).hexdigest(), 'size_bytes': len(candidate),
                'metadata': synthetic_metadata, 'reason': 'member_bytes'})

    def test_found_and_other_reply_never_enter_zip_admission_or_forge_clean(self):
        content = b'public synthetic scanner input'
        synthetic_metadata = {'engine': 'ClamAV', 'version': 'synthetic', 'definitions': 'synthetic'}
        for reply, expected_reply, error, results in (
                (b'stream: Fixture.Signature FOUND\0', 'FOUND', processor.InspectionDenied, ['CL_VIRUS']),
                (b'stream: native ERROR\0', 'OTHER', processor.InspectionUnavailable, [])):
            with self.subTest(reply=expected_reply), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                path = directory / 'original.docx'
                path.write_bytes(content)
                path.chmod(0o600)
                owner = processor.OwnedClamd(directory, time.monotonic() + 10)
                owner.child, owner.signatures = object(), [1]

                def exchange(chunks, *args, native_reply=reply):
                    return b'synthetic header\0' if chunks == [b'zVERSION\0'] else native_reply

                with patch.object(processor, '_clamd_definition', return_value=(synthetic_metadata, 'synthetic header')), \
                        patch.object(owner, '_exchange', side_effect=exchange), \
                        patch.object(processor, '_zip_admit') as gate:
                    with self.assertRaises(error):
                        owner.scan(path, content)
                gate.assert_not_called()
                self.assertEqual(owner.instream_attempts, 1)
                self.assertEqual([row['reply'] for row in owner.protocol_bindings], [expected_reply])
                self.assertEqual([row['result'] for row in owner.scan_bindings], results)
                self.assertIsNone(owner.zip_admission_failure)

    def test_actual_deflate_eof_trailing_bytes_and_no_progress_are_required(self):
        from types import SimpleNamespace
        content = b'actual bytes' * 1000
        compressor = zlib.compressobj(wbits=-15)
        compressed = compressor.compress(content) + compressor.flush()
        entry = SimpleNamespace(compress_type=ZIP_DEFLATED, file_size=len(content), CRC=zlib.crc32(content))
        self.assertEqual(processor._zip_inflate(compressed, 0, len(compressed), entry,
                                              {'bytes': 0}, time.monotonic() + 1), content)
        for invalid in (compressed[:-1], compressed + b'hidden trailing data'):
            with self.subTest(size=len(invalid)), self.assertRaises(processor.ZipAdmissionUnavailable):
                processor._zip_inflate(invalid, 0, len(invalid), entry, {'bytes': 0}, time.monotonic() + 1)
        stalled = SimpleNamespace(unconsumed_tail=compressed, unused_data=b'', eof=False,
                                  decompress=lambda *args: b'')
        with patch.object(processor.zlib, 'decompressobj', return_value=stalled):
            with self.assertRaises(processor.ZipAdmissionUnavailable):
                processor._zip_inflate(compressed, 0, len(compressed), entry, {'bytes': 0}, time.monotonic() + 1)

    def test_bounded_zip64_end_records_and_cross_record_agreement(self):
        raw = archive([('data', b'bounded bytes')])
        ending = raw.rindex(b'PK\x05\x06')
        fields = struct.unpack_from('<4s4H2IH', raw, ending)
        extended = struct.pack('<4sQ2H2I4Q', b'PK\x06\x06', 44, 45, 45, 0, 0,
                               fields[3], fields[4], fields[5], fields[6])
        locator = struct.pack('<4sIQI', b'PK\x06\x07', 0, ending, 1)
        tail = struct.pack('<4s4H2IH', b'PK\x05\x06', 0, 0, 65535, 65535,
                           0xffffffff, 0xffffffff, 0)
        widened = raw[:ending] + extended + locator + tail
        self.admit(widened)
        broken = bytearray(widened)
        struct.pack_into('<Q', broken, ending + 32, 501)
        self.refused(bytes(broken), 'unsupported')

    def test_global_expansion_and_member_count_include_nested_archives(self):
        self.refused(archive([(str(i) + '.txt', b'x' * (5 * 1024**2)) for i in range(5)]), 'total_bytes')
        self.refused(archive([(str(i) + '.txt', b'x') for i in range(501)]), 'members')
        child = archive([(str(i) + '.txt', b'x') for i in range(300)])
        self.refused(archive([('one.zip', child), ('two.zip', child)]), 'members')

    def test_recursion_depth_and_renamed_nested_zip_are_byte_driven(self):
        nested = b'public synthetic bytes'
        for _ in range(16):
            nested = archive([('renamed.bin', nested)])
        self.admit(nested)
        nested = archive([('renamed.bin', nested)])
        self.refused(nested, 'depth')
        nested = archive([('renamed.bin', nested)])
        self.refused(nested, 'depth')

    def test_encrypted_unknown_method_and_local_central_disagreement(self):
        raw = archive([('data.txt', b'bounded bytes')])
        central = raw.index(b'PK\x01\x02')
        encrypted = bytearray(raw)
        for offset in (6, central + 8):
            flags = struct.unpack_from('<H', encrypted, offset)[0]
            struct.pack_into('<H', encrypted, offset, flags | 1)
        self.refused(bytes(encrypted), 'encrypted')
        unknown = bytearray(raw)
        for offset in (8, central + 10):
            struct.pack_into('<H', unknown, offset, 99)
        self.refused(bytes(unknown), 'unsupported')
        mismatch = bytearray(raw)
        struct.pack_into('<H', mismatch, 8, 0)
        self.refused(bytes(mismatch), 'malformed')

    def test_unreferenced_prefix_overlap_count_lie_and_descriptor_mutation(self):
        raw = archive([('one', b'one'), ('two', b'two')])
        self.refused(b'unreferenced prefix' + raw, 'malformed')
        central = raw.index(b'PK\x01\x02')
        second = raw.index(b'PK\x01\x02', central + 4)
        changed = bytearray(raw)
        struct.pack_into('<I', changed, second + 42, 0)
        self.refused(bytes(changed), 'malformed')
        changed = bytearray(raw)
        ending = changed.rindex(b'PK\x05\x06')
        struct.pack_into('<HH', changed, ending + 8, 1, 1)
        self.refused(bytes(changed), 'malformed')
        changed = bytearray(archive([('data', b'bounded')], streamed=True))
        descriptor = changed.index(b'PK\x07\x08')
        changed[descriptor + 4] ^= 1
        self.refused(bytes(changed), 'malformed')

    def test_nonzip_pdf_and_text_bypass_without_claiming_format_support(self):
        for value in (b'%PDF-1.7\nplain synthetic bytes\n%%EOF', b'\\documentclass{article}\n', b'plain bytes'):
            self.admit(value)

    def test_deadline_checks_cover_inflation_and_metadata(self):
        raw = archive([('data', b'x' * 100000)])
        with self.assertRaises(processor.ZipAdmissionUnavailable) as caught:
            processor._zip_admit(raw, time.monotonic() - 1)
        self.assertEqual(caught.exception.reason, 'deadline')
        with patch.object(processor.time, 'monotonic', side_effect=[0, 2]):
            with self.assertRaises(processor.ZipAdmissionUnavailable) as caught:
                processor._zip_admit(b'plain nonzip bytes', 1)
        self.assertEqual(caught.exception.reason, 'deadline')
        actual = processor.zlib.decompressobj

        def late(*args):
            result = actual(*args)
            time.sleep(0.002)
            return result

        with patch.object(processor.zlib, 'decompressobj', side_effect=late):
            with self.assertRaises(processor.ZipAdmissionUnavailable) as caught:
                processor._zip_admit(raw, time.monotonic() + 0.001)
        self.assertEqual(caught.exception.reason, 'deadline')

    def test_no_unbounded_flush_and_scan_attempt_gate_precedes_exchange(self):
        tree = ast.parse(PROCESSOR.read_bytes())
        self.assertFalse(any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                             and n.func.attr == 'flush' for n in ast.walk(next(
                                 n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_zip_inflate'))))
        owner = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'OwnedClamd')
        scan = next(n for n in owner.body if isinstance(n, ast.FunctionDef) and n.name == 'scan')
        text = ast.get_source_segment(PROCESSOR.read_text(), scan)
        self.assertLess(text.index('self.instream_attempts += 1'), text.index('result = self._exchange(chunks'))
        self.assertIn('self.instream_attempts >= 2', text)
        self.assertLess(text.index('self.protocol_bindings.append'), text.index('_zip_admit(content'))
        self.assertLess(text.index('_zip_admit(content'), text.index('"result": "CL_CLEAN"'))


if __name__ == '__main__':
    unittest.main()
