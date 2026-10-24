"""Bounded, seek-based RIFF-WAVE loop/cue inspection without decoding audio."""

from __future__ import annotations

import os
import stat
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

MAX_BYTES = 2 * 1024**3
MAX_CHUNKS = 10000
MAX_MARKERS = 100000
_GUID_TAIL = bytes.fromhex("00001000800000aa00389b71")


class WaveError(ValueError):
    """Unsupported or structurally unsafe input; never a clean audit result."""


@dataclass(frozen=True)
class _Chunk:
    name: bytes
    offset: int
    size: int

    @property
    def payload(self) -> int:
        return self.offset + 8


class _Reader:
    def __init__(self, stream: BinaryIO, size: int):
        self.stream = stream
        self.size = size
        self.chunk_count = 0
        self.marker_count = 0

    def read(self, offset: int, size: int) -> bytes:
        if offset < 0 or size < 0 or offset + size > self.size:
            raise WaveError(f"Read outside container at byte {offset}")
        self.stream.seek(offset)
        data = self.stream.read(size)
        if len(data) != size:
            raise WaveError(f"Truncated input at byte {offset}")
        return data

    def chunks(self, start: int, end: int):
        cursor = start
        while cursor < end:
            self.chunk_count += 1
            if self.chunk_count > MAX_CHUNKS:
                raise WaveError(f"Chunk count exceeds {MAX_CHUNKS}")
            if cursor + 8 > end:
                raise WaveError(f"Incomplete chunk header at byte {cursor}")
            name, size = struct.unpack("<4sI", self.read(cursor, 8))
            next_cursor = cursor + 8 + size + (size & 1)
            # Python's wave writer (and common exporters) omit the last data pad.
            # Only tolerate that unambiguous EOF case; all other padding is required.
            if (
                start == 12
                and end == self.size
                and name == b"data"
                and size & 1
                and cursor + 8 + size == end
            ):
                next_cursor = end
            if next_cursor > end:
                raise WaveError(f"Chunk or padding exceeds its parent at byte {cursor}")
            yield _Chunk(name, cursor, size)
            cursor = next_cursor

    def markers(self, count: int) -> None:
        self.marker_count += count
        if self.marker_count > MAX_MARKERS:
            raise WaveError(f"Total loop/cue/reference count exceeds {MAX_MARKERS}")


def _format(reader: _Reader, chunk: _Chunk) -> dict[str, Any]:
    if chunk.size < 16 or chunk.size > 4096 or chunk.size == 17:
        raise WaveError("fmt must contain 16..4096 bytes and a complete extension header")
    data = reader.read(chunk.payload, chunk.size)
    tag, channels, rate, byte_rate, align, bits = struct.unpack_from("<HHIIHH", data)
    valid_bits = bits
    if chunk.size >= 18:
        extension = struct.unpack_from("<H", data, 16)[0]
        if extension + 18 != chunk.size:
            raise WaveError("fmt extension length does not match chunk length")
    if tag == 65534:
        if chunk.size < 40:
            raise WaveError("WAVE_FORMAT_EXTENSIBLE requires at least 22 extension bytes")
        valid_bits = struct.unpack_from("<H", data, 18)[0]
        guid = data[24:40]
        if guid[4:] != _GUID_TAIL:
            raise WaveError("Unsupported extensible WAV subtype GUID")
        tag = struct.unpack_from("<I", guid)[0]
    if tag not in (1, 3):
        raise WaveError("Only linear PCM and IEEE-float data are supported")
    if not channels or not rate:
        raise WaveError("Channel count and sample rate must be positive")
    if bits not in ((8, 16, 24, 32) if tag == 1 else (32, 64)):
        raise WaveError("Unsupported container bit depth")
    if not 1 <= valid_bits <= bits or (tag == 3 and valid_bits != bits):
        raise WaveError("Invalid valid-bits value for the sample container")
    if align != channels * (bits // 8) or byte_rate != rate * align:
        raise WaveError("Inconsistent block alignment or byte rate")
    return {
        "codec": "pcm" if tag == 1 else "ieee_float",
        "channels": channels,
        "sample_rate": rate,
        "container_bits": bits,
        "valid_bits": valid_bits,
        "block_align": align,
    }


def _loops(reader: _Reader, chunk: _Chunk) -> list[dict[str, int]]:
    if chunk.size < 36:
        raise WaveError("smpl header is shorter than 36 bytes")
    header = struct.unpack("<9I", reader.read(chunk.payload, 36))
    count, extra_bytes = header[7:9]
    reader.markers(count)
    if 36 + count * 24 + extra_bytes != chunk.size:
        raise WaveError("smpl loop count / sampler-specific length does not match chunk length")
    result = []
    for index in range(count):
        offset = chunk.payload + 36 + index * 24
        identifier, kind, start, end, fraction, plays = struct.unpack(
            "<6I", reader.read(offset, 24)
        )
        result.append(
            {
                "id": identifier,
                "type": kind,
                "start": start,
                "end_inclusive": end,
                "fraction": fraction,
                "play_count": plays,
                "byte_offset": offset,
            }
        )
    return result


def _cues(reader: _Reader, chunk: _Chunk) -> list[dict[str, Any]]:
    if chunk.size < 4:
        raise WaveError("cue header is shorter than 4 bytes")
    count = struct.unpack("<I", reader.read(chunk.payload, 4))[0]
    reader.markers(count)
    if 4 + count * 24 != chunk.size:
        raise WaveError("cue record count does not match chunk length")
    result = []
    for index in range(count):
        offset = chunk.payload + 4 + index * 24
        identifier, position, target, chunk_start, block_start, sample = struct.unpack(
            "<II4sIII", reader.read(offset, 24)
        )
        result.append(
            {
                "id": identifier,
                "sample_offset": sample,
                "play_order_position": position,
                "target": target.hex(),
                "chunk_start": chunk_start,
                "block_start": block_start,
                "byte_offset": offset,
            }
        )
    return result


def _references(reader: _Reader, chunk: _Chunk) -> list[dict[str, Any]]:
    if chunk.size < 4:
        raise WaveError("LIST type is missing")
    kind = reader.read(chunk.payload, 4)
    if kind == b"wavl":
        raise WaveError("LIST wavl segmented audio is outside the supported single-data contract")
    if kind != b"adtl":
        return []
    result = []
    for child in reader.chunks(chunk.payload + 4, chunk.payload + chunk.size):
        if child.name not in (b"labl", b"note", b"ltxt"):
            continue
        if child.size < (20 if child.name == b"ltxt" else 5):
            raise WaveError(f"Short associated-data record at byte {child.offset}")
        reader.markers(1)
        identifier = struct.unpack("<I", reader.read(child.payload, 4))[0]
        record: dict[str, Any] = {
            "cue_id": identifier,
            "kind": child.name.decode("ascii"),
            "byte_offset": child.offset,
        }
        if child.name == b"ltxt":
            record["sample_length"] = struct.unpack("<I", reader.read(child.payload + 4, 4))[0]
        else:
            # Never decode or emit label/note text. Check only the final terminator byte.
            record["terminated"] = reader.read(child.payload + child.size - 1, 1) == b"\0"
        result.append(record)
    return result


def _scan(reader: _Reader, *, require_loop: bool, require_cue: bool) -> dict[str, Any]:
    if reader.size < 12:
        raise WaveError("RIFF-WAVE header is shorter than 12 bytes")
    magic, declared, form = struct.unpack("<4sI4s", reader.read(0, 12))
    if magic != b"RIFF" or form != b"WAVE":
        raise WaveError("Only little-endian RIFF-WAVE is supported (no RIFX/RF64)")
    if declared + 8 != reader.size:
        raise WaveError(
            "RIFF declared size differs from file size; trailing/truncated data is unsupported"
        )
    seen = set()
    audio_format = None
    data_bytes = None
    loops: list[dict[str, int]] = []
    cues: list[dict[str, Any]] = []
    references: list[dict[str, Any]] = []
    for chunk in reader.chunks(12, reader.size):
        if chunk.name in (b"fmt ", b"data", b"smpl", b"cue "):
            if chunk.name in seen:
                raise WaveError(f"Duplicate format/data/marker chunk at byte {chunk.offset}")
            seen.add(chunk.name)
        if chunk.name == b"fmt ":
            audio_format = _format(reader, chunk)
        elif chunk.name == b"data":
            if audio_format is None:
                raise WaveError("fmt must precede data")
            data_bytes = chunk.size
            # Audio payload is skipped by the chunk walker, never read or decoded.
        elif chunk.name == b"smpl":
            loops = _loops(reader, chunk)
        elif chunk.name == b"cue ":
            cues = _cues(reader, chunk)
        elif chunk.name == b"LIST":
            references.extend(_references(reader, chunk))
        elif chunk.name == b"plst":
            raise WaveError("Playlist playback order is outside the supported linear-data contract")
    if audio_format is None or data_bytes is None:
        raise WaveError("Exactly one fmt and one data chunk are required")
    if data_bytes % audio_format["block_align"]:
        raise WaveError("data length is not a whole number of sample frames")
    frames = data_bytes // audio_format["block_align"]
    findings: list[dict[str, Any]] = []

    def issue(code: str, message: str, offset: int, identifier: int | None = None):
        finding: dict[str, Any] = {"code": code, "message": message, "byte_offset": offset}
        if identifier is not None:
            finding["marker_id"] = identifier
        findings.append(finding)

    loop_ids = set()
    for loop in loops:
        identifier, offset = loop["id"], loop["byte_offset"]
        if identifier in loop_ids:
            issue(
                "duplicate_loop_id", "Sampler loop identifiers must be unique", offset, identifier
            )
        loop_ids.add(identifier)
        if loop["start"] > loop["end_inclusive"]:
            issue("loop_inverted", "Loop start exceeds inclusive end", offset, identifier)
        if loop["end_inclusive"] >= frames or loop["start"] >= frames:
            issue(
                "loop_out_of_bounds",
                "Loop must reference existing sample frames; end is inclusive",
                offset,
                identifier,
            )
        if loop["type"] not in (0, 1, 2):
            issue(
                "loop_type_unsupported",
                "Custom/reserved loop type cannot receive a clean result",
                offset,
                identifier,
            )
    cue_by_id = {}
    for cue in cues:
        identifier, offset = cue["id"], cue["byte_offset"]
        if identifier in cue_by_id:
            issue("duplicate_cue_id", "Cue identifiers must be unique", offset, identifier)
        cue_by_id[identifier] = cue
        if cue["target"] != b"data".hex() or cue["chunk_start"] or cue["block_start"]:
            issue(
                "cue_addressing_unsupported",
                "Only data-relative cues with zero chunk/block start are evaluated",
                offset,
                identifier,
            )
        elif cue["sample_offset"] > frames:
            issue(
                "cue_out_of_bounds",
                "Cue exceeds sample-frame count (EOF boundary itself is permitted)",
                offset,
                identifier,
            )
    for ref in references:
        identifier, offset = ref["cue_id"], ref["byte_offset"]
        cue = cue_by_id.get(identifier)
        if cue is None:
            issue(
                "orphan_cue_reference",
                "Associated-data record refers to a missing cue",
                offset,
                identifier,
            )
        if ref.get("terminated") is False:
            issue("label_unterminated", "Label/note must end in a NUL byte", offset, identifier)
        if "sample_length" in ref and cue is not None:
            if cue["target"] == b"data".hex() and not cue["chunk_start"] and not cue["block_start"]:
                if cue["sample_offset"] + ref["sample_length"] > frames:
                    issue(
                        "region_out_of_bounds",
                        "Associated text region exceeds available frames",
                        offset,
                        identifier,
                    )
    if require_loop and not loops:
        issue("required_loop_missing", "At least one smpl loop is required", 0)
    if require_cue and not cues:
        issue("required_cue_missing", "At least one cue point is required", 0)
    return {
        "schema_version": 1,
        "ok": not findings,
        "file_size": reader.size,
        "format": audio_format,
        "frame_count": frames,
        "duration_seconds": frames / audio_format["sample_rate"],
        "chunk_count": reader.chunk_count,
        "loops": loops,
        "cues": cues,
        "references": references,
        "findings": findings,
    }


def analyze(
    path: str | Path, *, require_loop: bool = False, require_cue: bool = False
) -> dict[str, Any]:
    """Read one regular file; raise WaveError for unsafe/unsupported structures."""
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_BYTES:
                raise WaveError("Input must be a regular file no larger than 2 GiB")
            report = _scan(
                _Reader(stream, before.st_size), require_loop=require_loop, require_cue=require_cue
            )
            after = os.fstat(stream.fileno())
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise WaveError("Input changed while being read")
            return report
    except OSError as exc:
        raise WaveError(f"Cannot read WAV: {exc.strerror or 'I/O error'}") from exc
