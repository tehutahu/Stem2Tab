"""Exact rational readback of synthetic notation-clock derivatives, no models."""
from fractions import Fraction
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from src.evaluation.notation_fidelity import write_fidelity_musicxml
from tests.unit.test_evaluation_bass_notation_trial import source
from tests.unit.test_evaluation_midi_clock import clock_source


def read_clock(data: bytes) -> dict:
    """Reassemble tied pitches from XML durations, without pretty_midi/AlphaTab."""
    root = ET.fromstring(data)
    divisions = int(root.findtext(".//divisions"))
    tempo = Fraction(root.find(".//sound").get("tempo"))
    unit = Fraction(60, divisions) / tempo
    rows, opened, rests = [], {}, []
    measures = root.findall("./part/measure")
    for index, measure in enumerate(measures):
        cursor = index * divisions * 4
        for child in measure:
            if child.tag == "backup":
                cursor -= int(child.findtext("duration"))
            if child.tag != "note":
                continue
            duration = int(child.findtext("duration"))
            voice = int(child.findtext("voice"))
            start, end = cursor * unit, (cursor + duration) * unit
            if child.find("rest") is not None:
                rests.append((voice, start, end))
            else:
                pitch = child.find("pitch")
                midi = 12 * (int(pitch.findtext("octave")) + 1) + dict(C=0,D=2,E=4,F=5,G=7,A=9,B=11)[pitch.findtext("step")] + int(pitch.findtext("alter", "0"))
                key = (voice, midi)
                ties = {t.get("type") for t in child.findall("tie")}
                if "stop" in ties:
                    previous = opened.pop(key)
                    assert previous[1] == start
                    start = previous[0]
                if "start" in ties:
                    opened[key] = (start, end)
                else:
                    rows.append((midi, start, end))
            cursor += duration
        assert cursor == (index + 1) * divisions * 4
    assert not opened
    return dict(events=sorted(rows), tempo=tempo, bars=len(measures),
                seconds=len(measures)*divisions*4*unit, rests=rests,
                ties=len(root.findall('.//tie[@type="stop"]')))


@pytest.mark.parametrize("events", [
    [(0.13, 0.48, 28), (0.4801, 0.4802, 28), (1.9, 4.13, 34), (4.13, 4.9, 34)],
    [(0, 0.01, 28), (0, 5.1, 33), (4.2, 9.93, 40)],
    [],
])
def test_double_notation_keeps_exact_seconds_pitch_repeated_short_notes_and_tail(tmp_path, events):
    midi = tmp_path / "source.mid"
    original = source(midi, events)
    normal, double = tmp_path / "normal.xml", tmp_path / "double.xml"
    first = write_fidelity_musicxml(midi, normal)
    second = write_fidelity_musicxml(midi, double, notation_scale=2)
    a, b = read_clock(normal.read_bytes()), read_clock(double.read_bytes())
    assert a["events"] == b["events"] == sorted((p, Fraction(str(s)), Fraction(str(e))) for s,e,p in events)
    assert b["tempo"] == a["tempo"]*2 == 120
    assert b["bars"] == a["bars"]*2 and b["seconds"] == a["seconds"]
    assert second.original_tempo_bpm == first.tempo_bpm == 60
    assert midi.read_bytes() == original
    assert second.note_count == len(events) and second.notation_scale == 2


@pytest.mark.parametrize("scale,timing", [(0,"ticks"),(3,"ticks"),(True,"ticks"),(2,"grid48")])
def test_rejects_unsupported_scaling_and_combined_quantization(tmp_path,scale,timing):
    path = tmp_path / "source.mid"; source(path,[(.13,.48,28)])
    out = tmp_path / "out.xml"
    with pytest.raises(ValueError):
        write_fidelity_musicxml(path,out,timing=timing,notation_scale=scale)
    assert not out.exists()


@pytest.mark.parametrize("kind", ["tempo","meter"])
def test_rebarring_does_not_silently_flatten_clock_changes(tmp_path,kind):
    path=tmp_path/"source.mid";path.write_bytes(clock_source(kind,0))
    with pytest.raises(ValueError):
        write_fidelity_musicxml(path,tmp_path/"out.xml",notation_scale=2)
    assert not (tmp_path/"out.xml").exists()


def test_rebarring_will_not_overwrite_existing_candidate(tmp_path):
    midi=tmp_path/"source.mid";source(midi,[(.13,.48,28)])
    out=tmp_path/"out.xml";out.write_bytes(b"keep")
    with pytest.raises(FileExistsError): write_fidelity_musicxml(midi,out,notation_scale=2)
    assert out.read_bytes()==b"keep"
