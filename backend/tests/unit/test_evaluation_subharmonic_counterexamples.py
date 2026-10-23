"""Diagnostic regressions for a deliberately unadopted support-range guard."""
from scripts.subharmonic_counterexamples import (
    counterexample_cases, evaluate_counterexamples, guard_support_below_e1, observed_events,
)
from src.evaluation.harmonic_suppression import suppress_contained_harmonics


def test_identical_observations_do_not_identify_the_true_octave():
    low, high = counterexample_cases()[:2]
    assert observed_events(low) == observed_events(high)
    assert low["true_pitch"] != high["true_pitch"]
    assert suppress_contained_harmonics(observed_events(low))[0].notes == suppress_contained_harmonics(observed_events(high))[0].notes
    rows = evaluate_counterexamples()
    current = {r["case"]: r for r in rows if r["method"] == "pr8_filter"}
    assert current["identical_E1_true"]["onset_recall"] == 1
    assert current["identical_E2_true"]["onset_recall"] == 0


def test_range_guard_rescues_below_range_example_but_cannot_resolve_in_range_false_low():
    rows = {(r["case"], r["method"]): r for r in evaluate_counterexamples()}
    assert rows["below_E1_false_B0", "pr8_filter"]["onset_recall"] == 0
    assert rows["below_E1_false_B0", "offline_support_ge28"]["onset_recall"] == 1
    assert rows["identical_E2_true", "offline_support_ge28"]["onset_recall"] == 0
    assert rows["strong_false_low", "offline_support_ge28"]["onset_recall"] == 0
    assert rows["third_harmonic_false_low", "offline_support_ge28"]["onset_recall"] == 0


def test_range_guard_loses_precision_for_true_five_string_low_b():
    rows = {(r["case"], r["method"]): r for r in evaluate_counterexamples()}
    assert rows["five_string_true_B0", "pr8_filter"]["onset_precision"] == 1
    assert rows["five_string_true_B0", "offline_support_ge28"]["onset_precision"] == .5


def test_offline_guard_only_retains_original_notes_with_identical_boundaries():
    for case in counterexample_cases():
        raw = observed_events(case)
        before = raw.model_dump_json()
        guarded = guard_support_below_e1(raw)
        assert all(note in raw.notes for note in guarded.notes)
        assert raw.model_dump_json() == before
