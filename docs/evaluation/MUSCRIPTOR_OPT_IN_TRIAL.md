# Experimental local audio trial

The existing Upload/Celery/Basic Pitch path remains the default. `/muscriptor-trial`
is a separate opt-in page for a maximum 10-second, 10-MiB mono/stereo WAV or FLAC
clip (8–96 kHz). It is a functional integration trial, not an approved accuracy
upgrade. Existing RWC examples have no independently verified reference notes;
R001 received favorable subjective listening feedback, which does not certify
the other examples or establish generalization.

## Deployment and execution

By default the route is unavailable. A local Linux/WSL API process may set
`STEM2TAB_MUSCRIPTOR_DEPLOYMENT` to a **server-owned**, non-public JSON file. It
must specify `schema_version: 1`, already confirmed `terms_accepted: true` and
`noncommercial: true`, and these existing local assets:

- `source_dir`, `source_commit`: official MuScriptor source at
  `8a1ef88672cc23c26619adaff37ec48d82c0b906`, clean and unchanged.
- `model_path`: `model.safetensors`, small revision
  `8c127f603b807520fa465c838e9bfee8a91ada4e`.
- `model_sha256`: `bbd482c786b895cf7d8f44185073d951adae2ebb8a66f82ca84cd1f84569549c`.
- `config_sha256`: adjacent mandatory `config.json`,
  `3008fc481e4a1cd978e337eb3759260c270892204db5039235ac939e1f42aeb2`.
- `demucs_checkpoint`: existing `955717e8-8726e21a.th`;
  `demucs_sha256`: `8726e21a993978c7ba086d3872e7608d7d5bfca646ca4aca459ffda844faa8b4`.
- `demucs_python`, `muscriptor_python`: absolute paths to the existing dedicated
  executable runtimes. No dependency installation is performed by this route.

The manifest records a prior operator decision; neither the API nor the browser
accepts provider terms, creates credentials or obtains weights. The small weights
remain subject to CC BY-NC 4.0 and additional provider terms. Do not bundle or
redistribute weights or private audio/MIDI. This route is intended for the local
loopback trial server, not an internet-facing service.

Availability checks local structure/existence, not CUDA usability or inference
success. Full asset/source verification is repeated in each worker before and
after inference. A mismatch fails explicitly. Client form fields cannot supply
asset paths, commands, extra models or configuration files.

Each fresh UUID run has its own directory under `FILE_BUCKET_PATH/muscriptor-trials`.
Subprocesses use isolated output caches, offline flags, denied network socket
operations, and no implicit Hugging Face token use. The existing shared environment
and caches are not changed. htdemucs loads only the exact hash-pinned existing
checkpoint; its legacy pickle loader is permitted only for that verified file.
Only the unique `bass` source is accepted. Missing bass never falls back to another
stem. Separation uses seed 0, one shift, overlap 0.25 and the model's segment size;
WAV FLOAT export restores the original scale without clipping/rescaling.

MuScriptor uses verified local safetensors/config, CUDA device 0, float32 weights
with official CUDA autocast, greedy decoding, batch 1 and the two bass conditioning
labels `acoustic_bass`/`electric_bass` (representative programs 32/33). It never
loads medium/large, a companion model, beat detector or remote checkpoint.

## State and retry semantics

`GET /api/v1/muscriptor-trials/availability` returns readiness reasons.
`POST /api/v1/muscriptor-trials` requires an explicit `method=muscriptor-small`,
`rights_confirmed=true`, canonical UUID4 `request_id`, file, bass profile and
optional tuning/fret/GP5-preview fields. Its 202 response uses `job_id=request_id`.
`GET /api/v1/muscriptor-trials/{job_id}` returns running/completed/failed state.

An identical request ID plus input/config hash returns its existing state; it
does not run twice. Different input/config with the same ID is rejected. The UI
persists a pending ID in session storage. An uncertain POST response or a recovery
404 keeps the form locked and offers only another GET status check. Confirmed
terminal states stop polling. There is **no user cancellation API**: leaving the
page does not cancel a run. Graceful server shutdown terminates only its owned
children. Children inherit the execution lock and have a Linux parent-death guard;
interrupted state is marked without automatic rerun.

One local trial at a time is allowed by an OS flock. This is not a global GPU
scheduler for unrelated applications. Each stage is supervised with a 600-second
wall limit and 12-GiB sampled process-RAM limit. Child processes exit before the
next stage; resource measurements are recorded locally. An operational failure
preserves the input and available raw outputs rather than silently retrying.

## Artifact meaning and limitations

Original input, exact separated bass, raw decoded notes/events/token stream,
Performance MIDI, configuration, hashes and per-stage records remain local.
The raw Performance MIDI uses 10,000 ticks/second, at most 50 microseconds of tick
rounding, velocity 100 because decoded events expose no magnitude, and the model's
instrument programs. It is not beat-quantized Score MIDI. The strict saved-MIDI
converter can reject ambiguous/unsupported note events rather than repair them.

Successful conversion links to `/midi-trial/saved/{id}`. Its MusicXML preserves
loaded pitch/onset/end on a storage clock; displayed bars/tempo are not verified
musical meter or tempo. Velocity, bends, controllers and instrument timbre are
outside the viewer's fidelity claim. Optional GP5 is a sequential **quarter-note
fingering preview**, without performed rhythm/overlap fidelity. Out-of-range notes
block the complete GP5, remain in raw/MIDI/XML artifacts, and are explicitly listed;
there is no octave transposition or silent dropping.

For a local real-clip integration check, separate the exact supplied 10-second mix.
This has different separator context from a 10-second crop of an earlier 40-second
stem. Do not compare those outputs as if their separation context were identical.
Keep raw note correctness, interchange fidelity, rendered positions, audible output
and subjective musical quality as separate claims.
