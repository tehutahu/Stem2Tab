"""Synthetic musical-clock fixtures and independent exact rational XML readback."""
from fractions import Fraction
import xml.etree.ElementTree as ET

import pytest
from pydantic import ValidationError

from src.evaluation.musical_clock import MusicalClock, write_musical_clock
from src.evaluation.notation_fidelity import write_fidelity_musicxml
from tests.unit.test_evaluation_bass_notation_trial import source
from tests.unit.test_evaluation_notation_clock import read_clock


def read_musical_xml(data: bytes) -> dict:
    """Read actual measures/voices/ties, without using the exporter or MIDI loader."""
    root = ET.fromstring(data)
    divisions = int(root.findtext(".//divisions"))
    bpm = Fraction(root.find(".//sound").get("tempo"))
    unit = Fraction(60, divisions)/bpm
    offset = 0
    events, opened, boundaries, rests = [], {}, [Fraction(0)], []
    for measure in root.findall("./part/measure"):
        cursor = maximum = 0
        for element in measure:
            duration = int(element.findtext("duration", "0"))
            if element.tag == "backup":
                cursor -= duration
            elif element.tag == "note":
                start, end = (offset+cursor)*unit, (offset+cursor+duration)*unit
                voice = element.findtext("voice")
                pitch = element.find("pitch")
                if pitch is None:
                    rests.append((start, end))
                else:
                    midi = 12*(int(pitch.findtext("octave"))+1)+dict(C=0,D=2,E=4,F=5,G=7,A=9,B=11)[pitch.findtext("step")]+int(pitch.findtext("alter", "0"))
                    key = voice, midi
                    ties = {t.get("type") for t in element.findall("tie")}
                    if "stop" in ties:
                        previous = opened.pop(key)
                        assert previous[1] == start
                        start = previous[0]
                    if "start" in ties:
                        opened[key] = start, end
                    else:
                        events.append((midi, start, end))
                cursor += duration
                maximum = max(cursor, maximum)
        offset += maximum
        boundaries.append(offset*unit)
    assert not opened
    return dict(events=sorted(events), horizon=offset*unit, boundaries=boundaries, rests=rests,
        meter=(int(root.findtext(".//time/beats")), int(root.findtext(".//time/beat-type"))), bpm=bpm)


@pytest.mark.parametrize("bpm,numerator,denominator,origin", [(96,3,4,.25),(123.5,7,8,.375),(120,4,4,0),(80,6,8,5.25)])
def test_explicit_known_clock_preserves_events_origin_meter_and_tail(tmp_path,bpm,numerator,denominator,origin):
    midi = tmp_path/"input.mid"
    events = [(0.13,.48,28),(.4801,.4802,28),(.75,5.1,33),(4.2,9.93,40)]
    original = source(midi,events)
    base = tmp_path/"base.xml"; write_fidelity_musicxml(midi,base)
    output = tmp_path/"explicit.xml"
    report = write_musical_clock(midi,output,MusicalClock(bpm=bpm,numerator=numerator,denominator=denominator,origin_seconds=origin,evidence="synthetic known clock"))
    actual = read_musical_xml(output.read_bytes())
    assert actual["events"] == sorted((p,Fraction(str(s)),Fraction(str(e))) for s,e,p in events)
    assert actual["events"] == read_clock(base.read_bytes())["events"]
    assert actual["horizon"] == read_clock(base.read_bytes())["seconds"] == Fraction(report.horizon_seconds)
    assert actual["meter"] == (numerator,denominator) and actual["bpm"] == Fraction(str(bpm))
    period = Fraction(numerator*4,denominator)*60/Fraction(str(bpm))
    phase = Fraction(str(origin)) % period
    assert all((boundary-Fraction(str(origin))) % period == 0 for boundary in actual["boundaries"][1:-1])
    if phase:
        assert actual["boundaries"][1] == phase
    assert midi.read_bytes() == original and report.quantization == "none"
    assert report.clock.provenance == "explicit-user-input" and report.estimation_status == "not-estimated"
    assert report.confidence is None and report.ambiguity_bpm == [bpm/2,bpm*2]


@pytest.mark.parametrize("events", [[],[(.2,4.8,28)],[(.25,.3,28),(.75,.8,28),(1.25,1.3,28),(1.75,1.8,28)],[(.1,.2,28),(.23,.3,29),(.87,.92,28),(2.16,2.2,30),(3.001,3.08,28)]])
def test_no_beats_regular_pulses_and_variable_rhythm_never_claim_estimation(tmp_path,events):
    path=tmp_path/"input.mid";source(path,events)
    report=write_musical_clock(path,tmp_path/"out.xml",MusicalClock(bpm=100,numerator=4,denominator=4,origin_seconds=0))
    assert report.estimation_status=="not-estimated" and report.confidence is None
    assert report.ambiguity_bpm==[50,200]
    assert read_musical_xml((tmp_path/"out.xml").read_bytes())["events"]==sorted((p,Fraction(str(s)),Fraction(str(e))) for s,e,p in events)


@pytest.mark.parametrize("changes", [{"bpm":0},{"bpm":float("nan")},{"bpm":120.0001},{"bpm":120.00125},{"origin_seconds":.0003125},{"origin_seconds":False},{"origin_seconds":-.1},{"origin_seconds":.1234567},{"numerator":True},{"denominator":3}])
def test_rejects_unsupported_explicit_settings(changes):
    with pytest.raises(ValidationError):
        MusicalClock(**(dict(bpm=120,numerator=4,denominator=4,origin_seconds=0)|changes))


def test_precision_is_rejected_without_rounding_and_output_is_never_overwritten(tmp_path):
    path=tmp_path/"input.mid";source(path,[(.13,.48,28)])
    output=tmp_path/"out.xml"
    with pytest.raises(ValueError,match="divisions"):
        write_musical_clock(path,output,MusicalClock(bpm=123.457,numerator=4,denominator=4,origin_seconds=.123457))
    assert not output.exists()
    output.write_bytes(b"retained")
    with pytest.raises(FileExistsError):
        write_musical_clock(path,output,MusicalClock(bpm=120,numerator=4,denominator=4,origin_seconds=0))
    assert output.read_bytes()==b"retained"


@pytest.mark.parametrize("events,bpm,eligible", [([(0,.5,28),(.5,1,33)],120,True),([(.13,.4801,28)],120,False),([(0,.5,28)],123.5,False)])
def test_renderer_precision_gate_is_explicit(tmp_path,events,bpm,eligible):
    path=tmp_path/"input.mid";source(path,events)
    report=write_musical_clock(path,tmp_path/"out.xml",MusicalClock(bpm=bpm,numerator=4,denominator=4,origin_seconds=0))
    assert report.preview_eligible is eligible
    assert (report.preview_reason is None) is eligible


@pytest.mark.parametrize("name,numerator,denominator,origin", [("regular",4,4,0),("pickup",3,4,.25),("compound",6,8,.25)])
def test_frontend_real_renderer_fixtures_are_actual_exporter_output(tmp_path,name,numerator,denominator,origin):
    from pathlib import Path
    path=tmp_path/"input.mid";source(path,[(.125,.5,28),(.5,1.25,33),(2,4.25,38),(.25,2.75,40)])
    output=tmp_path/"out.xml"
    write_musical_clock(path,output,MusicalClock(bpm=120,numerator=numerator,denominator=denominator,origin_seconds=origin))
    fixture=Path(__file__).resolve().parents[3]/"frontend/src/test/fixtures/musical-clock"/f"{name}.musicxml"
    assert output.read_bytes()==fixture.read_bytes()


def test_last_effective_zero_tick_storage_tempo_is_used(tmp_path):
    import mido
    path=tmp_path/"input.mid";source(path,[(.25,.5,28),(1,3,33)])
    midi=mido.MidiFile(path)
    midi.tracks[0].insert(1,mido.MetaMessage("set_tempo",tempo=500000,time=0))
    midi.save(path)
    base=tmp_path/"base.xml";write_fidelity_musicxml(path,base)
    output=tmp_path/"out.xml"
    report=write_musical_clock(path,output,MusicalClock(bpm=96,numerator=7,denominator=8,origin_seconds=.25))
    actual=read_musical_xml(output.read_bytes())
    assert actual["events"]==read_clock(base.read_bytes())["events"]
    assert actual["horizon"]==read_clock(base.read_bytes())["seconds"]
    assert report.storage_bpm==120
