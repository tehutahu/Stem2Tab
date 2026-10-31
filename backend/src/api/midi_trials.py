"""Explicit bounded saved-MIDI import; no jobs, models or separation calls."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import io
import math
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
import mido
from pydantic import BaseModel, ConfigDict, Field, ValidationError
import structlog

from src.core.config import settings
from src.pipelines.bass_output import BassOutputConfig
from src.evaluation.midi_clock import UnsupportedMidiClockError, validate_fixed_storage_clock

logger = structlog.get_logger()
router = APIRouter(prefix="/api/v1/midi-trials", tags=["saved MIDI trials"])

MAX_UPLOAD_BYTES = 2*1024*1024
MAX_TRACKS = 32
MAX_MESSAGES = 20_000
MAX_NOTES = 2000
MAX_DURATION_SECONDS = 600.0
MAX_STORAGE_QUARTERS = 2400
MAX_SIMULTANEOUS_NOTES = 32


class MidiTrialError(BaseModel):
    """Stable public error without internal paths or exception details."""

    code: str
    message: str


class MidiTrialErrorResponse(BaseModel):
    """Envelope used by rejected requests and missing/changed artifacts."""

    detail: MidiTrialError


class StorageClockSummary(BaseModel):
    """MIDI storage clock metadata, never an inferred musical beat grid."""

    ppq: int
    tempo_bpm: float
    meter: Literal["4/4"] = "4/4"
    musical_tempo_and_meter_verified: Literal[False] = False


class UnmappableEventSummary(BaseModel):
    """A precise retained event that blocked the complete optional GP5."""

    source_index: int
    midi: int
    start: float
    end: float
    reason: str


class Gp5TrialSummary(BaseModel):
    """Optional preview outcome, separate from full retained MIDI/XML."""

    requested: bool
    status: Literal["not_requested", "written", "unmappable", "failed"]
    mapped_count: int | None = None
    unmappable: list[UnmappableEventSummary] = Field(default_factory=list)


class MidiTrialArtifact(BaseModel):
    """One allowlisted artifact with a relative same-origin download URL."""

    key: str
    kind: str
    filename: str
    bytes: int
    sha256: str
    url: str


class MidiTrialSummary(BaseModel):
    """Persisted UI-safe trial response; no absolute server paths."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    trial_id: str
    created_at: datetime
    status: Literal["completed", "gp5_blocked_out_of_range", "failed"]
    source_kind: Literal["muscriptor-small", "local-midi"]
    original_filename: str
    input_sha256: str
    input_bytes: int
    input_note_count: int
    source_duration_seconds: float
    last_note_end_seconds: float
    source_duration_is_storage_midi: Literal[True] = True
    time_basis: Literal["storage-clock"] = "storage-clock"
    storage_clock: StorageClockSummary
    config: dict[str, Any]
    gp5: Gp5TrialSummary
    warnings: list[str]
    artifacts: list[MidiTrialArtifact]
    error: MidiTrialError | None = None


# Values are server-owned constants; URL parameters never become file paths.
ARTIFACTS = {
    "original-midi": ("input.mid", "original-midi", "audio/midi"),
    "storage-clock-xml": ("output/performance-clock.musicxml", "storage-clock-musicxml", "application/vnd.recordare.musicxml+xml"),
    "canonical-json": ("output/canonical.json", "canonical-notes", "application/json"),
    "canonical-csv": ("output/canonical.csv", "canonical-notes", "text/csv"),
    "source-events": ("output/source-events.json", "source-events", "application/json"),
    "gp5-preview": ("output/gp5-preview/bass.preview.gp5", "quarter-note-gp5-preview", "application/octet-stream"),
}
ERROR_RESPONSES = {code: {"model": MidiTrialErrorResponse} for code in (404,409,413,415,422)}


def _reject(code: int, reason: str, message: str) -> None:
    raise HTTPException(status_code=code, detail=MidiTrialError(code=reason,message=message).model_dump())


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _base_dir() -> Path:
    bucket = settings.file_bucket_path.resolve()
    base = bucket/"midi-trials"
    if base.is_symlink():
        _reject(409,"unsafe_storage","Saved-MIDI storage is unavailable.")
    return base


def _trial_dir(trial_id: str) -> Path:
    try:
        identifier = UUID(trial_id)
        if identifier.version != 4 or str(identifier) != trial_id:
            raise ValueError("Noncanonical UUID4")
    except ValueError:
        _reject(404,"trial_not_found","Saved-MIDI trial not found.")
    base = _base_dir()
    path = base/trial_id
    if path.is_symlink() or not path.is_dir() or path.resolve().parent != base.resolve():
        _reject(404,"trial_not_found","Saved-MIDI trial not found.")
    return path


def _artifact_path(folder: Path, key: str) -> Path | None:
    record = ARTIFACTS.get(key)
    if record is None:
        return None
    path = folder/record[0]
    # Reject links at every component, including otherwise-internal links.
    if any(parent.is_symlink() for parent in [path,*path.parents] if parent != folder.parent):
        return None
    if not path.resolve().is_relative_to(folder.resolve()) or not path.is_file():
        return None
    return path


def _config(profile: str | None, strings: str | None, tuning: str | None,
            max_fret: str | None, source_kind: str | None, preview: str) -> tuple[BassOutputConfig,bool]:
    try:
        if source_kind not in {"muscriptor-small","local-midi"}:
            raise ValueError("Explicit source_kind must be muscriptor-small or local-midi")
        value = preview.strip().lower()
        if value not in {"true","1","yes","on","false","0","no","off"}:
            raise ValueError("gp5_preview must be a boolean")
        config = BassOutputConfig(profile=profile, strings=int(strings) if strings is not None else None,
            tuning_midi=tuple(int(v.strip()) for v in tuning.split(",")) if tuning is not None else None,
            max_fret=int(max_fret) if max_fret is not None else None)
        return config,value in {"true","1","yes","on"}
    except (ValueError,ValidationError):
        _reject(422,"invalid_config","Use an explicit bass profile/source kind and valid strings, low-to-high MIDI tuning, max fret0..36 and gp5_preview boolean.")


def _preflight(data: bytes) -> tuple[Any,list[dict[str,Any]],float]:
    """Bound storage-clock work before the frozen CLI performs any export."""
    try:
        raw = mido.MidiFile(file=io.BytesIO(data))
        if raw.type == 2 or raw.ticks_per_beat <= 0:
            _reject(422,"unsupported_midi","Use synchronous type0/1 positive-PPQ MIDI.")
        validate_fixed_storage_clock(raw)
        if len(raw.tracks) > MAX_TRACKS or sum(len(t) for t in raw.tracks) > MAX_MESSAGES:
            _reject(422,"midi_complexity_limit","Experimental limit:32 tracks and20000 MIDI messages.")
        max_tick = max((sum(m.time for m in track) for track in raw.tracks),default=0)
        if max_tick/raw.ticks_per_beat > MAX_STORAGE_QUARTERS:
            _reject(422,"storage_clock_limit","Experimental limit:2400 storage-clock quarter units.")
        note_count = sum(m.type=="note_on" and m.velocity>0 for track in raw.tracks for m in track)
        if note_count > MAX_NOTES:
            _reject(422,"note_limit","Experimental limit:2000 note attacks.")
        tempo,seconds,active = 500000,0.0,set()
        for message in mido.merge_tracks(raw.tracks):
            if type(message.time) is not int or message.time<0:
                _reject(422,"invalid_midi","MIDI delta ticks must be nonnegative integers.")
            seconds += mido.tick2second(message.time,raw.ticks_per_beat,tempo)
            if not math.isfinite(seconds) or seconds>MAX_DURATION_SECONDS:
                _reject(422,"duration_limit","Experimental limit:600 seconds of storage-MIDI duration.")
            if message.type=="set_tempo":
                if message.tempo<=0:
                    _reject(422,"invalid_midi","MIDI tempo must be positive.")
                tempo=message.tempo
            elif message.type=="note_on" and message.velocity>0:
                active.add((message.channel,message.note))
                if len(active)>MAX_SIMULTANEOUS_NOTES:
                    _reject(422,"voice_limit","Experimental limit:32 simultaneous notes.")
            elif message.type in {"note_on","note_off"}:
                active.discard((message.channel,message.note))
        # This is the existing strict loader, not a repair/normalization variant.
        from src.evaluation.saved_midi_trial import _load_unambiguous_midi
        midi,rows = _load_unambiguous_midi(data)
        return midi,rows,seconds
    except HTTPException:
        raise
    except UnsupportedMidiClockError as exc:
        _reject(422,"unsupported_midi_clock",str(exc))
    except Exception:
        _reject(422,"invalid_midi","The file is malformed, ambiguous, or unsupported by the saved-MIDI trial; no silent event repair is performed.")


def _run_trial(input_path: Path, output: Path, **kwargs: Any) -> dict[str,Any]:
    from src.evaluation.saved_midi_trial import run_saved_midi_trial
    return run_saved_midi_trial(input_path,output,**kwargs)


def _public_artifacts(folder: Path, trial_id: str, *, success: bool, gp5: bool) -> list[MidiTrialArtifact]:
    results = []
    for key,(relative,kind,_) in ARTIFACTS.items():
        if key=="storage-clock-xml" and not success or key=="gp5-preview" and not gp5:
            continue
        path = _artifact_path(folder,key)
        if path is None:
            continue
        results.append(MidiTrialArtifact(key=key,kind=kind,filename=path.name,bytes=path.stat().st_size,
            sha256=_sha(path),url=f"/api/v1/midi-trials/{trial_id}/files/{key}"))
    return results


def _read_summary(folder: Path) -> MidiTrialSummary:
    path = folder/"summary.json"
    if path.is_symlink() or not path.is_file():
        _reject(404,"trial_not_found","Saved-MIDI trial summary not found.")
    try:
        return MidiTrialSummary.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError,ValidationError):
        _reject(409,"invalid_summary","Saved-MIDI trial summary is unavailable.")


@router.post("",response_model=MidiTrialSummary,status_code=201,
             responses={**ERROR_RESPONSES,500:{"model":MidiTrialSummary}})
def create_midi_trial(
    file: UploadFile = File(...), profile: str | None = Form(None),
    strings: str | None = Form(None), tuning_midi: str | None = Form(None), max_fret: str | None = Form(None),
    gp5_preview: str = Form("false"), source_kind: str | None = Form(None),
) -> MidiTrialSummary | JSONResponse:
    """Import one saved MIDI locally and synchronously, without the job pipeline."""
    config,preview_requested = _config(profile,strings,tuning_midi,max_fret,source_kind,gp5_preview)
    return import_midi_bytes(file.file.read(MAX_UPLOAD_BYTES+1), filename=file.filename or "",
        config=config, source_kind=source_kind, preview_requested=preview_requested)


def import_midi_bytes(data: bytes, *, filename: str, config: BassOutputConfig,
                      source_kind: Literal["muscriptor-small", "local-midi"],
                      preview_requested: bool) -> MidiTrialSummary | JSONResponse:
    """Export bounded supplied bytes with the same strict saved-MIDI contract.

    Also used after the opt-in local audio worker has finished. This function
    performs no separation, model loading, event repair or network requests.
    """
    name = filename.replace("\\","/").rsplit("/",1)[-1]
    if Path(name).suffix.lower() not in {".mid",".midi"}:
        _reject(415,"unsupported_extension","Upload a .mid or .midi saved MIDI file.")
    name = "".join(c for c in name if c.isprintable())[:180] or "saved.mid"
    if len(data)>MAX_UPLOAD_BYTES:
        _reject(413,"upload_limit","Saved-MIDI uploads are limited to2MiB.")
    midi,rows,source_duration = _preflight(data)
    trial_id = str(uuid4())
    base = _base_dir()
    base.mkdir(parents=True,exist_ok=True)
    folder = base/trial_id
    folder.mkdir(exist_ok=False)
    input_path = folder/"input.mid"
    with input_path.open("xb") as stream:
        stream.write(data)
    run_report: dict[str,Any] = {}
    try:
        run_report = _run_trial(input_path,folder/"output",config=config,
            source_kind=source_kind,time_basis="storage-clock",gp5_preview=preview_requested)
        if run_report.get("status") not in {"completed","gp5_blocked_out_of_range","failed"}:
            raise ValueError("Unexpected trial outcome")
    except Exception:
        logger.exception("saved_midi_trial_export_failed",trial_id=trial_id)
        run_report={"status":"failed"}
    failed = run_report["status"]=="failed"
    raw_gp5 = run_report.get("gp5") or {}
    gp5_status = "not_requested" if not preview_requested else "failed" if failed else raw_gp5.get("status","failed")
    if gp5_status not in {"not_requested","written","unmappable","failed"}:
        gp5_status="failed"
    unmappable = [UnmappableEventSummary(source_index=e["source_index"],midi=e["midi"],start=e["start"],end=e["end"],
        reason="No string/fret mapping in the selected tuning and max fret; event retained, no transposition.")
        for e in raw_gp5.get("events",[]) if e.get("unmappable_reason")]
    warnings = ["STORAGE_CLOCK_ONLY: MIDI duration and XML bar grid use the file storage clock; musical beats/meter are unverified.",
        "XML_SCOPE: loaded note pitch/onset/end interchange only; source velocity, timbre, bends and controllers are not rendered.",
        "SOURCE_LABEL: source_kind is caller-declared provenance, not independent model authentication."]
    if preview_requested:
        warnings.append("GP5_PREVIEW_ONLY: sequential quarter-note fingering at an explicit preview clock, not performed rhythm or overlap fidelity.")
    if gp5_status=="unmappable":
        warnings.append("GP5_BLOCKED_OUT_OF_RANGE: no GP5 generated; every event remains in original MIDI, canonical files and storage-clock XML.")
    if failed:
        warnings.append("EXPORT_FAILED: the original upload is retained; partial GP5/XML are not offered as successful outputs.")
    summary = MidiTrialSummary(trial_id=trial_id,created_at=datetime.now(timezone.utc),status=run_report["status"],
        source_kind=source_kind,original_filename=name,input_sha256=hashlib.sha256(data).hexdigest(),input_bytes=len(data),
        input_note_count=len(rows),source_duration_seconds=source_duration,
        last_note_end_seconds=max((r["end"] for r in rows),default=0.0),
        storage_clock=StorageClockSummary(ppq=midi.resolution,tempo_bpm=float(midi.get_tempo_changes()[1][0])),
        config=dict(profile=config.profile,strings=len(config.resolved_tuning),tuning_midi=config.resolved_tuning,
                    max_fret=config.resolved_max_fret,preview_tempo_bpm=config.preview_tempo_bpm),
        gp5=Gp5TrialSummary(requested=preview_requested,status=gp5_status,mapped_count=raw_gp5.get("mapped_event_count"),unmappable=unmappable),
        warnings=warnings,artifacts=_public_artifacts(folder,trial_id,success=not failed,gp5=gp5_status=="written" and not failed),
        error=MidiTrialError(code="export_failed",message="Saved-MIDI export failed. The original upload and available safe artifacts are retained.") if failed else None)
    with (folder/"summary.json").open("x",encoding="utf-8") as stream:
        stream.write(summary.model_dump_json(indent=2)+"\n")
    if failed:
        return JSONResponse(status_code=500,content=summary.model_dump(mode="json"))
    return summary


@router.get("/{trial_id}",response_model=MidiTrialSummary,responses=ERROR_RESPONSES)
def get_midi_trial(trial_id: str) -> MidiTrialSummary:
    """Return only the persisted UI-safe summary for a canonical UUID4."""
    return _read_summary(_trial_dir(trial_id))


@router.get("/{trial_id}/files/{artifact_key}",responses=ERROR_RESPONSES)
def download_midi_trial_file(trial_id: str,artifact_key: str) -> FileResponse:
    """Download a listed, unchanged, regular allowlisted artifact only."""
    folder = _trial_dir(trial_id)
    summary = _read_summary(folder)
    artifact = next((a for a in summary.artifacts if a.key==artifact_key),None)
    path = _artifact_path(folder,artifact_key)
    if artifact is None or path is None:
        _reject(404,"artifact_not_found","Saved-MIDI artifact not found.")
    if path.stat().st_size!=artifact.bytes or _sha(path)!=artifact.sha256:
        _reject(409,"artifact_changed","Saved-MIDI artifact changed after export and is unavailable.")
    filename = summary.original_filename if artifact_key=="original-midi" else artifact.filename
    return FileResponse(path,media_type=ARTIFACTS[artifact_key][2],filename=filename)


@router.get("/{trial_id}/notation-clock/double.musicxml", responses=ERROR_RESPONSES)
def double_notation_clock(trial_id: str) -> Response:
    """Return a separate 2x notation clock; never alter MIDI or saved artifacts.

    Only the bounded, unchanged original MIDI and its verified ticks export are
    accepted. Both tempo and durations scale together; no grid rounding, model,
    GP5 conversion, or musical tempo estimation is involved.
    """
    from src.evaluation.notation_fidelity import write_fidelity_musicxml

    folder = _trial_dir(trial_id)
    summary = _read_summary(folder)
    if summary.status == "failed":
        _reject(409, "failed_source", "A failed saved-MIDI export cannot be rebarred.")
    checked: dict[str, Path] = {}
    for key in ("original-midi", "storage-clock-xml"):
        artifact = next((a for a in summary.artifacts if a.key == key), None)
        path = _artifact_path(folder, key)
        if artifact is None or path is None:
            _reject(404, "artifact_not_found", "Original MIDI and storage-clock XML are required.")
        if path.stat().st_size != artifact.bytes or _sha(path) != artifact.sha256:
            _reject(409, "artifact_changed", "Saved-MIDI artifact changed after export and is unavailable.")
        checked[key] = path
    data = checked["original-midi"].read_bytes()
    if len(data) > MAX_UPLOAD_BYTES:
        _reject(413, "upload_limit", "Saved MIDI exceeds the bounded export limit.")
    _preflight(data)
    with TemporaryDirectory(prefix="stem2tab-notation-clock-") as temporary:
        root = Path(temporary)
        source = root / "source.mid"
        source.write_bytes(data)
        original = root / "original.musicxml"
        write_fidelity_musicxml(source, original)
        if original.read_bytes() != checked["storage-clock-xml"].read_bytes():
            _reject(409, "unsupported_source_export", "Original XML does not match this exact tick exporter; no silent normalization.")
        candidate = root / "notation-clock-double.musicxml"
        write_fidelity_musicxml(source, candidate, notation_scale=2)
        payload = candidate.read_bytes()
    return Response(payload, media_type="application/vnd.recordare.musicxml+xml", headers={
        "Content-Disposition": 'inline; filename="notation-clock-double.musicxml"',
        "Cache-Control": "no-store",
        "X-Notation-Scale": "2",
        "X-Source-Midi-SHA256": hashlib.sha256(data).hexdigest(),
    })
