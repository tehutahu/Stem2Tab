# Optional bass fingering and explicit rhythm trial

This local saved-MIDI CLI adds optional derivatives. It does not run models,
separation, download assets, change app defaults, or overwrite an existing output
directory. It uses existing dependencies. Original MIDI, canonical notes and the
exact-tick storage-clock MusicXML are retained under `preserved/` first. Do not
interpret that storage clock as the song's musical tempo or meter.

The existing Web audio/import routes keep their behavior. These new controls are
currently CLI-only. Do not present the synthetic browser preview as Web file-upload
coverage or claim improved transcription accuracy.

## Constrained fingering

Run from `backend` with the already installed environment:

```bash
python -m src.evaluation.bass_notation_trial \
  --midi /path/to/saved/performance.mid --output-dir /path/to/new/trial \
  --source-kind muscriptor-small --bass-profile bass4 --max-fret 24 \
  --fingering-mode constrained --max-position-shift 5 --max-hand-span 4 \
  --gp5-preview
```

Use `local-midi` for other permitted MIDI. `bass4` means low-to-high MIDI
28,33,38,43 (E1,A1,D2,G2); `bass5` means 23,28,33,38,43. Custom tuning requires
`--bass-profile custom --strings N --tuning-midi ... --max-fret N`. User settings
take precedence. No octave transposition or silent range drop is applied.

The optional planner jointly assigns strings at each exact onset. Sustained
notes occupy a string for their original half-open interval `[start,end)`.
Positive frets must fit an explicit integer hand window; open strings do not force
the hand back to the nut. The first hand location is free. Later hand shifts,
including shifts across rests, must respect the chosen limit. This is a discrete
position proxy, not a tempo-aware movement-speed or physical ergonomics model.

The deterministic objective first minimizes total hand-anchor travel, then total
fret numbers. It also measures the legacy independent mapping under the same proxy
and reports constraint violations. A smaller proxy does not prove playability.
Search caps are 500 events, 2,048 states and 100,000 expansions. `too_complex`
means the search limit was reached; `unplayable` means no assignment under these
declared constraints, not proof that a human cannot perform the passage.

`fingering-plan.json` contains original events, exact pitch/string/fret consistency,
selected positions, hand timeline, baseline comparison, warnings and reasons.
An unsuccessful plan never creates partial GP5. Original events remain retained.
GP5 is still a **sequential quarter-note fingering preview**: no rhythm, rests or
overlap preservation. The new rhythm mode described below exports MusicXML only.

## Explicit rhythmic derivative

Add all of the following only when the musical clock is explicitly supplied:

```bash
--rhythm-mode explicit-grid --tempo-bpm 120 --meter 4/4 \
--origin-seconds 0 --subdivisions-per-quarter 4 --max-boundary-shift-ms 50
```

This is an example for synthetic data, not a recommended real-song setting.
Tempo is quarter-note BPM. The origin is the first grid/bar boundary in original
source seconds. Derivative score time starts at that origin; reports retain both
source times and score units. Events before it block output rather than inventing
a pickup or moving notes. No musical tempo, meter or phase is inferred.

Supported values are BPM20..300, numerator1..12, denominator2/4/8, nonnegative
origin, subdivisions per quarter1/2/4/8, and maximum boundary change0..250ms.
Nearest-grid rounding uses exact decimal fractions, with halfway ties rounded
later. Reports list each original/proposed start/end and signed change in ms.
Unsupported changing clocks, tuplets and swing are not silently approximated.

The entire optional derivative is blocked if a note collapses, distinct onsets
merge, any pair's overlap relation changes, a boundary exceeds the chosen limit,
an event predates the origin, or a representation bound is exceeded. No note is
dropped, stretched or repaired to make output succeed. Bounds are 500 events,
7 voices and 128 bars; MIDI pitches below12 are unsupported. The plan JSON is
retained even when XML is blocked. These conservative checks can reject useful
quantizations; the caller must not silently loosen limits after seeing output.

Successful output is `rhythm/score-explicit-quantized.musicxml`, with exact explicit
tempo/meter, dyadic and dotted note types, rests, voices and ties through bar
boundaries. Logical note correspondence is verified after tie joining. Its
playback program is the explicit bass-preview program; it is not source timbre.
The original `preserved/performance-clock.musicxml` remains a distinct artifact.
Do not use GP5's quarter-note preview as the quantized rhythmic score.

## Result, safety and validation

`notation-trial.json` records source hash, artifacts, modes, completed/blocked/failed
status and zero model/separator calls. Exit codes: 0 complete; 2 optional derivative
blocked but preservation completed; 1 rejected/failed. Incomplete or unused mode
parameters are errors. Inputs are bounded to2MiB,32tracks,20,000messages,500notes,
600seconds and the existing unambiguous saved-MIDI contract. Existing output
directories are rejected; never reuse a trial directory.

Use only licensed local material. MuScriptor weights retain CC BY-NC 4.0 and the
accepted additional conditions; this feature does not distribute weights or
change their terms. Private MIDI/audio and model outputs must remain local unless
separately authorized. No reliable reference means no precision/recall/F1 claim.

NAJA synthetic validation preserved5 pitches. At120BPM with0.125-second grid,
0.130–0.480s became0.125–0.500s (start−5ms/end+20ms); 3 of5 events changed. Rest,
repeat and bar-crossing tie checks passed. Constrained hand-travel proxy changed
10→0 versus an infeasible legacy mapping under the selected movement bound.
Saved R001 preserved20 notes and original times; both legacy and planned travel
were0, so no movement improvement is claimed. Its musical clock remains unknown:
no real-song rhythmic quantization was applied. Neither case establishes audio
accuracy, generalization, or human playing comfort.
