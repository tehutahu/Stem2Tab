# Audio tempo suggestions

The audio estimator proposes pulse interpretations. It does not set musical
clock fields, infer meter or a measure downbeat, create notation, change playback,
or edit MIDI. MIDI storage tempo and note attacks are not estimator inputs.
Existing manual-clock MusicXML and exact-preview checks are unchanged.

## Source contract

`GET /api/v1/tempo-estimates/sources` accepts caller-known `midi_trial_id` and
`transcription_job_id` UUID4 values. It verifies one association, without scanning
other jobs or estimating tempo. The returned `stored-selection` descriptor
contains both IDs, MIDI and audio SHA-256 values, encoded bytes, decoded duration,
rate, channels, WAV format, source selection offset, and correspondence type.

`POST /api/v1/tempo-estimates/stored` accepts the exact descriptor returned by GET.
It verifies the descriptor again, analyzes the saved original `selection.wav`,
and rechecks source hashes and correspondence before releasing a result. It does
not analyze a separated stem or rerun transcription. Analysis interval seconds
start at zero within this selection. `source_origin_seconds` identifies its
position in the original audio, not a musical downbeat.

Normal completed jobs require matching job ID and saved MIDI ID, completion
receipt, request selection, raw MIDI hash, saved original MIDI hash, audio hash,
selection frame count, rate, duration, and offset. The workflow's original-payload
digest binds original audio bytes to selection settings. Completed review
previews additionally require the source manifest identity, source job, preview
ID equal to saved MIDI ID, planned and completed counts, and preview MIDI hash.
Read-only inspection does not recover incomplete previews or rewrite receipts.
Legacy or changed sources without a provable association are refused.

`POST /api/v1/tempo-estimates/upload` accepts multipart field `file`, with a WAV or
FLAC filename and supported native content. Its `uploaded-audio` descriptor
records the file hash and metadata. Correspondence to any MIDI is explicitly
unverified. This provides an explicit fallback for independent MIDI imports and
sources without a verified workflow receipt. No decoder, model, dependency,
service, or asset is installed or downloaded.

## Signal method and response

`spectral-flux-autocorrelation-v1` resamples bounded decoded windows to 8 kHz,
using the existing NumPy, SciPy, and SoundFile dependencies. A 512-sample Hann
window and 80-sample hop produce spectral magnitudes. Stereo magnitudes are
combined per channel, so opposite-polarity audio does not cancel. Positive log
spectral flux, after local median subtraction, forms the transient envelope.
A raw flux-to-spectrum floor of 0.01 rejects tiny periodic FFT leakage from
stationary tones before normalization.

The method searches 40..240 BPM using normalized onset-envelope autocorrelation.
Audio transient intervals supply additional grid support to reduce missing-note
and syncopation aliases. The ranking combines 0.65 autocorrelation and 0.35 grid
support. At least five transient peaks and autocorrelation 0.22 are required in
a supporting local window. These are heuristic thresholds, not a calibrated
confidence test. Primary candidates are rounded to 0.1 BPM to avoid suggesting
false precision.

Short sources are fully covered by up to three equal intervals of at most twenty
seconds. Sources longer than sixty seconds use the first, centered, and last
twenty seconds. Each interval has at most two local windows, giving at most six
windows and sixty analyzed seconds. The response lists every analyzed interval
and local window. Unanalyzed gaps may contain changes or rhythms that the method
cannot detect.

Results have one of these statuses:

- `candidates`: enough local windows support periodic audio, and their primary
  BPM estimates agree within 8% of their median. At least two thirds of the
  available windows must support periodicity.
- `insufficient_evidence`: source duration is below eight seconds, or too few
  windows support periodicity. Silence, steady tones, weak or irregular evidence
  produce no selectable global candidate.
- `unstable`: supported primary BPM estimates disagree across windows. A
  secondary hypothesis cannot override this gate or create a tempo map.

The first measured family is `pulse-1`, relation `primary`. Up to one other
measured non-octave interpretation is `pulse-2`, relation `alternative`.
Its autocorrelation and supporting-window count come from real local peaks.
Each family also lists arithmetic half/double interpretations with null
periodicity and zero measured support. Values outside the manual 20..300 BPM
range have `adoptable=false`. The families are not exhaustive. Triplets and
syncopation can produce strong periodicity at a pulse different from the intended
musical beat, including a wrong non-octave interpretation.

All results label quality `uncalibrated`. Periodicity is an autocorrelation
statistic, not a probability or song-tempo accuracy measurement. No pulse phase,
meter, or measure origin is returned. Selecting a candidate remains an explicit
user edit of BPM and an evidence note; it does not make that BPM ground truth.

## Resource and refusal bounds

Uploaded encoded audio is limited to 32 MiB and 120 decoded seconds. Verified
stored selections are limited to 512 MiB and 600 seconds, matching the existing
workflow bounds. Both accept one or two channels at 8..96 kHz. Metadata is checked
before sample allocation. Native WAV container lengths are checked so truncated
files are not silently treated as shorter selections. Nonfinite, incomplete, or
unsupported decoded data is refused. Native WAV/FLAC decoding uses the existing
SoundFile dependency; no external transcoder is invoked. An existing verified
decoded workflow selection can still be used for sources originally imported in
other formats.

An independent file lock serializes tempo CPU work across threads and API
processes sharing the file bucket. It does not acquire or change GPU slots.
Concurrent requests receive HTTP 409 and require an explicit retry. The estimator
checks a fifteen-second cooperative elapsed budget between bounded local-window
operations. This is not a hard timeout or cancellation guarantee. Hash
verification and native decoder calls are also bounded by source sizes and
window geometry, not preemptively killed. Client request cancellation does not
claim to terminate already-running server CPU work.

## Reproduce synthetic checks

From `backend`, use the configured uv environment:

```sh
PYTHONPATH=$PWD uv run --no-sync python -B -m src.evaluation.audio_tempo_synthetic --output benchmark_results/audio-tempo-synthetic.json
PYTHONPATH=$PWD uv run --no-sync python -B -m pytest tests/unit -q -p no:cacheprovider
```

The generator creates audio in memory. It does not check in audio, binary MIDI,
or generated result files. Its 22 cases include known tempos, phase offset,
accents, eighth and triplet subdivisions, syncopation, missing attacks, noise,
antiphase stereo, silence, a drone, DC, short audio, irregular timing, and a tempo
change. In this implementation run, the primary estimate matched within 2.5% for
12 of 15 positive cases; its octave family matched 14; the complete candidate set
matched 15. All seven negative/control cases received the expected refusal or
unstable status.

An independent reviewer froze a separate thirty-case suite before executing the
estimator. The two-family prototype matched the known tempo within 3% in 17 of
18 periodic candidate sets, compared with 16 of 18 for the one-family prototype.
Exact primary coverage remained 14 of 18, and primary-octave coverage remained
16 of 18. All ten insufficient/unstable controls withheld selectable candidates;
one additional weak-noise case was refused. A syncopated 100 BPM case still
produced 133.4 BPM and 80 BPM families, missing the intended beat. This known
limitation remains explicit rather than forcing its expected answer. Supplemental
checks covered 8..12-second inputs, opposed and uneven stereo channels, DC,
clipped tones, and disclosed sampling on longer inputs.

These counts describe synthetic behavior, not accuracy on real songs. The suite
has no licensed real-song dataset or trusted real-song BPM labels. API regression
tests exercise actual saved transcription and completed review-preview receipts,
source and descriptor mutation, job substitution, symlinks, decoded expansion
bounds, CPU-lock release and busy behavior, and unchanged source artifacts.
