# Offline initial-carry candidate

This experiment is not imported by the worker, decoder, API or default pipeline.
It examines validated worker-shaped note/token dictionaries, retaining only a
first-prologue bass tie explicitly closed in the first chunk. Existing notes
remain unchanged. Onset0 means left-censored at the clip boundary, with the real
attack unknown. The model's declaration is not an independent reference label.

Run `python -B test_carry_candidate.py` from this directory for five synthetic
contract groups. No recordings, MIDI artifacts, reference annotations, models or
inference are included. These tests do not establish real-song precision.

The separate `frontend/experiments/initial-carry/prefix-player.js` is an isolated
four-second playback prototype. Its Vitest tests use synthetic audio-clock objects.
Neither that player nor song-specific comparison/feedback HTML is connected to
the product. The normal listening route continues to offer only saved packet
tracks, with amplitude decay opt-in and no automatic initial-note completion.

Future quality evaluation must keep the raw baseline, use the prospective
`correction_audit` protocol with all17 previously used recordings excluded from
holdouts, and verify independent references and synchronization first.
