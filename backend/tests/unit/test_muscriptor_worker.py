"""Mocked backend/synthetic artifact tests; never load model weights or infer."""
from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import sys
import subprocess
from types import SimpleNamespace

import mido
import numpy as np
from pydantic import ValidationError
import pytest
import soundfile as sf
import torch

from src.pipelines import muscriptor_worker as worker


@pytest.fixture
def manifest(tmp_path: Path) -> worker.WorkerManifest:
    source = tmp_path / "official"
    (source / "muscriptor").mkdir(parents=True)
    (source / "muscriptor" / "__init__.py").write_text("# test fixture, never imported\n")
    model = tmp_path / "model.safetensors"
    model.write_bytes(b"not model weights")
    model.with_name("config.json").write_text("{}")
    checkpoint = tmp_path / "955717e8-8726e21a.th"
    checkpoint.write_bytes(b"not a checkpoint")
    return worker.WorkerManifest(schema_version=1, terms_accepted=True, noncommercial=True,
        source_dir=source, source_commit=worker.SOURCE_COMMIT, model_path=model,
        model_sha256=worker.MODEL_SHA256, config_sha256=worker.CONFIG_SHA256,
        demucs_checkpoint=checkpoint, demucs_sha256=worker.DEMUCS_SHA256,
        demucs_python=Path(sys.executable), muscriptor_python=Path(sys.executable))


def _manifest_file(manifest: worker.WorkerManifest, tmp_path: Path) -> Path:
    path = tmp_path / "server-manifest.json"
    path.write_text(manifest.model_dump_json())
    return path


@pytest.mark.parametrize("changes", [
    {"terms_accepted": False}, {"terms_accepted": 1}, {"noncommercial": False},
    {"source_dir": Path("relative")}, {"model_path": Path("hf://unapproved/model")},
    {"model_sha256": "0" * 64}, {"demucs_sha256": "0" * 64},
    {"config_sha256": "0" * 64}, {"source_commit": "0" * 40}, {"unapproved": True},
])
def test_manifest_rejects_unconsented_unpinned_or_remote_configuration(
    manifest: worker.WorkerManifest, changes: dict,
) -> None:
    with pytest.raises(ValidationError):
        worker.WorkerManifest.model_validate({**manifest.model_dump(), **changes})


def test_assets_are_exact_and_source_must_be_clean(
    manifest: worker.WorkerManifest, monkeypatch: pytest.MonkeyPatch,
) -> None:
    checked = []
    monkeypatch.setattr(worker, "_verified_file", lambda path, expected: checked.append((path, expected)))
    monkeypatch.setattr(worker, "_git", lambda source, *args: worker.SOURCE_COMMIT if args[0] == "rev-parse" else "")
    result = worker._verify_assets(manifest, "separate")
    assert result["source_clean"] and result["source_commit"] == worker.SOURCE_COMMIT
    assert checked == [(manifest.model_path, worker.MODEL_SHA256),
                       (manifest.model_path.with_name("config.json"), worker.CONFIG_SHA256),
                       (manifest.demucs_checkpoint, worker.DEMUCS_SHA256)]
    monkeypatch.setattr(worker, "_git", lambda source, *args: worker.SOURCE_COMMIT if args[0] == "rev-parse" else " M official.py")
    with pytest.raises(worker.WorkerFailure) as failure:
        worker._verify_assets(manifest, "transcribe")
    assert failure.value.code == "asset_changed"


@pytest.mark.parametrize(("channels", "format_name"), [(1, "WAV"), (2, "WAV"), (1, "FLAC")])
def test_audio_decodes_every_sample_without_crop(
    tmp_path: Path, channels: int, format_name: str,
) -> None:
    path = tmp_path / ("audio.wav" if format_name == "WAV" else "audio.flac")
    data = np.tile(np.linspace(-.5, .5, 10000, dtype=np.float32)[:, None], (1, channels))
    sf.write(path, data, 1000, format=format_name, subtype="FLOAT" if format_name == "WAV" else "PCM_16")
    expected, _ = sf.read(path, dtype="float32", always_2d=True)
    wave, sr, meta = worker._read_audio(path)
    assert sr == 1000 and wave.shape == (channels, 10000)
    np.testing.assert_array_equal(wave, np.ascontiguousarray(expected.T))
    assert meta["seconds"] == 10 and meta["cropped"] is False and meta["truncated"] is False
    assert meta["decoded_float32_sha256"] == hashlib.sha256(wave.tobytes()).hexdigest()


@pytest.mark.parametrize(("frames", "channels", "bad_value"), [
    (10001, 1, False), (10, 3, False), (10, 1, True), (0, 1, False),
])
def test_audio_rejects_over_ten_seconds_nonfinite_wrong_channels_or_empty(
    tmp_path: Path, frames: int, channels: int, bad_value: bool,
) -> None:
    path = tmp_path / "bad.wav"
    data = np.zeros((frames, channels), dtype=np.float32)
    if bad_value:
        data[0, 0] = np.nan
    sf.write(path, data, 1000, format="WAV", subtype="FLOAT")
    with pytest.raises(worker.WorkerFailure) as failure:
        worker._read_audio(path)
    assert failure.value.code == "invalid_audio"


def test_demucs_local_repo_only_uses_hash_guarded_exact_pickle_path_and_restores_loader(
    manifest: worker.WorkerManifest, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []
    sentinel = object()
    def original(path, *args, **kwargs):
        calls.append((path, args, kwargs))
        return sentinel
    fake_torch = SimpleNamespace(load=original)
    monkeypatch.setattr(worker, "_verified_file", lambda *args: None)
    class Repo:
        def __init__(self, root):
            assert root == manifest.demucs_checkpoint.parent
            self._models = {"955717e8": manifest.demucs_checkpoint}
        def get_model(self, signature):
            assert signature == "955717e8"
            return fake_torch.load(manifest.demucs_checkpoint, "cpu")
    assert worker._load_local_demucs(manifest, fake_torch, Repo) is sentinel
    assert calls == [(manifest.demucs_checkpoint, ("cpu",), {"weights_only": False})]
    assert fake_torch.load is original
    class WrongPath(Repo):
        def get_model(self, signature):
            return fake_torch.load(manifest.model_path)
    with pytest.raises(worker.WorkerFailure) as failure:
        worker._load_local_demucs(manifest, fake_torch, WrongPath)
    assert failure.value.code == "asset_changed" and fake_torch.load is original
    assert len(calls) == 1


class _FakeDemucs:
    sources = ["drums", "bass", "other", "vocals"]
    samplerate = 16000
    audio_channels = 2
    segment = 7.8
    def cpu(self):
        return self
    def eval(self):
        return self


def test_demucs_exact_bass_and_official_normalization_restore_float_scale(
    manifest: worker.WorkerManifest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = _FakeDemucs()
    wave = np.tile(np.linspace(-1, 1, 80, dtype=np.float32) + .4, (2, 1))
    captured = {}
    def convert(wav, sr, target_sr, channels):
        assert (sr, target_sr, channels) == (16000, 16000, 2)
        return wav
    def apply(model_arg, batch, **kwargs):
        captured.update(kwargs)
        assert model_arg is model
        assert torch.allclose(batch.mean(1).mean(), torch.tensor(0.), atol=1e-6)
        sources = torch.zeros((1, 4, 2, 80))
        sources[:, 1] = batch * 2
        return sources
    monkeypatch.setattr(worker, "_demucs_components", lambda: (object(), _FakeDemucs, convert, apply))
    monkeypatch.setattr(worker, "_load_local_demucs", lambda *args: model)
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    state = {"model_calls": 0}
    result = worker._separate(manifest, wave, 16000, tmp_path, torch, state)
    saved, sr = sf.read(result["bass_path"], dtype="float32", always_2d=True)
    expected = 2 * wave - wave.mean(axis=0).mean()
    np.testing.assert_allclose(saved.T, expected, rtol=1e-6, atol=2e-7)
    assert sr == 16000 and abs(saved).max() > 1 and state["model_calls"] == 1
    assert result["separator"]["clipping"] is False and result["separator"]["rescaling"] is False
    assert result["decoded_bass_float32_sha256"] == hashlib.sha256(np.ascontiguousarray(saved.T).tobytes()).hexdigest()
    assert captured == dict(device="cpu", shifts=1, split=True, overlap=.25, transition_power=1.,
                            progress=False, num_workers=0, segment=None)
    assert sf.info(result["bass_path"]).subtype == "FLOAT"


@pytest.mark.parametrize("sources", [["vocals", "other"], ["bass", "bass"]])
def test_missing_or_duplicate_exact_bass_never_falls_back(
    manifest: worker.WorkerManifest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sources: list,
) -> None:
    model = _FakeDemucs()
    model.sources = sources
    def forbidden(*args, **kwargs):
        pytest.fail("No conversion or separation may occur without exact bass")
    monkeypatch.setattr(worker, "_demucs_components", lambda: (object(), _FakeDemucs, forbidden, forbidden))
    monkeypatch.setattr(worker, "_load_local_demucs", lambda *args: model)
    state = {"model_calls": 0}
    with pytest.raises(worker.WorkerFailure) as failure:
        worker._separate(manifest, np.zeros((1, 80), dtype=np.float32), 16000, tmp_path, torch, state)
    assert failure.value.code == "bass_missing" and state["model_calls"] == 0
    assert not (tmp_path / "bass.wav").exists()


def test_mu_requires_cuda_before_model_import(
    manifest: worker.WorkerManifest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    monkeypatch.setattr(worker, "_muscriptor_components", lambda *args: pytest.fail("No Mu import without CUDA"))
    with pytest.raises(worker.WorkerFailure) as failure:
        worker._transcribe(manifest, np.zeros((1, 80), dtype=np.float32), 16000, tmp_path, torch, {"model_calls": 0})
    assert failure.value.code == "cuda_unavailable"


def test_direct_worker_entrypoint_resolves_its_sibling_from_another_cwd(tmp_path: Path) -> None:
    result = subprocess.run([sys.executable, str(Path(worker.__file__)), "--help"], cwd=tmp_path,
                            capture_output=True, text=True, timeout=20,
                            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": ""})
    assert result.returncode == 0, result.stderr
    assert "--stage" in result.stdout


@dataclass
class _Start:
    pitch: int
    start_time: float
    index: int
    instrument: str


@dataclass
class _End:
    end_time: float
    start_event: _Start


@dataclass
class _Progress:
    completed: int
    total: int


@dataclass
class _Boundary:
    seek_time: float
    next_seek_time: float | None


EVENT_TYPES = SimpleNamespace(NoteStartEvent=_Start, NoteEndEvent=_End,
                              ProgressEvent=_Progress, ChunkBoundary=_Boundary)


def test_mocked_mu_raw_events_tokens_programs_and_literal_midi_are_preserved(
    manifest: worker.WorkerManifest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _Start(40, .12345, 0, "acoustic_bass")
    second = _Start(43, .50, 1, "electric_bass")
    observed = {}
    class Model:
        _tokenizer = SimpleNamespace(_vocab=[SimpleNamespace(type="tie", value=0),
            SimpleNamespace(type="pitch", value=40), SimpleNamespace(type="pitch", value=43)])
        def _program_for_instrument(self, name):
            return {"acoustic_bass": 32, "electric_bass": 33}[name]
        def _generate_token_stream(self):
            yield _Boundary(0., None)
            yield 0
            yield 1
            yield 2
            yield _Progress(1, 1)
        def transcribe(self, audio, **kwargs):
            observed["parameters"] = kwargs
            yield _Progress(0, 1)
            for item in self._generate_token_stream():
                if item == 1:
                    yield first
                    yield _End(.47, first)
                elif item == 2:
                    yield second
                    yield _End(.83, second)
                elif isinstance(item, _Progress):
                    yield item
    model = Model()
    def load(**kwargs):
        observed["load"] = kwargs
        return model
    tm = SimpleNamespace(TranscriptionModel=SimpleNamespace(load_model=load))
    fake_torch = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: True, synchronize=lambda *args: None),
                                 from_numpy=lambda wave: wave, inference_mode=nullcontext)
    monkeypatch.setattr(worker, "_muscriptor_components", lambda *args: (tm, EVENT_TYPES))
    state = {"model_calls": 0}
    result = worker._transcribe(manifest, np.zeros((1, 32000), dtype=np.float32), 16000, tmp_path, fake_torch, state)
    assert observed["load"] == dict(weights_path=manifest.model_path, device="cuda:0", dtype="float32")
    assert observed["parameters"] == worker.PARAMETERS and state["model_calls"] == 1
    notes = json.loads((tmp_path / "notes.json").read_text())
    assert [(n["index"], n["pitch"], n["program"], n["onset"], n["offset"]) for n in notes] == [
        (0, 40, 32, .12345, .47), (1, 43, 33, .50, .83)]
    tokens = [json.loads(line) for line in (tmp_path / "tokens.jsonl").read_text().splitlines()]
    assert [row["id"] for row in tokens if row["type"] == "token"] == [0, 1, 2]
    assert result["counts"] == dict(notes=2, events=6, stream_tokens=3)
    midi = mido.MidiFile(tmp_path / "performance.mid")
    assert midi.ticks_per_beat == 10000
    assert [msg.program for track in midi.tracks for msg in track if msg.type == "program_change"] == [32, 33]
    starts = [msg for track in midi.tracks for msg in track if msg.type == "note_on"]
    assert [msg.note for msg in starts] == [40, 43] and all(msg.velocity == 100 for msg in starts)
    assert result["beat_detection"] is False and result["postprocessing"] is False


def test_raw_serializer_rejects_unpaired_and_does_not_filter_unexpected_program() -> None:
    start = _Start(50, .1, 9, "program_0")
    model = SimpleNamespace(_program_for_instrument=lambda name: 0)
    rows, notes = worker._serialize_events([start, _End(.2, start)], model, EVENT_TYPES)
    assert len(rows) == 2 and notes[0]["program"] == 0 and notes[0]["pitch"] == 50
    with pytest.raises(worker.WorkerFailure):
        worker._serialize_events([start], model, EVENT_TYPES)


@pytest.mark.parametrize("offset", [.2, .3])
def test_invalid_decoded_timing_preserves_raw_artifacts_and_diagnostics(
    manifest: worker.WorkerManifest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, offset: float,
) -> None:
    start = _Start(40, .3, 0, "electric_bass")
    class Model:
        _tokenizer = SimpleNamespace(_vocab=[SimpleNamespace(type="tie", value=0)])
        def _program_for_instrument(self, name):
            return 33
        def _generate_token_stream(self):
            yield _Boundary(0., None)
            yield 0
        def transcribe(self, audio, **kwargs):
            yield _Progress(0, 1)
            for _ in self._generate_token_stream():
                pass
            yield start
            yield _End(offset, start)
    model = Model()
    tm = SimpleNamespace(TranscriptionModel=SimpleNamespace(load_model=lambda **kwargs: model))
    fake_torch = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: True, synchronize=lambda *args: None),
                                from_numpy=lambda wave: wave, inference_mode=nullcontext)
    monkeypatch.setattr(worker, "_muscriptor_components", lambda *args: (tm, EVENT_TYPES))
    state = {"model_calls": 0}
    with pytest.raises(worker.WorkerFailure) as failure:
        worker._transcribe(manifest, np.zeros((1, 80), dtype=np.float32), 16000, tmp_path, fake_torch, state)
    assert failure.value.code == "invalid_model_output"
    assert json.loads((tmp_path / "notes.json").read_text())[0]["offset"] == offset
    assert (tmp_path / "events.jsonl").exists() and (tmp_path / "tokens.jsonl").exists()
    integrity = json.loads((tmp_path / "decoder-integrity.json").read_text())
    assert integrity["status"] == "invalid" and integrity["notes_modified"] is False
    assert state["times_seconds"]["inference"] >= 0 and not (tmp_path / "performance.mid").exists()


def test_literal_midi_boundary_and_empty_output(tmp_path: Path) -> None:
    notes = [dict(index=0, program=32, onset=.1, offset=.6, pitch=40),
             dict(index=1, program=32, onset=.6, offset=1., pitch=40)]
    worker._literal_midi(tmp_path / "notes.mid", notes)
    midi = mido.MidiFile(tmp_path / "notes.mid")
    tick, boundary = 0, []
    for msg in midi.tracks[1]:
        tick += msg.time
        if tick == 6000:
            boundary.append(msg.type)
    assert boundary == ["note_off", "note_on"]
    worker._literal_midi(tmp_path / "empty.mid", [])
    assert len(mido.MidiFile(tmp_path / "empty.mid").tracks) == 1


@pytest.mark.parametrize("end", [.2, .3])
def test_literal_midi_rejects_nonpositive_duration_without_writing(tmp_path: Path, end: float) -> None:
    notes = [dict(index=0, program=33, onset=.3, offset=end, pitch=43)]
    with pytest.raises(worker.WorkerFailure) as failure:
        worker._literal_midi(tmp_path / "invalid.mid", notes)
    assert failure.value.code == "invalid_model_output"
    assert notes[0]["offset"] == end and not (tmp_path / "invalid.mid").exists()


def test_offline_process_and_network_and_beat_guards(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(worker.os, "environ", {"HF_TOKEN": "test-token"})
    monkeypatch.setattr(worker.sys, "meta_path", list(sys.meta_path))
    hooks = []
    monkeypatch.setattr(worker.sys, "addaudithook", hooks.append)
    worker._offline_process(tmp_path)
    assert hooks == [worker._deny_network]
    assert worker.os.environ["HF_HUB_OFFLINE"] == "1" and "HF_TOKEN" not in worker.os.environ
    assert str(tmp_path) in worker.os.environ["CUDA_CACHE_PATH"]
    with pytest.raises(worker.WorkerFailure):
        worker._deny_network("socket.connect", ())
    with pytest.raises(worker.WorkerFailure):
        worker._NoBeatImports().find_spec("beat_this.inference")


def test_parent_death_signal_is_opt_in_and_detects_parent_change(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    monkeypatch.delenv("STEM2TAB_TRIAL_PARENT_PID", raising=False)
    monkeypatch.setattr(worker, "_prctl_parent_signal", lambda: calls.append(True))
    worker._parent_death_signal()
    assert calls == []
    monkeypatch.setenv("STEM2TAB_TRIAL_PARENT_PID", "1234")
    monkeypatch.setattr(worker.sys, "platform", "linux")
    parents = iter([1234, 1234])
    monkeypatch.setattr(worker.os, "getppid", lambda: next(parents))
    worker._parent_death_signal()
    assert calls == [True]
    parents = iter([1234, 1235])
    with pytest.raises(worker.WorkerFailure) as failure:
        worker._parent_death_signal()
    assert failure.value.code == "parent_gone"


def test_invalid_assets_fail_before_any_torch_or_cuda_initialization_and_safe_result(
    manifest: worker.WorkerManifest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(worker, "_offline_process", lambda output: None)
    monkeypatch.setattr(worker, "_parent_death_signal", lambda: None)
    monkeypatch.setattr(worker, "_torch_runtime", lambda: pytest.fail("Assets must fail before torch/CUDA"))
    output = tmp_path / "stage"
    result = worker.run_stage("transcribe", _manifest_file(manifest, tmp_path), tmp_path / "private-input.wav", output)
    assert result["status"] == "failed" and result["error"]["code"] == "asset_changed"
    assert result["model_calls"] == 0 and result["cuda_peak"] is None
    assert str(tmp_path) not in result["error"]["message"]
    assert (output / "failure-detail.json").is_file()
    assert json.loads((output / "result.json").read_text())["status"] == "failed"


def test_fresh_output_is_never_overwritten(tmp_path: Path) -> None:
    output = tmp_path / "existing"
    output.mkdir()
    old = output / "result.json"
    old.write_text("original")
    with pytest.raises(worker.WorkerFailure) as failure:
        worker.run_stage("separate", tmp_path / "manifest.json", tmp_path / "input.wav", output)
    assert failure.value.code == "output_exists" and old.read_text() == "original"


def test_cli_failure_stdout_contains_safe_code_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setattr(worker, "run_stage", lambda *args: dict(status="failed", error=dict(code="bass_missing")))
    status = worker.main(["--stage", "separate", "--manifest", str(tmp_path / "secret.json"),
                          "--input", str(tmp_path / "private.wav"), "--output", str(tmp_path / "out")])
    assert status == 1
    assert json.loads(capsys.readouterr().out) == dict(status="failed", code="bass_missing")
