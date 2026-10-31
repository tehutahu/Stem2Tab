"""Opt-in, one-stage local Demucs/MuScriptor subprocess worker.

No heavy backend is imported at module scope. The service owns paths, consent,
wall/RSS supervision and process termination; this CLI never downloads assets.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.abc
import json
import math
import os
from pathlib import Path
import random
import signal
import subprocess
import sys
import time
import traceback
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, StrictBool, model_validator

sys.dont_write_bytecode = True
SOURCE_COMMIT = "8a1ef88672cc23c26619adaff37ec48d82c0b906"
MODEL_SHA256 = "bbd482c786b895cf7d8f44185073d951adae2ebb8a66f82ca84cd1f84569549c"
CONFIG_SHA256 = "3008fc481e4a1cd978e337eb3759260c270892204db5039235ac939e1f42aeb2"
DEMUCS_SHA256 = "8726e21a993978c7ba086d3872e7608d7d5bfca646ca4aca459ffda844faa8b4"
PARAMETERS = dict(use_sampling=False, temperature=1.0, cfg_coef=1.0,
                  instruments=["acoustic_bass", "electric_bass"], batch_size=1,
                  no_eos_is_ok=True, beam_size=1, prelude_forcing=True)
SAFE_ERRORS = {
    "invalid_manifest": "The server model configuration is invalid.",
    "asset_changed": "A required local model asset or official source failed verification.",
    "invalid_audio": "Audio must be finite, nonempty WAV/FLAC, at most ten seconds, with one or two channels.",
    "bass_missing": "The configured separator does not provide exactly one bass source.",
    "cuda_unavailable": "MuScriptor requires CUDA device zero; CPU fallback is disabled.",
    "invalid_model_output": "The model returned an invalid or unrepresentable output.",
    "network_disabled": "Network access is disabled for this worker.",
    "output_exists": "The worker output directory must be new.",
    "parent_gone": "The supervising service is no longer available.",
    "worker_failed": "The local model worker failed. Local diagnostics were retained.",
}


class WorkerManifest(BaseModel):
    """Server-owned, consented configuration of the exact existing local assets."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal[1]
    terms_accepted: StrictBool
    noncommercial: StrictBool
    source_dir: Path
    source_commit: Literal["8a1ef88672cc23c26619adaff37ec48d82c0b906"]
    model_path: Path
    model_sha256: Literal["bbd482c786b895cf7d8f44185073d951adae2ebb8a66f82ca84cd1f84569549c"]
    config_sha256: Literal["3008fc481e4a1cd978e337eb3759260c270892204db5039235ac939e1f42aeb2"]
    demucs_checkpoint: Path
    demucs_sha256: Literal["8726e21a993978c7ba086d3872e7608d7d5bfca646ca4aca459ffda844faa8b4"]
    demucs_python: Path
    muscriptor_python: Path

    @model_validator(mode="after")
    def validate_consent_and_paths(self) -> "WorkerManifest":
        """Require explicit consent and absolute local paths, never model URLs."""
        if not self.terms_accepted or not self.noncommercial:
            raise ValueError("Explicit terms acceptance and noncommercial use are required")
        for name in ("source_dir", "model_path", "demucs_checkpoint", "demucs_python", "muscriptor_python"):
            if not getattr(self, name).is_absolute():
                raise ValueError("Asset/interpreter paths must be absolute local paths")
        return self


class WorkerFailure(RuntimeError):
    """Stable public failure code; detailed exceptions stay in local diagnostics."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(SAFE_ERRORS[code])


class _NoBeatImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname: str, path: Any = None, target: Any = None) -> None:
        if fullname == "beat_this" or fullname.startswith("beat_this."):
            raise WorkerFailure("network_disabled")
        return None


def _deny_network(event: str, args: Any) -> None:
    if event in {"socket.connect", "socket.connect_ex", "socket.getaddrinfo", "socket.sendto"}:
        raise WorkerFailure("network_disabled")


def _forbidden(*args: Any, **kwargs: Any) -> None:
    raise WorkerFailure("network_disabled")


def _prctl_parent_signal() -> None:
    import ctypes
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, signal.SIGTERM, 0, 0, 0) != 0:  # PR_SET_PDEATHSIG
        raise WorkerFailure("worker_failed")


def _parent_death_signal() -> None:
    expected = os.environ.get("STEM2TAB_TRIAL_PARENT_PID")
    if expected is None:
        return
    try:
        pid = int(expected)
    except ValueError as exc:
        raise WorkerFailure("parent_gone") from exc
    before = os.getppid()
    if sys.platform != "linux" or pid <= 1 or before != pid:
        raise WorkerFailure("parent_gone")
    _prctl_parent_signal()
    if os.getppid() != before:
        raise WorkerFailure("parent_gone")


def _offline_process(output: Path) -> None:
    cache = output / "cache"
    (cache / "tmp").mkdir(parents=True)
    os.environ.update(HF_HUB_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1",
        HF_HUB_DISABLE_IMPLICIT_TOKEN="1", TRANSFORMERS_OFFLINE="1", DO_NOT_TRACK="1",
        HF_HOME=str(cache / "hf"), XDG_CACHE_HOME=str(cache), TORCH_HOME=str(cache / "torch"),
        DEMUCS_CACHEDIR=str(cache / "demucs"), CUDA_CACHE_PATH=str(cache / "cuda"),
        TORCH_EXTENSIONS_DIR=str(cache / "extensions"), TORCHINDUCTOR_CACHE_DIR=str(cache / "inductor"),
        TRITON_CACHE_DIR=str(cache / "triton"), NUMBA_CACHE_DIR=str(cache / "numba"),
        MPLCONFIGDIR=str(cache / "matplotlib"), TMPDIR=str(cache / "tmp"))
    for key in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "HUGGINGFACE_TOKEN"):
        os.environ.pop(key, None)
    sys.addaudithook(_deny_network)
    sys.meta_path.insert(0, _NoBeatImports())


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write("\n")


def _jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("x", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, allow_nan=False) + "\n")


def _verified_file(path: Path, expected: str) -> None:
    if not path.is_file() or path.is_symlink() or _sha(path) != expected:
        raise WorkerFailure("asset_changed")


def _git(source: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(source), *args], text=True, stderr=subprocess.PIPE).strip()


def _verify_assets(manifest: WorkerManifest, stage: str) -> dict[str, Any]:
    try:
        expected_python = manifest.demucs_python if stage == "separate" else manifest.muscriptor_python
        if Path(sys.executable).absolute() != expected_python.absolute():
            raise WorkerFailure("asset_changed")
        for interpreter in (manifest.demucs_python, manifest.muscriptor_python):
            if not interpreter.is_file():
                raise WorkerFailure("asset_changed")
        _verified_file(manifest.model_path, manifest.model_sha256)
        _verified_file(manifest.model_path.with_name("config.json"), manifest.config_sha256)
        _verified_file(manifest.demucs_checkpoint, manifest.demucs_sha256)
        if manifest.demucs_checkpoint.name != "955717e8-8726e21a.th":
            raise WorkerFailure("asset_changed")
        if (_git(manifest.source_dir, "rev-parse", "HEAD") != SOURCE_COMMIT or
                _git(manifest.source_dir, "status", "--porcelain", "--untracked-files=all")):
            raise WorkerFailure("asset_changed")
        source_hashes = {str(path.relative_to(manifest.source_dir)): _sha(path)
                         for path in sorted((manifest.source_dir / "muscriptor").rglob("*.py"))}
        if not source_hashes:
            raise WorkerFailure("asset_changed")
        return dict(source_commit=SOURCE_COMMIT, source_clean=True, source_sha256=source_hashes,
                    model_path=str(manifest.model_path), model_sha256=manifest.model_sha256,
                    config_sha256=manifest.config_sha256, demucs_checkpoint=str(manifest.demucs_checkpoint),
                    demucs_sha256=manifest.demucs_sha256, interpreter=sys.executable)
    except WorkerFailure:
        raise
    except Exception as exc:
        raise WorkerFailure("asset_changed") from exc


def _read_audio(path: Path) -> tuple[Any, int, dict[str, Any]]:
    import numpy as np
    import soundfile as sf
    try:
        before = _sha(path)
        with sf.SoundFile(path, mode="r") as source:
            sr, channels, frames = source.samplerate, source.channels, source.frames
            if (source.format not in {"WAV", "WAVEX", "FLAC"} or channels not in (1, 2) or
                    sr <= 0 or frames <= 0 or frames > 10 * sr):
                raise WorkerFailure("invalid_audio")
            data = source.read(dtype="float32", always_2d=True)
            format_name = source.format
        wave = np.ascontiguousarray(data.T)
        if wave.shape != (channels, frames) or not np.isfinite(wave).all():
            raise WorkerFailure("invalid_audio")
        if _sha(path) != before:
            raise WorkerFailure("invalid_audio")
        return wave, sr, dict(file_sha256=before, decoded_float32_sha256=hashlib.sha256(wave.tobytes()).hexdigest(),
             sample_rate=sr, channels=channels, frames=frames, seconds=frames / sr, format=format_name,
             layout="channels_first", dtype="float32", cropped=False, truncated=False)
    except WorkerFailure:
        raise
    except Exception as exc:
        raise WorkerFailure("invalid_audio") from exc


def _torch_runtime() -> Any:
    import torch
    for name in ("load", "load_state_dict_from_url", "download_url_to_file"):
        if hasattr(torch.hub, name):
            setattr(torch.hub, name, _forbidden)
    random.seed(0)
    torch.manual_seed(0)
    if torch.cuda.is_available():
        torch.cuda.set_device(0)
        torch.cuda.manual_seed_all(0)
        torch.cuda.reset_peak_memory_stats(0)
    torch.backends.cudnn.benchmark = False
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.backends.cudnn.allow_tf32 = False
    return torch


def _demucs_components() -> tuple[Any, Any, Any, Any]:
    from demucs.repo import LocalRepo
    from demucs.htdemucs import HTDemucs
    from demucs.audio import convert_audio
    from demucs.apply import apply_model
    return LocalRepo, HTDemucs, convert_audio, apply_model


def _load_local_demucs(manifest: WorkerManifest, torch: Any, local_repo: Any) -> Any:
    # Demucs4 stores a Python model class in its official .th package. Only the
    # exact full-hash-pinned local checkpoint may cross this pickle boundary.
    _verified_file(manifest.demucs_checkpoint, DEMUCS_SHA256)
    original = torch.load
    def trusted_local_load(path: Any, *args: Any, **kwargs: Any) -> Any:
        if not isinstance(path, (str, Path)) or Path(path).resolve() != manifest.demucs_checkpoint.resolve():
            raise WorkerFailure("asset_changed")
        _verified_file(manifest.demucs_checkpoint, DEMUCS_SHA256)
        kwargs["weights_only"] = False
        return original(path, *args, **kwargs)
    torch.load = trusted_local_load
    try:
        repo = local_repo(manifest.demucs_checkpoint.parent)
        if repo._models.get("955717e8") != manifest.demucs_checkpoint:
            raise WorkerFailure("asset_changed")
        return repo.get_model("955717e8")
    finally:
        torch.load = original


def _separate(manifest: WorkerManifest, wave: Any, sr: int, output: Path, torch: Any, state: dict[str, Any]) -> dict[str, Any]:
    import soundfile as sf
    started = time.perf_counter()
    local_repo, model_class, convert_audio, apply_model = _demucs_components()
    model = _load_local_demucs(manifest, torch, local_repo)
    if not isinstance(model, model_class):
        raise WorkerFailure("asset_changed")
    if list(model.sources).count("bass") != 1:
        raise WorkerFailure("bass_missing")
    model.cpu().eval()
    device = "cuda:0" if torch.cuda.is_available() else "cpu"
    initialization = time.perf_counter() - started
    started = time.perf_counter()
    wav = convert_audio(torch.from_numpy(wave), sr, model.samplerate, model.audio_channels)
    ref = wav.mean(0)
    mean, std = ref.mean(), ref.std()
    if not torch.isfinite(std).item() or std.item() <= 0:
        raise WorkerFailure("invalid_audio")
    normalized = (wav - mean) / std
    preprocess = time.perf_counter() - started
    started = time.perf_counter()
    state["model_calls"] += 1
    with torch.inference_mode():
        sources = apply_model(model, normalized[None], device=device, shifts=1, split=True,
                              overlap=.25, transition_power=1., progress=False, num_workers=0, segment=None)[0]
    if device.startswith("cuda"):
        torch.cuda.synchronize(0)
    inference = time.perf_counter() - started
    if sources.shape != (len(model.sources), model.audio_channels, wav.shape[-1]):
        raise WorkerFailure("invalid_model_output")
    bass = (sources[list(model.sources).index("bass")] * std + mean).detach().cpu()
    if not torch.isfinite(bass).all().item():
        raise WorkerFailure("invalid_model_output")
    path = output / "bass.wav"
    with path.open("xb") as stream:
        sf.write(stream, bass.numpy().T, model.samplerate, format="WAV", subtype="FLOAT")
    return dict(device=device, bass_path=str(path), bass_sha256=_sha(path),
                decoded_bass_float32_sha256=hashlib.sha256(bass.contiguous().numpy().tobytes()).hexdigest(),
                sample_rate=model.samplerate, channels=model.audio_channels, frames=bass.shape[-1],
                separator=dict(model="htdemucs", signature="955717e8", sources=list(model.sources),
                    seed=0, shifts=1, overlap=.25, split=True, segment_override=None,
                    official_model_segment=float(model.segment), normalization="official mono-reference mean/std; restore original scale",
                    output_format="WAV FLOAT", clipping=False, rescaling=False),
                times_seconds=dict(initialization=initialization, preprocess=preprocess, inference=inference))


def _muscriptor_components(manifest: WorkerManifest) -> Any:
    sys.path.insert(0, str(manifest.source_dir))
    import muscriptor.transcription_model as tm
    import muscriptor.events as events
    import muscriptor.utils.beats as beats
    if not Path(tm.__file__).resolve().is_relative_to(manifest.source_dir.resolve()):
        raise WorkerFailure("asset_changed")
    def local_only(path: Any) -> Path:
        if not isinstance(path, Path) or path.resolve() != manifest.model_path.resolve():
            raise WorkerFailure("asset_changed")
        _verified_file(path, manifest.model_sha256)
        return path
    tm.download_if_necessary = local_only
    tm.download_companion = _forbidden
    tm.detect_grid = beats.detect_grid = _forbidden
    return tm, events


def _serialize_events(events: list[Any], model: Any, event_types: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows, notes, opened = [], [], {}
    for ordinal, event in enumerate(events):
        if isinstance(event, event_types.ProgressEvent):
            rows.append(dict(type="progress", ordinal=ordinal, completed=event.completed, total=event.total))
            continue
        start = event if isinstance(event, event_types.NoteStartEvent) else event.start_event
        program = 128 if start.instrument == "drums" else model._program_for_instrument(start.instrument)
        fields = dict(index=start.index, pitch=start.pitch, instrument=start.instrument,
                      program=program, is_drum=program == 128)
        if isinstance(event, event_types.NoteStartEvent):
            if start.index in opened:
                raise WorkerFailure("invalid_model_output")
            opened[start.index] = dict(onset=start.start_time, **fields)
            rows.append(dict(type="note_start", ordinal=ordinal, start_time=start.start_time, **fields))
        elif isinstance(event, event_types.NoteEndEvent):
            if start.index not in opened:
                raise WorkerFailure("invalid_model_output")
            notes.append(dict(offset=event.end_time, **opened.pop(start.index)))
            rows.append(dict(type="note_end", ordinal=ordinal, end_time=event.end_time, **fields))
        else:
            raise WorkerFailure("invalid_model_output")
    if opened:
        raise WorkerFailure("invalid_model_output")
    return rows, notes


def _literal_midi(path: Path, notes: list[dict[str, Any]]) -> dict[str, Any]:
    import mido
    midi = mido.MidiFile(type=1, ticks_per_beat=10000)
    metadata = mido.MidiTrack()
    metadata.append(mido.MetaMessage("set_tempo", tempo=1000000, time=0))
    midi.tracks.append(metadata)
    programs = sorted({note["program"] for note in notes})
    channels = [channel for channel in range(16) if channel != 9]
    melodic = [program for program in programs if program != 128]
    if len(melodic) > 15 or any(type(program) is not int or not 0 <= program <= 128 for program in programs):
        raise WorkerFailure("invalid_model_output")
    mapping = dict(zip(melodic, channels))
    mapping[128] = 9
    for program in programs:
        track = mido.MidiTrack()
        midi.tracks.append(track)
        channel = mapping[program]
        if program != 128:
            track.append(mido.Message("program_change", program=program, channel=channel, time=0))
        messages = []
        for ordinal, note in enumerate(notes):
            if note["program"] != program:
                continue
            a, b, pitch = note["onset"], note["offset"], note["pitch"]
            if (not math.isfinite(a) or not math.isfinite(b) or not 0 <= a <= b or
                    type(pitch) is not int or not 0 <= pitch <= 127):
                raise WorkerFailure("invalid_model_output")
            messages.extend([(round(a * 10000), a, ordinal * 2, mido.Message("note_on", note=pitch, velocity=100, channel=channel)),
                             (round(b * 10000), b, ordinal * 2 + 1, mido.Message("note_off", note=pitch, velocity=0, channel=channel))])
        previous = 0
        for tick, _, _, message in sorted(messages, key=lambda row: row[:3]):
            message.time = tick - previous
            previous = tick
            track.append(message)
    with path.open("xb") as stream:
        midi.save(file=stream)
    return dict(timebase_bpm=60, ticks_per_beat=10000, maximum_rounding_error_seconds=.00005,
                velocity=100, velocity_reason="Official public events expose no velocity magnitude",
                program_to_channel=mapping, quantization=False, note_repair=False)


def _transcribe(manifest: WorkerManifest, wave: Any, sr: int, output: Path, torch: Any, state: dict[str, Any]) -> dict[str, Any]:
    if not torch.cuda.is_available():
        raise WorkerFailure("cuda_unavailable")
    started = time.perf_counter()
    tm, event_types = _muscriptor_components(manifest)
    model = tm.TranscriptionModel.load_model(weights_path=manifest.model_path, device="cuda:0", dtype="float32")
    torch.cuda.synchronize(0)
    initialization = time.perf_counter() - started
    tokens = []
    original = model._generate_token_stream
    def capture(*args: Any, **kwargs: Any) -> Any:
        for item in original(*args, **kwargs):
            if isinstance(item, int):
                token = model._tokenizer._vocab[item]
                tokens.append(dict(type="token", id=item, event_type=token.type, value=token.value))
            elif isinstance(item, event_types.ChunkBoundary):
                tokens.append(dict(type="chunk_boundary", seek_time=item.seek_time, next_seek_time=item.next_seek_time))
            else:
                tokens.append(dict(type="progress", completed=item.completed, total=item.total))
            yield item
    model._generate_token_stream = capture
    started = time.perf_counter()
    stream = model.transcribe((torch.from_numpy(wave), sr), **PARAMETERS)
    try:
        first = next(stream)
        if not isinstance(first, event_types.ProgressEvent) or first.completed != 0:
            raise WorkerFailure("invalid_model_output")
        torch.cuda.synchronize(0)
        preprocess = time.perf_counter() - started
        started = time.perf_counter()
        state["model_calls"] += 1
        with torch.inference_mode():
            events = [first, *stream]
        torch.cuda.synchronize(0)
        inference = time.perf_counter() - started
    finally:
        model._generate_token_stream = original
    started = time.perf_counter()
    rows, notes = _serialize_events(events, model, event_types)
    _json(output / "notes.json", notes)
    _jsonl(output / "events.jsonl", rows)
    _jsonl(output / "tokens.jsonl", tokens)
    serialization = time.perf_counter() - started
    started = time.perf_counter()
    midi = output / "performance.mid"
    midi_settings = _literal_midi(midi, notes)
    return dict(device="cuda:0", performance_midi=str(midi), performance_midi_sha256=_sha(midi),
                counts=dict(notes=len(notes), events=len(rows), stream_tokens=sum(row["type"] == "token" for row in tokens)),
                parameters=PARAMETERS, precision=dict(weights="float32", conditioning="float32", transformer_compute="official CUDA float16 autocast"),
                midi=midi_settings, unexpected_programs=sorted({note["program"] for note in notes} - {32, 33}),
                decoder_disclosure="Official streaming tie/retrigger/window/malformed-chunk and final10ms fallback rules retained; EOS already removed from captured stream",
                postprocessing=False, beat_detection=False,
                times_seconds=dict(initialization=initialization, preprocess=preprocess, inference=inference,
                                   raw_serialization=serialization, midi_export=time.perf_counter() - started))


def run_stage(stage: str, manifest_path: Path, input_path: Path, output: Path) -> dict[str, Any]:
    """Execute one bounded service-owned stage and write one terminal result."""
    began = time.perf_counter()
    try:
        output.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise WorkerFailure("output_exists") from exc
    result: dict[str, Any] = dict(status="started", stage=stage, worker_code_sha256=_sha(Path(__file__)),
                                 automatic_retry=False, model_calls=0, times_seconds={})
    torch = None
    try:
        _parent_death_signal()
        _offline_process(output)
        started = time.perf_counter()
        try:
            manifest = WorkerManifest.model_validate_json(manifest_path.read_text())
        except Exception as exc:
            raise WorkerFailure("invalid_manifest") from exc
        result["manifest_sha256"] = _sha(manifest_path)
        result["provenance"] = _verify_assets(manifest, stage)
        result["times_seconds"]["validation"] = time.perf_counter() - started
        started = time.perf_counter()
        wave, sr, audio = _read_audio(input_path)
        result["input"] = dict(path=str(input_path), **audio)
        result["times_seconds"]["audio_decode"] = time.perf_counter() - started
        _json(output / "started.json", result)
        with (output / "backend.log").open("x", encoding="utf-8") as log, contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            started = time.perf_counter()
            torch = _torch_runtime()
            result["times_seconds"]["runtime_import"] = time.perf_counter() - started
            stage_result = (_separate if stage == "separate" else _transcribe)(manifest, wave, sr, output, torch, result)
        result["times_seconds"].update(stage_result.pop("times_seconds"))
        result.update(stage_result)
        if _sha(input_path) != audio["file_sha256"]:
            raise WorkerFailure("invalid_audio")
        _verify_assets(manifest, stage)
        result["assets_verified_after"] = True
        result["status"] = "completed"
    except Exception as exc:
        code = exc.code if isinstance(exc, WorkerFailure) else "worker_failed"
        result.update(status="failed", error=dict(code=code, message=SAFE_ERRORS[code]))
        _json(output / "failure-detail.json", dict(exception=repr(exc), traceback=traceback.format_exc()))
    finally:
        if torch is not None and torch.cuda.is_initialized():
            result["cuda_peak"] = dict(allocated_bytes=torch.cuda.max_memory_allocated(0),
                                       reserved_bytes=torch.cuda.max_memory_reserved(0))
        else:
            result["cuda_peak"] = None
        result["times_seconds"]["total"] = time.perf_counter() - began
        result["gpu_lifetime"] = "Worker subprocess lifetime; service confirms process exit before next stage"
        _json(output / "result.json", result)
    return result


def main(argv: list[str] | None = None) -> int:
    """CLI contract consumed by the opt-in service; stdout contains safe status only."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("separate", "transcribe"), required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = run_stage(args.stage, args.manifest, args.input, args.output)
    except WorkerFailure as exc:
        print(json.dumps(dict(status="failed", code=exc.code)))
        return 1
    print(json.dumps(dict(status=result["status"], **({"code": result["error"]["code"]} if result["status"] == "failed" else {}))))
    return 0 if result["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
