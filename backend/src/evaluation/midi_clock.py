"""Reject unsupported raw clock metadata before a MIDI library normalizes it."""
from __future__ import annotations

import mido


class UnsupportedMidiClockError(ValueError):
    """Safe public clock refusal; contains no user paths or discarded note data."""


def validate_fixed_storage_clock(raw: mido.MidiFile) -> None:
    """Accept a constant conductor tempo and absent/fixed4/4 storage meter only.

    pretty_midi may warn and ignore tempo/signature messages outside track0.
    Inspect every raw track before loading; do not let a warning turn into a
    successful but time-shifted derivative. Identical repeated clock declarations
    are harmless on track0; the source bytes are retained unchanged by callers.
    """
    tempo = 500000  # MIDI's default before the first explicit tempo.
    for track_index, track in enumerate(raw.tracks):
        tick = 0
        for message in track:
            tick += message.time
            if message.type not in {"set_tempo", "time_signature"}:
                continue
            if track_index != 0:
                raise UnsupportedMidiClockError(
                    "Tempo/time-signature metadata outside track0 is unsupported; "
                    "refusing silent normalization. Original MIDI is unchanged.")
            if message.type == "set_tempo":
                if message.tempo <= 0:
                    raise UnsupportedMidiClockError("MIDI tempo must be positive; original MIDI is unchanged.")
                if tick > 0 and message.tempo != tempo:
                    raise UnsupportedMidiClockError(
                        "Tempo changes are unsupported by this fixed-storage-clock trial; original MIDI is unchanged.")
                tempo = message.tempo
            elif (message.numerator, message.denominator) != (4, 4):
                raise UnsupportedMidiClockError(
                    "Changing or non4/4 source meter is unsupported by this storage-clock trial; original MIDI is unchanged.")
