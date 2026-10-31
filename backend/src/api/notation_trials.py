"""Idempotent, local saved-MIDI derivatives; no inference or arbitrary file input."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, StrictInt, Field, model_validator
import structlog

from src.core.config import settings
from src.api.midi_trials import _trial_dir, _read_summary, _artifact_path, _sha
from src.evaluation.bass_notation_trial import run_bass_notation_trial
from src.evaluation.midi_clock import UnsupportedMidiClockError
from src.pipelines.bass_output import BassOutputConfig
from src.pipelines.explicit_rhythm import ExplicitRhythmConfig
from src.pipelines.explicit_rhythm import RhythmReport
from src.pipelines.bass_fingering import BassFingeringReport
from src.pipelines.rhythmic_gp5 import write_rhythmic_gp5

router = APIRouter(prefix="/api/v1/notation-trials", tags=["local notation"])
logger = structlog.get_logger()
FILES = {
    "original-midi": "output/preserved/performance.mid",
    "original-xml": "output/preserved/performance-clock.musicxml",
    "fingering-plan": "output/fingering-plan.json",
    "rhythm-plan": "output/rhythm/rhythm-plan.json",
    "rhythm-xml": "output/rhythm/score-explicit-quantized.musicxml",
    "gp5-preview": "output/gp5-preview/bass.constrained.preview.gp5",
    "gp5-rhythm": "output/gp5-rhythm/bass.explicit-rhythm.gp5",
}


class NotationRequest(BaseModel):
    """An explicit derivation from a completed server-owned saved MIDI."""
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    request_id: str
    midi_trial_id: str
    input_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    config: BassOutputConfig
    fingering: bool = False
    max_position_shift: StrictInt | None = Field(default=None, ge=0, le=36)
    max_hand_span: StrictInt | None = Field(default=None, ge=0, le=36)
    rhythm: ExplicitRhythmConfig | None = None
    gp5: Literal["none", "quarter-preview", "explicit-rhythm"] = "none"

    @model_validator(mode="after")
    def validate_choices(self) -> "NotationRequest":
        for value in (self.request_id, self.midi_trial_id):
            parsed = UUID(value)
            if parsed.version != 4 or str(parsed) != value:
                raise ValueError("Canonical UUID4 required")
        if self.fingering != (self.max_position_shift is not None and self.max_hand_span is not None):
            raise ValueError("Fingering requires both explicit limits")
        if not self.fingering and (self.max_position_shift is not None or self.max_hand_span is not None):
            raise ValueError("Unused fingering parameters")
        if self.gp5 != "none" and not self.fingering:
            raise ValueError("GP5 requires explicit constrained fingering")
        if self.gp5 == "explicit-rhythm" and self.rhythm is None:
            raise ValueError("Rhythmic GP5 requires an explicit musical clock")
        if not self.fingering and self.rhythm is None:
            raise ValueError("Select a derivative")
        return self


def _folder(identifier: str, *, creating: bool = False) -> Path:
    try:
        parsed = UUID(identifier)
        if parsed.version != 4 or str(parsed) != identifier:
            raise ValueError()
    except ValueError:
        raise HTTPException(404, "Notation trial not found")
    base = settings.file_bucket_path.resolve()/"notation-trials"
    folder = base/identifier
    if base.is_symlink() or folder.is_symlink():
        raise HTTPException(409, "Unsafe notation storage")
    if not creating and not folder.is_dir():
        raise HTTPException(404, "Notation trial not found")
    return folder


def _save(folder: Path, summary: dict) -> None:
    temporary = folder/"summary.tmp"
    temporary.write_text(json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(folder/"summary.json")


@router.get("/{identifier}")
def get_notation_trial(identifier: str) -> dict:
    """Read persisted state; never rerun an interrupted request."""
    folder = _folder(identifier)
    path = folder/"summary.json"
    if path.is_symlink():
        raise HTTPException(409, "Unsafe summary")
    if not path.exists():
        return {"trial_id": identifier, "status": "running", "artifacts": []}
    return json.loads(path.read_text(encoding="utf-8"))


@router.post("")
def create_notation_trial(request: NotationRequest) -> dict:
    """Reserve once before bounded CPU work; duplicates return the existing state."""
    folder = _folder(request.request_id, creating=True)
    serialized = request.model_dump_json()
    digest = hashlib.sha256(serialized.encode()).hexdigest()
    if folder.exists():
        previous = folder/"request.json"
        if previous.is_symlink() or not previous.is_file():
            raise HTTPException(409, "Request reservation is incomplete; inspect existing trial")
        if hashlib.sha256(previous.read_bytes()).hexdigest() != digest:
            raise HTTPException(409, "Request ID already used with different settings")
        return get_notation_trial(request.request_id)
    source_folder = _trial_dir(request.midi_trial_id)
    source = _read_summary(source_folder)
    original = _artifact_path(source_folder, "original-midi")
    if source.status == "failed" or original is None or _sha(original) != request.input_sha256 or source.input_sha256 != request.input_sha256:
        raise HTTPException(409, "Source MIDI changed or unavailable")
    folder.parent.mkdir(parents=True, exist_ok=True)
    try:
        folder.mkdir(exist_ok=False)
    except FileExistsError:
        raise HTTPException(409, "Request already reserved; read its state")
    (folder/"request.json").write_text(serialized, encoding="utf-8")
    summary = dict(trial_id=request.request_id, midi_trial_id=request.midi_trial_id,
        input_sha256=request.input_sha256, status="running", artifacts=[], request=request.model_dump(mode="json"))
    _save(folder, summary)
    try:
        report = run_bass_notation_trial(original, folder/"output", config=request.config, source_kind=source.source_kind,
            fingering_mode="constrained" if request.fingering else "none", max_position_shift=request.max_position_shift,
            max_hand_span=request.max_hand_span, rhythm=request.rhythm, gp5_preview=request.gp5=="quarter-preview")
        if report["input_sha256"] != request.input_sha256 or not report["source_unchanged"] or _sha(original) != request.input_sha256:
            raise ValueError("Source MIDI changed during processing; derivatives withheld without overwriting source")
        summary.update(status=report["status"], input_note_count=report["input_note_count"],
            source_unchanged=report["source_unchanged"], fingering=report["fingering"],
            rhythm=report["rhythm"].get("plan", {"status": "not_requested"}),
            gp5_status=report["gp5"]["status"], error="Export failed; original MIDI retained" if report["status"]=="failed" else None)
        if request.gp5 == "explicit-rhythm" and report["status"] != "failed":
            summary["gp5_status"] = "blocked"
            if report["fingering"]["status"] != "planned" or report["rhythm"].get("plan",{}).get("status") != "planned":
                summary["gp5_reason"] = "完全な運指・リズム計画が必要です。原MIDI・原時刻版は保持されています。"
            elif not request.rhythm.tempo_bpm.is_integer():
                summary["gp5_reason"] = "GP5は整数BPMのみ対応です。明示量子化MusicXMLを利用できます。"
            else:
                try:
                    summary["gp5"] = write_rhythmic_gp5(RhythmReport.model_validate(report["rhythm"]["plan"]),
                        BassFingeringReport.model_validate(report["fingering"]),folder/"output/gp5-rhythm",config=request.config)
                    summary["gp5_status"] = "written"
                except Exception:
                    logger.exception("rhythmic_gp5_failed",trial_id=request.request_id)
                    summary["gp5_reason"] = "GP5の完全な読み戻し検証に失敗しました。GP5は提供せず、MusicXMLと原MIDIを保持します。"
            if summary["gp5_status"] != "written":
                summary["status"] = "blocked_optional_derivative"
        # Public plans contain event data but never server paths. Partial scores are not offered.
        if report["status"] != "failed":
            for key, relative in FILES.items():
                if key == "gp5-rhythm" and (request.gp5 != "explicit-rhythm" or summary["gp5_status"] != "written"):
                    continue
                path = folder/relative
                if path.is_file() and not path.is_symlink():
                    summary["artifacts"].append(dict(key=key, filename=path.name, bytes=path.stat().st_size,
                        sha256=_sha(path), url=f"/api/v1/notation-trials/{request.request_id}/files/{key}"))
    except UnsupportedMidiClockError as exc:
        summary.update(status="failed", error_code="unsupported_midi_clock", error=str(exc), source_unchanged=_sha(original)==request.input_sha256)
    except Exception:
        logger.exception("notation_trial_failed", trial_id=request.request_id)
        summary.update(status="failed", error="Local derivative failed; original MIDI retained")
    _save(folder, summary)
    return summary


@router.get("/{identifier}/files/{key}")
def get_notation_file(identifier: str, key: str) -> FileResponse:
    """Serve only hash-verified public outputs within the reserved trial."""
    folder = _folder(identifier)
    summary = get_notation_trial(identifier)
    row = next((a for a in summary["artifacts"] if a["key"]==key), None)
    if key not in FILES or row is None:
        raise HTTPException(404, "Artifact not found")
    path = folder/FILES[key]
    if any(p.is_symlink() for p in [path, *path.parents]) or not path.is_file() or path.stat().st_size != row["bytes"] or _sha(path) != row["sha256"]:
        raise HTTPException(409, "Artifact changed or unavailable")
    return FileResponse(path, filename=path.name)
