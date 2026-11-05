"""Bounded, non-destructive review of retained zero-duration model events."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator

if __package__:
    from .muscriptor_decoder import inspect_decoder_integrity
else:
    from muscriptor_decoder import inspect_decoder_integrity

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
RAW_FILES = {"notes.json", "events.jsonl", "tokens.jsonl", "decoder-integrity.json", "model-events.mid"}
MAX_SOURCE_BYTES = 20 * 1024**2


class ReviewDataError(ValueError):
    """A retained source is changed, incomplete or outside the review contract."""


class RetainedNote(BaseModel):
    """Validate existing raw rows; never rewrite the retained JSON."""
    model_config = ConfigDict(extra="forbid", frozen=True)
    index: Annotated[StrictInt, Field(ge=0)]
    pitch: Annotated[StrictInt, Field(ge=0, le=127)]
    program: Annotated[StrictInt, Field(ge=0, le=128)]
    instrument: Annotated[str, Field(min_length=1, max_length=128)]
    is_drum: StrictBool
    onset: Annotated[float, Field(strict=True, ge=0, allow_inf_nan=False)]
    offset: Annotated[float, Field(strict=True, ge=0, allow_inf_nan=False)]


class OutputReviewInfo(BaseModel):
    """Inference finished, while strict score conversion awaits an explicit choice."""
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["zero_duration"] = "zero_duration"
    raw_note_count: Annotated[int, Field(ge=1, le=50000)]
    zero_duration_count: Annotated[int, Field(ge=1, le=50000)]
    positive_note_count: Annotated[int, Field(ge=0, le=50000)]
    recorded_status: Literal["needs_review", "failed"]
    notes_modified: Literal[False] = False

    @model_validator(mode="after")
    def counts_match(self) -> "OutputReviewInfo":
        if self.raw_note_count != self.zero_duration_count + self.positive_note_count:
            raise ValueError("Review counts differ")
        return self


class OutputReviewManifest(BaseModel):
    """Bind all raw output files, including every zero-length MIDI event pair."""
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    info: OutputReviewInfo
    duration_seconds: Annotated[float, Field(gt=0, le=600, allow_inf_nan=False)]
    files_sha256: dict[str, Digest]

    @model_validator(mode="after")
    def known_files(self) -> "OutputReviewManifest":
        if set(self.files_sha256) != RAW_FILES:
            raise ValueError("Unexpected raw output files")
        return self


def read_bytes(path: Path) -> bytes:
    """Read one regular bounded output, refusing linked directories and files."""
    if path.is_symlink() or path.parent.is_symlink() or not path.is_file() or path.stat().st_size > MAX_SOURCE_BYTES:
        raise ReviewDataError("Retained output is unavailable or exceeds the review bound")
    return path.read_bytes()


def digest(path: Path) -> str:
    """Hash the exact bounded source bytes."""
    return hashlib.sha256(read_bytes(path)).hexdigest()


def inspect_source(output: Path, duration: float, recorded_status: Literal["needs_review", "failed"]) -> tuple[OutputReviewInfo, list[dict[str, Any]]]:
    """Allow review only for zero-duration rows; negative/regressing data stays failed."""
    try:
        raw = json.loads(read_bytes(output/"notes.json"))
        if not isinstance(raw, list) or not 0 < len(raw) <= 50000:
            raise ValueError("Raw note list required")
        notes = [RetainedNote.model_validate(note).model_dump(mode="json") for note in raw]
        if len({note["index"] for note in notes}) != len(notes):
            raise ValueError("Duplicate note index")
        tokens = [json.loads(line) for line in read_bytes(output/"tokens.jsonl").splitlines()]
        integrity = inspect_decoder_integrity(raw, tokens)
        if integrity.status != "invalid" or integrity.shift_regressions or not integrity.note_violations:
            raise ValueError("Zero-duration review is not applicable")
        if any(violation.kind != "zero_duration" for violation in integrity.note_violations):
            raise ValueError("Other timing errors require separate investigation")
        end_limit = math.ceil(duration*100-1e-8)/100
        if not math.isfinite(duration) or not 0 < duration <= 600 or any(
            note["onset"] >= duration or note["offset"] > end_limit+1e-9 for note in notes
        ):
            raise ValueError("Events outside source audio")
        saved = json.loads(read_bytes(output/"decoder-integrity.json"))
        if saved != integrity.model_dump(mode="json"):
            raise ValueError("Retained diagnostics differ")
        count = len(integrity.note_violations)
        return OutputReviewInfo(raw_note_count=len(notes), zero_duration_count=count,
            positive_note_count=len(notes)-count, recorded_status=recorded_status), notes
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        raise ReviewDataError("Saved output does not satisfy zero-duration review") from exc


def write_manifest(output: Path, midi: Path, duration: float, recorded_status: Literal["needs_review", "failed"]) -> OutputReviewManifest:
    """Write a new receipt after the caller has preserved and validated raw MIDI."""
    info, _ = inspect_source(output, duration, recorded_status)
    hashes = {name: digest(midi if name == "model-events.mid" else output/name) for name in RAW_FILES}
    manifest = OutputReviewManifest(info=info, duration_seconds=duration, files_sha256=hashes)
    with (midi.parent/"output-review.json").open("x", encoding="utf-8") as stream:
        stream.write(manifest.model_dump_json(indent=2)+"\n")
    return manifest


def read_manifest(manifest_path: Path, output: Path, expected_sha256: str | None = None) -> OutputReviewManifest:
    """Check every source digest before enabling downloads or a derived preview."""
    try:
        data = read_bytes(manifest_path)
        if expected_sha256 is not None and hashlib.sha256(data).hexdigest() != expected_sha256:
            raise ValueError("Receipt changed")
        manifest = OutputReviewManifest.model_validate_json(data)
        for name, expected in manifest.files_sha256.items():
            path = manifest_path.parent/name if name == "model-events.mid" else output/name
            if digest(path) != expected:
                raise ValueError("Raw output changed")
        info, _ = inspect_source(output, manifest.duration_seconds, manifest.info.recorded_status)
        if info != manifest.info:
            raise ValueError("Review counts changed")
        return manifest
    except (OSError, ValueError, TypeError) as exc:
        raise ReviewDataError("Retained output is incomplete or changed; no derived preview was made") from exc
