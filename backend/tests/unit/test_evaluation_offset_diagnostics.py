"""Counterexamples explain why the RMS-rise onset gate is not adopted."""
import numpy as np
from scripts.pyin_offset_diagnostics import rms_rise_onsets
from src.evaluation.note_assembly import assemble_notes


def assembly(energy,voiced=None,pitches=None,onsets=(.15,)):
    t=np.arange(30)*.01
    # Construct exact MIDI F0; keep energy independent of voicing and pitch.
    f0=440*2**(((np.full(30,33) if pitches is None else np.asarray(pitches))-69)/12)
    v=np.ones(30,dtype=bool) if voiced is None else voiced
    return assemble_notes(f0,v,t,.30,active=np.asarray(energy)>1e-4,onset_times=onsets)


def test_rejected_gate_removes_decay_split_but_also_true_quieter_repick():
    t=np.arange(30)*.01
    energy=np.r_[np.full(15,.7),np.full(15,.07)]
    kept=rms_rise_onsets(t,energy,np.array([.15]))
    assert kept.size==0
    # Same observed F0 and RMS can be one decaying note or a softer same-pitch
    # re-pick. Dropping the onset merges the latter and loses a reference onset.
    assert len(assembly(energy).notes)==2
    assert len(assembly(energy,onsets=kept).notes)==1


def test_gate_keeps_rising_repick_and_never_bridges_unvoiced_rest():
    t=np.arange(30)*.01;energy=np.r_[np.full(15,.07),np.full(15,.7)]
    kept=rms_rise_onsets(t,energy,np.array([.15]))
    assert kept.tolist()==[.15]
    voiced=np.ones(30,dtype=bool);voiced[14:17]=False
    notes=assembly(energy,voiced=voiced,onsets=kept).notes
    assert [(n.start,n.end) for n in notes]==[(0,.14),(.17,.3)]


def test_weak_voiced_loss_cannot_be_recovered_from_amplitude_alone():
    energy=np.full(30,.01);voiced=np.ones(30,dtype=bool);voiced[20:]=False
    notes=assembly(energy,voiced=voiced,onsets=[]).notes
    assert len(notes)==1 and notes[0].end==.2
    assert all(n.end<=.2 for n in notes)


def test_activity_floor_can_clip_a_tail_even_when_f0_remains_voiced():
    energy=np.r_[np.full(20,.01),np.full(10,8e-5)]
    notes=assembly(energy,onsets=[]).notes
    assert len(notes)==1 and notes[0].end==.2
    # This differs from voiced loss: disabling the activity mask retains the
    # F0 tail, but that is only an ablation and can include real silent gaps.
    assert assembly(np.full(30,.01),onsets=[]).notes[0].end==.3


def test_legato_pitch_transition_is_kept_even_without_independent_onset():
    notes=assembly(np.full(30,.2),pitches=[28]*15+[33]*15,onsets=[]).notes
    assert [(n.midi,n.start,n.end) for n in notes]==[(28,0,.15),(33,.15,.3)]


def test_gate_requires_aligned_frame_arrays():
    import pytest
    with pytest.raises(ValueError):rms_rise_onsets(np.array([0,.01]),np.array([.1]),np.array([.01]))
