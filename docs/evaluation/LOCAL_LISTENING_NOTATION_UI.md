# Local listening and notation trial

This opt-in route reuses completed local outputs. It does not run separation or
transcription, infer tempo/meter, change the default pipeline, or redistribute
MuScriptor weights. RWC has no independently verified reference notes: listening
observations are not note accuracy, and no F1/rank/generalization claim is made.

## Listening

`/listening-trial/:trialId` loads a server-created packet with original mix,
separated bass and additive resynthesis of the **saved Performance MIDI**.
The packet validates the completed source job, hashes, sample alignment and
duration (maximum10.01 seconds). Audio stays local. Resynthesis uses fixed timbre,
no loudness normalization, and at most half a sample of boundary rounding.
It is not a reconstruction of source timbre, dynamics, bends or controllers.

All available buffers are fully loaded and hash-checked before playback. Source
switching preserves the current source-second position; a single Web Audio
source owns playback. Loops/endpoints use the audio clock, while the displayed
position refreshes approximately every40ms. Epoch guards discard stale resume
and ended callbacks. Interval editing pauses, Apply resets to the interval start,
and Stop returns there. Minimum interval50ms. Navigation destroys the old player.

### Optional amplitude-decay audition

The legacy `synth.wav` has constant amplitude between its existing 2ms edge fades.
A new packet can additionally include `synth-decay.wav`, explicitly selected by
the `採譜再生（減衰あり・試聴のみ）` button. Both variants use the same fixed additive
tone, sample rate, per-note attack/release fades and original note boundaries.
Only the extra track multiplies each note by `2 ** (-note_age_seconds / 0.35)`.
This fixed audition policy, `amplitude-half-life-v1`, halves amplitude every0.35s.
Overlapping notes decay independently; a repeated attack starts its own envelope.
No threshold shortens a note, no release extends beyond its original offset, and
leading/trailing silence stays aligned. Seek/resume uses the existing rendered
sample offset, so it does not restart a note's envelope. No loudness matching or
normalization is added. The envelope does not estimate an acoustic instrument.

The builder's default remains the original three-track packet. To opt in, call
the existing helper with `include_decay_preview=True` and a **new UUID4** output:

```python
from pathlib import Path
from uuid import uuid4
from src.evaluation.listening_packet import prepare_listening_packet

prepare_listening_packet(
    completed_job_dir, Path(file_bucket) / "listening-trials" / str(uuid4()),
    label="Local audition", include_decay_preview=True,
)
```

This reads existing completed outputs without inference. It does not modify or
overwrite the source job or existing packets. Older packets still open with the
original three buttons; they do not gain decay automatically. The fourth track
requires matching policy metadata, filename and hash; unavailable or changed
audio fails explicitly. Original mix remains the initially selected track.
Saved MIDI, TAB, MusicXML, GP5 and note metrics are unchanged. Reduced sustained
loudness is not corrected transcription duration or a note-accuracy improvement.
Cloud checks use synthetic notes; real-song listening and instrument playability
remain unverified. The user's earlier listening approval did not identify which
duration/envelope candidate was heard, so it does not validate this exact policy.

## Explicit derivatives

`/notation-trial/:midiTrialId` (also linked from saved-MIDI and listening results)
requires an explicit tuning/fret selection. Optional constrained fingering uses
the existing bounded joint-assignment planner. Pitches and original note events
are unchanged; no octave repair or partial output is allowed. Movement counts
are a discrete hand-window proxy, not evidence of improved human playability.

Optional quantization requires quarter-note BPM, numerator/denominator, source
origin seconds, subdivisions per quarter, and maximum boundary shift. No musical
clock values are prefilled. Every onset/end delta and refusal is available in the
UI and JSON. Collapsed notes, merged attacks and changed overlap topology block
the derived score. Original MIDI bytes and storage-clock XML are retained.

POST `/api/v1/notation-trials` accepts server-owned saved-MIDI UUID + expected
hash, never arbitrary input paths. A client-generated UUID reserves one bounded
CPU operation. Repeated identical requests read the persisted result; different
settings with the same ID fail409. UI stores the ID before POST, locks repeat
clicks and recovers by GET after interruption. A known422 permits editing because
validation ran before execution. It does not retry uncertain POSTs. Running work
cannot currently be cancelled. A process crash may leave a `running` record;
inspect it, do not automatically regenerate it. There is no endless polling.
To create another derivative, explicitly select the new-version button; old
directories remain. Current input bounds:2MiB/32tracks/20k messages/500notes/600s.

Raw MIDI clock metadata is inspected **before** pretty_midi normalization.
Input tempo changes, non4/4/changing source meter, and tempo/time-signature
messages on nonconductor tracks are explicitly refused. The latter must not
become a library warning followed by a silently fixed clock. Uploads return
`unsupported_midi_clock` (422) before creating a normalized copy; local CLI
sources remain byte-for-byte unchanged. Previously stored MIDI is checked again
when creating a derivative and records a terminal explicit clock error if needed.
Repeated identical conductor declarations remain valid. This input restriction
does not change the separate explicit output meter selected by the user.

## GP5 modes and exact scope

- `quarter-preview` remains the old sequential-quarter fingering preview. It
  does not preserve rhythm, rests or overlaps and is labelled accordingly.
- `explicit-rhythm` uses the complete explicit rhythm and fingering plans.
  Fixed **integer** BPM and fixed meter are supported; durations down to1/32,
  dotted values, rests, staggered chords, repeated attacks, ties and bar boundaries
  are serialized. Ties join only contiguous continuations on the same string.
  The derived score begins at the explicit origin; add that origin to align it
  with source audio. This is not unchanged original timing when deltas are nonzero.
- GP5 is parsed back with PyGuitarPro and all logical pitches, attacks/releases,
  multiplicities, strings/frets, tempo, meter and measure lengths are compared.
  A failed check suppresses the GP5 download. MusicXML/originals remain available.
- GP5 dynamic levels are rounded explicitly; bass program33 is a preview timbre.
  Fractional BPM is refused by GP5, with explicit MusicXML still available.
  Changing tempo/meter, tuplets, swing and pickups before origin are unsupported;
  unexpected configuration fields are rejected, never silently reduced to4/4.
- AlphaTab reads GP5 with treble clef by default. This explicitly selected bass
  GP5 view sets the displayed clef to F4 and disables the importer's default
  display-only octave transposition, leaving sounding pitches and TAB unchanged.
  It deliberately shows sounding pitch rather than conventional octave-up bass
  notation. Other GP5 applications' clef/octave presentation is not verified.

Tests synthesize all musical examples locally. No private audio, annotations,
weights or binary fixtures belong in the repository. Browser success establishes
UI rendering/transport behavior; it is separate from human listening approval.
