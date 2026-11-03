# Product UI review

Issue #19 reorganizes the normal transcription workflow. At the base revision,
normal and experimental navigation had equal prominence, deployment notes were
always expanded, and result actions shared a block with raw diagnostics.

The shell now separates normal navigation from experimental routes. The audio
workflow has source selection, processing, and review/save steps, with distinct
pending, running, review, failure, cancellation, and completion states. Raw JSON
and environment details use native disclosures. Recovery IDs, rejection reasons,
source preservation, and unverified-accuracy warnings remain visible. Request,
submission, cancellation, and polling guards are unchanged.

## Run locally

From `frontend/`, use the existing dependency installation and run:

```sh
npm run dev -- --host 127.0.0.1 --port 4319 --strictPort
```

Open `http://127.0.0.1:4319` to inspect the UI. An unavailable API is shown as an
explicit service state. Do not select private or real recordings for this review.

For synthetic success and recovery previews, run the browser proof against that
server. It intercepts every API call and supplies generated fixtures. It does not
contact a backend, fetch model assets, or process real audio.

```sh
PLAYWRIGHT_MODULE=/path/to/installed/playwright-core/index.mjs \
CHROMIUM_PATH=/path/to/chromium \
UI_PROOF_OUT=/tmp/stem2tab-ui-proof \
node scripts/check_product_ui.mjs
```

Run this command from the repository root. `UI_ORIGIN` overrides the default
`http://127.0.0.1:4319`. Use an existing browser installation; this change adds no
dependencies. The managed cloud environment has Playwright at
`/opt/codex/cua_node/lib/node_modules/playwright-core/index.mjs` and Chromium at
`/usr/bin/chromium`.

The proof generates screenshots and `results.json` for desktop, 390px, and 320px
layouts, keyboard skip/disclosure/navigation controls, text contrast, success,
output review, 422 rejection, lost response, and same-ID recovery after reload.
It checks one POST per submission and no document overflow. It does not validate
transcription accuracy or musical playback.

## Verification

From `frontend/`:

```sh
npm test -- --run
npm run build
./node_modules/.bin/tsc --noEmit
```

The base had 219 passing tests. This change adds four navigation, disclosure,
status, focus, and download checks, for 223 passing tests. The browser proof uses
seven isolated contexts and asserts common text contrast of at least 4.5:1.

Score geometry, score cursor rendering, MIDI clock controls, and output-review
error handling are reviewed in their separately owned changes. The shared CSS
adds no SVG or score sizing/transform rules. The page wrapper class is opt-in.
