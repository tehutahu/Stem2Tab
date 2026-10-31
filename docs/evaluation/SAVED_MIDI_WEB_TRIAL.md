# Experimental saved-MIDI Web trial

Open the existing app's **保存MIDIインポート（実験）** link or `/midi-trial`.
This route is optional; the existing audio/job/model defaults are unchanged.
It imports an already-saved local MIDI through `POST /api/v1/midi-trials`,
without model inference, source separation, Celery jobs or new model downloads.
Do not bundle MuScriptor weights. Their noncommercial terms remain applicable.

Choose source provenance explicitly (caller-declared, not independently verified),
bass4/bass5/custom tuning and maximum fret. Custom tuning uses 1–7 ascending
low-to-high MIDI pitches; maximum fret is 0–36. Bass4 is E1/A1/D2/G2
`28,33,38,43`; bass5 adds B0 `23`. There is no octave transposition.

The original MIDI download retains the exact uploaded bytes. MusicXML is a
separate note-event interchange using the MIDI storage clock, not an inferred
musical tempo/meter. The viewer synthesizes that MusicXML using a bundled local
soundfont; it does not play the original recording or the original MIDI file.
Velocity, timbre, bends and controllers are outside this XML scope. Parsed note
count excludes tie destinations and is not a count of SVG glyphs. Full storage
bars may add trailing rest time to the transport beyond the final note.

Optional GP5 is a **sequential quarter-note fingering preview**, not performed
rhythm. If one event is out of the selected string/fret range, all GP5 output is
blocked; the UI lists its index, pitch, time and reason. Original MIDI, canonical
events and MusicXML remain complete. GP5 has no rhythm-preservation claim.

Each accepted import gets a fresh UUID and preserves input and safe summary.
Limits: 2 MiB, 32 tracks, 20,000 MIDI messages, 2,000 notes, 600 seconds,
2,400 storage-quarter units and 32 simultaneous notes. Ambiguous/unsupported
MIDI is rejected rather than silently repaired. Files are served only through
allowlisted keys, with size/hash and symlink/containment checks. Export failure
returns a persisted safe HTTP500 summary; successful partial XML/GP5 is not
advertised. Changing input/settings clears results, aborts the request and
destroys playback; late responses cannot replace the new state.

## Local validation and remaining gate

Use the existing installed environments; do not install dependencies solely to
run these checks. Backend: all `test_evaluation_*.py`, `test_bass_output.py`,
`test_tab.py`, `test_midi_trials_api.py` and existing mocked worker/jobs/health
and integration smoke tests. Frontend: `npm test -- --run`, `npm run build`,
and `node node_modules/typescript/bin/tsc --noEmit`. No lint script is configured.

For the 2026-10-27 isolated trial, backend113 and frontend21 tests passed;
production build passed. Global typecheck still reports pre-existing legacy
viewer/import/test-global errors, and is not green. Node18 cannot start the
installed Vite7 dev optimizer (`crypto.hash is not a function`); production
preview of the generated build was used without changing dependencies.

The user's Chrome verified navigation/form/tuning/GP5 warning display.
Automated file selection was blocked by the Chrome extension's file-URL access
permission. No permission was changed, no alternate upload bypass was used,
and no HTTP import reached the running API. Actual score SVG, playback advance,
pause/stop/seek and re-input browser behavior remain unverified. Mocked lifecycle
tests are not actual audio or browser evidence. Complete this gate with a user
selected local MIDI before claiming the Web trial is ready for routine use.

Existing CLI/AlphaTab parser checks preserve57 saved MuScriptor notes across
four already-used RWC intervals, but do not establish actual browser rendering
or musical accuracy. RWC has no certified note reference; report no accuracy F1.

## Browser follow-up, 2026-10-27

The earlier file-selection gate above is historical. The user manually selected
the file in Chrome; import, 20 logical notes at MIDI 34..45, SVG, playback,
pause/seek/stop, blocked GP5 range and restored settings were verified. Original
MIDI and exact-time XML remained unchanged. The user subsequently confirmed
audible output. This is not approval of timbre, musical quality or transcription.

AlphaTab positioned cursor elements, but their default transparent backgrounds
made them invisible. `MidiTrialViewer.css` supplies a red beat cursor and a pale
blue bar background, scoped to `.midi-trial-score`; playback and note data are
unchanged. The existing file has 9.93 seconds of notes and a 12-second, three-bar
storage-clock XML display (trailing rests, not stretched note endings).

`/midi-trial/saved/<trial UUID>` reuses the persisted GET summary and artifact
URLs without a new upload or conversion. It hides the import form, validates a
canonical UUIDv4 and summary identity, and aborts stale requests. This allows a
separate verification tab without reloading a user's selected input. The
original import route and worker/model defaults remain unchanged.

Cursor follow-up: 27 frontend tests and production build passed. Global tsc has
the same 11 existing diagnostics as before; no new diagnostics in the changed
code. No lint script exists. Actual Chrome checks confirm visible red cursor,
blue bar highlight, seek to 2.40 seconds, playback into the next bar, and stop.
Detailed local evidence is in `muscriptor-trial/web-midi-trial-20261027/`
`cursor-fix-20261027/` under this task workspace. No RWC reference accuracy is
claimed. Sound program remains the XML import default (0); GP5 is still a
rhythm-free quarter-note fingering preview, not a faithful performance score.
