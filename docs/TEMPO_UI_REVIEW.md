# Audio BPM suggestion UI review

Issue #25 adds explicit audio analysis alongside the existing manual musical
clock. The estimator owns audio selection, request state, and report validation.
MusicalClockSettings retains ownership of manual BPM, meter, origin, exact
MusicXML generation, and the currently selected derivative.

## Source and adoption contract

A `tempo_source_job` query value is an untrusted hint carried from the completed
transcription or review-preview link. It triggers no request. The user first
selects stored audio and asks the API to verify its association with the exact
saved MIDI ID and SHA-256. Only that verified descriptor can be submitted for
analysis; the API verifies it again. The analyzed artifact is the original
`selection.wav`, not the separated bass.

Standalone WAV/FLAC upload is a separate explicit choice. The browser checks size,
computes SHA-256, and binds the response to those bytes and format. The interface
states that correspondence between uploaded audio and MIDI is unverified.

Responses are parsed at the network boundary. Source IDs/hashes/metadata,
method, intervals, coverage, window limits, result status, measured pulse
families, arithmetic octave interpretations, quality, and adoption bounds are
checked before any candidate is shown. Insufficient and unstable results have
no selectable candidates. Reports expose source hashes, analyzed windows,
uncalibrated quality, limitations, and a separate JSON download.

Candidate selection edits only BPM and its evidence note. If either is already
filled, a focused replacement panel requires an explicit replacement action.
Subsequent edits to BPM or its evidence note invalidate that pending action.
Meter and origin remain as entered. Neither estimation nor adoption creates a derivative, invokes onSelect,
resets playback, changes score layout, or changes the old derivative/downloads.
Changing only the source-job query invalidates estimates while preserving manual
settings and the old derivative. Reload restores no candidate and starts no
analysis. Cancellation stops waiting and rejects late responses; the API may
continue its bounded CPU operation.

The list can contain two measured pulse hypotheses plus arithmetic half/double
interpretations. It is not exhaustive, probability-ranked, or guaranteed to
contain the musical quarter-note BPM. Strong periodicity can still select the
wrong rhythmic interpretation. There is no inferred meter or measure downbeat.

## Rerun frontend checks

From `frontend/`:

```sh
npm test -- --run
npm run build
./node_modules/.bin/tsc --noEmit
npm run preview -- --host 127.0.0.1 --port 4315 --strictPort
```

Use a built preview for actual alphaTab playback. In another terminal, from the
repository root:

```sh
PLAYWRIGHT_MODULE=/path/to/installed/playwright-core/index.mjs \
CHROMIUM_PATH=/path/to/chromium \
UI_PROOF_OUT=/tmp/stem2tab-tempo-ui-proof \
node scripts/check_tempo_ui.mjs
```

The managed environment already provides Playwright at
`/opt/codex/cua_node/lib/node_modules/playwright-core/index.mjs` and Chromium at
`/usr/bin/chromium`. `UI_ORIGIN` overrides the default localhost port. No new
dependencies are required.

The proof intercepts all API requests with synthetic fixtures and uses the real
built React app, checked-in exporter-generated MusicXML, and alphaTab renderer
and player. It checks an active exact derivative paused at two seconds through
estimation and adoption, preserving SDK note ticks, tempo, playback speed,
position, layout, and the old XML URL. It also checks manual replacement,
source-query changes, network recovery, upload hashing, insufficient/unstable
results, wrong-source rejection, no estimation on reload, and 320/390/1280px
layouts. It writes screenshots and results outside the checkout.

These browser checks validate frontend behavior with controlled responses.
Backend audio estimation quality and source verification require the separate
backend tests and independent synthetic audio evaluation. Synthetic UI success
is not evidence of real-song BPM accuracy.
