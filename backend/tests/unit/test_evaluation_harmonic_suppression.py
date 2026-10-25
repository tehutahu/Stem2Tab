"""Behavioral regressions for optional monophonic harmonic suppression."""
import pytest
from pydantic import ValidationError
from src.evaluation.models import NoteEvent, NoteEventSet
from src.evaluation.harmonic_suppression import HarmonicSuppressionConfig,suppress_contained_harmonics


def events(*specs):
    return NoteEventSet(notes=tuple(NoteEvent(start=s,end=e,midi=p,velocity=.7) for s,e,p in specs))


def test_rejects_contained_simultaneous_octave_and_fifth_harmonics_without_moving_survivors():
    raw=events((.2,.8,28),(.21,.7,40),(.22,.75,47),(.2,.8,33))
    filtered,decisions=suppress_contained_harmonics(raw)
    assert filtered.notes==(raw.notes[0],raw.notes[1])
    assert {raw.notes[d['rejected_index']].midi for d in decisions}=={40,47}
    assert len(raw.notes)==4


@pytest.mark.parametrize('upper',[(.26,.7,40),(.21,.9,40),(.19,.7,40)])
def test_preserves_octave_changes_without_full_containment_and_coincident_onset(upper):
    raw=events((.2,.8,28),upper)
    filtered,decisions=suppress_contained_harmonics(raw)
    assert filtered.notes==raw.notes
    assert decisions==[]


def test_keeps_same_pitch_repick_and_nonharmonic_pitch():
    raw=events((.2,.8,28),(.21,.7,29),(.22,.7,28))
    filtered,decisions=suppress_contained_harmonics(raw)
    assert filtered.notes==raw.notes
    assert decisions==[]


def test_dropped_partial_cannot_suppress_another_note():
    # 19-semitone partial could explain MIDI66 as its third harmonic; MIDI28
    # cannot explain 66 within the configured second-to-eighth harmonic range.
    raw=events((0,1,28),(0,.8,47),(0,.7,66))
    filtered,decisions=suppress_contained_harmonics(raw)
    assert [n.midi for n in filtered.notes]==[28,66]
    assert len(decisions)==1


def test_empty_and_single_notes_are_identity():
    for raw in [events(),events((0,.1,28))]:
        assert suppress_contained_harmonics(raw)[0].notes==raw.notes


def test_configuration_rejects_invalid_or_mutable_values():
    with pytest.raises(ValidationError): HarmonicSuppressionConfig(harmonic_tolerance_cents=float('nan'))
    with pytest.raises(ValidationError): HarmonicSuppressionConfig(max_harmonic=1)
    with pytest.raises(ValidationError): HarmonicSuppressionConfig().max_harmonic=3


def test_input_order_does_not_change_surviving_notes():
    specs=[(0,1,28),(.01,.8,40),(.02,.7,47),(.1,.9,33)]
    assert suppress_contained_harmonics(events(*specs))[0].notes == suppress_contained_harmonics(events(*reversed(specs)))[0].notes


def test_onset_coincidence_boundary_is_inclusive_but_not_widened():
    assert len(suppress_contained_harmonics(events((0,1,28),(.05,.8,40)))[0].notes)==1
    assert len(suppress_contained_harmonics(events((0,1,28),(.050001,.8,40)))[0].notes)==2


def test_harmonic_cents_boundary_uses_fixed_inference_config():
    from math import log2
    raw=events((0,1,28),(.01,.8,47))
    cents=abs(1900-1200*log2(3))
    assert len(suppress_contained_harmonics(raw,config=HarmonicSuppressionConfig(harmonic_tolerance_cents=cents))[0].notes)==1
    assert len(suppress_contained_harmonics(raw,config=HarmonicSuppressionConfig(harmonic_tolerance_cents=cents-.001))[0].notes)==2


def test_false_subharmonic_limitation_is_explicit():
    # The filter cannot tell a false low subharmonic from a true fundamental.
    # If MIDI40 is the true sound, this deliberately documents lost recall risk.
    raw=events((0,1,28),(.01,.8,40))
    assert [n.midi for n in suppress_contained_harmonics(raw)[0].notes]==[28]
