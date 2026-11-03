# Retained-output candidate rejection diagnostics

Issue #18 reported a generic `invalid_midi` HTTP 422 while creating a candidate.
The reported private MIDI has not been inspected, so its specific cause remains
unknown. All regressions here use anonymous synthesized events and mocked models.

## Reproduced boundary failures

Raw note rows have no chronological ordering requirement. Previously, when rows
for adjacent equal-pitch notes appeared in reverse order, the literal MIDI writer
put the new attack before the preceding release at the shared timestamp. Strict
saved-MIDI validation then rejected that valid pair as overlapping. The writer
now orders positive-interval releases, literal zero-length pairs, then positive
attacks at an identical timestamp. Each zero-length pair keeps its own attack
before release. Pitches, velocities, event multiplicity and timestamps remain
unchanged. Distinct real timestamps still determine order when rounded ticks tie.

A second failure occurred after saving a pending candidate receipt. An import
rejection escaped as HTTP 422, while the browser discarded the candidate ID as
unaccepted. The receipt remained pending. Candidate creation now returns HTTP 201
with `status: blocked`, no saved score ID, and a durable public `error` when strict
validation refuses the candidate. This acknowledges the receipt, not a successful
score. GET and repeated POST with the same ID return the same blocked result.

Older pending receipts with a retained candidate MIDI can recover a definite
validation refusal by preflighting those bytes under the candidate lock. Recovery
never regenerates MIDI, repeats conversion, or runs inference. Unknown or incomplete
work remains pending or explicitly unavailable; it is not assumed successful.

## Public contract

Direct saved-MIDI imports still return HTTP 422 for unsupported note streams.
Known refusals use stable codes: `midi_same_pitch_overlap`,
`midi_nonpositive_duration`, `midi_unmatched_note_off`, `midi_unclosed_note_on`,
`midi_multiple_instruments`, `midi_drum_notes`, `midi_pitch_range`, and
`midi_note_count_mismatch`. The error contains an actionable message and a
`diagnostic` with boundary `saved_midi_note_stream` and the available tick,
channel, pitch or count. It contains no filesystem paths or raw exception text.
Unsupported clock metadata retains `unsupported_midi_clock`.

True same-pitch overlaps, multiple instruments, drums, out-of-range XML pitches,
and positive source notes collapsed by MIDI tick rounding remain refused.
There is no implicit instrument selection, note deletion, extension, transposition,
overlap repair, or automatic inference retry. Only the explicit zero-duration
exclusion policy removes rows from its separate candidate. Raw output, source
manifests, original jobs and existing candidates remain intact.

New receipts bind the generated candidate SHA-256. Result recovery checks source
receipts, candidate identity/counts, saved MIDI identity/hash, and published
artifact hashes. Changed data returns HTTP 409 without replacing the files.
The browser retains accepted, blocked and uncertain candidate IDs, shows the
saved rejection reason, and checks the same ID using GET. Preacceptance rejection
is separate from a saved blocked receipt.

## Synthetic verification

From `backend`, use the existing environment without synchronization:

```bash
PYTHONPATH=$PWD UV_CACHE_DIR=/workspace/stem2tab-coordination/uv-cache \
UV_PROJECT_ENVIRONMENT=/workspace/Stem2Tab/backend/.venv \
uv run --no-sync python -B -m pytest \
  tests/unit/test_evaluation_*.py tests/unit/test_model_output_reviews.py \
  tests/unit/test_midi_trials_api.py tests/unit/test_muscriptor_worker.py \
  tests/unit/test_muscriptor_decoder.py tests/unit/test_transcriptions_api.py \
  -q -p no:cacheprovider
```

Run `npm test -- --run`, `npm run build`, and the existing TypeScript check from
`frontend`. Report baseline type errors separately. API/unit fixtures verify all
six row permutations around a zero-duration reattack, complete producer-to-import
flow, retained note boundaries and multiplicity, explicit refusal causes,
same-ID replay, legacy pending recovery, and changed-source/artifact refusal.

Browser validation must separately exercise a successful synthesized candidate,
a blocked one with a visible cause, same-ID reload/recheck, response loss and
changed-source refusal. Unit tests alone do not establish browser playback or
the cause of the uninspected reported MIDI. No transcription-accuracy claim is
made.

Validation of this change passed 451 backend unit tests, 221 frontend tests,
the production build and `tsc --noEmit`. A real headless Chromium session against
the production preview and local FastAPI server verified successful reversed-row
import with exact tick/velocity readback, a visible blocked cause, same-ID reload
and GET-only recheck, recovery after losing an accepted POST response, and
changed-source refusal. Models were disabled and all retained events were
synthesized. This did not exercise real-audio inference or assess playback sound.
