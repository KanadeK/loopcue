from __future__ import annotations

import contextlib
import io
import json
import os
import random
import struct
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from loopcue import core
from loopcue.cli import main
from loopcue.core import WaveError, analyze


def chunk(name, body):
    return name + struct.pack("<I", len(body)) + body + (b"\0" if len(body) & 1 else b"")


def riff(*chunks):
    body = b"WAVE" + b"".join(chunks)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def fmt(tag=1, bits=16, channels=1, rate=8000, *, subtype=1, valid_bits=None):
    align = channels * (bits // 8)
    data = struct.pack("<HHIIHH", tag, channels, rate, rate * align, align, bits)
    if tag == 65534:
        guid = struct.pack("<I", subtype) + bytes.fromhex("00001000800000aa00389b71")
        data += struct.pack("<HHI", 22, bits if valid_bits is None else valid_bits, 0) + guid
    elif tag != 1:
        data += struct.pack("<H", 0)
    return chunk(b"fmt ", data)


def smpl(*records, extra=b""):
    header = struct.pack("<9I", 0, 0, 125000, 60, 0, 0, 0, len(records), len(extra))
    return chunk(
        b"smpl", header + b"".join(struct.pack("<6I", *record) for record in records) + extra
    )


def cue(*records):
    return chunk(
        b"cue ",
        struct.pack("<I", len(records))
        + b"".join(struct.pack("<II4sIII", *record) for record in records),
    )


def point(identifier=1, offset=0, target=b"data", chunk_start=0, block_start=0):
    return identifier, offset, target, chunk_start, block_start, offset


def adtl(*records):
    return chunk(b"LIST", b"adtl" + b"".join(records))


def wav(*metadata, frames=16):
    return riff(fmt(), chunk(b"data", b"\0\0" * frames), *metadata)


class FileCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "input.wav"

    def inspect(self, data, **kwargs):
        self.path.write_bytes(data)
        report = analyze(self.path, **kwargs)
        self.assertEqual(self.path.read_bytes(), data, "Analyzer must not alter the input")
        return report

    def codes(self, data, **kwargs):
        return [item["code"] for item in self.inspect(data, **kwargs)["findings"]]


class MarkerTests(FileCase):
    def test_inclusive_last_frame_and_one_frame_loops(self):
        report = self.inspect(
            wav(smpl((1, 0, 0, 15, 0, 0), (2, 0, 15, 15, 0, 1))), require_loop=True
        )
        self.assertTrue(report["ok"])
        self.assertEqual(report["loops"][1]["end_inclusive"], 15)
        self.assertEqual(report["frame_count"], 16)

    def test_one_past_end_is_invalid(self):
        self.assertIn("loop_out_of_bounds", self.codes(wav(smpl((1, 0, 0, 16, 0, 0)))))

    def test_inverted_and_out_of_bounds_start(self):
        codes = self.codes(wav(smpl((1, 0, 100, 3, 0, 0))))
        self.assertIn("loop_inverted", codes)
        self.assertIn("loop_out_of_bounds", codes)

    def test_empty_audio_has_no_valid_loop(self):
        self.assertIn("loop_out_of_bounds", self.codes(wav(smpl((1, 0, 0, 0, 0, 0)), frames=0)))

    def test_standard_and_custom_loop_types(self):
        for kind in (0, 1, 2):
            self.assertTrue(self.inspect(wav(smpl((1, kind, 1, 8, 2**31, 0))))["ok"])
        self.assertIn("loop_type_unsupported", self.codes(wav(smpl((1, 32, 1, 8, 0, 0)))))

    def test_duplicate_loop_identifiers(self):
        self.assertIn(
            "duplicate_loop_id", self.codes(wav(smpl((1, 0, 0, 3, 0, 0), (1, 0, 4, 6, 0, 0))))
        )

    def test_sampler_specific_data_is_skipped(self):
        report = self.inspect(wav(smpl((8, 0, 1, 3, 0, 7), extra=b"private sampler extension")))
        self.assertTrue(report["ok"])
        self.assertNotIn("private", json.dumps(report))

    def test_cue_at_zero_last_frame_and_eof_boundary(self):
        report = self.inspect(wav(cue(point(1, 0), point(2, 15), point(3, 16))), require_cue=True)
        self.assertTrue(report["ok"])
        self.assertEqual([item["sample_offset"] for item in report["cues"]], [0, 15, 16])

    def test_out_of_bounds_cue_and_duplicate_identifiers(self):
        codes = self.codes(wav(cue(point(1, 17), point(1, 2))))
        self.assertIn("cue_out_of_bounds", codes)
        self.assertIn("duplicate_cue_id", codes)

    def test_unsupported_cue_addressing_cannot_pass(self):
        for record in (point(target=b"slnt"), point(chunk_start=1), point(block_start=2)):
            self.assertIn("cue_addressing_unsupported", self.codes(wav(cue(record))))

    def test_forward_label_references_and_text_never_emitted(self):
        secret = b"SYNTHETIC_LABEL_CANARY_991\x1b[31m\0"
        labels = adtl(
            chunk(b"labl", struct.pack("<I", 7) + secret),
            chunk(b"note", struct.pack("<I", 7) + b"note\0"),
        )
        report = self.inspect(wav(labels, cue(point(7, 4))))
        self.assertTrue(report["ok"])
        self.assertNotIn("CANARY", json.dumps(report))
        self.assertEqual(len(report["references"]), 2)

    def test_orphan_and_unterminated_label(self):
        labels = adtl(chunk(b"labl", struct.pack("<I", 9) + b"missing terminator"))
        codes = self.codes(wav(labels))
        self.assertIn("orphan_cue_reference", codes)
        self.assertIn("label_unterminated", codes)

    def test_associated_text_region_bounds(self):
        def region(length):
            return adtl(chunk(b"ltxt", struct.pack("<II4sHHHH", 1, length, b"rgn ", 0, 0, 0, 0)))

        self.assertTrue(self.inspect(wav(cue(point(1, 4)), region(12)))["ok"])
        self.assertIn("region_out_of_bounds", self.codes(wav(cue(point(1, 4)), region(13))))

    def test_region_with_unsupported_cue_addressing_remains_a_finding(self):
        labels = adtl(chunk(b"ltxt", struct.pack("<II4sHHHH", 1, 4, b"rgn ", 0, 0, 0, 0)))
        self.assertIn(
            "cue_addressing_unsupported", self.codes(wav(cue(point(1, 2, block_start=2)), labels))
        )

    def test_required_markers_detect_stripped_export(self):
        self.assertTrue(self.inspect(wav())["ok"])
        self.assertEqual(
            self.codes(wav(smpl(), cue()), require_loop=True, require_cue=True),
            ["required_loop_missing", "required_cue_missing"],
        )


class ContainerTests(FileCase):
    def test_final_odd_data_compatibility_does_not_relax_middle_chunk_padding(self):
        odd_data = b"data" + struct.pack("<I", 1) + b"\x80"
        report = self.inspect(riff(fmt(bits=8), odd_data))
        self.assertEqual(report["frame_count"], 1)
        with self.assertRaises(WaveError):
            self.inspect(riff(fmt(bits=8), odd_data, smpl()))

    def test_stdlib_wave_writer_is_an_independent_pcm_frame_oracle(self):
        for channels in (1, 2):
            for width in (1, 2, 3, 4):
                buffer = io.BytesIO()
                with wave.open(buffer, "wb") as writer:
                    writer.setnchannels(channels)
                    writer.setsampwidth(width)
                    writer.setframerate(44100)
                    writer.writeframes(b"\0" * (23 * channels * width))
                data = buffer.getvalue()
                with wave.open(io.BytesIO(data), "rb") as oracle:
                    report = self.inspect(data)
                    self.assertEqual(report["frame_count"], oracle.getnframes())
                    self.assertEqual(
                        report["format"]["block_align"],
                        oracle.getnchannels() * oracle.getsampwidth(),
                    )

    def test_float_and_extensible_supported_formats(self):
        for bits in (32, 64):
            report = self.inspect(
                riff(
                    fmt(tag=3, bits=bits),
                    chunk(b"fact", struct.pack("<I", 4)),
                    chunk(b"data", b"\0" * (bits // 8) * 4),
                )
            )
            self.assertEqual(report["format"]["codec"], "ieee_float")
            self.assertEqual(report["frame_count"], 4)
        report = self.inspect(
            riff(fmt(tag=65534, bits=32, valid_bits=24), chunk(b"data", b"\0" * 16))
        )
        self.assertEqual(report["format"]["valid_bits"], 24)
        report = self.inspect(riff(fmt(tag=65534, subtype=3, bits=64), chunk(b"data", b"\0" * 16)))
        self.assertEqual(report["frame_count"], 2)

    def test_unknown_chunks_and_non_adtl_lists_are_skipped(self):
        report = self.inspect(
            wav(
                chunk(b"JUNK", b"odd"),
                chunk(b"LIST", b"INFO" + b"arbitrary payload"),
                adtl(chunk(b"UNKN", b"anything")),
            )
        )
        self.assertTrue(report["ok"])
        self.assertEqual(report["chunk_count"], 6)

    def test_fmt_data_order_and_required_chunks(self):
        for data in (
            riff(),
            riff(fmt()),
            riff(chunk(b"data", b"\0\0")),
            riff(chunk(b"data", b"\0\0"), fmt()),
        ):
            with self.assertRaises(WaveError):
                self.inspect(data)

    def test_duplicate_interpreted_chunks_are_ambiguous(self):
        for extra in (fmt(), chunk(b"data", b""), smpl(), cue()):
            initial = [smpl(), cue()] if extra[:4] in (b"smpl", b"cue ") else []
            with self.subTest(kind=extra[:4]), self.assertRaises(WaveError):
                self.inspect(wav(*initial, extra))

    def test_non_linear_or_other_riff_types_are_unsupported(self):
        for data in (
            b"RF64" + wav()[4:],
            b"RIFX" + wav()[4:],
            wav()[:8] + b"AVI " + wav()[12:],
            wav(chunk(b"LIST", b"wavl")),
            wav(chunk(b"plst", b"")),
            riff(fmt(tag=2), chunk(b"data", b"\0" * 16)),
        ):
            with self.assertRaises(WaveError):
                self.inspect(data)

    def test_fmt_short_extension_mismatch_and_bit_depth(self):
        good = fmt()[8:24]
        broken = [b"", good[:15], good + b"\0", good + struct.pack("<H", 2), b"x" * 4097]
        for tag, bits in ((1, 12), (3, 16), (65534, 32)):
            if tag == 65534:
                broken.append(fmt(tag=tag, bits=bits)[8:30])
            else:
                broken.append(fmt(tag=tag, bits=bits)[8:])
        for payload in broken:
            with self.subTest(length=len(payload)), self.assertRaises(WaveError):
                self.inspect(riff(chunk(b"fmt ", payload), chunk(b"data", b"\0" * 16)))

    def test_fmt_positive_rates_alignment_and_extensible_guids(self):
        payload = fmt()[8:24]
        malformed = [
            struct.pack("<HHIIHH", 1, 0, 8000, 16000, 2, 16),
            struct.pack("<HHIIHH", 1, 1, 0, 0, 2, 16),
            struct.pack("<HHIIHH", 1, 1, 8000, 16000, 4, 16),
            struct.pack("<HHIIHH", 1, 1, 8000, 123, 2, 16),
        ]
        self.assertEqual(len(payload), 16)
        extension = bytearray(fmt(tag=65534, bits=32)[8:])
        extension[-1] ^= 1
        malformed.append(bytes(extension))
        malformed.extend(
            [
                fmt(tag=65534, bits=32, valid_bits=0)[8:],
                fmt(tag=65534, bits=32, valid_bits=33)[8:],
                fmt(tag=65534, subtype=3, bits=32, valid_bits=24)[8:],
            ]
        )
        for body in malformed:
            with self.assertRaises(WaveError):
                self.inspect(riff(chunk(b"fmt ", body), chunk(b"data", b"\0" * 16)))

    def test_pcm_fmt_zero_length_extension(self):
        self.assertTrue(
            self.inspect(riff(chunk(b"fmt ", fmt()[8:24] + b"\0\0"), chunk(b"data", b"")))["ok"]
        )

    def test_non_frame_aligned_data(self):
        with self.assertRaises(WaveError):
            self.inspect(riff(fmt(channels=2), chunk(b"data", b"\0" * 3)))

    def test_every_truncated_prefix_is_rejected(self):
        data = wav(smpl((1, 0, 0, 15, 0, 0)), cue(point(1, 2)))
        for length in range(len(data)):
            with self.subTest(length=length), self.assertRaises(WaveError):
                self.inspect(data[:length])

    def test_trailing_bytes_and_incomplete_chunk_header(self):
        for data in (wav() + b"extra", riff(fmt(), chunk(b"data", b""), b"JUNK")):
            with self.assertRaises(WaveError):
                self.inspect(data)

    def test_missing_odd_chunk_padding_is_not_silently_accepted(self):
        unpadded = b"JUNK" + struct.pack("<I", 1) + b"x"
        with self.assertRaises(WaveError):
            self.inspect(riff(fmt(), chunk(b"data", b""), unpadded))

    def test_data_pad_exception_does_not_apply_inside_associated_metadata(self):
        nested = b"data" + struct.pack("<I", 1) + b"x"
        with self.assertRaises(WaveError):
            self.inspect(wav(adtl(nested)))

    def test_declared_chunk_length_cannot_escape_parent(self):
        for bad in (
            b"JUNK" + struct.pack("<I", 0xFFFFFFFF),
            adtl(b"labl" + struct.pack("<I", 100) + b"short"),
        ):
            with self.assertRaises(WaveError):
                self.inspect(wav(bad))

    def test_marker_count_and_extension_sizes_are_checked(self):
        malformed = [
            chunk(b"smpl", b"\0" * 35),
            chunk(b"cue ", b"\0" * 3),
            chunk(b"smpl", struct.pack("<9I", 0, 0, 0, 0, 0, 0, 0, 1, 0)),
            chunk(b"smpl", struct.pack("<9I", 0, 0, 0, 0, 0, 0, 0, 0, 1)),
            chunk(b"cue ", struct.pack("<I", 1)),
            chunk(b"cue ", struct.pack("<I", core.MAX_MARKERS + 1)),
        ]
        for entry in malformed:
            with self.assertRaises(WaveError):
                self.inspect(wav(entry))

    def test_associated_data_malformed_headers(self):
        for bad in (
            chunk(b"LIST", b""),
            adtl(chunk(b"labl", b"\0" * 4)),
            adtl(chunk(b"note", b"\0" * 3)),
            adtl(chunk(b"ltxt", b"\0" * 19)),
            adtl(b"x"),
        ):
            with self.assertRaises(WaveError):
                self.inspect(wav(bad))

    def test_chunk_count_limit_includes_nested_chunks(self):
        with self.assertRaises(WaveError):
            self.inspect(wav(adtl(*(chunk(b"JUNK", b"") for _ in range(core.MAX_CHUNKS)))))

    def test_size_limit_and_missing_or_directory_input(self):
        self.path.write_bytes(wav())
        with patch.object(core, "MAX_BYTES", 12), self.assertRaises(WaveError):
            analyze(self.path)
        with self.assertRaises(WaveError):
            analyze(self.path.parent / "absent.wav")
        with self.assertRaises(WaveError):
            analyze(self.path.parent)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "POSIX FIFO safety check")
    def test_fifo_is_rejected_without_waiting_for_a_writer(self):
        os.mkfifo(self.path)
        with self.assertRaises(WaveError):
            analyze(self.path)

    def test_concurrent_change_does_not_receive_clean_report(self):
        self.path.write_bytes(wav())
        before = self.path.stat()
        after = SimpleNamespace(st_size=before.st_size, st_mtime_ns=before.st_mtime_ns + 1)
        with (
            patch.object(core.os, "fstat", side_effect=[before, after]),
            self.assertRaises(WaveError),
        ):
            analyze(self.path)

    def test_seek_over_large_audio_payload(self):
        audio_size = 32 * 1024**2
        metadata = smpl((1, 0, 0, audio_size // 2 - 1, 0, 0))
        prefix = b"WAVE" + fmt() + b"data" + struct.pack("<I", audio_size)
        with self.path.open("wb") as stream:
            stream.write(b"RIFF" + struct.pack("<I", len(prefix) + audio_size + len(metadata)))
            stream.write(prefix)
            stream.seek(audio_size, 1)
            stream.write(metadata)
        sizes = []
        original_read = core._Reader.read

        def observed_read(reader, offset, count):
            sizes.append(count)
            return original_read(reader, offset, count)

        with patch.object(core._Reader, "read", observed_read):
            report = analyze(self.path, require_loop=True)
        self.assertTrue(report["ok"])
        self.assertEqual(report["frame_count"], audio_size // 2)
        self.assertLess(sum(sizes), 512, "Audio data should be skipped, not loaded")

    def test_deterministic_mutation_campaign_has_no_unhandled_parser_errors(self):
        rng = random.Random(20261024)  # noqa: S311 - reproducible mutation input
        original = wav(
            smpl((1, 0, 0, 15, 0, 0)),
            cue(point()),
            adtl(chunk(b"labl", struct.pack("<I", 1) + b"label\0")),
        )
        for _ in range(300):
            data = bytearray(original)
            for _flip in range(rng.randrange(1, 4)):
                index = rng.randrange(len(data))
                data[index] ^= 1 << rng.randrange(8)
            self.path.write_bytes(data)
            try:
                report = analyze(self.path)
            except WaveError:
                continue
            self.assertEqual(report["ok"], not report["findings"])
            self.assertLessEqual(report["chunk_count"], core.MAX_CHUNKS)

    def test_reader_bounds_and_short_reads(self):
        reader = core._Reader(io.BytesIO(b"RIFF"), 12)
        with self.assertRaises(WaveError):
            reader.read(0, 12)
        with self.assertRaises(WaveError):
            reader.read(11, 2)


class CliTests(FileCase):
    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "loopcue", *map(str, args)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=20,
        )

    def test_version_help_and_argument_errors(self):
        self.assertEqual(self.run_cli("--version").stdout.strip(), "0.1.0")
        self.assertEqual(self.run_cli("--help").returncode, 0)
        self.assertEqual(self.run_cli().returncode, 2)

    def test_actual_cli_clean_finding_and_unsupported_workflows(self):
        self.path.write_bytes(wav(smpl((1, 0, 0, 15, 0, 0))))
        result = self.run_cli(self.path, "--require-loop")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["ok"])
        self.path.write_bytes(wav(smpl((1, 0, 0, 16, 0, 0))))
        result = self.run_cli(self.path, "--format", "text")
        self.assertEqual(result.returncode, 1)
        self.assertIn("loop_out_of_bounds", result.stdout)
        self.path.write_bytes(b"unsupported")
        result = self.run_cli(self.path)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("loopcue:", result.stderr)

    def test_direct_cli_output_and_pipe_contracts(self):
        self.path.write_bytes(wav())
        for format_name in ("json", "text"):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = main([str(self.path), "--require-loop", "--format", format_name])
            self.assertEqual(status, 1)
            self.assertIn("required_loop_missing", output.getvalue())
        with patch("builtins.print", side_effect=BrokenPipeError):
            self.assertEqual(main([str(self.path)]), 2)
        self.path.write_bytes(b"bad")
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            self.assertEqual(main([str(self.path)]), 2)
        self.assertIn("header", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
