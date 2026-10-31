"""Allowlisted local listening packets; no arbitrary paths, uploads or models."""
from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from src.core.config import settings
from src.evaluation.listening_packet import sha256

router = APIRouter(prefix="/api/v1/listening-trials", tags=["local listening"])


class ListeningTrack(BaseModel):
    """A fixed local waveform, never a caller-supplied download URL."""
    model_config = ConfigDict(extra="forbid")
    key: str
    filename: str
    sha256: str
    bytes: int = Field(gt=0, le=20*1024**2)
    url: str = ""


class ListeningSummary(BaseModel):
    """UI-safe comparison identity and timing convention."""
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    trial_id: str
    label: str
    duration_seconds: float = Field(gt=0, le=10.01)
    source_job_id: str
    midi_trial_id: str
    midi_sha256: str
    note_count: int = Field(ge=0, le=500)
    tracks: list[ListeningTrack]
    synthesis_sample_rate: int
    synthesis_max_boundary_rounding_seconds: float
    source_timing_origin_seconds: int
    accuracy_verified: bool
    warning: str


def listening_folder(identifier: str) -> Path:
    """Resolve a canonical UUID4 inside the configured local packet bucket."""
    try:
        value = UUID(identifier)
        if str(value) != identifier or value.version != 4:
            raise ValueError()
    except ValueError:
        raise HTTPException(404, "Listening trial not found")
    base = settings.file_bucket_path.resolve() / "listening-trials"
    folder = base / identifier
    if base.is_symlink() or folder.is_symlink() or not folder.is_dir():
        raise HTTPException(404, "Listening trial not found")
    return folder


@router.get("/{identifier}", response_model=ListeningSummary)
def get_listening_trial(identifier: str) -> ListeningSummary:
    """Read existing packet metadata without creating files or invoking a model."""
    folder = listening_folder(identifier)
    try:
        path = folder / "summary.json"
        if path.is_symlink():
            raise ValueError()
        result = ListeningSummary.model_validate(json.loads(path.read_text()))
        if result.trial_id != identifier or {t.key for t in result.tracks} != {"original", "bass", "synth"} or len(result.tracks) != 3:
            raise ValueError()
        for t in result.tracks:
            if t.filename not in {t.key+".wav", t.key+".flac"}:
                raise ValueError()
            t.url = f"/api/v1/listening-trials/{identifier}/files/{t.key}"
        return result
    except (ValueError, OSError):
        raise HTTPException(409, "Listening metadata unavailable")


@router.get("/{identifier}/files/{key}")
def get_listening_audio(identifier: str, key: str) -> FileResponse:
    """Serve only unchanged allowlisted original/bass/synthesis audio."""
    summary = get_listening_trial(identifier)
    track = next((t for t in summary.tracks if t.key == key), None)
    if track is None:
        raise HTTPException(404, "Audio not found")
    path = listening_folder(identifier) / track.filename
    if path.is_symlink() or not path.is_file() or path.stat().st_size != track.bytes or sha256(path) != track.sha256:
        raise HTTPException(409, "Audio changed or unavailable")
    return FileResponse(path, media_type="audio/wav" if path.suffix == ".wav" else "audio/flac")
