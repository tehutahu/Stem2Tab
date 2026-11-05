"""Independent synthetic GP5 readback across rhythm, tuning and failure cases."""
from collections import Counter
from fractions import Fraction

import guitarpro
import pytest
from pydantic import ValidationError

from src.pipelines.bass_output import BassOutputConfig,BassOutputEvent
from src.pipelines.bass_fingering import plan_bass_fingering
from src.pipelines.explicit_rhythm import ExplicitRhythmConfig,plan_explicit_rhythm,write_explicit_rhythm
from src.pipelines.rhythmic_gp5 import write_rhythmic_gp5,read_gp5_events


def plans(rows,config=None,**kwargs):
    instrument=config or BassOutputConfig(profile="bass4",max_fret=24)
    events=tuple(BassOutputEvent(source_index=i,start=a,end=b,midi=p) for i,(a,b,p) in enumerate(rows))
    clock=ExplicitRhythmConfig(**(dict(tempo_bpm=120,numerator=4,denominator=4,origin_seconds=0,
        subdivisions_per_quarter=8,max_boundary_shift_ms=30)|kwargs))
    return plan_explicit_rhythm(events,config=clock),plan_bass_fingering(events,config=instrument),instrument


@pytest.mark.parametrize("config,pitches",[
    (BassOutputConfig(profile="bass4",max_fret=24),[28,33,38,43]),
    (BassOutputConfig(profile="bass5",max_fret=24),[23,28,33,38,43]),
    (BassOutputConfig(profile="custom",strings=6,tuning_midi=(23,28,33,38,43,48),max_fret=24),[23,28,33,38,43,48]),
])
def test_open_string_chords_4_5_6(tmp_path,config,pitches):
    rhythm,fingering,instrument=plans([(0,.5,p) for p in pitches],config)
    result=write_rhythmic_gp5(rhythm,fingering,tmp_path/"out",config=instrument)
    song=guitarpro.parse(str(tmp_path/"out/bass.explicit-rhythm.gp5"))
    notes=song.tracks[0].measures[0].voices[0].beats[0].notes
    assert sorted(n.realValue for n in notes)==pitches
    assert len(set(n.string for n in notes))==len(pitches)
    assert all(n.value==0 for n in notes)
    assert result["readback"]["logical_note_count"]==len(pitches)


def test_rests_ties_repeated_short_notes_and_deltas(tmp_path):
    rows=[(.130,.480,28),(.5,.75,33),(.75,1,33),(1.75,2.5,38),(2.75,2.8125,43)]
    rhythm,fingering,config=plans(rows)
    result=write_rhythmic_gp5(rhythm,fingering,tmp_path/"out",config=config)
    actual,back=read_gp5_events(tmp_path/"out/bass.explicit-rhythm.gp5")
    expected=Counter([(240,960,28),(960,1440,33),(1440,1920,33),(3360,4800,38),(5280,5400,43)])
    assert Counter((a,b,p) for a,b,p,s,f,v in actual.elements())==expected
    assert back["tie_segments"]>=1 and back["rest_beats"]>=3 and back["bars"]==2
    assert result["velocity_changed_count"]==5
    assert rhythm.events[0].start_change_ms==-5 and rhythm.events[0].end_change_ms==20
    with pytest.raises(FileExistsError):write_rhythmic_gp5(rhythm,fingering,tmp_path/"out",config=config)


def test_staggered_chord_lengths_and_same_pitch_rearticulation(tmp_path):
    rhythm,fingering,config=plans([(0,1,28),(.25,.5,33),(.5,.75,33)])
    write_rhythmic_gp5(rhythm,fingering,tmp_path/"out",config=config)
    actual,back=read_gp5_events(tmp_path/"out/bass.explicit-rhythm.gp5")
    assert Counter((a,b,p) for a,b,p,s,f,v in actual.elements())==Counter([(0,1920,28),(480,960,33),(960,1440,33)])
    assert back["tie_segments"]==3


@pytest.mark.parametrize("n,d",[(3,4),(7,8),(2,2)])
def test_meter_origin_and_bar_crossing(tmp_path,n,d):
    length=n*4/d*.5
    rhythm,fingering,config=plans([(1+length-.0625,1+length+.125,28)],numerator=n,denominator=d,origin_seconds=1)
    result=write_rhythmic_gp5(rhythm,fingering,tmp_path/"out",config=config)
    assert result["readback"]["meters"]==[(n,d)]*2
    assert result["readback"]["tie_segments"]>=1
    assert result["origin_seconds"]==1


def test_fractional_bpm_refuses_gp5_but_musicxml_survives(tmp_path):
    rhythm,fingering,config=plans([(0,60/120.5,28)],tempo_bpm=120.5)
    assert write_explicit_rhythm(rhythm,tmp_path/"xml")["status"]=="written"
    with pytest.raises(ValueError,match="integer BPM"):
        write_rhythmic_gp5(rhythm,fingering,tmp_path/"out",config=config)
    assert not (tmp_path/"out").exists()


def test_changed_meter_is_explicitly_unsupported():
    with pytest.raises(ValidationError):
        ExplicitRhythmConfig(tempo_bpm=120,numerator=4,denominator=4,origin_seconds=0,
            subdivisions_per_quarter=4,max_boundary_shift_ms=30,meter_changes=[(2,3,4)])


@pytest.mark.parametrize("rows",[[(0,.01,28)],[(0,.5,20)]])
def test_short_collapsed_or_unplayable_never_partial(tmp_path,rows):
    rhythm,fingering,config=plans(rows)
    with pytest.raises(ValueError,match="Complete"):
        write_rhythmic_gp5(rhythm,fingering,tmp_path/"out",config=config)
    assert not (tmp_path/"out").exists()
