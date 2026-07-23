"""Dependency-free audio previews for canonical note-event artifacts."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import soundfile as sf

from src.evaluation.models import NoteEventSet

DEFAULT_SAMPLE_RATE = 22_050
MIN_PREVIEW_SECONDS = 1.0
PREVIEW_TAIL_SECONDS = 0.25
ATTACK_SECONDS = 0.005
RELEASE_SECONDS = 0.04


def write_preview_wav(
    events: NoteEventSet,
    path: Path,
    *,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
) -> Path:
    """Render note events as a normalized bass-like WAV for quick listening."""
    if sample_rate <= 0:
        raise ValueError("sample_rate must be greater than zero")

    last_note_end = max((note.end for note in events.notes), default=0.0)
    duration_seconds = max(MIN_PREVIEW_SECONDS, last_note_end + PREVIEW_TAIL_SECONDS)
    frame_count = max(1, math.ceil(duration_seconds * sample_rate))
    audio = np.zeros(frame_count, dtype=np.float32)

    for note in events.notes:
        start_frame = max(0, round(note.start * sample_rate))
        end_frame = min(frame_count, max(start_frame + 1, round(note.end * sample_rate)))
        note_frames = end_frame - start_frame
        time = np.arange(note_frames, dtype=np.float32) / sample_rate
        frequency = 440.0 * 2.0 ** ((note.midi - 69) / 12.0)
        waveform = (
            0.76 * np.sin(2.0 * np.pi * frequency * time)
            + 0.19 * np.sin(4.0 * np.pi * frequency * time)
            + 0.05 * np.sin(6.0 * np.pi * frequency * time)
        )
        envelope = _note_envelope(note_frames, sample_rate=sample_rate)
        audio[start_frame:end_frame] += waveform * envelope * note.velocity

    peak = float(np.max(np.abs(audio)))
    if peak > 0.0:
        audio *= 0.9 / peak

    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(path, audio, sample_rate, subtype="PCM_16")
    return path


def _note_envelope(frame_count: int, *, sample_rate: int) -> np.ndarray:
    envelope = np.ones(frame_count, dtype=np.float32)
    attack_frames = min(frame_count // 2, max(1, round(ATTACK_SECONDS * sample_rate)))
    release_frames = min(frame_count // 2, max(1, round(RELEASE_SECONDS * sample_rate)))
    if attack_frames:
        envelope[:attack_frames] *= np.linspace(
            0.0,
            1.0,
            attack_frames,
            endpoint=False,
            dtype=np.float32,
        )
    if release_frames:
        envelope[-release_frames:] *= np.linspace(
            1.0,
            0.0,
            release_frames,
            endpoint=True,
            dtype=np.float32,
        )
    return envelope
