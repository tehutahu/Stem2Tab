"""Counterexamples and safety conditions for an offline, unadopted hypothesis."""
from pathlib import Path
import importlib.util
import numpy as np
import pytest
from src.evaluation.note_assembly import assemble_notes

spec=importlib.util.spec_from_file_location('periodicity',Path(__file__).parents[2]/'scripts/pyin_periodicity_diagnostics.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

def fixture():
    time=np.arange(0,1.4,.01);hz=55.
    audio=np.sin(2*np.pi*hz*np.arange(round(1.4*module.SR))/module.SR)
    frame=dict(time=time,f0=np.full(len(time),hz),voiced_flag=np.ones(len(time),bool),active=np.ones(len(time),bool),onset_times=np.array([.7]))
    return audio,frame

def test_periodic_sustain_vetoes_split():
    audio,frame=fixture();onsets,decisions=module.continuity_onsets(audio,frame)
    assert len(onsets)==0 and decisions[0]['veto']
    assert min(decisions[0]['correlations'])>.999

def test_known_failure_small_same_phase_amplitude_rise_is_vetoed():
    audio,frame=fixture();audio[:round(.7*module.SR)]*=.8;audio[round(.7*module.SR):]*=.9
    onsets,decisions=module.continuity_onsets(audio,frame)
    assert len(onsets)==0 and decisions[0]['veto']
    old=assemble_notes(frame['f0'],frame['voiced_flag'],frame['time'],1.4,active=frame['active'],onset_times=frame['onset_times'])
    new=assemble_notes(frame['f0'],frame['voiced_flag'],frame['time'],1.4,active=frame['active'],onset_times=onsets)
    assert len(old.notes)==2 and len(new.notes)==1

@pytest.mark.parametrize('field',['voiced_flag','active'])
def test_gap_preserves_uncertain_onset(field):
    audio,frame=fixture();frame[field][70]=False
    kept,decisions=module.continuity_onsets(audio,frame)
    assert kept.tolist()==[.7] and not decisions[0]['veto']

def test_pitch_transition_and_phase_reset_preserve_onset():
    audio,frame=fixture();frame['f0'][70:]=61.7354
    kept,_=module.continuity_onsets(audio,frame);assert kept.tolist()==[.7]
    audio,frame=fixture();audio[round(.7*module.SR):]*=-1
    kept,_=module.continuity_onsets(audio,frame);assert kept.tolist()==[.7]

def test_zero_energy_or_edge_cannot_veto():
    audio,frame=fixture();audio[:]=0
    kept,_=module.continuity_onsets(audio,frame);assert kept.tolist()==[.7]
    assert np.isnan(module.periodic_correlation(audio,0,1/55))

def test_input_shape_validation():
    audio,frame=fixture()
    with pytest.raises(ValueError):module.continuity_onsets(audio[:,None],frame)

def test_known_failure_veto_changes_rounding_boundary_jitter_protection():
    time=np.arange(0,1.4,.01);midi=np.full(len(time),33.49);midi[70]=33.51
    f0=440*2**((midi-69)/12)
    audio=np.sin(2*np.pi*f0[0]*np.arange(round(1.4*module.SR))/module.SR)
    frame=dict(time=time,f0=f0,voiced_flag=np.ones(len(time),bool),active=np.ones(len(time),bool),onset_times=np.array([float(time[70])]))
    kept,decisions=module.continuity_onsets(audio,frame)
    assert not len(kept) and decisions[0]['veto']
    baseline=assemble_notes(f0,frame['voiced_flag'],time,1.4,active=frame['active'],onset_times=frame['onset_times'])
    candidate=assemble_notes(f0,frame['voiced_flag'],time,1.4,active=frame['active'],onset_times=kept)
    assert len(baseline.notes)==2 and len(candidate.notes)==1
    assert baseline.notes[1].start-baseline.notes[0].end==pytest.approx(.01)
    assert candidate.notes[0].start==0 and candidate.notes[0].end==1.4
