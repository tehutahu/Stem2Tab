"""Reproduce note-only ambiguity; this is fault injection, not model inference.

The below-E1 support guard is an offline, rejected hypothesis. It is never
registered as an adapter and does not change the PR8 filter or the default path.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.evaluation.harmonic_suppression import suppress_contained_harmonics
from src.evaluation.metrics import MetricConfig, evaluate_note_events
from src.evaluation.models import NoteEvent, NoteEventSet


def counterexample_cases() -> list[dict]:
    """Return fixed artificial observations with explicitly separate true notes."""
    return [
        {"id": "identical_E1_true", "observed": [(28, .7), (40, .7)], "true_pitch": 28},
        {"id": "identical_E2_true", "observed": [(28, .7), (40, .7)], "true_pitch": 40},
        {"id": "below_E1_false_B0", "observed": [(23, .7), (35, .7)], "true_pitch": 35},
        {"id": "five_string_true_B0", "observed": [(23, .7), (35, .7)], "true_pitch": 23},
        {"id": "third_harmonic_false_low", "observed": [(28, .7), (47, .7)], "true_pitch": 47},
        {"id": "strong_false_low", "observed": [(28, .9), (40, .4)], "true_pitch": 40},
    ]


def observed_events(case: dict) -> NoteEventSet:
    """Build raw observations without passing the artificial truth to a filter."""
    return NoteEventSet(notes=tuple(NoteEvent(start=.31, end=.86, midi=p, velocity=v)
                                   for p, v in case["observed"]))


def guard_support_below_e1(raw: NoteEventSet) -> NoteEventSet:
    """Offline hypothesis: retain sub-E1 notes but do not use them as support.

    This cannot fix false E1->true E2 and can retain harmonic FPs of true B0.
    The value 28 is the standard four-string E1 pitch, not a searched threshold.
    """
    below = tuple(n for n in raw.notes if n.midi < 28)
    above = NoteEventSet(notes=tuple(n for n in raw.notes if n.midi >= 28))
    filtered, _ = suppress_contained_harmonics(above)
    return NoteEventSet(notes=below + filtered.notes)


def evaluate_counterexamples() -> list[dict]:
    """Evaluate fixed injected notes; never claim that Basic Pitch emitted them."""
    rows = []
    for case in counterexample_cases():
        raw = observed_events(case)
        reference = NoteEventSet(notes=(NoteEvent(start=.31, end=.86,
                                                  midi=case["true_pitch"], velocity=.7),))
        current, _ = suppress_contained_harmonics(raw)
        for name, estimate in [("injected_raw", raw), ("pr8_filter", current),
                               ("offline_support_ge28", guard_support_below_e1(raw))]:
            metrics = evaluate_note_events(reference, estimate, config=MetricConfig(),
                                           runtime_seconds=0)
            rows.append({"case": case["id"], "method": name,
                         "data_kind": "artificial_raw_fault_injection",
                         **metrics.model_dump()})
    return rows


def main() -> int:
    """Write a new private diagnostic directory, or print aggregate rows."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    payload = {"production_changed": False, "guard_adopted": False,
               "warning": "Injected raw notes; no model inference or real recording involved",
               "results": evaluate_counterexamples()}
    text = json.dumps(payload, indent=2) + "\n"
    if args.output_dir is not None:
        args.output_dir.mkdir(parents=True, exist_ok=False)
        (args.output_dir / "results.json").write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
