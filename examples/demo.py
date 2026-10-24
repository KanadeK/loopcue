"""Generate original WAV fixtures and exercise the installed CLI, no audio downloads."""

import argparse
import json
import math
import struct
import subprocess
import sys
import tempfile
import wave
from pathlib import Path


def chunk(name, body):
    return name + struct.pack("<I", len(body)) + body + (b"\0" if len(body) & 1 else b"")


def create_and_check(directory):
    plain = directory / "stripped.wav"
    with wave.open(str(plain), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(8000)
        tone = b"".join(
            struct.pack("<h", round(math.sin(index * math.tau * 220 / 8000) * 4000))
            for index in range(8000)
        )
        writer.writeframes(tone)
    base = plain.read_bytes()
    cue = chunk(b"cue ", struct.pack("<I", 1) + struct.pack("<II4sIII", 1, 100, b"data", 0, 0, 100))
    for filename, end in (("valid.wav", 7899), ("bad-loop.wav", 8000)):
        sampler = chunk(
            b"smpl",
            struct.pack("<9I", 0, 0, 125000, 60, 0, 0, 0, 1, 0)
            + struct.pack("<6I", 1, 0, 100, end, 0, 0),
        )
        body = base[8:] + sampler + cue
        (directory / filename).write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    for filename, expected, code in (
        ("valid.wav", 0, None),
        ("bad-loop.wav", 1, "loop_out_of_bounds"),
        ("stripped.wav", 1, "required_loop_missing"),
    ):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "loopcue",
                str(directory / filename),
                "--require-loop",
                "--require-cue",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
        if result.returncode != expected:
            raise RuntimeError(
                f"Unexpected CLI status for {filename}: {result.returncode}: {result.stderr}"
            )
        report = json.loads(result.stdout)
        if report["frame_count"] != 8000 or (
            code and code not in {item["code"] for item in report["findings"]}
        ):
            raise RuntimeError(f"Unexpected marker/frame report for {filename}")
    print(
        "Demo passed: valid inclusive loop accepted; one-past-end loop and stripped markers rejected; inputs contain 8000 frames."
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir", type=Path, help="Keep the three synthetic WAV files in a NEW directory"
    )
    args = parser.parse_args()
    if args.output_dir:
        args.output_dir.mkdir(parents=True, exist_ok=False)
        create_and_check(args.output_dir)
    else:
        with tempfile.TemporaryDirectory(prefix="loopcue-demo-") as temp:
            create_and_check(Path(temp))


if __name__ == "__main__":
    main()
