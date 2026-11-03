"""CPU-only, explicit preview of retained zero-duration output; no audio jobs."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Form
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, model_validator

from src.api import midi_trials, muscriptor_trials as trial
from src.pipelines import model_output_review as review
from src.pipelines.bass_output import BassOutputConfig

router = APIRouter(prefix="/api/v1/transcriptions", tags=["Retained output review"])
REVIEW_FILES = {"model-events.mid": "model-events.mid", "model-notes.json": "notes.json",
                "model-events.jsonl": "events.jsonl", "model-tokens.jsonl": "tokens.jsonl",
                "model-output-issues.json": "decoder-integrity.json"}


@dataclass(frozen=True)
class ReviewSource:
    manifest: review.OutputReviewManifest
    manifest_path: Path
    output: Path

    def path(self, name: str) -> Path:
        """Resolve one fixed allowlisted artifact, never a caller-supplied path."""
        filename = REVIEW_FILES[name]
        return self.manifest_path.parent/filename if filename == "model-events.mid" else self.output/filename


def review_base(folder: Path) -> Path:
    """Keep derived receipts outside the immutable source job."""
    base = trial._base()/"output-reviews"
    for path in (base, base/folder.name):
        if path.is_symlink():
            raise review.ReviewDataError("Review storage is unavailable")
    return base/folder.name


def read_review_source(folder: Path) -> ReviewSource | None:
    """Validate a new worker receipt or an explicitly registered legacy snapshot."""
    output = folder/"transcribe"
    result_path = output/"result.json"
    result = json.loads(review.read_bytes(result_path)) if result_path.is_file() else {}
    if result.get("status") == "needs_review":
        expected = result.get("output_review_manifest_sha256")
        if not isinstance(expected, str) or result.get("assets_verified_after") is not True:
            raise review.ReviewDataError("Worker output lacks a verified review receipt")
        path = output/"output-review.json"
        manifest = review.read_manifest(path, output, expected)
        if manifest.info.recorded_status != "needs_review":
            raise review.ReviewDataError("Worker review status differs")
    else:
        path = review_base(folder)/"source"/"output-review.json"
        if not path.is_file():
            return None
        manifest = review.read_manifest(path, output)
        state = json.loads(review.read_bytes(folder/"status.json"))
        if state.get("status") != "failed" or state.get("stage") != "invalid_model_output" or manifest.info.recorded_status != "failed":
            raise review.ReviewDataError("Legacy source is not the registered failed output")
    request = json.loads(review.read_bytes(folder/"request.json"))
    if abs(request["selection"]["selected_seconds"]-manifest.duration_seconds) > 1e-8:
        raise review.ReviewDataError("Source duration differs")
    return ReviewSource(manifest=manifest, manifest_path=path, output=output)


class ReviewPreview(BaseModel):
    """A distinct explicit derivative, with its own durable request identifier."""
    model_config = ConfigDict(extra="forbid")
    request_id: str
    source_job_id: str
    status: Literal["pending", "completed", "blocked"]
    policy: Literal["exclude_zero_duration"] = "exclude_zero_duration"
    raw_note_count: int
    excluded_note_count: int
    retained_note_count: int
    midi_trial_id: str | None = None

    @model_validator(mode="after")
    def consistent_preview(self) -> "ReviewPreview":
        for value in (self.request_id, self.source_job_id):
            identifier = UUID(value)
            if identifier.version != 4 or str(identifier) != value:
                raise ValueError("Canonical UUID4 required")
        if not 0 < self.excluded_note_count <= self.raw_note_count or not 0 < self.retained_note_count <= 2000:
            raise ValueError("Invalid preview counts")
        if self.raw_note_count != self.excluded_note_count + self.retained_note_count:
            raise ValueError("Preview counts differ")
        if (self.status == "completed") != (self.midi_trial_id is not None):
            raise ValueError("Only a complete preview has a saved MIDI trial")
        if self.midi_trial_id is not None and self.midi_trial_id != self.request_id:
            raise ValueError("Preview trial identity differs")
        return self


def _preview_folder(folder: Path, identifier: str) -> Path:
    try:
        parsed = UUID(identifier)
        if parsed.version != 4 or str(parsed) != identifier:
            raise ValueError
    except ValueError:
        trial._reject(404, "review_preview_not_found", "Review preview not found.")
    base = trial._base()/"output-review-previews"
    path = base/identifier
    if base.is_symlink() or path.is_symlink():
        trial._reject(409, "unsafe_review_storage", "Review storage is unavailable.")
    return path


def _source(folder: Path) -> ReviewSource:
    state = json.loads(review.read_bytes(folder/"status.json"))
    if state.get("status") not in {"failed", "needs_review"}:
        trial._reject(409, "not_reviewable", "Only a retained terminal output can be reviewed.")
    try:
        source = read_review_source(folder)
    except (review.ReviewDataError, OSError, ValueError, KeyError):
        trial._reject(409, "review_source_changed", "保存出力の整合性を確認できません。候補は作成せず原データを保持しています。")
    if source is None:
        trial._reject(409, "not_reviewable", "This output has no verified zero-duration review receipt.")
    return source


@contextmanager
def _lock(folder: Path, identifier: str):
    import fcntl
    base = _preview_folder(folder, identifier).parent
    base.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(base/(identifier+".lock"), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            trial._reject(409, "review_preview_busy", "候補を作成中です。同じ候補IDで結果を確認してください。")
        yield
    finally:
        os.close(descriptor)


def _read_preview(path: Path, job_id: str, identifier: str) -> ReviewPreview:
    from src.api.transcriptions import _folder
    try:
        plan = json.loads(review.read_bytes(path/"request.json"))
        state = ReviewPreview.model_validate(plan["state"])
        if state.request_id != identifier or state.source_job_id != job_id:
            raise ValueError("Preview identity differs")
        ready = path/"result.json"
        if ready.is_file():
            state = ReviewPreview.model_validate_json(review.read_bytes(ready))
            if state.request_id != identifier or state.source_job_id != job_id:
                raise ValueError("Result identity differs")
        else:
            summary_path = midi_trials._base_dir()/identifier/"summary.json"
            if summary_path.is_file():
                source = _source(_folder(job_id))
                if plan["identity"] != dict(source_manifest_sha256=review.digest(source.manifest_path), policy=state.policy):
                    raise ValueError("Preview source changed before completion was recorded")
                summary = midi_trials._read_summary(midi_trials._trial_dir(identifier))
                if summary.input_sha256 != review.digest(path/"preview.mid") or summary.input_note_count != state.retained_note_count:
                    raise ValueError("Saved candidate differs from preview plan")
                state = ReviewPreview.model_validate({**state.model_dump(),
                    "status": "completed" if summary.status == "completed" else "blocked",
                    "midi_trial_id": identifier if summary.status == "completed" else None})
                trial._write_json(ready, state.model_dump(mode="json"))
        if state.status == "completed":
            summary = midi_trials._read_summary(midi_trials._trial_dir(identifier))
            if summary.input_sha256 != review.digest(path/"preview.mid") or summary.input_note_count != state.retained_note_count:
                raise ValueError("Saved candidate changed")
        return state
    except (OSError, ValueError, KeyError):
        trial._reject(409, "review_preview_incomplete", "候補の記録が不完全です。重複作成せず、保存済み記録を確認してください。")


@router.get("/{job_id}/review-previews/{request_id}", response_model=ReviewPreview)
def get_review_preview(job_id: str, request_id: str) -> ReviewPreview:
    """Recover an explicit CPU preview without repeating any conversion or model."""
    from src.api.transcriptions import _folder
    folder = _folder(job_id)
    path = _preview_folder(folder, request_id)
    if not path.is_dir():
        trial._reject(404, "review_preview_not_found", "Review preview not found.")
    with _lock(folder, request_id):
        return _read_preview(path, job_id, request_id)


@router.post("/{job_id}/review-previews", response_model=ReviewPreview, status_code=201)
def create_review_preview(job_id: str, request_id: str = Form(...),
                          policy: Literal["exclude_zero_duration"] = Form(...)) -> ReviewPreview:
    """Exclude only zero-length rows after an explicit choice; preserve the source."""
    from src.api.transcriptions import _folder
    from src.pipelines.muscriptor_worker import _literal_midi
    folder = _folder(job_id)
    path = _preview_folder(folder, request_id)
    with _lock(folder, request_id):
        source = _source(folder)
        identity = dict(source_manifest_sha256=review.digest(source.manifest_path), policy=policy)
        if path.exists():
            state = _read_preview(path, job_id, request_id)
            plan = json.loads(review.read_bytes(path/"request.json"))
            if plan["identity"] != identity:
                trial._reject(409, "review_request_conflict", "This preview ID refers to different source output.")
            if state.status != "completed":
                trial._reject(409, "review_preview_incomplete", "候補の作成結果を確認中です。同じ候補IDで確認してください。")
            return state
        if (midi_trials._base_dir()/request_id).exists():
            trial._reject(409, "review_request_conflict", "This candidate ID is already used by another saved trial.")
        info, notes = review.inspect_source(source.output, source.manifest.duration_seconds, source.manifest.info.recorded_status)
        positive = [note for note in notes if note["offset"] > note["onset"]]
        if not 0 < len(positive) <= 2000:
            trial._reject(422, "review_preview_limit", "譜面候補は1～2000音に対応します。全出力の保存は引き続き利用できます。")
        path.mkdir(parents=True, exist_ok=False)
        state = ReviewPreview(request_id=request_id, source_job_id=job_id, status="pending",
            raw_note_count=info.raw_note_count, excluded_note_count=info.zero_duration_count,
            retained_note_count=info.positive_note_count)
        trial._write_json(path/"request.json", dict(identity=identity, state=state.model_dump(mode="json")))
        midi = path/"preview.mid"
        _literal_midi(midi, positive)
        trial._write_json(path/"excluded-zero-duration-rows.json", [note for note in notes if note["offset"] == note["onset"]])
        config = BassOutputConfig.model_validate(json.loads(review.read_bytes(folder/"request.json"))["config"])
        disclosure = (f"明示選択した別版：全{info.raw_note_count}音のうちゼロ長{info.zero_duration_count}件を除外。"
                      f"残る{info.positive_note_count}音の音高・開始・終了は保持。元の全音符・トークン・生MIDIは保存されています。正解精度は未確認です。")
        converted = midi_trials.import_midi_bytes(midi.read_bytes(), filename=f"確認用別版_ゼロ長{info.zero_duration_count}件を除外.mid",
            config=config, source_kind="local-midi", preview_requested=False,
            trial_id=request_id, additional_warnings=(disclosure,))
        if isinstance(converted, JSONResponse) or converted.status != "completed":
            state = state.model_copy(update={"status": "blocked"})
        else:
            # Recheck all source hashes after conversion; never authorize a candidate from a changing source.
            _source(folder)
            state = state.model_copy(update={"status": "completed", "midi_trial_id": converted.trial_id})
        trial._write_json(path/"result.json", state.model_dump(mode="json"))
        return state
