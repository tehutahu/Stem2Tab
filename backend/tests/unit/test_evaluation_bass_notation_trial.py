"""Opt-in orchestration uses generated MIDI, never inference or private fixtures."""
import hashlib
import json
from pathlib import Path

import guitarpro
import mido
import pretty_midi
import pytest

from src.evaluation.bass_notation_trial import main,run_bass_notation_trial
from src.pipelines.bass_output import BassOutputConfig
from src.pipelines.explicit_rhythm import ExplicitRhythmConfig


def source(path: Path,events):
    midi=mido.MidiFile(type=1,ticks_per_beat=10000)
    track=mido.MidiTrack([mido.MetaMessage('set_tempo',tempo=1000000,time=0),mido.Message('program_change',program=33,time=0)])
    rows=[]
    for a,b,pitch in events:
        rows.extend([(round(a*10000),1,mido.Message('note_on',note=pitch,velocity=95)),
                     (round(b*10000),0,mido.Message('note_off',note=pitch,velocity=0))])
    prior=0
    for tick,_,message in sorted(rows,key=lambda v:v[:2]):
        message.time=tick-prior;prior=tick;track.append(message)
    midi.tracks.append(track);midi.save(path)
    return path.read_bytes()


def rhythm():
    return ExplicitRhythmConfig(tempo_bpm=120,numerator=4,denominator=4,origin_seconds=0,
        subdivisions_per_quarter=4,max_boundary_shift_ms=50)


def test_opt_in_derivatives_preserve_original_and_clock_xml(tmp_path,monkeypatch):
    def forbidden(*a,**k): raise AssertionError('No tempo estimation')
    monkeypatch.setattr(pretty_midi.PrettyMIDI,'estimate_tempo',forbidden)
    path=tmp_path/'synthetic.mid'
    original=source(path,[(.13,.48,55),(.63,.98,46),(1.13,1.48,55)])
    report=run_bass_notation_trial(path,tmp_path/'trial',config=BassOutputConfig(profile='bass4'),source_kind='local-midi',
        fingering_mode='constrained',max_position_shift=2,max_hand_span=4,rhythm=rhythm(),gp5_preview=True)
    assert report['status']=='completed'
    assert report['model_calls']==report['separator_calls']==0
    assert report['source_unchanged']
    assert path.read_bytes()==(tmp_path/'trial/preserved/performance.mid').read_bytes()==original
    assert (tmp_path/'trial/preserved/performance-clock.musicxml').is_file()
    assert report['fingering']['status']=='planned'
    assert report['fingering']['movement_comparison']['reduction']>0
    assert report['gp5']['timing_preserved'] is False
    assert report['gp5']['readback']['quarter_note_only'] is True
    assert [e['midi'] for e in report['gp5']['readback']['notes']]==[55,46,55]
    assert report['rhythm']['status']=='written'
    assert [e['start_change_ms'] for e in report['rhythm']['plan']['events']]==pytest.approx([-5,-5,-5],abs=1e-9)
    assert json.loads((tmp_path/'trial/notation-trial.json').read_text())==json.loads(json.dumps(report))


def test_short_rhythm_failure_retains_every_original_event_and_raw_artifact(tmp_path):
    path=tmp_path/'short.mid'
    original=source(path,[(.13,.14,28),(.25,.5,33)])
    report=run_bass_notation_trial(path,tmp_path/'trial',config=BassOutputConfig(profile='bass4'),source_kind='local-midi',rhythm=rhythm())
    assert report['status']=='blocked_optional_derivative'
    assert report['input_note_count']==2 and report['preserved']['input_note_count']==2
    assert (tmp_path/'trial/preserved/performance.mid').read_bytes()==original
    assert report['rhythm']['xml'] is None
    assert 'short_note_collapsed' in report['rhythm']['plan']['reasons']
    assert not (tmp_path/'trial/rhythm/score-explicit-quantized.musicxml').exists()


def test_unassignable_chord_never_emits_partial_gp5(tmp_path):
    path=tmp_path/'conflict.mid'
    original=source(path,[(0,.5,28),(0,.5,29)])
    report=run_bass_notation_trial(path,tmp_path/'trial',config=BassOutputConfig(profile='bass4'),source_kind='local-midi',
        fingering_mode='constrained',max_position_shift=5,max_hand_span=4,gp5_preview=True)
    assert report['status']=='blocked_optional_derivative' and report['fingering']['status']=='unplayable'
    assert report['gp5']['status']=='blocked'
    assert not (tmp_path/'trial/gp5-preview').exists()
    assert (tmp_path/'trial/preserved/performance.mid').read_bytes()==original
    assert (tmp_path/'trial/preserved/performance-clock.musicxml').is_file()


def test_modes_off_keep_current_legacy_output_policy(tmp_path):
    path=tmp_path/'legacy.mid'
    source(path,[(0,.5,55),(.5,1,46)])
    report=run_bass_notation_trial(path,tmp_path/'trial',config=BassOutputConfig(profile='bass4'),source_kind='local-midi',gp5_preview=True)
    assert report['fingering']['status']==report['rhythm']['status']=='not_requested'
    assert [(e['string'],e['fret']) for e in report['gp5']['events']]==[(1,12),(1,3)]
    assert report['gp5']['readback']['quarter_note_only'] is True


@pytest.mark.parametrize('extra',[['--rhythm-mode','explicit-grid'],['--tempo-bpm','120'],
    ['--fingering-mode','constrained'],['--max-position-shift','5']])
def test_cli_requires_explicit_mode_and_all_related_settings(tmp_path,extra):
    path=tmp_path/'source.mid';source(path,[(0,.5,28)])
    output=tmp_path/'out'
    assert main(['--midi',str(path),'--output-dir',str(output),'--source-kind','local-midi','--bass-profile','bass4',*extra])==1
    assert not output.exists()


def test_cli_blocked_exit_and_existing_output_untouched(tmp_path):
    path=tmp_path/'source.mid';source(path,[(.13,.14,28)])
    out=tmp_path/'out'
    args=['--midi',str(path),'--output-dir',str(out),'--source-kind','local-midi','--bass-profile','bass4',
        '--rhythm-mode','explicit-grid','--tempo-bpm','120','--meter','4/4','--origin-seconds','0',
        '--subdivisions-per-quarter','4','--max-boundary-shift-ms','50']
    assert main(args)==2
    before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in out.rglob('*') if p.is_file()}
    assert main(args)==1
    assert before=={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in out.rglob('*') if p.is_file()}
