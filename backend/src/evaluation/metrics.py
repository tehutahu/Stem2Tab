"""Core note-level and frame-level transcription quality metrics."""

from __future__ import annotations

from typing import Any

import mir_eval.transcription
import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from src.evaluation.models import NoteEventSet


class MetricConfig(BaseModel):
    """Configurable matching tolerances recorded with each benchmark run."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    onset_tolerance_ms: float = Field(default=50.0, gt=0.0)
    pitch_tolerance_cents: float = Field(default=50.0, gt=0.0)
    offset_ratio: float = Field(default=0.2, gt=0.0)
    offset_min_tolerance_ms: float = Field(default=50.0, gt=0.0)
    frame_hop_ms: float = Field(default=10.0, gt=0.0)

    @property
    def onset_tolerance_seconds(self) -> float:
        return self.onset_tolerance_ms / 1000.0

    @property
    def offset_min_tolerance_seconds(self) -> float:
        return self.offset_min_tolerance_ms / 1000.0

    @property
    def frame_hop_seconds(self) -> float:
        return self.frame_hop_ms / 1000.0


class BenchmarkMetrics(BaseModel):
    """Complete machine-readable metric payload for one adapter combination."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    reference_notes: int = Field(ge=0)
    estimated_notes: int = Field(ge=0)
    onset_precision: float = Field(ge=0.0, le=1.0)
    onset_recall: float = Field(ge=0.0, le=1.0)
    onset_f1: float = Field(ge=0.0, le=1.0)
    onset_offset_precision: float = Field(ge=0.0, le=1.0)
    onset_offset_recall: float = Field(ge=0.0, le=1.0)
    onset_offset_f1: float = Field(ge=0.0, le=1.0)
    average_overlap_ratio: float = Field(ge=0.0, le=1.0)
    frame_precision: float = Field(ge=0.0, le=1.0)
    frame_recall: float = Field(ge=0.0, le=1.0)
    frame_f1: float = Field(ge=0.0, le=1.0)
    octave_error_count: int = Field(ge=0)
    octave_error_rate: float = Field(ge=0.0)
    extra_note_count: int = Field(ge=0)
    missed_note_count: int = Field(ge=0)
    duration_mae_ms: float | None = Field(default=None, ge=0.0)
    runtime_seconds: float = Field(ge=0.0)


def evaluate_note_events(
    reference: NoteEventSet,
    estimated: NoteEventSet,
    *,
    config: MetricConfig,
    runtime_seconds: float,
) -> BenchmarkMetrics:
    """Evaluate estimated note events against a non-empty reference."""
    if not reference.notes:
        raise ValueError("Reference note events must not be empty")

    ref_intervals, ref_pitches = _as_mir_eval_arrays(reference)
    est_intervals, est_pitches = _as_mir_eval_arrays(estimated)
    common_kwargs: dict[str, Any] = {
        "onset_tolerance": config.onset_tolerance_seconds,
        "pitch_tolerance": config.pitch_tolerance_cents,
        "offset_min_tolerance": config.offset_min_tolerance_seconds,
    }

    if estimated.notes:
        onset_precision, onset_recall, onset_f1, overlap = (
            mir_eval.transcription.precision_recall_f1_overlap(
                ref_intervals,
                ref_pitches,
                est_intervals,
                est_pitches,
                offset_ratio=None,
                **common_kwargs,
            )
        )
        onset_offset_precision, onset_offset_recall, onset_offset_f1, _ = (
            mir_eval.transcription.precision_recall_f1_overlap(
                ref_intervals,
                ref_pitches,
                est_intervals,
                est_pitches,
                offset_ratio=config.offset_ratio,
                **common_kwargs,
            )
        )
        onset_matches = mir_eval.transcription.match_notes(
            ref_intervals,
            ref_pitches,
            est_intervals,
            est_pitches,
            offset_ratio=None,
            **common_kwargs,
        )
    else:
        onset_precision = onset_recall = onset_f1 = overlap = 0.0
        onset_offset_precision = onset_offset_recall = onset_offset_f1 = 0.0
        onset_matches = []

    frame_precision, frame_recall, frame_f1 = _frame_metrics(
        reference,
        estimated,
        hop_seconds=config.frame_hop_seconds,
    )
    octave_errors = _octave_error_count(
        reference,
        estimated,
        onset_matches=onset_matches,
        onset_tolerance_seconds=config.onset_tolerance_seconds,
    )
    duration_mae_ms = _duration_mae_ms(reference, estimated, onset_matches)

    return BenchmarkMetrics(
        reference_notes=len(reference.notes),
        estimated_notes=len(estimated.notes),
        onset_precision=onset_precision,
        onset_recall=onset_recall,
        onset_f1=onset_f1,
        onset_offset_precision=onset_offset_precision,
        onset_offset_recall=onset_offset_recall,
        onset_offset_f1=onset_offset_f1,
        average_overlap_ratio=overlap,
        frame_precision=frame_precision,
        frame_recall=frame_recall,
        frame_f1=frame_f1,
        octave_error_count=octave_errors,
        octave_error_rate=octave_errors / len(reference.notes),
        extra_note_count=len(estimated.notes) - len(onset_matches),
        missed_note_count=len(reference.notes) - len(onset_matches),
        duration_mae_ms=duration_mae_ms,
        runtime_seconds=runtime_seconds,
    )


def _as_mir_eval_arrays(events: NoteEventSet) -> tuple[np.ndarray, np.ndarray]:
    intervals = np.asarray(
        [(note.start, note.end) for note in events.notes],
        dtype=float,
    ).reshape((-1, 2))
    pitches = np.asarray(
        [440.0 * 2.0 ** ((note.midi - 69) / 12.0) for note in events.notes],
        dtype=float,
    )
    return intervals, pitches


def _frame_metrics(
    reference: NoteEventSet,
    estimated: NoteEventSet,
    *,
    hop_seconds: float,
) -> tuple[float, float, float]:
    all_notes = reference.notes + estimated.notes
    if not all_notes:
        return 0.0, 0.0, 0.0

    end_time = max(note.end for note in all_notes)
    times = np.arange(0.0, end_time, hop_seconds, dtype=float)
    if times.size == 0:
        times = np.asarray([0.0], dtype=float)

    reference_roll = _events_to_roll(reference, times)
    estimated_roll = _events_to_roll(estimated, times)
    true_positive = int(np.logical_and(reference_roll, estimated_roll).sum())
    false_positive = int(np.logical_and(~reference_roll, estimated_roll).sum())
    false_negative = int(np.logical_and(reference_roll, ~estimated_roll).sum())
    precision = _safe_ratio(true_positive, true_positive + false_positive)
    recall = _safe_ratio(true_positive, true_positive + false_negative)
    f1 = _f1(precision, recall)
    return precision, recall, f1


def _events_to_roll(events: NoteEventSet, times: np.ndarray) -> np.ndarray:
    roll = np.zeros((times.size, 128), dtype=bool)
    for note in events.notes:
        active = np.logical_and(times >= note.start, times < note.end)
        roll[active, note.midi] = True
    return roll


def _octave_error_count(
    reference: NoteEventSet,
    estimated: NoteEventSet,
    *,
    onset_matches: list[tuple[int, int]],
    onset_tolerance_seconds: float,
) -> int:
    matched_estimates = {estimated_index for _, estimated_index in onset_matches}
    candidates: list[tuple[float, int, int]] = []
    for reference_index, reference_note in enumerate(reference.notes):
        for estimated_index, estimated_note in enumerate(estimated.notes):
            if estimated_index in matched_estimates:
                continue
            onset_error = abs(reference_note.start - estimated_note.start)
            if onset_error <= onset_tolerance_seconds and abs(reference_note.midi - estimated_note.midi) == 12:
                candidates.append((onset_error, reference_index, estimated_index))

    matched_references: set[int] = set()
    matched_octave_estimates: set[int] = set()
    for _, reference_index, estimated_index in sorted(candidates):
        if reference_index in matched_references or estimated_index in matched_octave_estimates:
            continue
        matched_references.add(reference_index)
        matched_octave_estimates.add(estimated_index)
    return len(matched_references)


def _duration_mae_ms(
    reference: NoteEventSet,
    estimated: NoteEventSet,
    onset_matches: list[tuple[int, int]],
) -> float | None:
    if not onset_matches:
        return None
    absolute_errors = [
        abs(
            (estimated.notes[estimated_index].end - estimated.notes[estimated_index].start)
            - (reference.notes[reference_index].end - reference.notes[reference_index].start)
        )
        for reference_index, estimated_index in onset_matches
    ]
    return float(np.mean(absolute_errors) * 1000.0)


def _safe_ratio(numerator: int, denominator: int) -> float:
    return float(numerator / denominator) if denominator else 0.0


def _f1(precision: float, recall: float) -> float:
    return 2.0 * precision * recall / (precision + recall) if precision + recall else 0.0
