# Local compressed audio input

The MuScriptor product entry accepts WAV, FLAC, M4A containing AAC/ALAC, and
Ogg containing Opus under `.opus` or `.ogg`. Ogg/Vorbis and raw, uncontainerized
Opus are not accepted by this route. The older 10-second experimental route
retains its existing WAV/FLAC limits.

Uploads remain bounded to 512 MiB and selected decoded audio to 600 seconds,
one or two channels at 8–96 kHz. Existing local `ffmpeg` and `ffprobe` are required
for the compressed formats. Missing tools fail explicitly; nothing is installed
or downloaded automatically. There is no remote conversion or paid API.

Original encoded bytes are retained and offered for download with the accepted
original extension. Conversion produces a separate float32 WAV selection before
the existing separation/transcription path. Retry keeps the pinned original and
selection hashes. No musical note, tempo, offset or model correction is added.

FFmpeg handles container codec delay/skip metadata. Selection uses decoded PCM
sample counts; no compressed-packet seeking or resampling to another rate is
introduced. Container duration can differ from the actual decoded PCM duration,
which is recorded separately. Lossy encoding is not reversed. A selected range
must decode completely; a too-long full selection is rejected rather than
silently truncated.

The decoder uses private local temporary files, an explicit local-file protocol
whitelist, disabled MOV external data references, one CPU decode slot, probe and
decode timeouts, and a decoded-output byte cap. Long leading material can exceed
the bounded decode timeout; use a shorter source or a prepared WAV/FLAC in that
case. Codec failures never select Basic Pitch or launch inference.

`test_local_audio_input.py` creates synthetic AAC/ALAC/Opus locally and checks
actual decoding, sample-exact cropping relative to the full decoded signal,
container/codec rejection, resource limits and API retention/download before
separation. These checks do not establish transcription accuracy.

An HTTP 422 response rejects the request before a job is accepted. The product
page displays the API's public error code/message or the affected FastAPI form
field. It keeps the selected input available for an explicit corrected request;
it does not silently crop, convert a refusal into success, or resubmit a job.
Malformed or missing error details remain a visible HTTP rejection. Network and
5xx outcomes remain uncertain and retain the original request ID for a GET status
check. Do not infer an API outage from a 422 response.

A failed command identifies its stage (`ffprobe` metadata read or `ffmpeg` PCM
conversion), exit status and diagnostic ID. The bounded, path/URL-redacted command
error is written only to the local service log with that ID. HTTP responses do
not include raw stderr or infer file damage, encryption or DRM from a nonzero
exit status. Original failed-command text from versions that discarded stderr
cannot be recovered retrospectively.

Zero-byte files are rejected as `empty_audio` before model checks or any decoder
command. The product page identifies an empty selection immediately and blocks
submission until a nonempty input is selected. No original is changed or replaced.
