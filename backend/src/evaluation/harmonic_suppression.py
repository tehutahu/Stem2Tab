"""Conservative, optional removal of contained harmonic duplicates in mono bass."""
from __future__ import annotations

from math import log2
from pydantic import BaseModel, ConfigDict, Field
from src.evaluation.models import NoteEventSet


class HarmonicSuppressionConfig(BaseModel):
    """Fixed coincidence tolerances; no reference annotations are accepted."""
    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")
    onset_tolerance_seconds: float = Field(default=.05, ge=0)
    harmonic_tolerance_cents: float = Field(default=50., ge=0)
    max_harmonic: int = Field(default=8, ge=2)


def suppress_contained_harmonics(
    events: NoteEventSet,
    *,
    config: HarmonicSuppressionConfig = HarmonicSuppressionConfig(),
) -> tuple[NoteEventSet, list[dict[str, int | float]]]:
    """Remove upper harmonics only when a retained lower note fully covers them.

    This is an explicit monophonic-bass assumption, not polyphonic transcription.
    Preserve every surviving note's original time, pitch and velocity. A lower
    note rejected earlier cannot suppress another note. Octave changes whose
    onsets differ by more than the tolerance, or lack full coverage, are kept.
    """
    kept: list[int] = []
    rejected: list[dict[str, int | float]] = []
    for index in sorted(range(len(events.notes)), key=lambda i: (events.notes[i].midi, events.notes[i].start, events.notes[i].end)):
        upper = events.notes[index]
        support = None
        for lower_index in kept:
            lower = events.notes[lower_index]
            if lower.midi >= upper.midi:
                continue
            if abs(upper.start-lower.start) > config.onset_tolerance_seconds + 1e-12:
                continue
            if lower.start > upper.start or lower.end < upper.end:
                continue
            harmonic = min(range(2,config.max_harmonic+1), key=lambda h: abs(100*(upper.midi-lower.midi)-1200*log2(h)))
            error = abs(100*(upper.midi-lower.midi)-1200*log2(harmonic))
            if error <= config.harmonic_tolerance_cents + 1e-9:
                support = dict(rejected_index=index,support_index=lower_index,harmonic=harmonic,harmonic_error_cents=error)
                break
        if support is None:
            kept.append(index)
        else:
            rejected.append(support)
    return NoteEventSet(source={**events.source,"processing":"contained_harmonic_suppression"},notes=tuple(events.notes[i] for i in sorted(kept))), rejected
