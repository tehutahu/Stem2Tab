# Local trial code handoff

This branch contains the completed optional local trials. It does not replace the
default Upload/Celery/Basic Pitch route. The saved-MIDI, notation and score-viewer
checks can run without MuScriptor weights, private recordings or a GPU. The audio
inference trial is unavailable until an operator supplies an existing consented
local deployment manifest. Do not obtain weights or accept terms to run tests.

## Validation

Use Python 3.11 or newer and the locked backend dependencies. Use Node 20.19 or
newer, or Node 22.12 or newer, for the Vite 7 toolchain. Dependency preparation
uses the repository's existing `uv sync --locked --dev` and `npm ci` procedures.
Basic Pitch's separate ONNX-only installation is described in `docs/TESTING.md`;
it is needed for the real Basic Pitch integration smoke, not model inference in
the mocked unit suite below. Do not install TensorFlow.

```bash
cd backend
uv run --no-sync pytest tests/unit -q -p no:cacheprovider
cd ../frontend
npm test -- --run
npm run build
node node_modules/typescript/bin/tsc --noEmit
```

On 2026-10-31, the existing local environment passed 291 backend unit tests and
124 frontend tests. The production build passed. Full TypeScript checking still
has 11 baseline errors in `AlphaTabViewer.tsx`, `main.tsx` and `Upload.test.tsx`.
The saved-MIDI trial and new synchronization files have no diagnostics. Report
the baseline failures; do not treat the full typecheck as passing. There is no
configured lint script. The backend reports a FastAPI `on_event` deprecation.

All new tests create synthetic input in temporary directories or use mocks.
No private audio, annotations, derived MIDI, model weights, runtime deployment
manifests or browser screenshots are included in this branch.

## Run the saved-MIDI UI locally in a cloud checkout

Use a new disposable directory for generated trial files. Start the API and
frontend in separate terminals, with the backend dependencies already prepared:

```bash
# Terminal 1, from backend. The directory must be local to this environment.
FILE_BUCKET_PATH=/tmp/stem2tab-cloud-trial \
  uv run --no-sync uvicorn src.api.main:app --host 127.0.0.1 --port 8290

# Terminal 2, from frontend, after npm run build.
API_PROXY_TARGET=http://127.0.0.1:8290 \
  npm run preview -- --host 127.0.0.1 --port 4290 --strictPort
```

Open `/midi-trial` on the frontend using the environment's approved browser
connection. Select a synthetic single-instrument bass MIDI and choose the bass
tuning explicitly. Do not copy private local recordings or saved results into
the cloud. These routes do not require a Celery worker or Redis for saved-MIDI
conversion and playback. Do not submit the default audio job route as part of
this no-model check.

An accepted import returns a fresh result ID. The saved route is
`/midi-trial/saved/<id>`. Add `?view=tab&notation=double&spacing=time` for the
original-time staff/TAB comparison with a doubled notation clock and proportional
spacing. Omit `notation=double` to use the original clock. Omit `spacing=time` to
compare normal engraving. The doubled clock is not an estimated song tempo.

Check barlines, bass clefs, rests, ties, note/TAB alignment, row transitions,
pause/resume/stop/seek and repeated display changes. The installed alphaTab 1.7.1
renderer supplies all musical symbols. The guarded layout adapter changes only
column positions, at 360 px per second with one bar per system. Internal renderer
changes require a new integration check. Raw unquantized event times are retained;
the source duration symbols are not an exact conventional notation of every
fractional duration. Unsupported layouts fail explicitly.

The audio model route and its offline asset verification are described in
`MUSCRIPTOR_OPT_IN_TRIAL.md`. MuScriptor small weights have CC BY-NC 4.0 plus
provider conditions and must not be bundled. Successful UI transport or the
earlier subjective local listening result does not establish transcription
accuracy. The local real recordings have no independently verified reference.

## Git and CI scope

The working branch is `codex/gp5-note-preservation-20261027`, based on
`3d1f730213eb0101058b105b5e04351d20847c9f`. It remains separate from main.
The existing CI workflow runs on pull requests and pushes to main, develop,
feature/** and fix/**. A push to this codex branch alone does not trigger it.
Do not report absent runs as passing CI. No merge, deployment, default-route
change or workflow-permission change is part of this handoff.
