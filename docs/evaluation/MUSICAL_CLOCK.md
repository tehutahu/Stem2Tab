# Explicit musical clocks

The saved MIDI clock is a storage coordinate system. Its tempo and 4/4 meter do
not establish the song's pulse, meter, or downbeat. The historical doubled
storage-clock URL remains readable, but the UI no longer offers it as a measured
musical BPM candidate.

Saved MIDI results now expose blank, explicit musical settings. Enter a
quarter-note BPM, meter, and downbeat origin in source seconds. An optional note
records the reason for those choices. Creating a version returns separate
MusicXML and JSON downloads; it does not overwrite or quantize the source MIDI,
canonical notes, or storage-clock XML. The derivative is held in the browser
until downloaded and is not saved to the server trial.

The report records `provenance: explicit-user-input`, `estimation_status:
not-estimated`, and `confidence: null`. Half/double BPM values describe unresolved
interpretations of the entered pulse, not measured candidates. No audio analysis,
automatic beat estimation, meter inference, variable-tempo detection, or tempo
map generation runs. Empty MIDI, sustained notes, regular attacks and irregular
or changing attack intervals all retain that unestimated status. A fixed clock
entered for a changing performance is a user-selected notation assumption.
These synthetic tests do not establish tempo accuracy on real recordings.

## API and preservation

`POST /api/v1/musical-clock/{saved_midi_trial_id}/derive` takes:

```json
{
  "input_sha256": "<saved source hash>",
  "clock": {
    "bpm": 120,
    "numerator": 3,
    "denominator": 4,
    "origin_seconds": 0.25,
    "evidence": "Optional user context",
    "provenance": "explicit-user-input"
  }
}
```

The response contains `musicxml`, its SHA-256, and a typed `report`. The endpoint
hash-verifies the saved original, repeats the strict bounded MIDI preflight,
and performs its work in a temporary directory. Existing source artifacts are
never written. MIDI storage tempo changes remain unsupported by that preflight;
performed rhythm can vary freely without changing the storage clock.

The exporter represents source tick seconds and the declared musical clock as
rational numbers. It chooses exact integer divisions rather than rounding event
boundaries. Bars extend backward and forward from the specified downbeat;
material before that origin remains at its original seconds. Partial first and
last bars retain the full storage-clock XML horizon, including trailing rests.
MusicXML retains loaded pitches and note boundaries. Original velocities,
controllers, bends and timbre remain in the unchanged MIDI and are not reproduced
by the XML synthetic playback.

Limits are quarter BPM 20..300 with up to three decimal places; meter numerator
1..12 and denominator 2, 4, 8, or 16; origin 0..600 seconds with up to six decimal
places; divisions up to 1,000,000,000; total duration units up to 9,000,000,000,000;
and at most 4,000 measures. Precision combinations that exceed those limits are
rejected explicitly. There is no rounding fallback.

## Browser preview limits

Exact downloads support more clocks than the current renderer. alphaTab uses
integer BPM and a 960-tick quarter. Preview eligibility requires every attack,
release, bar boundary and the final tail to fall exactly on that renderer clock.
Unsupported settings still produce exact XML/report downloads, with preview
withheld. Eligible musical versions use ordinary engraving; time-proportional
layout remains available on the original storage-clock view.

On import, a musical-version-only adapter recomputes cumulative master-bar
starts. alphaTab's importer assigns a zero advance after pickups, which otherwise
shortens a tie crossing the first partial bar. After that correction the adapter
generates actual renderer MIDI and compares all note-on/off ticks and the final
track horizon against the source-derived proof. A mismatch stops preview rather
than enabling altered playback. The original MIDI and downloaded XML are unchanged.

This exact preview requirement excludes many unquantized inputs. A deliberately
small synthetic sample containing six patterns accepted 2/6 at each of 60, 96 and
120 BPM: quarter/eighth-aligned patterns passed, while examples containing
0.13/0.48-second boundaries or 100-microsecond boundaries failed the 960-tick
grid. At 123.5 BPM all six were download-only because the SDK uses integer BPM.
Those counts describe renderer capability, not transcription quality or a
representative dataset. No relaxed tolerance or automatic quantization is used.

## Verification

Run backend checks from `backend` using the configured uv environment:

```sh
PYTHONPATH=$PWD uv run --no-sync python -m pytest tests/unit/test_evaluation_*.py tests/unit/test_musical_clock_api.py tests/unit/test_midi_trials_api.py tests/unit/test_notation_trials_api.py -q
```

Run `npm test -- --run`, `npm run build`, and `npx tsc --noEmit` in `frontend`.
The three small text MusicXML fixtures under
`frontend/src/test/fixtures/musical-clock/` are generated from synthetic MIDI.
A backend test checks that they equal actual exporter output. Frontend tests
import them with the real SDK and compare generated MIDI event seconds and the
8-second tail for 4/4, 3/4 with a pickup, and 6/8 with a pickup, including
polyphony and ties. Independent Fraction-based XML readback covers fractional
BPM, nonzero origins, short/repeated notes, overlaps, rests, and unchanged source
bytes. API tests exercise the actual saved-import path and reject changed hashes,
changed bytes and symlinks.

A Chromium smoke check in this task used synthetic MIDI only. It exercised saved
import, explicit 120 BPM / 3/4 / origin 0.25 seconds, derivative selection,
play/pause, seek to 3 seconds and stop to 0 seconds. The tail stayed 8 seconds,
source bytes were unchanged, and no page errors or playback alerts occurred.
This is automated browser evidence, not the user's local visual or listening
acceptance check.
