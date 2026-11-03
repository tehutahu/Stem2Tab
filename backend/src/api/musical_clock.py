"""Generate explicit, ephemeral musical-clock derivatives from verified saved MIDI."""
from __future__ import annotations

import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from src.api.midi_trials import _artifact_path, _preflight, _read_summary, _sha, _trial_dir, MAX_UPLOAD_BYTES
from src.evaluation.musical_clock import MusicalClock, MusicalClockReport, write_musical_clock

router = APIRouter(prefix="/api/v1/musical-clock", tags=["explicit musical clock"])


class MusicalClockRequest(BaseModel):
    """Source hash and explicit settings; no file paths or inferred tempo."""

    model_config = ConfigDict(extra="forbid")
    input_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    clock: MusicalClock


class MusicalClockDerivative(BaseModel):
    """A separate downloadable version, with complete user-input provenance."""

    report: MusicalClockReport
    musicxml: str
    musicxml_sha256: str


@router.post("/{trial_id}/derive", response_model=MusicalClockDerivative)
def derive_musical_clock(trial_id: str, request: MusicalClockRequest) -> MusicalClockDerivative:
    """Return a separate exact XML version without writing the saved trial."""
    folder = _trial_dir(trial_id)
    summary = _read_summary(folder)
    if summary.status == "failed" or summary.input_sha256 != request.input_sha256:
        raise HTTPException(409, "Source MIDI changed or unavailable")
    original = _artifact_path(folder, "original-midi")
    artifact = next((a for a in summary.artifacts if a.key == "original-midi"), None)
    if original is None or artifact is None:
        raise HTTPException(404, "Original MIDI unavailable")
    if original.stat().st_size > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Source exceeds bounded MIDI limit")
    if original.stat().st_size != artifact.bytes or _sha(original) != request.input_sha256 or artifact.sha256 != request.input_sha256:
        raise HTTPException(409, "Source MIDI changed or unavailable")
    data = original.read_bytes()
    if hashlib.sha256(data).hexdigest() != request.input_sha256:
        raise HTTPException(409, "Source MIDI changed during read")
    _preflight(data)
    with TemporaryDirectory(prefix="stem2tab-musical-clock-") as temporary:
        root = Path(temporary)
        source = root/"source.mid"
        source.write_bytes(data)
        output = root/"musical-clock.musicxml"
        try:
            report = write_musical_clock(source, output, request.clock)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        xml = output.read_bytes()
    return MusicalClockDerivative(report=report, musicxml=xml.decode("utf-8"), musicxml_sha256=hashlib.sha256(xml).hexdigest())
