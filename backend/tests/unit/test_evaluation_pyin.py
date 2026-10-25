"""Small CPU adapter checks with mocked inference and synthesized local audio."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pretty_midi
import pytest
import soundfile as sf

from src.evaluation.adapters import AdapterConfig
from src.evaluation.io import read_midi, read_note_events_csv, read_note_events_json


def _config(tmp_path: Path) -> AdapterConfig:
    return AdapterConfig(demucs_model="unused", demucs_cache_dir=tmp_path / "unused-cache")


@pytest.fixture
def mocked_frames(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from src.evaluation import pyin

    sr = 22050
    audio = 0.05 * np.sin(2 * np.pi * 55 * np.arange(int(sr * 0.6)) / sr)
    audio_path = tmp_path / "synthetic.wav"
    sf.write(audio_path, audio, sr)
    times = np.arange(60, dtype=float) * 0.01
    f0 = np.full(60, pretty_midi.note_number_to_hz(28))
    voiced = np.ones(60, dtype=bool)
    probability = np.full(60, 0.95)
    # One missed frame and one isolated octave error should not rewrite raw output.
    f0[20] = np.nan
    voiced[20] = False
    probability[20] = 0
    f0[40] = pretty_midi.note_number_to_hz(40)
    active = np.ones(60, dtype=bool)
    energy = np.full(60, 0.5)
    onsets = np.array([0.0])

    def extract(actual_audio: np.ndarray, actual_sr: int):
        assert actual_sr == sr
        assert actual_audio.ndim == 1
        return f0.copy(), voiced.copy(), probability.copy(), times.copy()

    monkeypatch.setattr(pyin, "_extract_frames", extract)
    monkeypatch.setattr(
        pyin, "_activity_and_onsets",
        lambda actual_audio, actual_sr, actual_times: (active.copy(), onsets.copy(), energy.copy()),
    )
    return audio_path, (f0, voiced, probability, times, active, energy, onsets)


def _intervals(events):
    return [(n.midi, n.start, n.end) for n in events.notes]


def test_pyin_preserves_raw_frames_and_raw_midi_before_assembly(
    tmp_path: Path, mocked_frames,
) -> None:
    from src.evaluation.pyin import PyinTranscriber

    audio_path, expected_arrays = mocked_frames
    output = tmp_path / "assembled"
    adapter = PyinTranscriber()
    assert adapter.name == "pyin"
    result = adapter.run(audio_path, output, config=_config(tmp_path))

    assert result.raw_midi_path == output / "raw.mid"
    assert result.raw_midi_path.is_file()
    assert (output / "assembled.mid").is_file()
    with np.load(output / "pyin_frames.npz", allow_pickle=False) as saved:
        keys = ("f0", "voiced_flag", "voiced_probability", "time", "active", "energy", "onset_times")
        assert set(saved.files) == set(keys)
        for key, expected in zip(keys, expected_arrays):
            np.testing.assert_equal(saved[key], expected)

    raw = read_midi(result.raw_midi_path)
    assembled = read_midi(output / "assembled.mid")
    assert _intervals(result.events) == _intervals(assembled)
    assert _intervals(raw) != _intervals(assembled)
    assert len(assembled.notes) < len(raw.notes)
    assert len(raw.notes) >= 3
    assert 40 in [n.midi for n in raw.notes], "The raw artifact retains inference glitches"
    assert set(n.midi for n in assembled.notes) == {28}
    assert all(isinstance(v, (str, int, float, bool, type(None))) for v in result.metadata.values())
    assert result.metadata["backend"] == "pyin"
    assert result.metadata["processing"] == "assembled"
    assert result.metadata["uses_reference"] is False
    assert result.metadata["raw_midi_path"] == str(output / "raw.mid")
    assert result.metadata["selected_midi_path"] == str(output / "assembled.mid")
    assert result.metadata["raw_frames_path"] == str(output / "pyin_frames.npz")
    parameters = json.loads(result.metadata["parameters"])
    assert parameters["sample_rate"] == 22050
    assert parameters["hop_length"] == 256
    assert parameters["fmin"] == 27.5
    assert parameters["assembly"]
    json_events = read_note_events_json(output / "raw_events.json")
    csv_events = read_note_events_csv(output / "raw_events.csv")
    np.testing.assert_allclose(_intervals(json_events), _intervals(csv_events), atol=1e-11, rtol=0)
    assert [n.midi for n in json_events.notes] == [n.midi for n in raw.notes]


def test_pyin_raw_variant_returns_naive_events_without_overwriting_raw(
    tmp_path: Path, mocked_frames,
) -> None:
    from src.evaluation.pyin import PyinTranscriber

    audio_path, _ = mocked_frames
    adapter = PyinTranscriber(assembled=False)
    assert adapter.name == "pyin_raw"
    result = adapter.run(audio_path, tmp_path / "raw", config=_config(tmp_path))
    assert _intervals(result.events) == _intervals(read_midi(result.raw_midi_path))
    assert len(result.events.notes) >= 3
    assert 40 in [n.midi for n in result.events.notes]
    assert result.metadata["processing"] == "raw_frame_runs"
    assert result.metadata["uses_reference"] is False
    assembled = PyinTranscriber().run(audio_path, tmp_path / "assembled", config=_config(tmp_path))
    assert result.raw_midi_path.read_bytes() == assembled.raw_midi_path.read_bytes()


@pytest.mark.parametrize("helper", ["_extract_frames", "_activity_and_onsets"])
def test_pyin_inference_failure_propagates(
    tmp_path: Path, mocked_frames, monkeypatch: pytest.MonkeyPatch, helper: str,
) -> None:
    from src.evaluation import pyin

    audio_path, _ = mocked_frames

    def fail_inference(*args: object, **kwargs: object):
        raise RuntimeError("synthetic pYIN failure")

    monkeypatch.setattr(pyin, helper, fail_inference)
    with pytest.raises(RuntimeError, match="synthetic pYIN failure"):
        pyin.PyinTranscriber().run(audio_path, tmp_path / "failed", config=_config(tmp_path))


def test_pyin_missing_audio_fails_explicitly(tmp_path: Path) -> None:
    from src.evaluation.pyin import PyinTranscriber

    with pytest.raises((FileNotFoundError, sf.LibsndfileError)):
        PyinTranscriber().run(tmp_path / "missing.wav", tmp_path / "failed", config=_config(tmp_path))


def test_pyin_module_import_does_not_load_librosa_or_model_runtimes() -> None:
    script = (
        "import sys; import src.evaluation.pyin; "
        "assert 'librosa' not in sys.modules; "
        "assert 'tensorflow' not in sys.modules; "
        "assert 'onnxruntime' not in sys.modules"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=False,
        cwd=Path(__file__).resolve().parents[2],
    )
    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize("assembled", [False, True])
def test_pyin_unvoiced_input_produces_empty_notes_without_inventing_pitch(
    tmp_path: Path, mocked_frames, monkeypatch: pytest.MonkeyPatch, assembled: bool,
) -> None:
    from src.evaluation import pyin

    audio_path, arrays = mocked_frames
    times = arrays[3]
    monkeypatch.setattr(
        pyin, "_extract_frames",
        lambda audio, sr: (np.full(len(times), np.nan), np.zeros(len(times), dtype=bool),
                           np.zeros(len(times)), times),
    )
    monkeypatch.setattr(
        pyin, "_activity_and_onsets",
        lambda audio, sr, times: (np.zeros(len(times), dtype=bool), np.array([]), np.zeros(len(times))),
    )
    result = pyin.PyinTranscriber(assembled=assembled).run(
        audio_path, tmp_path / "silent", config=_config(tmp_path),
    )
    assert result.events.notes == ()
    assert read_midi(result.raw_midi_path).notes == ()
    assert (tmp_path / "silent" / "pyin_frames.npz").is_file()


def test_activity_preserves_quiet_notes_independently_of_earlier_loud_peak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import librosa
    from src.evaluation.pyin import _activity_and_onsets

    def forbidden_pitch_inference(*args: object, **kwargs: object):
        raise AssertionError("activity features must not run pYIN inference")

    monkeypatch.setattr(librosa, "pyin", forbidden_pitch_inference)
    sr = 22050
    sample_times = np.arange(round(1.5 * sr)) / sr
    quiet_only = np.where(
        (sample_times >= 0.9) & (sample_times < 1.3),
        0.006 * np.sin(2 * np.pi * 220 * sample_times),
        0.0,
    )
    loud_then_quiet = quiet_only + np.where(
        (sample_times >= 0.2) & (sample_times < 0.6),
        0.8 * np.sin(2 * np.pi * 220 * sample_times),
        0.0,
    )
    frame_times = np.arange(0, 1.5, 256 / sr)
    quiet_active, _, quiet_energy = _activity_and_onsets(quiet_only, sr, frame_times)
    mixed_active, _, mixed_energy = _activity_and_onsets(loud_then_quiet, sr, frame_times)
    quiet_interior = (frame_times >= 0.95) & (frame_times <= 1.25)
    assert np.any(quiet_interior)
    assert quiet_active[quiet_interior].all()
    assert mixed_active[quiet_interior].all()
    np.testing.assert_array_equal(mixed_active[quiet_interior], quiet_active[quiet_interior])
    np.testing.assert_allclose(mixed_energy[quiet_interior], quiet_energy[quiet_interior])


def test_activity_keeps_complete_silence_inactive() -> None:
    from src.evaluation.pyin import _activity_and_onsets

    sr = 22050
    times = np.arange(0, 1.5, 256 / sr)
    active, onsets, energy = _activity_and_onsets(np.zeros(round(1.5 * sr)), sr, times)
    assert not active.any()
    assert not energy.any()
    assert len(onsets) == 0
