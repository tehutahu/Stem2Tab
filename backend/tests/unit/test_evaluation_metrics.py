from __future__ import annotations

import pytest

from src.evaluation.metrics import MetricConfig, evaluate_note_events
from src.evaluation.models import NoteEvent, NoteEventSet

CONFIG = MetricConfig()


def _events(*notes: NoteEvent) -> NoteEventSet:
    return NoteEventSet(notes=notes)


def test_exact_match_scores_one() -> None:
    reference = _events(
        NoteEvent(start=0.0, end=0.5, midi=40),
        NoteEvent(start=0.5, end=1.0, midi=43),
    )

    metrics = evaluate_note_events(reference, reference, config=CONFIG, runtime_seconds=1.25)

    assert metrics.onset_f1 == pytest.approx(1.0)
    assert metrics.onset_offset_f1 == pytest.approx(1.0)
    assert metrics.frame_f1 == pytest.approx(1.0)
    assert metrics.average_overlap_ratio == pytest.approx(1.0)
    assert metrics.extra_note_count == 0
    assert metrics.missed_note_count == 0
    assert metrics.duration_mae_ms == pytest.approx(0.0)
    assert metrics.runtime_seconds == pytest.approx(1.25)


def test_onset_outside_tolerance_is_extra_and_missed() -> None:
    reference = _events(NoteEvent(start=0.0, end=0.5, midi=40))
    estimated = _events(NoteEvent(start=0.06, end=0.5, midi=40))

    metrics = evaluate_note_events(reference, estimated, config=CONFIG, runtime_seconds=0.0)

    assert metrics.onset_f1 == 0.0
    assert metrics.extra_note_count == 1
    assert metrics.missed_note_count == 1
    assert metrics.duration_mae_ms is None
    assert metrics.average_overlap_ratio == 0.0


def test_offset_mismatch_preserves_onset_score() -> None:
    reference = _events(NoteEvent(start=0.0, end=1.0, midi=40))
    estimated = _events(NoteEvent(start=0.0, end=1.5, midi=40))

    metrics = evaluate_note_events(reference, estimated, config=CONFIG, runtime_seconds=0.0)

    assert metrics.onset_f1 == pytest.approx(1.0)
    assert metrics.onset_offset_f1 == 0.0
    assert metrics.duration_mae_ms == pytest.approx(500.0)
    assert 0.0 < metrics.frame_f1 < 1.0


def test_octave_duplicate_is_reported_as_extra_octave_error() -> None:
    reference = _events(NoteEvent(start=0.0, end=1.0, midi=40))
    estimated = _events(
        NoteEvent(start=0.0, end=1.0, midi=40),
        NoteEvent(start=0.0, end=1.0, midi=52),
    )

    metrics = evaluate_note_events(reference, estimated, config=CONFIG, runtime_seconds=0.0)

    assert metrics.missed_note_count == 0
    assert metrics.extra_note_count == 1
    assert metrics.octave_error_count == 1
    assert metrics.octave_error_rate == pytest.approx(1.0)


def test_empty_estimate_returns_zero_scores_and_all_notes_missed() -> None:
    reference = _events(NoteEvent(start=0.0, end=1.0, midi=40))

    metrics = evaluate_note_events(
        reference,
        NoteEventSet(),
        config=CONFIG,
        runtime_seconds=0.0,
    )

    assert metrics.onset_precision == 0.0
    assert metrics.onset_recall == 0.0
    assert metrics.onset_f1 == 0.0
    assert metrics.frame_f1 == 0.0
    assert metrics.estimated_notes == 0
    assert metrics.extra_note_count == 0
    assert metrics.missed_note_count == 1
    assert metrics.duration_mae_ms is None
    assert metrics.average_overlap_ratio == 0.0


def test_empty_reference_is_rejected() -> None:
    with pytest.raises(ValueError, match="Reference note events"):
        evaluate_note_events(
            NoteEventSet(),
            NoteEventSet(),
            config=CONFIG,
            runtime_seconds=0.0,
        )


@pytest.mark.parametrize("reference_start,estimated_start", [(0.0, 0.03), (0.0, 0.04), (0.04, 0.0)])
def test_onset_matched_touching_or_disjoint_notes_have_zero_overlap(
    reference_start: float,
    estimated_start: float,
) -> None:
    reference = _events(NoteEvent(start=reference_start, end=reference_start + 0.03, midi=40))
    estimated = _events(NoteEvent(start=estimated_start, end=estimated_start + 0.03, midi=40))

    metrics = evaluate_note_events(reference, estimated, config=CONFIG, runtime_seconds=0.0)

    assert metrics.average_overlap_ratio == 0.0
    assert metrics.onset_f1 == 1.0
    assert metrics.onset_offset_f1 == 1.0
    assert metrics.extra_note_count == metrics.missed_note_count == 0


def test_partial_overlap_keeps_intersection_over_span() -> None:
    reference = _events(NoteEvent(start=0.0, end=0.08, midi=40))
    estimated = _events(NoteEvent(start=0.03, end=0.12, midi=40))

    metrics = evaluate_note_events(reference, estimated, config=CONFIG, runtime_seconds=0.0)

    assert metrics.average_overlap_ratio == pytest.approx(0.05 / 0.12)


def test_overlap_clamps_each_pair_before_averaging() -> None:
    reference = _events(
        NoteEvent(start=0.0, end=0.03, midi=40),
        NoteEvent(start=1.0, end=1.5, midi=43),
    )
    estimated = _events(
        NoteEvent(start=0.04, end=0.07, midi=40),
        NoteEvent(start=1.0, end=1.5, midi=43),
    )

    metrics = evaluate_note_events(reference, estimated, config=CONFIG, runtime_seconds=0.0)

    assert metrics.average_overlap_ratio == pytest.approx(0.5)
    assert metrics.onset_f1 == metrics.onset_offset_f1 == 1.0
    assert metrics.extra_note_count == metrics.missed_note_count == 0
