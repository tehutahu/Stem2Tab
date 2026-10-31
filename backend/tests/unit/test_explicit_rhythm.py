"""Synthetic explicit rhythm checks; no musical truth or model outputs are fixtures."""
from collections import Counter
from fractions import Fraction
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from music21 import converter
from pydantic import ValidationError
import pytest

from src.pipelines.bass_output import BassOutputEvent
from src.pipelines.explicit_rhythm import ExplicitRhythmConfig,plan_explicit_rhythm,write_explicit_rhythm


def event(i,start,end,pitch=28):
    return BassOutputEvent(source_index=i,start=start,end=end,midi=pitch,velocity=95)


def config(**changes):
    return ExplicitRhythmConfig(**{
        **dict(tempo_bpm=120,numerator=4,denominator=4,origin_seconds=0,
        subdivisions_per_quarter=4,max_boundary_shift_ms=50),**changes})


def independent_events(path: Path):
    # A separate library interprets score durations, voices, rests and tied notes.
    score=converter.parse(str(path)).stripTies(inPlace=False)
    return Counter((Fraction(n.offset),Fraction(n.offset+n.duration.quarterLength),int(n.pitch.midi))
        for n in score.flatten().notes if n.isNote)


def test_explicit_example_records_signed_deltas_and_retains_source(tmp_path):
    source=[event(7,.130,.480)]
    before=[n.model_dump() for n in source]
    report=plan_explicit_rhythm(source,config=config())
    assert report.status=="planned" and report.grid_step_seconds==.125
    row=report.events[0]
    assert (row.proposed_start_seconds,row.proposed_end_seconds)==(.125,.5)
    assert (row.start_change_ms,row.end_change_ms)==(-5,20)
    assert report.changed_event_count==1 and report.musical_tempo_meter_origin_verified is False
    result=write_explicit_rhythm(report,tmp_path/"score")
    assert result["logical_note_count"]==1 and result["event_readback_exact"]
    assert independent_events(Path(result["xml"]))==Counter({(Fraction(1,4),Fraction(1),28):1})
    assert [n.model_dump() for n in source]==before


def test_short_note_collapse_is_blocked_without_minimum_length_repair(tmp_path):
    report=plan_explicit_rhythm([event(2,.130,.140)],config=config())
    assert report.status=="blocked" and "short_note_collapsed" in report.reasons
    assert report.events[0].start==.130 and report.events[0].end==.140
    output=tmp_path/"blocked"
    result=write_explicit_rhythm(report,output)
    assert result["xml"] is None
    assert [p.name for p in output.iterdir()]==["rhythm-plan.json"]


def test_different_attacks_cannot_become_a_chord(tmp_path):
    report=plan_explicit_rhythm([event(0,.13,.45,28),event(1,.16,.52,33)],config=config())
    assert report.status=="blocked" and "distinct_attacks_merged" in report.reasons
    result=write_explicit_rhythm(report,tmp_path/"attacks")
    assert result["xml"] is None and len(report.events)==2


def test_existing_simultaneous_notes_get_separate_voices_without_pitch_change(tmp_path):
    report=plan_explicit_rhythm([event(0,0,.25,28),event(1,0,.5,33)],config=config())
    result=write_explicit_rhythm(report,tmp_path/"polyphony")
    assert result["voices"]==2
    # music21 may represent coincident notes in distinct Voices; flatten preserves both.
    assert independent_events(Path(result["xml"]))==Counter({(Fraction(0),Fraction(1,2),28):1,(Fraction(0),Fraction(1),33):1})


def test_overlap_may_not_be_silently_removed():
    report=plan_explicit_rhythm([event(0,0,.26,28),event(1,.25,.5,33)],config=config())
    assert report.status=="blocked" and "overlap_topology_changed" in report.reasons


def test_repeated_note_boundary_retained_not_tied_to_previous_attack(tmp_path):
    report=plan_explicit_rhythm([event(0,0,.25),event(1,.25,.5)],config=config())
    result=write_explicit_rhythm(report,tmp_path/"repeated")
    assert independent_events(Path(result["xml"]))==Counter({(Fraction(0),Fraction(1,2),28):1,(Fraction(1,2),Fraction(1),28):1})
    root=ET.parse(result["xml"]).getroot()
    assert not root.findall('.//tie')


@pytest.mark.parametrize("numerator,denominator",[(4,4),(3,4),(6,8),(3,8)])
def test_explicit_meter_bar_boundary_ties_rests_and_readable_types(tmp_path,numerator,denominator):
    c=config(numerator=numerator,denominator=denominator)
    bar_seconds=numerator*4/denominator*.5
    source=[event(0,bar_seconds-.125,bar_seconds+.25),event(1,bar_seconds+.5,bar_seconds+.625,33)]
    report=plan_explicit_rhythm(source,config=c)
    result=write_explicit_rhythm(report,tmp_path/f"meter-{numerator}-{denominator}")
    root=ET.parse(result["xml"]).getroot()
    assert root.findtext('.//time/beats')==str(numerator)
    assert root.findtext('.//time/beat-type')==str(denominator)
    assert root.findall('.//tie[@type="start"]') and root.findall('.//tie[@type="stop"]')
    assert root.findall('.//note/rest')
    assert all(note.find('type') is not None for note in root.findall('.//note'))
    expected=Counter((Fraction(str(e.start))*2,Fraction(str(e.end))*2,e.midi) for e in source)
    assert independent_events(Path(result["xml"]))==expected


def test_origin_is_explicit_and_alignment_offset_reported(tmp_path):
    report=plan_explicit_rhythm([event(0,2.13,2.48)],config=config(origin_seconds=2))
    assert report.events[0].proposed_start_seconds==2.125
    assert report.events[0].start_change_ms==-5
    result=write_explicit_rhythm(report,tmp_path/"origin")
    assert independent_events(Path(result["xml"]))==Counter({(Fraction(1,4),Fraction(1),28):1})
    blocked=plan_explicit_rhythm([event(0,1.9,2.2)],config=config(origin_seconds=2))
    assert "before_explicit_origin" in blocked.reasons


def test_boundary_shift_limit_blocks_whole_derivative():
    report=plan_explicit_rhythm([event(0,.13,.48)],config=config(max_boundary_shift_ms=10))
    assert report.status=="blocked" and "boundary_shift_limit" in report.reasons
    assert report.max_absolute_end_change_ms==20


@pytest.mark.parametrize("values",[{},dict(tempo_bpm=120),dict(tempo_bpm=0),dict(denominator=16),
    dict(numerator=0),dict(subdivisions_per_quarter=3),dict(origin_seconds=-1),dict(max_boundary_shift_ms=-1),
    dict(tempo_bpm=float('nan')),dict(origin_seconds=True),dict(subdivisions_per_quarter=True)])
def test_invalid_implicit_or_unsupported_settings_rejected(values):
    with pytest.raises(ValidationError):
        if values=={} or values==dict(tempo_bpm=120): ExplicitRhythmConfig(**values)
        else: config(**values)


def test_format_limits_do_not_drop_notes(tmp_path):
    for name,events,reason in [
        ("pitch",[event(0,0,.125,11)],"unsupported_musicxml_pitch"),
        ("voices",[event(i,0,.5,28+i) for i in range(8)],"notation_voice_limit"),
        ("bars",[event(0,0,300)],"score_bar_limit")]:
        report=plan_explicit_rhythm(events,config=config())
        assert reason in report.reasons and len(report.events)==len(events)
        assert write_explicit_rhythm(report,tmp_path/name)["xml"] is None


def test_no_overwrite_and_forged_plan_rejected(tmp_path):
    report=plan_explicit_rhythm([event(0,0,.5)],config=config())
    output=tmp_path/"once"
    write_explicit_rhythm(report,output)
    before={p.name:p.read_bytes() for p in output.iterdir()}
    with pytest.raises(FileExistsError): write_explicit_rhythm(report,output)
    assert {p.name:p.read_bytes() for p in output.iterdir()}==before
    forged=report.model_copy(update={"changed_event_count":999})
    with pytest.raises(ValueError,match="does not match"): write_explicit_rhythm(forged,tmp_path/"forged")
    assert not (tmp_path/"forged").exists()
