"""Optional fixed C extra stage for already-PR8 canonical note events."""
from math import log2
from .models import NoteEventSet


def additional_stage(events: NoteEventSet) -> tuple[NoteEventSet, list[dict]]:
    """Keep a subset without changing survivors; a true octave note may be deleted."""
    kept: list[int] = []
    rejected: list[dict] = []
    for index in sorted(range(len(events.notes)), key=lambda i: (
        events.notes[i].midi, events.notes[i].start, events.notes[i].end
    )):
        upper = events.notes[index]
        support = None
        for lower_index in kept:
            lower = events.notes[lower_index]
            if upper.midi - lower.midi != 12 or not upper.start < lower.start:
                continue
            if abs(upper.start - lower.start) > .05 + 1e-12:
                continue
            if upper.end > lower.end:
                continue
            harmonic = min(range(2, 9), key=lambda h: abs(
                100 * (upper.midi - lower.midi) - 1200 * log2(h)
            ))
            error = abs(100 * (upper.midi - lower.midi) - 1200 * log2(harmonic))
            if error <= 50 + 1e-9:
                support = dict(rejected_index=index, support_index=lower_index,
                               harmonic=harmonic, harmonic_error_cents=error,
                               reason='exact12_earlier_upper_start')
                break
        if support is None:
            kept.append(index)
        else:
            rejected.append(support)
    return NoteEventSet(source=events.source, notes=tuple(
        events.notes[i] for i in sorted(kept)
    )), rejected
