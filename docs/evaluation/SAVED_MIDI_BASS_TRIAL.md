# Opt-in saved-MIDI bass output trial

This local CLI imports an existing MIDI file. It performs no separation,
transcription, model download, authentication or network request. It does not
change the API/worker/frontend or the default Basic Pitch route.

From `backend`, use the existing environment:

```bash
python -B -m src.evaluation.saved_midi_trial \
  --midi /path/to/existing-bass.mid \
  --output-dir /path/to/new-trial-directory \
  --source-kind muscriptor-small \
  --time-basis storage-clock \
  --bass-profile bass4 \
  --max-fret 24 \
  --gp5-preview
```

Every output directory must be new. The input is never rewritten. `source-kind`
records caller-declared provenance; it does not authenticate a model. Retain the
existing MuScriptor small noncommercial and additional conditions. No weights
are bundled or redistributed. Keep private recordings and derived files local.

## Explicit instrument settings

| Profile | Sounding MIDI open strings, low to high | Meaning |
|---|---|---|
| `bass4` | 28,33,38,43 | E1,A1,D2,G2 |
| `bass5` | 23,28,33,38,43 | Low B0 plus standard four strings |
| `custom` | User supplies every MIDI number | Requires `--strings`, `--tuning-midi`, `--max-fret` |

For example, an explicitly selected four-string drop-D trial uses
`--bass-profile custom --strings 4 --tuning-midi 26,33,38,43 --max-fret 24`.
Named profiles reject conflicting string counts/tunings. MIDI pitches are
never octave-shifted to fit. Max fret must be within the supported 0..36 range.
This exporter chooses a simple valid string/fret, not optimized musical fingering.

The legacy converter retains its previous 4-string MIDI40/45/50/55 settings.
Those pitches are an octave above conventional sounding bass4; they exclude
notes below MIDI40. The new explicit route does not silently alter old jobs.

## Artifacts and failure states

- `performance.mid`: byte-identical copy of the supplied MIDI.
- `canonical.json` / `canonical.csv`: all loaded MIDI notes in seconds.
- `source-events.json`: stable indices, source program/velocity, seconds and
  ticks; this is MIDI-derived data, not original model raw-event JSON.
- `performance-clock.musicxml`: exact loaded note pitch/tick boundaries using
  the input storage clock, including rests, overlap voices and bar-crossing ties.
- `trial.json`: explicit status, warnings, configuration, scope, hashes and
  output records.
- Optional `gp5-preview/bass-output.json` and `*.gp5`: fingering preview and
  independent readback/report from the GP5 writer.

If **any** event is outside the chosen strings/fret range, no partial GP5 is
written. Every rejected index/pitch/time is reported. Performance MIDI,
canonical events and XML retain all notes. Exit code2 means the requested GP5
was blocked; it is not complete GP5 success. Exit code1 means rejection/failure;
code0 means the requested supported export finished. Later export failures are
recorded in `trial.json`; preflight failures create no output directory.

Inputs with drums, multiple populated instruments, ambiguous same-channel/pitch
note-ons, unmatched/unclosed note events, zero-duration notes, asynchronous
type2, SMPTE timing, varying tempo or unsupported meter are explicitly rejected.
There is no automatic instrument filtering, note merging or note-off repair.
MIDI pitches below12 are outside this bounded XML writer. The original MIDI
remains available on every rejection.

## Timing claims

The required `--time-basis storage-clock` acknowledges an explicit interchange
representation. XML's 4/4 containers and input MIDI clock are **not** a detected
musical meter/tempo. XML preserves nominal loaded note boundaries; it does not
reconstruct acoustic truth or render controllers, bends, velocity and timbre.
Symbolic note types may be omitted where they cannot express an exact tick
duration. Renderer compatibility/engraving still needs separate validation.

`--gp5-preview` is optional and off by default. It intentionally turns each
mapped attack into a quarter beat at a preview clock and groups four beats per
container. It does **not** preserve source rhythm, rests or simultaneous notes,
and must not be presented as an accurate score. Source timing is retained in
the other artifacts. GP5 velocity quantization is reported separately.

The earlier maximum XML onset/end drift of2.246545 seconds belongs to the
**legacy** sequential-append MusicXML converter. Correcting four tiny float-only
overlap counts to rational zero did not remove that timing drift. The separate
tick writer previously passed12 synthetic fixtures/72 events; this trial adds
the explicit saved-MIDI route and its own checks. Neither result establishes
real-recording transcription accuracy. No trusted RWC reference has been
certified; do not calculate or advertise real-recording F1 from these exports.

## Verification

All fixtures are synthesized in temporary directories. No private source MIDI,
recording, annotation or weights belongs in checked-in fixtures.

```bash
python -B -m pytest tests/unit/test_bass_output.py tests/unit/test_tab.py \
  tests/unit/test_evaluation_*.py tests/unit/test_worker_tasks.py \
  tests/unit/test_jobs_api.py tests/unit/test_health.py \
  --capture=sys -p no:cacheprovider -q
```

Tests verify bass4/5/custom ranges, source bytes, explicit rejection and no
overwrite, real GP5 pitch/readback, exact XML pitch/start/end ticks, reattacks,
one-tick notes, overlaps and bar ties. CLI artifact validation is not a live
web-worker/browser or renderer test.
