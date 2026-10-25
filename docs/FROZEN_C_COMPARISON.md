# Optional frozen C comparison

This CPU-only JSON tool applies the fixed **extra stage only** to already-PR8
canonical note events. It does not run PR8, load audio, infer notes, or change the
web pipeline. Baseline is the default and copies input bytes exactly.

From `backend`, using the existing locked environment:

```sh
uv run --no-sync python -m src.evaluation.compare_note_events --input already-pr8.json --output new-baseline.json
uv run --no-sync python -m src.evaluation.compare_note_events --input already-pr8.json --output new-c.json --mode frozen-c --already-pr8
```

The acknowledgement describes input provenance; it does not verify audio truth.
Create the destination directory first. Both output and `.decisions.json` sidecar
paths must be unused. C writes validated canonical JSON and a deletion ledger
whose indices refer to the canonical input order. Survivors retain all fields;
no threshold options are exposed. Duplicate events remain distinct. If writing
the second file fails, files created by this invocation are removed. This is not
a crash-atomic multi-file transaction; an interrupted run needs inspection.

C examines notes in pitch/start/end order, retaining lower supporters sequentially.
It deletes an exact-octave upper that starts strictly earlier than a retained lower,
within 50 ms (plus the frozen numerical tolerance), and ends no later. Deleted
notes cannot support subsequent deletions. The original harmonic calculation and
first-supporter rule are retained, with fixed 50-cent/8-harmonic constants.

Provenance: original extra-stage source SHA256
`e50c3bceb16f1d1939675714e0ad7261ef44a10762a0f35c1586a31c5277da56`.
This port excludes the original composed PR8 stage and rejected Harvest policies.

Private reused development evidence: nine tracks removed 65 false positives and
one true positive (onset F1 .633416 to .668426). Eight historically used confirmation
tracks removed 49 false positives with unchanged onset matches (F1 .732965 to
.767599), with a per-track frame-recall cost. These are not untouched holdout
results. Known synthetic true-octave notes can be deleted; eight acoustic controls
had no firings, and correlated robustness views contain harms. This is an optional
precision/recall research comparison, not a default transcription improvement.
No private event/audio/reference data are distributed with the tool.

Run `uv run --no-sync pytest -q tests/unit/test_evaluation_frozen_c.py`, then all
`test_evaluation_*.py` unit modules under repository CI. Runtime uses only the
standard library and existing Pydantic v2; pure tests require existing pytest.
No GPU, model weights, network service, or new dependencies are needed.
