from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from src.evaluation.models import NoteEvent, NoteEventSet
from src.evaluation.preview import write_preview_wav


def test_preview_wav_renders_audible_normalized_notes(tmp_path: Path) -> None:
    events = NoteEventSet(
        notes=(
            NoteEvent(start=0.0, end=0.5, midi=40, velocity=0.8),
            NoteEvent(start=0.5, end=1.0, midi=43, velocity=1.0),
        )
    )

    preview_path = write_preview_wav(events, tmp_path / "preview.wav", sample_rate=8_000)
    audio, sample_rate = sf.read(preview_path, dtype="float32")

    assert sample_rate == 8_000
    assert len(audio) == 10_000
    assert float(np.max(np.abs(audio))) == pytest.approx(0.9, abs=0.001)
    assert np.count_nonzero(audio) > 0


def test_preview_wav_writes_one_second_of_silence_for_empty_notes(tmp_path: Path) -> None:
    preview_path = write_preview_wav(NoteEventSet(), tmp_path / "empty.wav", sample_rate=8_000)
    audio, sample_rate = sf.read(preview_path, dtype="float32")

    assert sample_rate == 8_000
    assert len(audio) == 8_000
    assert np.count_nonzero(audio) == 0


def test_preview_wav_rejects_invalid_sample_rate(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="sample_rate"):
        write_preview_wav(NoteEventSet(), tmp_path / "invalid.wav", sample_rate=0)
