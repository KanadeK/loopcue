# Marker gate contract

## Acceptance scenario

A sample exporter writes a linear WAV. A maintainer requires a sampler loop and cue point. A loop ending at N-1 in an N-frame stream passes; ending at N fails. A later export that strips all loops/cues fails the enabled presence checks. An orphan `adtl` label points to the missing cue ID and reports its byte offset. A malformed or unsupported container never receives a clean partial result.

## Read path

Open one regular file in binary, read-only mode (nonblocking where available), and require a bounded size. A `_Reader` exposes exact absolute-offset reads. Its chunk walker checks parent bounds and word padding before yielding, counting nested associated chunks against the same resource budget. Cursor advancement uses declared lengths, not payload contents, so zero-length or unknown chunks cannot cause a zero-progress loop. The final odd-length data-pad omission is accepted only when its declared payload ends exactly at EOF; it cannot hide another chunk.

Parse bounded `fmt`, fixed-size loop/cue records and associated-data reference headers. Never read data payloads or sampler-specific bytes. Only the terminal byte of label/note text is inspected. References are resolved after the complete top-level walk so their position relative to `cue ` does not affect results. Integers use Python's exact arithmetic; values come from bounded unsigned RIFF fields. Report fields have a fixed schema and no source text/path. Numeric format/marker metadata is still disclosed by design.

Frame count = data byte length / block alignment. Frame count must be integral. Sampler start/end are zero-based, end inclusive. Cue offsets may equal frame count as an explicitly permitted EOF boundary policy. Nonzero cue chunk/block starts, non-data targets and custom loop types are findings because the implementation cannot give their semantics a clean result. A loop ID is only optionally associated with a cue in the specification, so an absent matching cue is not itself an error. `ltxt` regions use the referenced cue's data-relative offset plus length.

## Format boundaries

The engine is deliberately narrower than a full RIFF ecosystem validator. It accepts PCM and float only, in basic/extended/extensible format descriptors; basic 16-byte float headers are tolerated. `fact`, INFO and BWF chunks are skipped, not semantically validated. Unknown ordinary chunks remain opaque after boundary checks. `wavl`, playlists, RF64 and RIFX raise WaveError. Label encodings, channel-layout masks, sample values, sampler pitch parameters and audible seam quality are not analyzed. Multiple interpreted format/data/marker chunks are rejected as ambiguous.

Regular-file size/mtime checks catch ordinary concurrent edits; they are not cryptographic integrity proof. Keep files stable. Inputs larger than 2 GiB, more than 10,000 walked chunks or more than 100,000 combined loops/cues/adtl references fail with exit 2. A single metadata record can report multiple findings; output is bounded by those counts. Memory is proportional to marker metadata, not audio length. A 32 MiB PCM data test confirms fewer than 512 bytes of reads while retaining an accurate frame count.

## Architecture and sources

`core.analyze(path, require_loop=False, require_cue=False)` returns a schema-1 JSON-compatible dictionary or raises WaveError. `cli.main` maps marker findings to 1, unsafe/unsupported input to 2, clean reports to 0. It never executes a subprocess or network operation. Subprocesses in examples/tests exercise the installed tool only. The demo's output-directory option writes three synthetic files only in a newly created directory; the analyzer itself is read-only.

Implementation/fixtures are original MIT code. Format facts derive from [Microsoft's RIFF update, pages 12–18](https://www.mmsp.ece.mcgill.ca/Documents/AudioFormats/WAVE/Docs/RIFFNEW.pdf) and [Microsoft's extensible descriptor reference](https://learn.microsoft.com/en-us/windows-hardware/drivers/audio/extensible-wave-format-descriptors). No third-party specification or sample assets are redistributed.
