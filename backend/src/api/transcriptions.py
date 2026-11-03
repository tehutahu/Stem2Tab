"""MuScriptor-first local workflow. One GPU slot, durable attempts, no fallback.

The official model owns its sequential five-second chunks and tie continuity.
A retry starts a new attempt; incomplete model streams are never spliced together.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import os
from pathlib import Path
import shutil
import threading
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
import numpy as np
from pydantic import BaseModel, ConfigDict, model_validator
import soundfile as sf

from src.api import midi_trials, muscriptor_trials as trial
from src.pipelines.bass_output import BassOutputConfig
from src.pipelines import local_audio_input
from src.pipelines.muscriptor_decoder import DecoderIntegrity
from src.pipelines import model_output_review
from src.api.model_output_reviews import REVIEW_FILES, read_review_source

router = APIRouter(prefix="/api/v1/transcriptions", tags=["MuScriptor workflow"])
MAX_SECONDS = 600
MAX_BYTES = local_audio_input.MAX_UPLOAD_BYTES
_TERMINAL_LOCK = threading.RLock()
ARTIFACTS = {"original.audio": "original.audio", "selection.wav": "selection.wav",
             "bass.wav": "separate/bass.wav", "performance.mid": "transcribe/performance.mid"}


def _artifact_hashes(folder: Path) -> dict[str, str]:
    """Only verified stage records authorize downloads of derived artifacts."""
    def read(name: str) -> dict:
        path = folder/name
        try:
            value = json.loads(path.read_text()) if path.is_file() and not path.is_symlink() else {}
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}
    request = read("request.json")
    checkpoint = read("separation-checkpoint.json")
    completion = read("completion.json")
    stage = read("transcribe/result.json")
    raw_sha = completion.get("raw_midi_sha256") or (stage.get("performance_midi_sha256") if stage.get("status") == "completed" else None)
    values = {"original.audio": request.get("original_sha256"), "selection.wav": request.get("selection_sha256"),
              "bass.wav": checkpoint.get("bass_sha256"), "performance.mid": raw_sha}
    return {name: value for name, value in values.items() if isinstance(value, str) and
            len(value) == 64 and all(c in "0123456789abcdef" for c in value)}


class Job(BaseModel):
    """Persisted lifecycle; progress counts model chunks, never guessed accuracy."""
    model_config = ConfigDict(extra="forbid")
    job_id: str
    status: Literal["running", "completed", "failed", "cancelled", "needs_review"]
    stage: str
    midi_trial_id: str | None = None
    error: trial.TrialError | None = None
    cancel_requested: bool = False
    completed_chunks: int = 0
    total_chunks: int = 0
    accuracy_evaluated: Literal[False] = False
    available_files: tuple[str, ...] = ()
    review: model_output_review.OutputReviewInfo | None = None

    @model_validator(mode="after")
    def review_contract(self) -> "Job":
        if (self.status == "needs_review") != (self.review is not None):
            raise ValueError("Review state requires its retained-output manifest")
        return self


def _base() -> Path:
    base = trial._base()/"songs"
    if base.is_symlink():
        trial._reject(409, "unsafe_storage", "Workflow storage is unavailable.")
    base.mkdir(exist_ok=True)
    return base


def _folder(identifier: str, *, exists: bool = True) -> Path:
    try:
        parsed = UUID(identifier)
        if parsed.version != 4 or str(parsed) != identifier:
            raise ValueError
    except ValueError:
        trial._reject(404, "job_not_found", "Transcription not found.")
    folder = _base()/identifier
    if folder.is_symlink() or (exists and not folder.is_dir()):
        trial._reject(404, "job_not_found", "Transcription not found.")
    return folder


def _state(folder: Path, *, stage: str, status: str = "running", **kwargs) -> Job:
    with _TERMINAL_LOCK:
        if status == "completed":
            _cancelled(folder)
        source = read_review_source(folder) if status == "needs_review" else None
        if status == "needs_review" and source is None:
            raise trial.TrialFailure("invalid_review_output", "The model output review receipt is missing; raw data was retained.")
        state = Job(job_id=folder.name, stage=stage, status=status,
                    review=source.manifest.info if source is not None else None, **kwargs)
        if status != "running":
            hashes = _artifact_hashes(folder)
            state.available_files = tuple(name for name, relative in ARTIFACTS.items() if name in hashes
                if (folder/relative).is_file() and not (folder/relative).is_symlink())
            if source is not None:
                state.available_files += tuple(REVIEW_FILES)
        trial._write_json(folder/"status.json", state.model_dump(mode="json"))
    return state


def _cancelled(folder: Path) -> None:
    if (folder/"cancel.requested").exists():
        raise trial.TrialFailure("cancelled", "Processing stopped; original and partial outputs are retained.")


def _verify_inputs(folder: Path, request: dict) -> None:
    """Never bless modified or previously unverified source bytes on retry."""
    try:
        if any((folder/name).is_symlink() for name in ("original.audio", "selection.wav")):
            raise ValueError("symlink")
        expected = (request["original_sha256"], request["selection_sha256"])
        if any(trial._sha(folder/name) != digest for name, digest in zip(("original.audio", "selection.wav"), expected)):
            raise ValueError("changed input")
    except (OSError, KeyError, ValueError) as exc:
        raise trial.TrialFailure("input_changed", "Saved input is changed or lacks a trusted hash; no retry or transcription was started.") from exc


def _selection(data: bytes, filename: str, start: float, end: float | None) -> tuple[bytes, dict]:
    extension = Path(filename).suffix.lower()
    if extension not in local_audio_input.SUPPORTED_EXTENSIONS:
        trial._reject(415, "unsupported_audio", "Select WAV, FLAC, M4A or Ogg/Opus.")
    if extension in local_audio_input.COMPRESSED_EXTENSIONS:
        try:
            return local_audio_input.decode_selection(data, extension, start, end, max_seconds=MAX_SECONDS)
        except local_audio_input.AudioSelectionError as exc:
            trial._reject(exc.status, exc.code, exc.message)
    try:
        with sf.SoundFile(io.BytesIO(data)) as audio:
            if audio.format not in {"WAV", "WAVEX", "FLAC"} or audio.channels not in (1, 2) or not 8000 <= audio.samplerate <= 96000:
                raise ValueError("audio format")
            duration = len(audio)/audio.samplerate
            finish = duration if end is None else end
            if not math.isfinite(start) or not math.isfinite(finish) or not 0 <= start < finish <= duration:
                raise ValueError("selection bounds")
            a, b = round(start*audio.samplerate), round(finish*audio.samplerate)
            if not 0 < b-a <= MAX_SECONDS*audio.samplerate:
                trial._reject(422, "duration_limit", "Select at most 600 seconds. Longer songs require an explicit shorter interval.")
            audio.seek(a)
            samples = audio.read(b-a, dtype="float32", always_2d=True)
            if len(samples) != b-a or not np.isfinite(samples).all():
                raise ValueError("invalid samples")
            stream = io.BytesIO()
            sf.write(stream, samples, audio.samplerate, format="WAV", subtype="FLOAT")
            return stream.getvalue(), dict(source_duration_seconds=duration, start_frame=a, end_frame=b,
                source_format=audio.format,
                sample_rate=audio.samplerate, selected_seconds=(b-a)/audio.samplerate,
                note_time_origin="selection_start", source_origin_seconds=a/audio.samplerate)
    except (OSError, RuntimeError, ValueError):
        trial._reject(422, "invalid_selection", "Audio or selected interval is invalid. Nothing was silently cropped.")


@router.get("/health")
def health() -> dict:
    """API/assets readiness without GPU allocation, downloads or license acceptance."""
    available = trial.availability()
    return dict(api="ready", model_assets="configured" if available["available"] else "unavailable",
                reasons=available["reasons"], inference_verified=False, cuda="checked_at_execution",
                method="muscriptor-small", gpu_concurrency=1, max_audio_seconds=MAX_SECONDS,
                max_upload_bytes=MAX_BYTES, chunk_seconds=5, continuity="official-prelude-forcing",
                required_services=["web", "api", "local_model_runtimes"],
                legacy_basic_pitch="optional: requires Redis and Celery; never an automatic fallback")


def _run(folder: Path, deployment: trial.Deployment, slot: int, reuse: Path | None) -> None:
    try:
        request = json.loads((folder/"request.json").read_text())
        _verify_inputs(folder, request)
        _cancelled(folder)
        bass = folder/"separate"/"bass.wav"
        if reuse:
            bass.parent.mkdir()
            shutil.copyfile(reuse, bass)
            checkpoint = json.loads((reuse.parents[1]/"separation-checkpoint.json").read_text())
            if trial._sha(bass) != checkpoint["bass_sha256"]:
                raise trial.TrialFailure("checkpoint_changed", "The saved stem changed during reuse; no transcription was started.")
            trial._write_json(bass.parent/"reused.json", dict(source_attempt=request["retry_of"], bass_sha256=trial._sha(bass)))
        else:
            _state(folder, stage="separating")
            trial._run_stage("separate", deployment, folder, folder/"selection.wav", slot)
        if not bass.is_file() or bass.is_symlink():
            raise trial.TrialFailure("bass_missing", "The bass stem is missing; no other stem was substituted.")
        trial._write_json(folder/"separation-checkpoint.json", dict(input_sha256=trial._sha(folder/"selection.wav"),
            bass_sha256=trial._sha(bass), worker_sha256=trial._sha(trial.WORKER),
            deployment_sha256=trial._sha(folder/"deployment.json")))
        _cancelled(folder)
        _state(folder, stage="transcribing")
        transcribed = trial._run_stage("transcribe", deployment, folder, bass, slot)
        _cancelled(folder)
        _verify_inputs(folder, request)
        if transcribed.get("status") == "needs_review":
            _state(folder, status="needs_review", stage="output_needs_review",
                   error=trial.TrialError(code="model_output_review_required", message="推論出力を保存しました。ゼロ長音の扱いを譜面化前に確認してください。"))
            return
        midi = folder/"transcribe"/"performance.mid"
        if not midi.is_file() or midi.is_symlink() or midi.stat().st_size > midi_trials.MAX_UPLOAD_BYTES:
            raise trial.TrialFailure("missing_midi", "Raw MIDI is missing or exceeds the notation import limit.")
        _state(folder, stage="exporting")
        converted = midi_trials.import_midi_bytes(midi.read_bytes(), filename="muscriptor-performance.mid",
            config=BassOutputConfig.model_validate(request["config"]), source_kind="muscriptor-small", preview_requested=False)
        if isinstance(converted, JSONResponse) or converted.status == "failed":
            raise trial.TrialFailure("notation_export_failed", "Raw MIDI was retained; notation export failed.")
        _cancelled(folder)
        trial._write_json(folder/"completion.json", dict(midi_trial_id=converted.trial_id,
            raw_midi_sha256=trial._sha(midi), selection=request["selection"], accuracy_evaluated=False,
            correction_applied=False, chunking="official-5s-prelude-forcing-batch1", inference="local-model-worker"))
        _state(folder, status="completed", stage="completed", midi_trial_id=converted.trial_id)
    except trial.TrialFailure as exc:
        _state(folder, status="cancelled" if exc.error.code == "cancelled" else "failed", stage=exc.error.code, error=exc.error)
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        _state(folder, status="failed", stage="notation_rejected", error=trial.TrialError(
            code=detail.get("code", "notation_rejected"),
            message=detail.get("message", "Raw MIDI was retained, but strict notation conversion rejected it. No note repair was performed.")))
    except Exception:
        trial.logger.exception("transcription_failed", job_id=folder.name)
        _state(folder, status="failed", stage="failed", error=trial.TrialError(code="workflow_failed",
            message="Processing failed. Original and raw outputs remain local; no automatic retry or repair was made."))
    finally:
        os.close(slot)


def _launch(folder: Path, deployment: trial.Deployment, slot: int, reuse: Path | None = None) -> None:
    threading.Thread(target=_run, args=(folder, deployment, slot, reuse), daemon=True,
                     name=f"song-{folder.name}").start()


def _start(identifier: str, original: bytes, selected: bytes, request: dict, deployment: trial.Deployment,
           reuse: Path | None = None) -> Job:
    folder = _folder(identifier, exists=False)
    # Decoded selection is deterministic; generated WAV headers may contain a
    # creation timestamp. Identity belongs to original bytes plus frame/settings.
    payload_sha = hashlib.sha256(original+json.dumps(request, sort_keys=True).encode()).hexdigest()
    request = dict(**request, original_sha256=hashlib.sha256(original).hexdigest(),
                   selection_sha256=hashlib.sha256(selected).hexdigest())
    def existing() -> Job | None:
        if not folder.exists():
            return None
        try:
            saved = json.loads((folder/"request.json").read_text())
        except (OSError, ValueError):
            trial._reject(409, "invalid_status", "The existing request is incomplete; it was not overwritten.")
        if not isinstance(saved, dict):
            trial._reject(409, "invalid_status", "The existing request is incomplete; it was not overwritten.")
        if saved.get("payload_sha256") != payload_sha:
            trial._reject(409, "request_conflict", "This request ID already represents different input or settings.")
        return get_job(identifier)
    prior = existing()
    if prior is not None:
        return prior
    if trial._SHUTDOWN.is_set():
        trial._reject(503, "server_stopping", "The API is stopping; no process was started.")
    slot = trial._acquire_slot(trial._base())  # shared with the original 10-second route
    created = False
    try:
        # A fast prior attempt may have finished while this request acquired the
        # slot. Only the creator may write status or data in this folder.
        prior = existing()
        if prior is not None:
            os.close(slot)
            return prior
        folder.mkdir(exist_ok=False)
        created = True
        (folder/"original.audio").write_bytes(original)
        (folder/"selection.wav").write_bytes(selected)
        trial._write_json(folder/"deployment.json", deployment.model_dump(mode="json"))
        trial._write_json(folder/"workflow.json", dict(max_audio_seconds=MAX_SECONDS, stage_timeout_seconds=7200))
        trial._write_json(folder/"request.json", dict(**request, payload_sha256=payload_sha,
            owner_pid=os.getpid(), owner_token=trial._owner_token(os.getpid())))
        state = _state(folder, stage="queued")
        _launch(folder, deployment, slot, reuse)
        return state
    except Exception:
        os.close(slot)
        if created:
            _state(folder, status="failed", stage="launch_failed", error=trial.TrialError(code="launch_failed", message="The job could not be launched; input was retained."))
        raise


@router.post("", response_model=Job, status_code=202)
def create_job(file: UploadFile = File(...), request_id: str = Form(...), rights_confirmed: str = Form(...),
               start_seconds: float = Form(0), end_seconds: float | None = Form(None),
               profile: str = Form("bass4"), max_fret: str = Form("24")) -> Job:
    """Keep source bytes and explicitly select full audio or a sample-aligned interval."""
    _folder(request_id, exists=False)
    if rights_confirmed != "true":
        trial._reject(422, "rights_required", "Confirm rights and noncommercial model use before processing.")
    config, _ = midi_trials._config(profile, None, None, max_fret, "muscriptor-small", "false")
    data = file.file.read(MAX_BYTES+1)
    if not data:
        trial._reject(422, "empty_audio", local_audio_input.EMPTY_AUDIO_MESSAGE)
    if len(data) > MAX_BYTES:
        trial._reject(413, "upload_limit", "Maximum upload size is 512 MiB.")
    try:
        deployment = trial._load_deployment()
    except trial.TrialFailure as exc:
        trial._reject(503, exc.error.code, exc.error.message)
    selected, selection = _selection(data, file.filename or "", start_seconds, end_seconds)
    return _start(request_id, data, selected, dict(selection=selection, config=config.model_dump(mode="json"),
        method="muscriptor-small", rights_confirmed=True, retry_of=None), deployment)


@router.get("/{job_id}", response_model=Job)
def get_job(job_id: str) -> Job:
    """Recover a durable job after reload; interruption is a failure, never success."""
    folder = _folder(job_id)
    try:
        state = Job.model_validate_json((folder/"status.json").read_text())
        if state.job_id != job_id:
            raise ValueError("id mismatch")
        if state.status == "running":
            request = json.loads((folder/"request.json").read_text())
            if trial._owner_token(request["owner_pid"]) != request["owner_token"] or request["owner_token"] is None:
                return _state(folder, status="failed", stage="interrupted", error=trial.TrialError(code="interrupted",
                    message="The owning API stopped. Retry explicitly; partial outputs are retained."))
            state.cancel_requested = (folder/"cancel.requested").exists()
            progress = folder/"transcribe"/"progress.json"
            if progress.is_file():
                value = json.loads(progress.read_text())
                completed, total = value.get("completed"), value.get("total")
                if type(completed) is int and type(total) is int and 0 <= completed <= total <= 120:
                    state.completed_chunks, state.total_chunks = completed, total
        if state.status == "needs_review" or (state.status == "failed" and state.stage == "invalid_model_output"):
            try:
                source = read_review_source(folder)
                if source is not None:
                    info = source.manifest.info
                    state = Job.model_validate({**state.model_dump(mode="json"),
                        "status": "needs_review", "stage": "output_needs_review", "review": info,
                        "available_files": tuple(dict.fromkeys((*state.available_files, *REVIEW_FILES))),
                        "error": trial.TrialError(code="model_output_review_required", message=(
                            f"推論出力の全{info.raw_note_count}音を保存しました。ゼロ長{info.zero_duration_count}件があるため、譜面化は確認待ちです。"
                            "生MIDIと元音符は全件保持しています。除外する場合は明示した別版として作成できます。"))})
                elif state.status == "needs_review":
                    raise model_output_review.ReviewDataError("Review receipt missing")
            except (model_output_review.ReviewDataError, OSError, ValueError, KeyError):
                state = Job.model_validate({**state.model_dump(mode="json"), "status": "failed", "stage": "review_source_changed", "review": None,
                    "available_files": tuple(name for name in state.available_files if name not in REVIEW_FILES),
                    "error": trial.TrialError(code="review_source_changed", message="保存出力の整合性を確認できません。原データを保持し、候補作成を停止しています。")})
        return _explain_output_failure(folder, state)
    except (OSError, ValueError, KeyError):
        trial._reject(409, "invalid_status", "Job state is unreadable. Do not create another request until checked.")


def _explain_output_failure(folder: Path, state: Job) -> Job:
    """Explain retained diagnostics without rewriting the failed job or notes."""
    if state.status != "failed" or state.stage != "invalid_model_output":
        return state
    diagnostic = folder/"transcribe"/"decoder-integrity.json"
    if not diagnostic.is_file() or diagnostic.is_symlink():
        return state
    try:
        integrity = DecoderIntegrity.model_validate_json(diagnostic.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        trial.logger.warning("output_diagnostic_unreadable", job_id=folder.name)
        return state
    if integrity.status != "invalid":
        return state
    kinds = [violation.kind for violation in integrity.note_violations]
    details = []
    for kind, label in (("zero_duration", "開始と終了が同時刻"),
                        ("negative_duration", "終了が開始より前"), ("invalid_boundary", "時刻が不正")):
        count = kinds.count(kind)
        if count:
            details.append(f"{label}の音が{count}件")
    if integrity.shift_regressions:
        details.append(f"区間内の時刻逆行が{len(integrity.shift_regressions)}件")
    if not details:
        return state
    message = f"採譜後の検査で、出力{integrity.raw_note_count}音中、{'、'.join(details)}見つかりました。"
    message += "自動で音を削除・延長せず、MIDI化を停止しました。原音源・分離音・元の音符とトークンは保存されています。"
    return state.model_copy(update={"error": trial.TrialError(code="invalid_model_output", message=message)})


@router.post("/{job_id}/cancel", response_model=Job, status_code=202)
def cancel_job(job_id: str) -> Job:
    """Request actual child termination; terminal cancellation waits for process exit."""
    with _TERMINAL_LOCK:
        state = get_job(job_id)
        if state.status == "running":
            (_folder(job_id)/"cancel.requested").touch(exist_ok=True)
            return get_job(job_id)
        return state


@router.post("/{job_id}/retry", response_model=Job, status_code=202)
def retry_job(job_id: str, request_id: str = Form(...)) -> Job:
    """New attempt; reuse only a matching complete separation checkpoint."""
    state = get_job(job_id)
    if state.status not in {"failed", "cancelled"}:
        trial._reject(409, "not_retryable", "Only a confirmed failed or cancelled attempt can be retried.")
    old = _folder(job_id)
    request = json.loads((old/"request.json").read_text())
    try:
        _verify_inputs(old, request)
        original, selected = (old/"original.audio").read_bytes(), (old/"selection.wav").read_bytes()
        if (hashlib.sha256(original).hexdigest(), hashlib.sha256(selected).hexdigest()) != (request["original_sha256"], request["selection_sha256"]):
            raise trial.TrialFailure("input_changed", "Saved input changed during retry; no new attempt was started.")
        deployment = trial._load_deployment()
    except trial.TrialFailure as exc:
        trial._reject(409 if exc.error.code == "input_changed" else 503, exc.error.code, exc.error.message)
    expected = dict(input_sha256=trial._sha(old/"selection.wav"), worker_sha256=trial._sha(trial.WORKER),
                    deployment_sha256=hashlib.sha256((json.dumps(deployment.model_dump(mode="json"), indent=2)+"\n").encode()).hexdigest())
    checkpoint = old/"separation-checkpoint.json"
    bass = old/"separate"/"bass.wav"
    reuse = None
    if checkpoint.is_file() and bass.is_file() and not bass.is_symlink():
        try:
            saved = json.loads(checkpoint.read_text())
            if not isinstance(saved, dict):
                raise ValueError("checkpoint object required")
        except (OSError, ValueError):
            trial._reject(409, "invalid_checkpoint", "The separation checkpoint is unreadable; no retry was started.")
        if all(saved.get(k) == v for k, v in expected.items()) and saved.get("bass_sha256") == trial._sha(bass):
            reuse = bass
    return _start(request_id, original, selected,
        dict(selection=request["selection"], config=request["config"], method="muscriptor-small",
             rights_confirmed=True, retry_of=job_id), deployment, reuse)


@router.get("/{job_id}/files/{name}")
def download(job_id: str, name: str) -> FileResponse:
    """Offer original/selected/bass/raw MIDI independently of notation success."""
    if name not in ARTIFACTS and name not in REVIEW_FILES:
        trial._reject(404, "file_not_found", "Artifact not found.")
    folder = _folder(job_id)
    if get_job(job_id).status == "running":
        trial._reject(409, "artifact_not_ready", "Wait for processing to stop before downloading output.")
    if name in REVIEW_FILES:
        try:
            source = read_review_source(folder)
        except (model_output_review.ReviewDataError, OSError, ValueError, KeyError):
            trial._reject(409, "review_source_changed", "保存出力が変更または不完全なため、生出力の取得を停止しました。")
        if source is None:
            trial._reject(404, "file_not_found", "Verified raw model output is not available.")
        return FileResponse(source.path(name), filename=name)
    path = folder/ARTIFACTS[name]
    if not path.is_file() or any(p.is_symlink() for p in (path, path.parent)):
        trial._reject(404, "file_not_found", "Artifact not available; partial output is never substituted.")
    expected = _artifact_hashes(folder).get(name)
    if expected is None or trial._sha(path) != expected:
        trial._reject(409, "artifact_changed", "The artifact is incomplete, unverified or changed; raw local data was not overwritten.")
    filename = name
    if name in {"original.audio", "selection.wav"}:
        try:
            _verify_inputs(folder, json.loads((folder/"request.json").read_text()))
        except trial.TrialFailure as exc:
            trial._reject(409, exc.error.code, exc.error.message)
    if name == "original.audio":
        request = json.loads((folder/"request.json").read_text())
        selection = request["selection"]
        extension = selection.get("source_extension")
        if extension not in local_audio_input.SUPPORTED_EXTENSIONS:
            extension = ".flac" if selection["source_format"] == "FLAC" else ".wav"
        filename = "original"+extension
    return FileResponse(path, filename=filename)
