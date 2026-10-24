# LoopCue

[![CI](https://github.com/KanadeK/loopcue/actions/workflows/ci.yml/badge.svg)](https://github.com/KanadeK/loopcue/actions/workflows/ci.yml)

**Catch broken sampler loops and cue references before shipping a WAV export.** LoopCue is a read-only CLI for sample-library and game-audio build pipelines. It checks inclusive `smpl` loop bounds, cue offsets and associated labels/regions, and can fail when an export has lost all required loops or cues.

No audio decoding, network access, external executables or runtime Python dependencies. [中文说明](README.zh-CN.md) · [Design](docs/design.md) · [Validation](docs/validation.md)

## Install and run

Python 3.11+:

```sh
git clone https://github.com/KanadeK/loopcue.git
cd loopcue
python -m venv .venv
```

Activate with `.venv\Scripts\Activate.ps1` in PowerShell or `source .venv/bin/activate` on macOS/Linux, then:

```sh
python -m pip install .
python examples/demo.py
loopcue --version
```

Source installation downloads the open-source build backend. For offline installation, download the wheel and `SHA256SUMS` from [Releases](https://github.com/KanadeK/loopcue/releases), verify the checksum, then run `python -m pip install --no-index --no-deps loopcue-0.1.0-py3-none-any.whl`. The source archive contains examples/tests. This release is not published to PyPI.

## A reproducible export gate

Generate three original, one-second WAV fixtures in a **new** directory (an existing directory is never overwritten):

```sh
python examples/demo.py --output-dir demo-out
loopcue demo-out/valid.wav --require-loop --require-cue --format text
```

```text
8000 frames; 1 loops; 1 cues; 0 findings
```

The demo also invokes the real installed CLI against `bad-loop.wav` and `stripped.wav` and verifies their failures. Inspect them yourself:

```sh
loopcue demo-out/bad-loop.wav --require-loop --format text
loopcue demo-out/stripped.wav --require-loop --require-cue --format text
```

These last two commands intentionally return **1**: the first has a loop ending at frame 8000 in an 8000-frame file, and the second lost both kinds of marker. The final existing frame is 7999 because `smpl` ends are **inclusive**. The default demo uses a temporary directory and cleans it automatically.

For your own export:

```sh
loopcue instrument.wav --require-loop --require-cue > marker-report.json
```

| Exit | Meaning |
|---|---|
| 0 | Supported file; all enabled checks pass |
| 1 | Marker findings, including required markers missing or unsupported cue/loop semantics |
| 2 | Invalid arguments, I/O error, unsupported container/format or unsafe structural input; no partial report |

Default output is JSON (`schema_version: 1`): `ok`, format, frame count, duration, loops, cues, associated-data references and findings with stable `code`, `byte_offset` and optional `marker_id`. The source path, audio samples and label/note text are not included. Numeric marker IDs, frame offsets and format metadata remain visible; reports are not an anonymization format.

Library API:

```python
from loopcue.core import analyze, WaveError

try:
    report = analyze("instrument.wav", require_loop=True, require_cue=True)
    print(report["ok"], report["findings"])
except WaveError as error:
    print("Unsupported or unsafe input:", error)
```

## Checks and support boundary

- Little-endian RIFF-WAVE, exactly one `fmt ` before one `data` chunk. Linear PCM containers: 8/16/24/32 bits; IEEE float: 32/64 bits; corresponding WAVE_FORMAT_EXTENSIBLE subtypes, including reduced valid bits for PCM. Block alignment, byte rate and whole-frame data length must agree.
- `smpl` record counts and sampler-specific lengths, unique loop IDs, types 0/1/2, `start <= end < frame_count`. A one-frame loop is valid. Play count 0 means indefinite looping; fractional loop values are retained without predicting playback. Loop IDs need not have matching cue records.
- Unique `cue ` IDs and offsets for `data`-relative cues whose chunk/block starts are zero. A cue at `frame_count` is permitted as an **EOF boundary marker**; greater offsets fail. Unsupported targeting/addressing produces a finding and exit 1.
- `LIST/adtl` `labl`, `note`, `ltxt` references must point to an existing cue; labels/notes must end in NUL; associated text regions must fit available frames. Reference order is unrestricted. Text is neither decoded nor emitted.
- File size/RIFF size, chunk parent bounds, record lengths and padding are checked. Unknown ordinary chunks and non-adtl metadata are skipped. Compatibility exception: the **final odd-length data chunk** may omit its pad, as Python's `wave` writer does. Middle chunks and metadata require their padding.

Inputs must be regular files, at most 2 GiB, with at most 10,000 top-level/nested interpreted chunks and 100,000 combined loops/cues/associated references. Audio payloads and sampler-specific data are skipped by seeking. Inputs changing size/mtime during the read are rejected; keep input files stable while analyzing.

This release handles one file per invocation. RF64, RIFX, compressed formats, segmented `wavl` audio, playlists and custom sampler semantics are outside scope. A reserved/custom loop type is a finding. It does not compare a full marker manifest across exports, require a particular marker count beyond presence, decode audio values, test audible clicks, validate every metadata field, or guarantee compatibility with every DAW/sampler. `fact`, INFO/BWF metadata and text encodings are outside the marker checks; this is **not a universal WAV conformance validator**. Compact 16-byte float `fmt` headers are accepted for compatibility.

## Why this exists

Embedded loop interoperability causes real playback bugs: [Godot's loop-end issue](https://github.com/godotengine/godot/issues/119778), [sfizz's embedded-loop request](https://github.com/sfztools/sfizz/issues/202). [BWF MetaEdit](https://github.com/MediaArea/BWFMetaEdit) provides broad broadcast-metadata tooling and [libsndfile](https://github.com/libsndfile/libsndfile) handles decoding/inspection. LoopCue offers a small, dependency-free marker-presence/bounds gate for build scripts. It does not edit, repair or play audio.

Format references: [Microsoft's 1994 RIFF update](https://www.mmsp.ece.mcgill.ca/Documents/AudioFormats/WAVE/Docs/RIFFNEW.pdf) and [extensible format documentation](https://learn.microsoft.com/en-us/windows-hardware/drivers/audio/extensible-wave-format-descriptors). No specification text, third-party source code or audio assets are bundled.

## Develop and clean up

```sh
python -m pip install -r requirements-dev.txt
python -m pip install --no-build-isolation --no-deps -e .
python -m ruff check .
python -m ruff format --check .
python -m coverage run -m unittest discover -s tests -v
python -m coverage report --fail-under=85
python -m build --no-isolation
python examples/demo.py
```

CI covers Linux/macOS/Windows and Python 3.11/3.13. Tests use generated fixtures, a stdlib PCM frame oracle, truncated inputs, bounded mutation cases and real CLI flows; [validation](docs/validation.md) states what was actually tested.

Uninstall with `python -m pip uninstall loopcue`. Remove the dedicated environment/checkout when finished. There are no background services, remote accounts or runtime caches. The default demo cleans its files; delete your explicitly retained `demo-out` and redirected reports yourself. Analysis never modifies source WAV files.

MIT · [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md) · [Third-party notices](THIRD_PARTY.md)
