# Validation for 0.1.0

Local platform: Windows, Python 3.13.16. Local suite: **43 discovered tests, 42 passed, 1 POSIX FIFO test skipped**. Local statement coverage: **98.85%** (260 statements, 3 untraced). CLI subprocesses are executed but their child-process entry-point statements are not included in coverage. All release CI configurations run the complete suite; their six-job result is available in Actions.

Checks include inclusive loop boundaries, one-frame/zero-frame cases, ordered and inverted limits, standard/custom loop types, duplicate marker IDs, zero/infinite play counts, sampler-specific payload skipping, cue EOF policy, unsupported cue addressing, orphan/forward associated references, text-region lengths and required marker presence. A synthetic label canary confirms that text is absent from JSON. Inputs are byte-for-byte unchanged after regular fixture analysis.

An independent Python `wave` writer/reader verifies PCM frame counts for 8/16/24/32-bit mono/stereo files. This uncovered Python's omitted final odd data padding; regression tests accept the unambiguous EOF case while still rejecting missing padding before another chunk. Float/extensible fixtures are generated directly from the documented field layouts and have explicit expected frame counts; they are not a claim of live DAW interoperability testing.

Malformed-format tests cover endian/form variants, unsupported codecs, extension lengths/GUIDs, zero rates/channels, byte-rate/alignment errors, duplicate or missing chunks, count/length inconsistencies, truncated headers, every truncated prefix of a representative marked WAV, nested-chunk parent escapes and resource limits. A seeded 300-input byte-mutation campaign permits only a bounded report or WaveError, with no unhandled parser exceptions observed. This is a regression campaign, not exhaustive fuzzing.

The seek test creates a real 32 MiB PCM payload followed by a sampler chunk, verifies its frame count/loop, and instruments metadata reads to confirm fewer than 512 bytes were read. Audio content is not inspected. CLI integration checks statuses 0/1/2; the one-second demo creates 8,000-frame synthetic tones and tests a valid loop, one-past-end failure and stripped-marker failure.

Release gates run Ruff, build wheel/sdist, inspect zero-runtime-dependency/MIT metadata, scan sources for secrets and machine-specific paths, install the wheel into a clean environment without an index, execute help/version/copied standalone demo and uninstall. Remote acceptance requires exact-commit CI on Linux/macOS/Windows × Python 3.11/3.13, sole owner/author/committer/contributor, release asset integrity and anonymous source installation. Logs/receipts are retained privately outside the repository.

No third-party WAV fixtures, commercial DAWs, physical samplers or live audio devices were used. These tests do not prove universal compatibility, verify audible playback quality or certify every WAV metadata field.
