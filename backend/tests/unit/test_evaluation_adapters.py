from __future__ import annotations

from pathlib import Path

import pytest

from src.evaluation import adapters
from src.evaluation.adapters import (
    AdapterConfig,
    BasicPitchTranscriber,
    DemucsSeparator,
    DirectSeparator,
)
from src.evaluation.io import write_midi
from src.evaluation.models import NoteEvent, NoteEventSet


def _config(tmp_path: Path) -> AdapterConfig:
    return AdapterConfig(demucs_model="test-model", demucs_cache_dir=tmp_path / "cache")


def test_direct_separator_returns_original_audio(tmp_path: Path) -> None:
    audio_path = tmp_path / "input.wav"
    audio_path.write_bytes(b"audio")

    result = DirectSeparator().run(audio_path, tmp_path / "separator", config=_config(tmp_path))

    assert result.audio_path == audio_path
    assert result.artifacts == {}
    assert result.metadata["backend"] == "direct"


def test_demucs_separator_selects_bass_and_preserves_artifacts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    audio_path = tmp_path / "input.wav"
    audio_path.write_bytes(b"audio")
    calls: dict[str, object] = {}

    def fake_separate_stems(**kwargs) -> dict[str, Path]:
        calls.update(kwargs)
        output_dir = kwargs["output_dir"]
        bass_path = output_dir / "bass.wav"
        drums_path = output_dir / "drums.wav"
        bass_path.parent.mkdir(parents=True, exist_ok=True)
        bass_path.write_bytes(b"bass")
        drums_path.write_bytes(b"drums")
        return {"bass": bass_path, "drums": drums_path}

    monkeypatch.setattr(adapters, "separate_stems", fake_separate_stems)
    config = _config(tmp_path)

    result = DemucsSeparator().run(audio_path, tmp_path / "separator", config=config)

    assert result.audio_path.name == "bass.wav"
    assert set(result.artifacts) == {"bass", "drums"}
    assert calls["model_name"] == "test-model"
    assert calls["cache_dir"] == config.demucs_cache_dir
    assert config.demucs_cache_dir.is_dir()


def test_demucs_separator_requires_bass_stem(tmp_path: Path, monkeypatch) -> None:
    audio_path = tmp_path / "input.wav"
    audio_path.write_bytes(b"audio")
    monkeypatch.setattr(adapters, "separate_stems", lambda **kwargs: {"other": tmp_path / "other.wav"})

    with pytest.raises(RuntimeError, match="bass stem"):
        DemucsSeparator().run(audio_path, tmp_path / "separator", config=_config(tmp_path))


def test_basic_pitch_adapter_normalizes_raw_midi(
    tmp_path: Path,
    monkeypatch,
) -> None:
    audio_path = tmp_path / "input.wav"
    audio_path.write_bytes(b"audio")
    events = NoteEventSet(notes=(NoteEvent(start=0.0, end=0.5, midi=40),))

    def fake_transcribe(input_wav: Path, output_dir: Path, **kwargs) -> Path:
        assert input_wav == audio_path
        return write_midi(events, output_dir / "input.mid")

    monkeypatch.setattr(adapters, "transcribe_midi", fake_transcribe)
    output_dir = tmp_path / "run"

    result = BasicPitchTranscriber().run(audio_path, output_dir, config=_config(tmp_path))

    assert result.raw_midi_path == output_dir / "raw.mid"
    assert result.raw_midi_path.is_file()
    assert not (output_dir / "input.mid").exists()
    assert [note.midi for note in result.events.notes] == [40]
    assert result.metadata["serialization"] == "onnx"


def test_registry_reports_available_adapters() -> None:
    assert adapters.get_separator_adapter("direct").name == "direct"
    assert adapters.get_transcriber_adapter("basic_pitch").name == "basic_pitch"
    with pytest.raises(ValueError, match="available"):
        adapters.get_separator_adapter("missing")
    with pytest.raises(ValueError, match="available"):
        adapters.get_transcriber_adapter("missing")
