"""CLI contract: clean=0, marker findings=1, unsafe/unsupported input=2."""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .core import WaveError, analyze


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only WAV sampler-loop and cue-reference quality gate."
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("file", help="Little-endian linear PCM/float RIFF-WAVE file")
    parser.add_argument("--require-loop", action="store_true", help="Fail if no smpl loop remains")
    parser.add_argument("--require-cue", action="store_true", help="Fail if no cue point remains")
    parser.add_argument("--format", choices=("json", "text"), default="json")
    args = parser.parse_args(argv)
    try:
        report = analyze(args.file, require_loop=args.require_loop, require_cue=args.require_cue)
    except WaveError as exc:
        print(f"loopcue: {ascii(str(exc))[1:-1]}", file=sys.stderr)
        return 2
    try:
        if args.format == "json":
            print(json.dumps(report, indent=2, ensure_ascii=True))
        else:
            print(
                f"{report['frame_count']} frames; {len(report['loops'])} loops; {len(report['cues'])} cues; {len(report['findings'])} findings"
            )
            for finding in report["findings"]:
                print(f"{finding['code']} at byte {finding['byte_offset']}: {finding['message']}")
    except (BrokenPipeError, OSError):
        return 2
    return 0 if report["ok"] else 1
