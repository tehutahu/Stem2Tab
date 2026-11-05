"""Synthetic, original-time fingering constraints; no models or file writes."""
from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError
import pytest

from src.pipelines import bass_fingering as planner
from src.pipelines.bass_output import BassOutputConfig, BassOutputEvent, _mapping


def event(index: int, midi: int, start: float = 0., end: float = .5, velocity: int = 95) -> BassOutputEvent:
    return BassOutputEvent(source_index=index, midi=midi, start=start, end=end, velocity=velocity)


def assert_plan(events: list[BassOutputEvent], config: BassOutputConfig, report) -> None:
    assert report.status == "planned" and report.mapped_event_count == len(events)
    assert report.search["complete"] is True
    assert report.partial_plan is False and report.transposition_semitones == 0
    for index, row in enumerate(report.events):
        assert row.input_ordinal == index
        assert {key: getattr(row, key) for key in ("source_index", "midi", "start", "end", "velocity")} == events[index].model_dump()
        assert config.resolved_tuning[-row.string] + row.fret == row.midi
    for index, left in enumerate(report.events):
        for right in report.events[index + 1:]:
            if max(left.start, right.start) < min(left.end, right.end):
                assert left.string != right.string
    assert report.metrics["same_string_overlap_pairs"] == 0
    assert report.metrics["max_fretted_span"] <= report.max_hand_span
    assert report.metrics["max_position_shift_observed"] <= report.max_position_shift


@pytest.mark.parametrize(("profile", "pitches", "strings"), [
    ("bass4", [28, 33, 38, 43], [4, 3, 2, 1]),
    ("bass5", [23, 28, 33, 38, 43], [5, 4, 3, 2, 1]),
])
def test_open_tunings_preserve_low_b_e1_and_simultaneous_pitches(profile, pitches, strings) -> None:
    config = BassOutputConfig(profile=profile)
    events = [event(index, pitch) for index, pitch in enumerate(pitches)]
    report = planner.plan_bass_fingering(events, config=config)
    assert_plan(events, config, report)
    assert [row.string for row in report.events] == strings
    assert [row.fret for row in report.events] == [0] * len(events)
    assert report.cost.position_movement == report.cost.total_fret == 0
    assert report.timeline[0]["hand_position"] is None


def test_explicit_custom_drop_d_zero_fret_limit() -> None:
    config = BassOutputConfig(profile="custom", strings=4, tuning_midi=(26, 33, 38, 43), max_fret=0)
    events = [event(9, 26), event(3, 43)]
    report = planner.plan_bass_fingering(events, config=config, max_position_shift=0, max_hand_span=0)
    assert_plan(events, config, report)
    assert [(row.string, row.fret) for row in report.events] == [(4, 0), (1, 0)]


@pytest.mark.parametrize(("profile", "pitch"), [("bass4", 27), ("bass5", 22), ("bass4", 68)])
def test_pitch_outside_explicit_range_never_octaves_drops_or_returns_partial(profile, pitch) -> None:
    events = [event(100, 28, 0., .3), event(2, pitch, 1., 1.3)]
    before = [row.model_dump() for row in events]
    report = planner.plan_bass_fingering(events, config=BassOutputConfig(profile=profile))
    assert report.status == "unplayable" and report.cost is None
    assert report.reasons[0]["code"] == "pitch_out_of_range"
    assert report.mapped_event_count == 0 and len(report.events) == 2
    assert all(row.string is row.fret is None for row in report.events)
    assert [row.model_dump() for row in events] == before
    assert [row.midi for row in report.events] == [28, pitch]


def test_two_overlapping_notes_on_one_string_are_explicitly_unplayable() -> None:
    config = BassOutputConfig(profile="custom", strings=1, tuning_midi=(28,), max_fret=24)
    events = [event(0, 28, 0., 1.), event(1, 29, .5, 1.5)]
    report = planner.plan_bass_fingering(events, config=config)
    assert report.status == "unplayable" and report.search["complete"] is True
    assert report.legacy["metrics"]["same_string_overlap_pairs"] == 1
    assert report.reasons[0]["code"] == "constraints_unsatisfied"


def test_repeated_pitch_overlap_uses_distinct_strings_instead_of_shortening() -> None:
    events = [event(7, 43, 0., 1.), event(8, 43, .5, 1.5)]
    config = BassOutputConfig(profile="bass4")
    report = planner.plan_bass_fingering(events, config=config)
    assert_plan(events, config, report)
    assert report.events[0].string != report.events[1].string
    assert report.events[0].end == 1. and report.events[1].start == .5
    assert report.legacy["metrics"]["same_string_overlap_pairs"] == 1


def test_exact_release_boundary_allows_same_string_reattack() -> None:
    events = [event(4, 43, 0., .5), event(9, 43, .5, .9)]
    config = BassOutputConfig(profile="bass4")
    report = planner.plan_bass_fingering(events, config=config, max_hand_span=0, max_position_shift=0)
    assert_plan(events, config, report)
    assert [(row.string, row.fret) for row in report.events] == [(1, 0), (1, 0)]


def test_same_onset_chord_is_joint_and_independent_of_input_order() -> None:
    events = [event(70, 44), event(10, 45)]
    config = BassOutputConfig(profile="bass4")
    report = planner.plan_bass_fingering(events, config=config)
    reverse = planner.plan_bass_fingering(list(reversed(events)), config=config)
    assert_plan(events, config, report)
    assert_plan(list(reversed(events)), config, reverse)
    assert {row.source_index: (row.string, row.fret) for row in report.events} == {
        row.source_index: (row.string, row.fret) for row in reverse.events}
    assert [(row.string, row.fret) for row in report.events] == [(2, 6), (1, 2)]
    assert report.cost == reverse.cost


def test_chord_hand_span_is_explicit_and_open_strings_do_not_force_zero_position() -> None:
    config = BassOutputConfig(profile="custom", strings=2, tuning_midi=(28, 33), max_fret=8)
    blocked = planner.plan_bass_fingering([event(0, 29), event(1, 35)], config=config, max_hand_span=0)
    assert blocked.status == "unplayable"
    events = [event(0, 28), event(1, 35)]
    report = planner.plan_bass_fingering(events, config=config, max_hand_span=0)
    assert_plan(events, config, report)
    assert sorted(row.fret for row in report.events) == [0, 2]
    assert report.timeline[0]["hand_position"] == 2 and report.metrics["max_fretted_span"] == 0


def test_large_legacy_jump_gets_legal_alternative_with_comparable_travel_metrics() -> None:
    events = [event(0, 44, 0., .4), event(1, 55, .5, .9)]
    config = BassOutputConfig(profile="bass4")
    report = planner.plan_bass_fingering(events, config=config)
    assert_plan(events, config, report)
    assert report.cost.position_movement == 0
    assert report.legacy["metrics"]["position_movement"] == 7
    assert report.legacy["anchor_path_respects_movement_limit"] is False
    assert report.movement_comparison["reduction"] == 7
    assert [(row.string, row.fret) for row in report.events] == [(3, 11), (1, 12)]


@pytest.mark.parametrize("gap", [.5, 100.])
def test_one_string_jump_cannot_reset_position_during_rest_or_open_attack(gap) -> None:
    config = BassOutputConfig(profile="custom", strings=1, tuning_midi=(28,), max_fret=24)
    events = [event(0, 29, 0., .1), event(1, 28, .2, .3), event(2, 52, gap, gap + .1)]
    report = planner.plan_bass_fingering(events, config=config)
    assert report.status == "unplayable"
    allowed = planner.plan_bass_fingering(events, config=config, max_position_shift=19)
    assert_plan(events, config, allowed)
    assert allowed.cost.position_movement == 19
    assert allowed.timeline[0]["hand_position"] == allowed.timeline[1]["hand_position"] == 1


def test_sustained_fretted_notes_constrain_later_hand_window() -> None:
    config = BassOutputConfig(profile="custom", strings=2, tuning_midi=(28, 33), max_fret=24)
    events = [event(0, 29, 0., 2.), event(1, 50, .5, 1.)]
    report = planner.plan_bass_fingering(events, config=config, max_position_shift=36, max_hand_span=4)
    assert report.status == "unplayable"  # f1 held on low string, f17 required on high string.
    assert report.events[0].end == 2.


def test_unsorted_input_original_values_and_duplicate_indices_survive_json_roundtrip(tmp_path: Path) -> None:
    config = BassOutputConfig(profile="bass5")
    events = [event(8, 28, 2., 2.4, 115), event(8, 23, 0., .4, 31)]
    before = [row.model_dump() for row in events]
    report = planner.plan_bass_fingering(events, config=config)
    assert_plan(events, config, report)
    assert [row.model_dump() for row in events] == before
    assert [row.source_index for row in report.events] == [8, 8]
    assert any("Duplicate source" in warning for warning in report.warnings)
    loaded = planner.BassFingeringReport.model_validate_json(report.model_dump_json())
    assert loaded == report and not list(tmp_path.iterdir())


def test_existing_first_playable_default_mapper_remains_identical() -> None:
    config = BassOutputConfig(profile="bass4")
    events = [event(0, 44, 0., .4), event(1, 55, .5, .9)]
    before = [row.model_dump() for row in _mapping(events, config)]
    report = planner.plan_bass_fingering(events, config=config)
    assert [row.model_dump() for row in _mapping(events, config)] == before
    assert [(row["string"], row["fret"]) for row in report.legacy["events"]] == [(1, 1), (1, 12)]


def test_empty_plan_and_five_hundred_simple_events_are_bounded_and_complete() -> None:
    config = BassOutputConfig(profile="bass5")
    empty = planner.plan_bass_fingering([], config=config)
    assert empty.status == "planned" and empty.events == () and empty.cost.position_movement == 0
    events = [event(index, 23, index, index + .5) for index in range(500)]
    report = planner.plan_bass_fingering(events, config=config)
    assert_plan(events, config, report)
    assert report.search["expansions"] <= planner.MAX_EXPANSIONS


@pytest.mark.parametrize("cap", ["events", "states", "expansions"])
def test_any_search_cap_returns_too_complex_without_false_infeasibility(cap, monkeypatch) -> None:
    config = BassOutputConfig(profile="bass4")
    events = [event(0, 44), event(1, 45, 1., 1.5)]
    monkeypatch.setattr(planner, {"events": "MAX_EVENTS", "states": "MAX_STATES", "expansions": "MAX_EXPANSIONS"}[cap], 1)
    report = planner.plan_bass_fingering(events, config=config)
    assert report.status == "too_complex" and report.search["complete"] is False
    assert report.mapped_event_count == 0 and report.cost is None
    assert len(report.events) == len(events) and all(row.string is None for row in report.events)


@pytest.mark.parametrize("changes", [{"max_position_shift": -1}, {"max_hand_span": 37},
                                      {"max_position_shift": True}, {"max_hand_span": 1.5}])
def test_invalid_planner_constraints_are_rejected(changes) -> None:
    with pytest.raises(ValueError):
        planner.plan_bass_fingering([event(0, 28)], config=BassOutputConfig(profile="bass4"), **changes)


def test_invalid_tuning_or_unvalidated_event_is_rejected() -> None:
    with pytest.raises(ValidationError):
        BassOutputConfig(profile="custom", strings=2, tuning_midi=(33, 28), max_fret=24)
    with pytest.raises(TypeError):
        planner.plan_bass_fingering([dict(midi=28)], config=BassOutputConfig(profile="bass4"))
