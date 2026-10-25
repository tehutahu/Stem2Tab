# Frozen B cached decoder

This opt-in module decodes an existing Basic Pitch 0.4.0 activation cache. It does
not run a model, change benchmark/Web adapters or replace any default. The only
exposed rule is `dominant_gap_nms` (frozen B). Experimental `short_gap_nms` is not
available.

B combines raw onsets with inferred onsets, selects strict local maxima at the
existing 0.5 cutoff, then suppresses same-pitch peaks within 11 frames only when a
retained peak is stronger by at least 0.1 (original 1e-7 float tolerance). Equal or
close-strength peaks remain. Upstream note creation keeps frame cutoff 0.3,
127.7 ms rounded to 11 frames, duration scanning, adjacent-pitch suppression,
Melodia and frame-time conversion. No installed package is patched. Input arrays
are copied before processing.

A genuine weaker reattack can have the same observations as a spurious late onset
and be suppressed. The synthetic counterexample remains in the tests. Tests and
saved-event parity do not establish general quality or short-note/polyphony safety.

## Existing environment and cloud setup

Use Python 3.11+ and the committed backend lockfile. Basic Pitch is intentionally
installed outside the lock, without TensorFlow/dependency resolution:

```sh
cd backend
uv sync --locked --dev
uv pip install --no-deps basic-pitch==0.4.0
BASIC_PITCH_MODEL_SERIALIZATION=onnx uv run --no-sync pytest tests/unit/test_evaluation_*.py -q
```

Run sync before installing Basic Pitch: a later sync can remove that unmanaged
package. Use `--no-sync` or `UV_NO_SYNC=1` afterwards. Both Python decoding and CLI
reject Basic Pitch versions other than 0.4.0. Cache decoding needs no new model or
inference. The private parity run used Python 3.11.14, Basic Pitch 0.4.0, NumPy
1.26.4, SciPy 1.16.3, librosa 0.11.0 and Pydantic 2.12.5. The lockfile governs fresh
cloud installs; these are observed reproduction versions, not extra constraints.

## Decode without changing input

The trusted NPZ contains note/onset arrays shaped (frames, 88) and contour shaped
(frames, 264). Arrays must be finite; note/onset scores must be in [0, 1]. NumPy
pickle loading is disabled.

```sh
BASIC_PITCH_MODEL_SERIALIZATION=onnx uv run --no-sync python -m src.evaluation.bp_onset_conflicts \
  --cache /private/input/raw_activations.npz \
  --method dominant_gap_nms \
  --output-dir /private/output/new-b
```

The output directory must not exist. It contains an exact byte copy of
`raw_activations.npz`, canonical raw-B `alternative_events.json`, and
`decisions.json` with the cache hash and removed/retained onset frame IDs. Original
input is not rewritten. Decisions are onset-peak evidence, not reference labels
or proof of a false musical note. Keep private caches/events outside Git.

## Explicit B -> PR8 transformation

The supporting harmonic_suppression.py is unchanged from published commit
aca357a, SHA256
5cc10bbd0e8ee5e326f67d3adbb7261c1019499d2c192dc4be3b351976d4d4a5.
It is a separate optional filter with an explicit monophonic-bass assumption, and
can remove real upper notes in octave playing. Its historical adapter and pipeline
integration are not part of this change.

Preserve raw B and transform into a new output directory:

```sh
BASIC_PITCH_MODEL_SERIALIZATION=onnx uv run --no-sync python - <<'PY'
from pathlib import Path
import json
from src.evaluation.io import read_note_events_json, write_note_events_json
from src.evaluation.harmonic_suppression import suppress_contained_harmonics

raw = read_note_events_json(Path('/private/output/new-b/alternative_events.json'))
filtered, decisions = suppress_contained_harmonics(raw)
out = Path('/private/output/new-b-pr8')
out.mkdir(exist_ok=False)
write_note_events_json(filtered, out / 'events.json')
(out / 'decisions.json').write_text(json.dumps(decisions, indent=2))
PY
```

A separately reviewed C stage can consume these already-PR8 events. Do not apply
PR8 again inside an additional stage or feed raw B to a stage requiring B+PR8.
This module does not add or alter C.

## Validation and provenance

B arithmetic comes from frozen local source SHA256
cffa469130cffbd283fc1ac856f5372bbdf0a02458f3f69831404be81fa13d6f.
Packaging restricts the policy to B and adds the version check; arithmetic and
native event tuples are unchanged. Unit tests cover dominance, float boundaries,
equal/weak peaks, weaker-true-onset loss, spaced reattacks, silence, tiny notes,
array preservation, no-conflict replay and safe CLI creation. PR8's pure tests are
retained; its old adapter test is omitted because that adapter is outside scope.

Local results:24 focused B/PR8 tests and all50 evaluation-unit tests passed. An
existing-cache-only odd9 comparison reproduced every saved raw-B event (960) and
B+PR8 event (703), with exact start/end/MIDI/velocity/order/multiplicity and unchanged
input files/arrays. No references, matching/scoring, inference or other recordings
were accessed. Private data/artifacts/logs are not committed or needed by public
tests. Parity is not a new accuracy or held-out performance claim.
