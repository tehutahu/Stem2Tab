"""Explicit CPU audio tempo suggestions with verified source correspondence."""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import io
import json
import math
import os
from pathlib import Path
import stat
from typing import Annotated, Any, BinaryIO, Callable, Coroutine, Literal

from fastapi import APIRouter, File, HTTPException, Request, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from src.api import midi_trials
from src.core.config import settings
from src.evaluation.audio_tempo import AudioInfo, TempoAnalysis, TempoAudioError, estimate_audio_tempo, inspect_audio

class TempoRoute(APIRoute):
    """Keep validation errors inside this router's safe public error envelope."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def route(request: Request) -> Response:
            try:
                return await handler(request)
            except RequestValidationError as exc:
                raise HTTPException(422, detail={"code": "invalid_tempo_request", "message": "Use the current verified source descriptor or one supported WAV/FLAC upload."}) from exc
        return route


router = APIRouter(prefix="/api/v1/tempo-estimates", tags=["audio tempo evidence"], route_class=TempoRoute)
MAX_UPLOAD_BYTES = 32*1024**2
MAX_STORED_BYTES = 512*1024**2
MAX_UPLOAD_SECONDS = 120
MAX_STORED_SECONDS = 600
UUID4 = Annotated[str, Field(pattern=r"^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$")]
SHA256 = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class UploadedAudio(BaseModel):
    """Caller-selected audio whose correspondence to any MIDI is unverified."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)
    kind: Literal["uploaded-audio"] = "uploaded-audio"
    audio_sha256: SHA256
    audio_bytes: StrictInt = Field(gt=0, le=MAX_UPLOAD_BYTES)
    duration_seconds: float = Field(gt=0, le=MAX_UPLOAD_SECONDS, strict=True)
    sample_rate: StrictInt = Field(ge=8000, le=96000)
    channels: StrictInt = Field(ge=1, le=2)
    format: Literal["WAV", "FLAC"]
    correspondence: Literal["unverified-upload"] = "unverified-upload"


class StoredSelection(BaseModel):
    """The exact audio selection verified against a completed MIDI receipt."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)
    kind: Literal["stored-selection"] = "stored-selection"
    transcription_job_id: UUID4
    midi_trial_id: UUID4
    midi_sha256: SHA256
    audio_sha256: SHA256
    audio_bytes: StrictInt = Field(gt=0, le=MAX_STORED_BYTES)
    duration_seconds: float = Field(gt=0, le=MAX_STORED_SECONDS, strict=True)
    source_origin_seconds: float = Field(ge=0, strict=True)
    sample_rate: StrictInt = Field(ge=8000, le=96000)
    channels: StrictInt = Field(ge=1, le=2)
    format: Literal["WAV"] = "WAV"
    correspondence: Literal["verified-completed-job", "verified-review-preview"]


class TempoEstimate(TempoAnalysis):
    """An ephemeral audio-only report with a hash-identified source."""

    schema_version: Literal["1.0"] = "1.0"
    source: Annotated[StoredSelection | UploadedAudio, Field(discriminator="kind")]


def _reject(status_code: int, code: str, message: str) -> None:
    raise HTTPException(status_code, detail={"code": code, "message": message})


def _unsafe(path: Path) -> bool:
    return any(parent.is_symlink() for parent in (path, *path.parents))


@contextmanager
def _open_source(path: Path, max_bytes: int):
    if _unsafe(path):
        _reject(409, "source_unavailable", "The audio or source receipt is unavailable or changed.")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                _reject(409, "source_unavailable", "Only verified regular local source files are supported.")
            if info.st_size > max_bytes:
                _reject(413, "source_size_limit", "The source exceeds the bounded tempo-analysis byte limit.")
            if info.st_size == 0:
                _reject(422, "empty_audio", "The selected source file is empty.")
            yield stream
    except OSError as exc:
        raise HTTPException(409, detail={"code": "source_unavailable", "message": "The audio or source receipt is unavailable or changed."}) from exc


def _digest(stream: BinaryIO, max_bytes: int = MAX_STORED_BYTES) -> tuple[str, int]:
    stream.seek(0)
    digest, size = hashlib.sha256(), 0
    while block := stream.read(1024**2):
        size += len(block)
        if size > max_bytes:
            _reject(413, "source_size_limit", "The source exceeds the bounded tempo-analysis byte limit.")
        digest.update(block)
    stream.seek(0)
    return digest.hexdigest(), size


def _hash(path: Path, max_bytes: int) -> str:
    with _open_source(path, max_bytes) as stream:
        return _digest(stream, max_bytes)[0]


def _record(path: Path) -> dict:
    with _open_source(path, 1024**2) as stream:
        try:
            data = stream.read(1024**2+1)
            if len(data) > 1024**2:
                raise ValueError("Receipt too large")
            value = json.loads(data)
            if not isinstance(value, dict):
                raise ValueError("Expected a record")
            return value
        except (ValueError, UnicodeError) as exc:
            raise HTTPException(409, detail={"code": "invalid_receipt", "message": "The saved audio/MIDI correspondence receipt is invalid."}) from exc


def _saved_midi(identifier: str) -> midi_trials.MidiTrialSummary:
    folder = midi_trials._trial_dir(identifier)
    summary = midi_trials._read_summary(folder)
    original = next((artifact for artifact in summary.artifacts if artifact.key == "original-midi"), None)
    path = midi_trials._artifact_path(folder, "original-midi")
    if summary.trial_id != identifier or summary.status == "failed" or original is None or path is None:
        _reject(409, "midi_unavailable", "The completed saved MIDI and its original bytes are required.")
    # Size and hash must describe the same opened source. A resolved path may
    # disappear before it is opened, including during final revalidation.
    with _open_source(path, midi_trials.MAX_UPLOAD_BYTES) as stream:
        digest, size = _digest(stream, midi_trials.MAX_UPLOAD_BYTES)
    if original.sha256 != summary.input_sha256 or (digest, size) != (summary.input_sha256, original.bytes):
        _reject(409, "midi_changed", "The saved MIDI changed after its correspondence receipt was recorded.")
    return summary


def _normal_binding(folder: Path, state: dict, summary: midi_trials.MidiTrialSummary, request: dict) -> bool:
    if state.get("status") != "completed" or state.get("midi_trial_id") != summary.trial_id:
        return False
    completion = _record(folder/"completion.json")
    if completion.get("midi_trial_id") != summary.trial_id or completion.get("raw_midi_sha256") != summary.input_sha256 or completion.get("selection") != request.get("selection"):
        _reject(409, "correspondence_changed", "The completed audio/MIDI correspondence no longer matches the saved result.")
    if _hash(folder/"transcribe/performance.mid", midi_trials.MAX_UPLOAD_BYTES) != summary.input_sha256:
        _reject(409, "correspondence_changed", "The retained transcription MIDI differs from the saved result.")
    return True


def _preview_binding(folder: Path, summary: midi_trials.MidiTrialSummary) -> bool:
    from src.api.model_output_reviews import ReviewPreview, _source, _preview_folder
    from src.pipelines import model_output_review as review

    path = _preview_folder(folder, summary.trial_id)
    if not path.is_dir():
        return False
    # Read only complete receipts. Do not call recovery APIs that can write a
    # pending preview result, acquire model slots, or rerun conversion.
    try:
        plan = _record(path/"request.json")
        result = ReviewPreview.model_validate(_record(path/"result.json"))
        source = _source(folder)
        info = source.manifest.info
        if (result.status != "completed" or result.request_id != summary.trial_id or
                result.midi_trial_id != summary.trial_id or result.source_job_id != folder.name or
                result.policy != "exclude_zero_duration" or
                (result.raw_note_count, result.excluded_note_count, result.retained_note_count) !=
                (info.raw_note_count, info.zero_duration_count, info.positive_note_count) or
                summary.input_note_count != info.positive_note_count or
                plan.get("identity") != dict(source_manifest_sha256=review.digest(source.manifest_path), policy=result.policy) or
                plan.get("preview_sha256") != summary.input_sha256 or
                _hash(path/"preview.mid", midi_trials.MAX_UPLOAD_BYTES) != summary.input_sha256):
            raise ValueError("Review correspondence differs")
        planned = ReviewPreview.model_validate(plan["state"])
        if (planned.request_id, planned.source_job_id, planned.policy, planned.raw_note_count,
                planned.excluded_note_count, planned.retained_note_count) != (
                result.request_id, result.source_job_id, result.policy, result.raw_note_count,
                result.excluded_note_count, result.retained_note_count):
            raise ValueError("Review plan differs")
        return True
    except (ValueError, KeyError, OSError, review.ReviewDataError) as exc:
        raise HTTPException(409, detail={"code": "correspondence_changed", "message": "The completed review candidate is not verifiably bound to this audio and saved MIDI."}) from exc


def _selection_origin(request: dict, info: AudioInfo) -> float:
    try:
        value = request["selection"]
        start, end, rate = value["start_frame"], value["end_frame"], value["sample_rate"]
        duration, origin = value["selected_seconds"], value["source_origin_seconds"]
        if (type(start) is not int or type(end) is not int or type(rate) is not int or
                not 0 <= start < end or rate != info.sample_rate or end-start != info.frames or
                type(duration) not in {int, float} or type(origin) not in {int, float} or
                not math.isfinite(duration) or not math.isfinite(origin) or
                abs(duration-info.duration_seconds) > 1e-8 or abs(origin-start/rate) > 1e-8):
            raise ValueError("Selection metadata differs")
        return float(origin)
    except (KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
        raise HTTPException(409, detail={"code": "selection_changed", "message": "The saved selection frames, duration, or source offset do not match the audio receipt."}) from exc


def _verify_request_integrity(folder: Path, request: dict) -> None:
    """Bind selection frames/origin to the original durable request payload.

    Review previews lack a normal completion.selection receipt. The workflow's
    payload digest covers original audio bytes plus original request settings,
    so a changed offset cannot borrow an unchanged selection/MIDI hash.
    """
    request_fields = {key:value for key,value in request.items() if key not in {
        "original_sha256", "selection_sha256", "payload_sha256", "owner_pid", "owner_token"}}
    try:
        settings_bytes = json.dumps(request_fields, sort_keys=True, allow_nan=False).encode()
    except (TypeError, ValueError) as exc:
        raise HTTPException(409, detail={"code": "request_changed", "message": "The saved audio request settings are invalid or changed."}) from exc
    original_digest, payload_digest = hashlib.sha256(), hashlib.sha256()
    with _open_source(folder/"original.audio", MAX_STORED_BYTES) as stream:
        size = 0
        while block := stream.read(1024**2):
            size += len(block)
            if size > MAX_STORED_BYTES:
                _reject(413, "source_size_limit", "The original source exceeds the bounded verification byte limit.")
            original_digest.update(block)
            payload_digest.update(block)
    payload_digest.update(settings_bytes)
    if original_digest.hexdigest() != request.get("original_sha256") or payload_digest.hexdigest() != request.get("payload_sha256"):
        _reject(409, "request_changed", "The original audio or recorded selection settings changed; correspondence cannot be verified.")


def _stored_source(midi_trial_id: str, transcription_job_id: str) -> tuple[StoredSelection, Path]:
    from src.api.transcriptions import _folder

    summary = _saved_midi(midi_trial_id)
    folder = _folder(transcription_job_id)
    state, request = _record(folder/"status.json"), _record(folder/"request.json")
    _verify_request_integrity(folder, request)
    if state.get("job_id") != transcription_job_id:
        _reject(409, "correspondence_changed", "The source job receipt has a different identity.")
    correspondence = "verified-completed-job" if _normal_binding(folder, state, summary, request) else "verified-review-preview"
    if correspondence == "verified-review-preview" and not _preview_binding(folder, summary):
        _reject(409, "unverified_correspondence", "This saved MIDI has no completed correspondence receipt for the selected audio job. Select audio explicitly instead.")
    path = folder/"selection.wav"
    with _open_source(path, MAX_STORED_BYTES) as stream:
        info = inspect_audio(stream, max_seconds=MAX_STORED_SECONDS)
        digest, size = _digest(stream)
    if info.format != "WAV" or digest != request.get("selection_sha256"):
        _reject(409, "audio_changed", "The saved audio selection differs from its recorded hash or format.")
    origin = _selection_origin(request, info)
    return StoredSelection(transcription_job_id=transcription_job_id, midi_trial_id=midi_trial_id,
        midi_sha256=summary.input_sha256, audio_sha256=digest, audio_bytes=size,
        duration_seconds=info.duration_seconds, source_origin_seconds=origin,
        sample_rate=info.sample_rate, channels=info.channels, correspondence=correspondence), path


@contextmanager
def _cpu_slot():
    base = settings.file_bucket_path.resolve()/"tempo-estimates"
    if _unsafe(base):
        _reject(409, "unsafe_tempo_storage", "Tempo-analysis CPU reservation is unavailable.")
    base.mkdir(parents=True, exist_ok=True)
    lock = base/"cpu.lock"
    if _unsafe(lock):
        _reject(409, "unsafe_tempo_storage", "Tempo-analysis CPU reservation is unavailable.")
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    except OSError as exc:
        raise HTTPException(409, detail={"code": "unsafe_tempo_storage", "message": "Tempo-analysis CPU reservation is unavailable."}) from exc
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            _reject(409, "unsafe_tempo_storage", "Tempo-analysis CPU reservation is unavailable.")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            _reject(409, "tempo_cpu_busy", "Another audio tempo analysis is running. Retry explicitly after it finishes.")
        yield
    finally:
        os.close(descriptor)


@router.get("/sources", response_model=StoredSelection)
def get_tempo_source(midi_trial_id: UUID4, transcription_job_id: UUID4) -> StoredSelection:
    """Verify one caller-known source link without starting analysis or inference."""
    try:
        with _cpu_slot():
            return _stored_source(midi_trial_id, transcription_job_id)[0]
    except TempoAudioError as exc:
        _reject(exc.status, exc.code, exc.message)


@router.post("/stored", response_model=TempoEstimate)
def estimate_stored_audio(source: StoredSelection) -> TempoEstimate:
    """Recheck an exact source descriptor, then analyze original selected audio."""
    try:
        with _cpu_slot():
            current, path = _stored_source(source.midi_trial_id, source.transcription_job_id)
            if current != source:
                _reject(409, "source_descriptor_changed", "The verified source descriptor changed. Verify the current source again before analysis.")
            with _open_source(path, MAX_STORED_BYTES) as stream:
                if _digest(stream) != (source.audio_sha256, source.audio_bytes):
                    _reject(409, "audio_changed", "The audio changed before analysis; no result was produced.")
                analysis = estimate_audio_tempo(stream, max_seconds=MAX_STORED_SECONDS)
                if _digest(stream) != (source.audio_sha256, source.audio_bytes):
                    _reject(409, "audio_changed", "The audio changed during analysis; no result was produced.")
            # Source receipts and MIDI may change independently while CPU work is
            # running. Do not release suggestions under stale correspondence.
            if _stored_source(source.midi_trial_id, source.transcription_job_id)[0] != source:
                _reject(409, "source_descriptor_changed", "The source correspondence changed during analysis; no result was produced.")
            return TempoEstimate(**analysis.model_dump(), source=source)
    except TempoAudioError as exc:
        _reject(exc.status, exc.code, exc.message)


@router.post("/upload", response_model=TempoEstimate)
def estimate_uploaded_audio(file: UploadFile = File(...)) -> TempoEstimate:
    """Analyze one explicitly uploaded native WAV/FLAC without claiming MIDI linkage."""
    if Path(file.filename or "").suffix.lower() not in {".wav", ".flac"}:
        _reject(415, "unsupported_audio", "Upload WAV or FLAC audio; existing saved selections can be used without reuploading.")
    with _cpu_slot():
        data = file.file.read(MAX_UPLOAD_BYTES+1)
        if len(data) > MAX_UPLOAD_BYTES:
            _reject(413, "upload_limit", "Tempo uploads are limited to 32 MiB.")
        if not data:
            _reject(422, "empty_audio", "The selected audio file is empty.")
        stream = io.BytesIO(data)
        try:
            info = inspect_audio(stream, max_seconds=MAX_UPLOAD_SECONDS)
            source = UploadedAudio(audio_sha256=hashlib.sha256(data).hexdigest(), audio_bytes=len(data),
                duration_seconds=info.duration_seconds, sample_rate=info.sample_rate, channels=info.channels, format=info.format)
            analysis = estimate_audio_tempo(stream, max_seconds=MAX_UPLOAD_SECONDS)
            return TempoEstimate(**analysis.model_dump(), source=source)
        except TempoAudioError as exc:
            _reject(exc.status, exc.code, exc.message)
