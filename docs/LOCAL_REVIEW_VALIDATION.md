# Local review validation scope

Local listening previews and objective note-accuracy evaluation answer different questions. The completed local checks cover playback, switching, metadata delivery and saving feedback. Short-interval listening preferences and one confirmed initial-carry example do not establish general transcription accuracy or correct original onset times.

## JSON delivery failure

A stopped local API caused its Web proxy to return HTTP 500, text/plain, with an empty body. An unchecked Response.json() produced the reported syntax error. The static metadata files were valid. The exact trigger that terminated the old API was not established.

`frontend/src/utils/reviewFetch.ts` checks HTTP status, body availability, content type and JSON syntax. It returns unknown, then the normal `ListeningTrial` page validates its metadata schema. This caller integration preserves AbortSignal cancellation and rejects stale completions. Failed responses do not become empty successful metadata. Diagnostics contain status/size/type, not the response body. The helper does not start an API or supervise process lifetime, and it cannot prove saved feedback still exists.

Both the Web process and its read-only API must remain available while a local preview is in use. Earlier local process recovery is separate from this source change. No OS service, restart policy or guarantee against process failure is added here. Cloud browser error checks use synthetic HTTP responses and do not establish process supervision.

## Candidate limits

Amplitude decay remains opt-in and does not change pitches, onsets, offsets or the original MIDI. The trial half-life is a preview parameter, not a validated timing correction.

`backend/experiments/initial_carry/` retains the separate initial-carry experiment and synthetic contract script. It accepts validated worker-shaped inputs and retains only an explicit initial prologue tie with an explicit note-off in its first window. Its clipped onset is unknown in the original performance. Existing exported notes remain unchanged; retrigger-first and overlapping cases are refused. It is not connected to the normal worker, decoder or API. `frontend/experiments/initial-carry/` preserves the isolated four-second player and its synthetic Vitest tests. Neither experiment enables a product opening-note comparison UI.

Timing-shortening candidates that damaged previously correct offsets are not adopted. All17 IDMT recordings are already used and cannot be relabeled as independent holdouts. RWC annotations are not sufficiently verified for independent ground truth. Independent timing evaluation is therefore not run. The prospective audit CLI records this limitation and requires all17 known reused IDs before a real-data plan can freeze. Its small-screening criteria do not establish generalization or automatic adoption across songs.

Listening results suggest only a limited preference for amplitude decay in one reviewed interval, with little distinction elsewhere. Initial-carry playback was locally confirmed in one example, but its original onset remains unknown. These observations establish neither note-accuracy gains nor full integration of the private comparison page. Song-specific HTML, audio, MIDI artifacts, saved feedback and personal paths are not part of this source change. Decay stays opt-in; automatic note shortening and initial-carry completion remain disabled/unconnected.

## Reproduction

From frontend, run `npm test -- --run`, `node node_modules/typescript/bin/tsc --noEmit`, and `npm run build`. The JSON boundary suite retains the original eight cases and adds two cancellation cases. The ListeningTrial suite covers the actual helper caller, failed metadata, stale input and old audio decode completion.

From backend/experiments/initial_carry, `python -B test_carry_candidate.py` runs five groups of synthetic contract checks. From frontend, `npm test -- --run experiments/initial-carry/prefix-player.test.mjs` runs eight prototype transport cases, also included in the normal suite. No recordings, models, GPU, network calls or inference are required for these checks. They do not measure real-song accuracy. Audit commands and frozen protocol definitions are in [CORRECTION_AUDIT.md](evaluation/CORRECTION_AUDIT.md).

## Final source integration checks

The combined source passed350 backend tests,180 frontend tests across19 files,
TypeScript with zero diagnostics, and the production build. Eighteen synthetic
HTTP Chromium checks covered the existing preserved-MIDI/notation/playback flow,
opt-in amplitude decay, and empty HTTP500/HTML/truncated-JSON metadata followed
by recovery through the real local API. The existing one-second Basic Pitch
ONNX smoke used synthetic audio; no real-song or new GPU inference was run.
The initial-carry script passed five synthetic groups; the eight prototype
transport cases are already included in the frontend total. These software
checks are complete, while independent ground-truth quality evaluation and
product integration of initial-carry/private comparison prototypes remain open.
