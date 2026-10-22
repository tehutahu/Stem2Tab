"""Reference-free assembly of voiced F0 frames into performed note events."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
from numpy.typing import ArrayLike
from pydantic import BaseModel, ConfigDict, Field

from src.evaluation.models import NoteEvent, NoteEventSet


class AssemblyConfig(BaseModel):
    """Fixed frame-stability and independent-onset rules for note assembly."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    min_pitch_run_frames: int = Field(default=2, ge=1)
    min_onset_separation_seconds: float = Field(default=0.05, ge=0.0)


def _one_dimensional(value: ArrayLike, name: str, *, boolean: bool = False) -> np.ndarray:
    array = np.asarray(value)
    if array.ndim != 1:
        raise ValueError(f"{name} must be one-dimensional")
    if boolean:
        if array.size and array.dtype.kind != "b":
            raise ValueError(f"{name} must contain booleans")
        return array.astype(bool)
    try:
        return array.astype(float)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must contain numeric values") from exc


def assemble_notes(
    f0: ArrayLike,
    voiced: ArrayLike,
    times: ArrayLike,
    audio_duration: float,
    *,
    active: ArrayLike | None = None,
    onset_times: Sequence[float] = (),
    config: AssemblyConfig = AssemblyConfig(),
) -> NoteEventSet:
    """Assemble stable frame runs, preserving gaps and independent re-pick onsets.

    Frame i covers [times[i], times[i+1]); the final frame ends at audio duration.
    F0 is rounded to nearest MIDI (ties to even). Invalid/nonpositive frequencies
    and out-of-MIDI-range pitches form gaps. No gap is bridged. One-frame interior
    pitch excursions are replaced left-to-right only when both neighbors agree
    and no independent onset touches that frame. Short remaining runs are dropped.
    Onsets split at their supplied times only when both pieces retain the minimum
    number of frame starts and each piece meets the onset separation duration.
    """
    frequency = _one_dimensional(f0, "f0")
    voice_mask = _one_dimensional(voiced, "voiced", boolean=True)
    frame_times = _one_dimensional(times, "times")
    activity = (np.ones(len(frequency), dtype=bool) if active is None
                else _one_dimensional(active, "active", boolean=True))
    if not (len(frequency) == len(voice_mask) == len(frame_times) == len(activity)):
        raise ValueError("f0, voiced, times, and active must have equal lengths")
    if not np.isfinite(audio_duration) or audio_duration <= 0:
        raise ValueError("audio_duration must be finite and positive")
    if (not np.all(np.isfinite(frame_times)) or np.any(frame_times < 0)
            or np.any(np.diff(frame_times) <= 0)):
        raise ValueError("times must be finite, nonnegative, and strictly increasing")
    onsets = _one_dimensional(onset_times, "onset_times")
    if (not np.all(np.isfinite(onsets)) or np.any(onsets < 0)
            or np.any(onsets > audio_duration)):
        raise ValueError("onset_times must be finite and within the audio duration")
    onsets = np.unique(onsets)
    valid = (voice_mask & activity & np.isfinite(frequency) & (frequency > 0)
             & (frame_times < audio_duration))
    pitches = np.full(len(frequency), -1, dtype=int)
    # Convert valid frequencies only, avoiding log warnings from unvoiced NaNs.
    # Match librosa.hz_to_midi arithmetic so half-semitone rounding agrees
    # with the raw pYIN adapter, including floating-point boundary behavior.
    midi_values = np.rint(12 * (np.log2(frequency[valid]) - np.log2(440.0)) + 69)
    indices = np.flatnonzero(valid)
    in_range = (midi_values >= 0) & (midi_values <= 127)
    pitches[indices[in_range]] = midi_values[in_range].astype(int)

    if config.min_pitch_run_frames > 1:
        for i in range(1, len(pitches) - 1):
            if (pitches[i] >= 0 and pitches[i - 1] >= 0
                    and pitches[i - 1] == pitches[i + 1] and pitches[i] != pitches[i - 1]
                    and not np.any((onsets >= frame_times[i]) & (onsets <= frame_times[i + 1]))):
                pitches[i] = pitches[i - 1]

    notes = []
    i = 0
    while i < len(pitches):
        pitch = pitches[i]
        end_index = i + 1
        while end_index < len(pitches) and pitches[end_index] == pitch:
            end_index += 1
        if pitch >= 0 and end_index - i >= config.min_pitch_run_frames:
            start = float(frame_times[i])
            end = min(float(frame_times[end_index]) if end_index < len(frame_times)
                      else float(audio_duration), float(audio_duration))
            piece_index = i
            for onset in onsets[(onsets > start) & (onsets < end)]:
                split_index = int(np.searchsorted(frame_times, onset, side="left"))
                if (onset - start + 1e-12 >= config.min_onset_separation_seconds
                        and end - onset + 1e-12 >= config.min_onset_separation_seconds
                        and split_index - piece_index >= config.min_pitch_run_frames
                        and end_index - split_index >= config.min_pitch_run_frames):
                    notes.append(NoteEvent(start=start, end=float(onset), midi=int(pitch), velocity=80 / 127))
                    start = float(onset)
                    piece_index = split_index
            if end > start:
                notes.append(NoteEvent(start=start, end=end, midi=int(pitch), velocity=80 / 127))
        i = end_index
    return NoteEventSet(source={"kind": "estimate", "assembly": "stable_frame_runs_with_onsets"},
                        notes=tuple(notes))
