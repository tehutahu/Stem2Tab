"""Bounded, explicitly selected local audio trials, independent of the default worker.

Deployment is opt-in through a server-owned manifest. There are no download,
credential, license-acceptance or client-supplied model-path operations here.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import platform
import subprocess
import threading
import time
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
import numpy as np
from pydantic import BaseModel, ConfigDict, Field, ValidationError
import soundfile as sf
import structlog

from src.api import midi_trials
from src.core.config import settings
from src.pipelines.bass_output import BassOutputConfig

logger = structlog.get_logger()
router = APIRouter(prefix="/api/v1/muscriptor-trials", tags=["opt-in local audio trials"])
MAX_AUDIO_SECONDS = 10
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
STAGE_TIMEOUT_SECONDS = 600
MAX_CHILD_RSS_BYTES = 12 * 1024**3
SOURCE_COMMIT = "8a1ef88672cc23c26619adaff37ec48d82c0b906"
MODEL_SHA = "bbd482c786b895cf7d8f44185073d951adae2ebb8a66f82ca84cd1f84569549c"
CONFIG_SHA = "3008fc481e4a1cd978e337eb3759260c270892204db5039235ac939e1f42aeb2"
WORKER = Path(__file__).resolve().parents[1]/"pipelines"/"muscriptor_worker.py"
_CHILDREN: set[subprocess.Popen] = set()
_CHILDREN_LOCK = threading.Lock()
_SHUTDOWN = threading.Event()


class TrialError(BaseModel):
    """Public error code and explanation; no local absolute paths."""
    code: str
    message: str


class TrialStatus(BaseModel):
    """A persisted terminal or running state, safe for the browser to poll."""
    model_config = ConfigDict(extra="forbid")
    job_id: str
    status: Literal["running", "completed", "failed"]
    stage: str
    midi_trial_id: str | None = None
    error: TrialError | None = None


class Deployment(BaseModel):
    """Server-owned, explicit small-only deployment configuration."""
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1]
    terms_accepted: Literal[True]
    noncommercial: Literal[True]
    source_dir: Path
    source_commit: Literal[SOURCE_COMMIT]
    model_path: Path
    model_sha256: Literal[MODEL_SHA]
    config_sha256: Literal[CONFIG_SHA]
    demucs_checkpoint: Path
    demucs_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    demucs_python: Path
    muscriptor_python: Path


class TrialFailure(Exception):
    """Expected bounded trial failure with a safe public explanation."""
    def __init__(self, code: str, message: str):
        self.error = TrialError(code=code, message=message)
        super().__init__(code)


def _reject(status: int, code: str, message: str) -> None:
    raise HTTPException(status, detail=TrialError(code=code, message=message).model_dump())


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024*1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_deployment() -> Deployment:
    value = os.environ.get("STEM2TAB_MUSCRIPTOR_DEPLOYMENT")
    if not value:
        raise TrialFailure("not_deployed", "MuScriptor small local trial is not configured; no assets will be downloaded.")
    path = Path(value)
    if platform.system() != "Linux":
        raise TrialFailure("unsupported_runtime", "This bounded local trial requires the configured Linux/WSL runtimes.")
    try:
        if not path.is_absolute() or path.is_symlink() or path.stat().st_size > 16384:
            raise ValueError("Invalid deployment manifest")
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("Deployment must be an object")
        if raw.get("terms_accepted") is not True or raw.get("noncommercial") is not True:
            raise TrialFailure("terms_not_confirmed", "The deployment must already have accepted small-model terms and permit noncommercial use.")
        deployment = Deployment.model_validate(raw)
        for item in (deployment.source_dir, deployment.model_path, deployment.demucs_checkpoint,
                     deployment.demucs_python, deployment.muscriptor_python):
            if not item.is_absolute() or not item.exists():
                raise TrialFailure("assets_missing", "A required local source, checkpoint or runtime is missing; no automatic download is available.")
        if not deployment.source_dir.is_dir() or not (deployment.source_dir/"muscriptor"/"transcription_model.py").is_file():
            raise TrialFailure("source_missing", "The pinned official local source is unavailable.")
        if deployment.model_path.name != "model.safetensors" or deployment.model_path.is_symlink():
            raise TrialFailure("invalid_weights", "Only the configured local small safetensors and adjacent config are supported.")
        if deployment.model_path.stat().st_size != 411888600 or not deployment.model_path.with_name("config.json").is_file():
            raise TrialFailure("assets_missing", "The small weights or mandatory adjacent config are missing or have an unexpected size.")
        if deployment.demucs_checkpoint.name != "955717e8-8726e21a.th" or deployment.demucs_checkpoint.is_symlink():
            raise TrialFailure("invalid_separator", "The existing local htdemucs checkpoint is required.")
        if not all(os.access(p, os.X_OK) and p.is_file() for p in (deployment.demucs_python,deployment.muscriptor_python)):
            raise TrialFailure("runtime_missing", "A required existing Python runtime is unavailable.")
        return deployment
    except TrialFailure:
        raise
    except (OSError, ValueError, ValidationError):
        raise TrialFailure("invalid_deployment", "The local small-model deployment manifest is invalid or unavailable.") from None


@router.get("/availability")
def availability() -> dict:
    """Read readiness without loading weights, accepting terms or downloading."""
    try:
        _load_deployment()
        reasons = []
    except TrialFailure as exc:
        reasons = [exc.error.model_dump()]
    return {"available": not reasons, "reasons": reasons,
            "max_audio_seconds": MAX_AUDIO_SECONDS, "max_upload_bytes": MAX_UPLOAD_BYTES}


def _audio_preflight(data: bytes, filename: str) -> dict:
    if Path(filename).suffix.lower() not in {".wav", ".flac"}:
        _reject(415, "unsupported_audio", "Use WAV or FLAC for this local trial.")
    if len(data) > MAX_UPLOAD_BYTES:
        _reject(413, "upload_limit", "This trial accepts at most 10 MiB.")
    try:
        with sf.SoundFile(io.BytesIO(data)) as audio:
            if audio.format not in {"WAV", "WAVEX", "FLAC"}:
                _reject(415, "unsupported_audio", "The encoded audio must be WAV or FLAC.")
            if audio.channels not in {1,2} or not 8000 <= audio.samplerate <= 96000:
                _reject(422, "audio_format_limit", "Use one or two channels at 8–96 kHz.")
            if audio.frames <= 0 or audio.frames > MAX_AUDIO_SECONDS*audio.samplerate:
                _reject(422, "duration_limit", "Use a nonempty clip of at most 10 seconds; this trial never silently crops input.")
            samples = audio.read(dtype="float32", always_2d=True)
            if not np.isfinite(samples).all():
                _reject(422, "nonfinite_audio", "Audio contains nonfinite samples.")
            return dict(frames=len(samples), samplerate=audio.samplerate, channels=audio.channels,
                        duration_seconds=len(samples)/audio.samplerate,
                        decoded_float32_sha256=hashlib.sha256(samples.astype("<f4", copy=False).tobytes()).hexdigest())
    except HTTPException:
        raise
    except Exception:
        _reject(422, "invalid_audio", "The audio cannot be decoded safely as a bounded WAV/FLAC clip.")


def _base() -> Path:
    base = settings.file_bucket_path.resolve()/"muscriptor-trials"
    if base.is_symlink():
        _reject(409, "unsafe_storage", "Local trial storage is unavailable.")
    base.mkdir(parents=True, exist_ok=True)
    return base


def _write_json(path: Path, value: dict) -> None:
    temporary = path.with_name(path.name+"."+str(uuid4())+".tmp")
    with temporary.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write("\n")
    os.replace(temporary, path)


def _state(folder: Path, *, status: str = "running", stage: str,
           midi_trial_id: str | None = None, error: TrialError | None = None) -> TrialStatus:
    state = TrialStatus(job_id=folder.name,status=status,stage=stage,midi_trial_id=midi_trial_id,error=error)
    _write_json(folder/"status.json", state.model_dump())
    return state


def _acquire_slot(base: Path) -> int:
    import fcntl
    descriptor = os.open(base/"execution.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(descriptor)
        _reject(409, "trial_busy", "One local trial is already running. Wait for it; automatic duplicate runs are disabled.")
    return descriptor


def _owner_token(pid: int) -> str | None:
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")",1)[1].split()[19]
    except (OSError, IndexError):
        return None


def _child_rss(pid: int) -> int:
    # The worker uses one process and no dataloader children. /proc needs no new dependency.
    try:
        return int(Path(f"/proc/{pid}/statm").read_text().split()[1])*os.sysconf("SC_PAGE_SIZE")
    except (OSError, IndexError, ValueError):
        return 0


def _terminate(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def _run_stage(stage: str, deployment: Deployment, folder: Path, input_path: Path, slot: int) -> dict:
    output = folder/stage
    python = deployment.demucs_python if stage == "separate" else deployment.muscriptor_python
    command = [str(python), str(WORKER), "--stage", stage, "--manifest", str(folder/"deployment.json"),
               "--input", str(input_path), "--output", str(output)]
    started = time.monotonic()
    peak_rss = 0
    with (folder/f"{stage}.log").open("xb") as log:
        with _CHILDREN_LOCK:
            if _SHUTDOWN.is_set():
                raise TrialFailure("server_stopping", "The local server is stopping; no next stage was launched.")
            # The child also owns the flock until exit, even if the API is killed.
            # Its Linux parent-death guard terminates it on unexpected parent exit.
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                cwd=folder, start_new_session=True, pass_fds=(slot,),
                env={**os.environ,"STEM2TAB_TRIAL_PARENT_PID":str(os.getpid())})
            _CHILDREN.add(process)
        try:
            while process.poll() is None:
                peak_rss = max(peak_rss, _child_rss(process.pid))
                if _SHUTDOWN.is_set():
                    raise TrialFailure("server_stopping", "The local server stopped this trial; partial outputs remain local.")
                if time.monotonic()-started > STAGE_TIMEOUT_SECONDS:
                    raise TrialFailure("stage_timeout", "The local stage exceeded its 600-second limit; no automatic retry was made.")
                if peak_rss > MAX_CHILD_RSS_BYTES:
                    raise TrialFailure("memory_limit", "The local stage exceeded its 12-GiB process RAM limit; no automatic retry was made.")
                time.sleep(.1)
        finally:
            _terminate(process)
            with _CHILDREN_LOCK:
                _CHILDREN.discard(process)
    summary = dict(stage=stage, pid=process.pid, returncode=process.returncode,
                   wall_seconds=time.monotonic()-started, sampled_peak_rss_bytes=peak_rss)
    _write_json(folder/f"{stage}-supervisor.json", summary)
    result_path = output/"result.json"
    if process.returncode != 0:
        # Worker error details stay in local logs; only recognized semantic codes are exposed.
        code = f"{stage}_failed"
        if result_path.is_file() and not result_path.is_symlink():
            try:
                candidate = json.loads(result_path.read_text(encoding="utf-8")).get("error",{}).get("code")
                if candidate in {"bass_missing", "bass_stem_missing", "asset_changed", "cuda_unavailable", "source_changed"}:
                    code = candidate
            except (OSError, ValueError, AttributeError):
                pass
        raise TrialFailure(code, "The local stage failed. No other stem/model was substituted; details and partial output remain local.")
    if not result_path.is_file() or result_path.is_symlink():
        raise TrialFailure("missing_result", "The local worker did not produce a verified completion record.")
    result = json.loads(result_path.read_text(encoding="utf-8"))
    if result.get("status") != "completed":
        raise TrialFailure("invalid_result", "The local worker did not confirm successful completion.")
    return result


def _run_job(folder: Path, deployment: Deployment, input_path: Path, config: BassOutputConfig,
             preview: bool, slot: int) -> None:
    started = time.monotonic()
    try:
        _state(folder, stage="separating")
        _run_stage("separate", deployment, folder, input_path, slot)
        bass = folder/"separate"/"bass.wav"
        if not bass.is_file() or bass.is_symlink():
            raise TrialFailure("bass_missing", "The separator did not produce the bass stem; no other stem will be substituted.")
        _state(folder, stage="transcribing")
        _run_stage("transcribe", deployment, folder, bass, slot)
        midi = folder/"transcribe"/"performance.mid"
        if not midi.is_file() or midi.is_symlink() or midi.stat().st_size > midi_trials.MAX_UPLOAD_BYTES:
            raise TrialFailure("missing_midi", "The transcriber did not produce a bounded raw Performance MIDI.")
        _state(folder, stage="exporting")
        converted = midi_trials.import_midi_bytes(midi.read_bytes(),filename="muscriptor-performance.mid",
            config=config,source_kind="muscriptor-small",preview_requested=preview)
        if isinstance(converted, JSONResponse) or converted.status == "failed":
            raise TrialFailure("notation_export_failed", "Raw MIDI was retained locally, but notation export failed; no partial score is offered as successful.")
        _write_json(folder/"completion.json", {"midi_trial_id":converted.trial_id,
            "wall_seconds":time.monotonic()-started,"gp5_status":converted.gp5.status,
            "input_sha256":_sha(input_path),"bass_sha256":_sha(bass),"raw_midi_sha256":_sha(midi),
            "accuracy_evaluated":False,"musical_tempo_verified":False})
        _state(folder,status="completed",stage="completed",midi_trial_id=converted.trial_id)
    except HTTPException as exc:
        # Strict MIDI ambiguity rejection is preserved without silently repairing events.
        detail = exc.detail if isinstance(exc.detail,dict) else {}
        code = detail.get("code","notation_rejected")
        _state(folder,status="failed",stage="failed",error=TrialError(code=code,
            message="The raw output was retained locally but strict notation conversion rejected it; no note repair or retry was performed."))
    except TrialFailure as exc:
        _state(folder,status="failed",stage="failed",error=exc.error)
    except Exception:
        logger.exception("muscriptor_trial_failed",job_id=folder.name)
        _state(folder,status="failed",stage="failed",error=TrialError(code="trial_failed",
            message="The local trial failed; existing outputs are retained and no automatic retry was made."))
    finally:
        os.close(slot)


def _launch(*args) -> None:
    threading.Thread(target=_run_job,args=args,name=f"muscriptor-{args[0].name}",daemon=True).start()


def _existing_request(folder: Path, payload_sha: str) -> TrialStatus | None:
    if not folder.exists() and not folder.is_symlink():
        return None
    request = folder/"request.json"
    if folder.is_symlink() or request.is_symlink() or not request.is_file():
        _reject(409,"uncertain_existing_request","This request identifier already exists with incomplete state; inspect its status before any retry.")
    try:
        prior = json.loads(request.read_text(encoding="utf-8"))
        if not isinstance(prior, dict):
            raise ValueError
    except (OSError,ValueError):
        _reject(409,"uncertain_existing_request","This request identifier has an unreadable state; inspect it before any retry.")
    if prior.get("payload_sha256") != payload_sha:
        _reject(409,"request_conflict","This request identifier was already used for different input or settings.")
    return get_trial(folder.name)


@router.post("",response_model=TrialStatus,status_code=202)
def create_trial(file: UploadFile = File(...), request_id: str | None = Form(None), method: str | None = Form(None),
                 rights_confirmed: str | None = Form(None), profile: str | None = Form(None),
                 strings: str | None = Form(None), tuning_midi: str | None = Form(None),
                 max_fret: str | None = Form(None), gp5_preview: str = Form("false")) -> TrialStatus:
    """Start at most one explicitly selected, rights-confirmed bounded local trial."""
    if method != "muscriptor-small" or rights_confirmed != "true":
        _reject(422,"explicit_opt_in_required","Explicit MuScriptor small selection and confirmation of input/output rights for noncommercial local use are required.")
    try:
        identifier = UUID(request_id or "")
        if identifier.version != 4 or str(identifier) != request_id:
            raise ValueError
    except ValueError:
        _reject(422,"request_id_required","Use a canonical UUID4 request_id to prevent uncertain requests from running twice.")
    if _SHUTDOWN.is_set():
        _reject(503,"server_stopping","The local trial server is stopping.")
    config,preview = midi_trials._config(profile,strings,tuning_midi,max_fret,"muscriptor-small",gp5_preview)
    try:
        deployment = _load_deployment()
    except TrialFailure as exc:
        _reject(503,exc.error.code,exc.error.message)
    data = file.file.read(MAX_UPLOAD_BYTES+1)
    audio = _audio_preflight(data,file.filename or "")
    base = _base()
    folder = base/request_id
    payload_sha = hashlib.sha256(data+json.dumps({"config":config.model_dump(mode="json"),
        "gp5_preview":preview},sort_keys=True).encode()).hexdigest()
    prior = _existing_request(folder,payload_sha)
    if prior is not None:
        return prior
    slot = _acquire_slot(base)
    created = False
    try:
        prior = _existing_request(folder,payload_sha)
        if prior is not None:
            os.close(slot)
            return prior
        folder.mkdir(exist_ok=False)
        created = True
        input_path = folder/("input"+Path(file.filename or "").suffix.lower())
        with input_path.open("xb") as stream:
            stream.write(data)
        _write_json(folder/"deployment.json",deployment.model_dump(mode="json"))
        _write_json(folder/"request.json",{"method":method,"rights_confirmed":True,
            "input_sha256":hashlib.sha256(data).hexdigest(),"audio":audio,"config":config.model_dump(mode="json"),
            "gp5_preview":preview,"worker_sha256":_sha(WORKER),
            "payload_sha256":payload_sha,
            "owner_pid":os.getpid(),"owner_token":_owner_token(os.getpid()),"created_unix":time.time()})
        state = _state(folder,stage="queued")
        _launch(folder,deployment,input_path,config,preview,slot)
    except Exception:
        os.close(slot)
        if created:
            _state(folder,status="failed",stage="failed",error=TrialError(code="launch_failed",message="The local trial could not be started."))
        raise
    return state


@router.get("/{job_id}",response_model=TrialStatus)
def get_trial(job_id: str) -> TrialStatus:
    """Read terminal state; detect an interrupted owner without retrying work."""
    try:
        identifier = UUID(job_id)
        if identifier.version != 4 or str(identifier) != job_id:
            raise ValueError
    except ValueError:
        _reject(404,"trial_not_found","Local trial not found.")
    folder = _base()/job_id
    if folder.is_symlink() or not folder.is_dir() or (folder/"status.json").is_symlink():
        _reject(404,"trial_not_found","Local trial not found.")
    try:
        state = TrialStatus.model_validate_json((folder/"status.json").read_text(encoding="utf-8"))
        if state.job_id != job_id:
            raise ValueError("Mismatched job identifier")
        if state.status == "running":
            request = json.loads((folder/"request.json").read_text(encoding="utf-8"))
            token = _owner_token(request["owner_pid"])
            if token is None or token != request["owner_token"]:
                state = _state(folder,status="failed",stage="interrupted",error=TrialError(code="owner_interrupted",
                    message="The owning local server ended. Partial output remains local; the trial was not automatically retried."))
        return state
    except (OSError,ValueError,KeyError):
        _reject(409,"invalid_status","The local trial state is unavailable; do not resubmit until its state is checked.")


@router.on_event("shutdown")
def stop_owned_children() -> None:
    """Terminate only worker processes started by this local service on shutdown."""
    _SHUTDOWN.set()
    with _CHILDREN_LOCK:
        children = tuple(_CHILDREN)
    for child in children:
        _terminate(child)
