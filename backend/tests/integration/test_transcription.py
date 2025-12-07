from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

from src.pipelines.transcription import transcribe_midi


def test_transcribe_midi_generates_mid(tmp_path, monkeypatch) -> None:
    """Smoke: synthesize a short tone and ensure MIDI is produced."""
    sr = 22050
    duration = 1.0
    t = np.linspace(0, duration, int(sr * duration), endpoint=False)
    tone = 0.1 * np.sin(2 * np.pi * 440 * t)

    input_wav = tmp_path / "tone.wav"
    sf.write(input_wav, tone, sr)

    # Force ONNX backend even if other runtimes exist.
    monkeypatch.setenv("BASIC_PITCH_MODEL_SERIALIZATION", "onnx")

    midi_path = transcribe_midi(input_wav, tmp_path)

    assert midi_path.exists()
    assert midi_path.stat().st_size > 0
    assert midi_path.suffix == ".mid"

