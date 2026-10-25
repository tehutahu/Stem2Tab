"""Small activation fixtures only: no model inference or copyrighted media."""
import hashlib
import numpy as np
import pytest
from pydantic import ValidationError
from src.evaluation.bp_onset_conflicts import OnsetConflictConfig,decode_cached_activations,suppress_short_gap_peaks,main


def cache(peaks=((50,19,.9),), spans=((50,90,19,.8),)):
    note=np.zeros((140,88),dtype=np.float32);onset=np.zeros_like(note)
    for a,b,p,v in spans:note[a:b,p]=v
    for t,p,v in peaks:onset[t,p]=v
    return dict(note=note,onset=onset,contour=np.zeros((140,264),dtype=np.float32))


def decode(data,method='dominant_gap_nms'):
    return decode_cached_activations(data,config=OnsetConflictConfig(method=method))


def starts(events,pitch=40):
    from basic_pitch.note_creation import model_frames_to_time
    times=model_frames_to_time(140)
    return sorted(int(np.argmin(abs(times-n.start))) for n in events.notes if n.midi==pitch)


def legacy(data):
    from basic_pitch.note_creation import output_to_notes_polyphonic,model_frames_to_time
    raw=output_to_notes_polyphonic(data['note'].copy(),data['onset'].copy(),onset_thresh=.5,frame_thresh=.3,min_note_len=11,infer_onsets=True,min_freq=None,max_freq=None,melodia_trick=True)
    return sorted((int(a),int(b),int(p)) for a,b,p,v in raw)


def test_false_late_peak_and_stronger_later_true():
    early=cache(peaks=((50,19,.9),(59,19,.6)))
    assert starts(decode(early)[0])==[50]
    later=cache(peaks=((50,19,.6),(59,19,.9)),spans=((59,90,19,.8),))
    assert starts(decode(later)[0])==[59]


def test_weaker_true_later_counterworld_remains_an_explicit_limitation():
    data=cache(peaks=((50,19,.9),(59,19,.6)))
    # Exactly the same observations can mean a genuine weaker second attack.
    # A deterministic peak-only rule cannot certify both latent truths.
    assert starts(decode(data)[0])==[50]
    assert [a for a,b,p in legacy(data) if p==40]==[59]


def test_ambiguous_equal_or_close_peaks_abstain_in_guarded_rule():
    for strength in [.9,.85]:
        data=cache(peaks=((50,19,.9),(59,19,strength)))
        events,decisions=decode(data)
        assert not decisions
        assert starts(events)==[59]


def test_float32_margin_boundary_is_numerically_stable():
    peaks=np.zeros((100,88),dtype=np.float32)
    peaks[50,19]=.9;peaks[59,19]=.8
    selected,decisions=suppress_short_gap_peaks(peaks,minimum_note_frames=11,config=OnsetConflictConfig(method='dominant_gap_nms'))
    assert len(decisions)==1 and selected[50,19]>0 and selected[59,19]==0


def test_spaced_reattacks_supported_short_notes_and_rests():
    for second in [64,70]:
        data=cache(peaks=((50,19,.9),(second,19,.8)))
        assert starts(decode(data)[0])==[50,second]
    data=cache(peaks=((20,19,.9),(80,19,.8)),spans=((20,40,19,.8),(80,100,19,.8)))
    assert starts(decode(data)[0])==[20,80]
    assert not decode(cache(peaks=(),spans=()))[0].notes


def test_unsupported_tiny_note_is_not_extended_to_manufacture_recall():
    data=cache(spans=((50,55,19,.8),))
    assert not decode(data)[0].notes and not legacy(data)


def test_no_conflict_overlaps_legato_and_decay_replay_legacy():
    cases=[cache(peaks=((50,19,.9),(60,20,.8)),spans=((50,90,19,.8),(60,100,20,.8))),cache(peaks=((30,19,.9),(65,20,.8)),spans=((30,65,19,.8),(65,100,20,.8)))]
    decay=cache();decay['note'][50:90,19]=np.linspace(.8,.2,40);cases.append(decay)
    from basic_pitch.note_creation import model_frames_to_time
    times=model_frames_to_time(140)
    for data in cases:
        expected=legacy(data)
        events,decisions=decode(data);assert not decisions
        observed=sorted((int(np.argmin(abs(times-n.start))),int(np.argmin(abs(times-n.end))),n.midi) for n in events.notes)
        assert observed==expected


def test_inputs_are_not_mutated_and_configuration_is_explicit():
    data=cache(peaks=((50,19,.9),(59,19,.6)));before={k:v.tobytes() for k,v in data.items()};decode(data)
    assert all(v.tobytes()==before[k] for k,v in data.items())
    with pytest.raises(ValidationError):OnsetConflictConfig()
    with pytest.raises(ValidationError):OnsetConflictConfig(method='fit_on_references')
    with pytest.raises(ValueError):decode({'note':data['note']})
    data['note'][0,0]=np.nan
    with pytest.raises(ValueError):decode(data)


def test_cli_preserves_exact_cache_and_refuses_reuse(tmp_path,monkeypatch):
    source=tmp_path/'raw.npz';np.savez_compressed(source,**cache());before=hashlib.sha256(source.read_bytes()).hexdigest();out=tmp_path/'new'
    monkeypatch.setattr('sys.argv',['bp_onset_conflicts','--cache',str(source),'--method','dominant_gap_nms','--output-dir',str(out)])
    assert main()==0
    assert hashlib.sha256((out/'raw_activations.npz').read_bytes()).hexdigest()==before
    with pytest.raises(FileExistsError):main()
    assert hashlib.sha256(source.read_bytes()).hexdigest()==before


def test_inferred_tie_preserves_later_onset_without_dominant_margin():
    data=cache(peaks=((50,19,.6),(59,19,.9)))
    # Inferred onset normalization makes the early frame step as strong.
    assert starts(decode(data,'dominant_gap_nms')[0])==[59]



def test_rejects_unpublished_a_configuration():
    with pytest.raises(ValidationError):
        OnsetConflictConfig(method="short_gap_nms")


def test_rejects_unpinned_decoder_version(monkeypatch):
    monkeypatch.setattr("importlib.metadata.version", lambda name: "0.5.0")
    with pytest.raises(ValueError, match="0.4.0"):
        decode(cache())
