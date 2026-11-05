# Notation tempo with absolute time preserved

The saved-MIDI page has explicit original and double-clock links. The URL flag
`notation=double` requests a distinct MusicXML representation; omitting it keeps
the original. `view=tab` continues to use the validated saved bass tuning.

`GET /api/v1/midi-trials/{trial_id}/notation-clock/double.musicxml` verifies both
original MIDI and XML against the persisted manifest, reapplies bounded MIDI
preflight, and requires the original XML to match the exact tick exporter. It
produces a temporary derivative and returns bytes without modifying any saved
file. Changed/unsupported sources produce explicit errors, not normalization.

The exporter multiplies **both** integer note/rest timeline units and tempo by
two. The divisions per quarter stay fixed. Original complete-bar tail rests are
retained in seconds, so the number of 4/4 bars doubles. No boundary is moved onto
a rhythmic grid. This mode cannot be combined with `grid48`; tempo/meter changes
remain unsupported and rejected. The original MIDI is never rewritten.

For a 60 BPM source, the comparison candidate is 120 BPM: a half-second note
changes from half a quarter to one quarter, and 4/4 bars change from 4 seconds to
2 seconds. A 12-second original display changes from 3 to 6 bars, with the same
note-on/off seconds. A full page navigation stops the previous player and starts
the new view at zero; the viewer also destroys stale instances on source changes.

This is not musical tempo estimation. In the local MuScriptor adapter, 60 BPM
and PPQ 10000 are explicit seconds-to-MIDI serialization choices. The adapter
does not infer tempo or write a meter event. Exported 4/4 and origin zero are
notation assumptions, not verified musical bars/downbeats. The UI labels those
assumptions. Synth timbre/velocity/controller limitations remain unchanged.

GP5 export is **unsupported in this exact-time clock conversion mode**. The old
GP5 link remains labelled as sequential-quarter fingering preview. Explicit-grid
rhythmic GP5 is a separate quantized workflow and is not silently substituted.
