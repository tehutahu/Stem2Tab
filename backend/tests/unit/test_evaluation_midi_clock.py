"""Raw clock rejection before normalization, including ignored nonconductor data."""
import io
from pathlib import Path
from unittest.mock import patch

import mido
import pytest

from src.evaluation.midi_clock import UnsupportedMidiClockError,validate_fixed_storage_clock
from src.evaluation.saved_midi_trial import _load_unambiguous_midi,run_saved_midi_trial
from src.evaluation.bass_notation_trial import run_bass_notation_trial
from src.evaluation.notation_fidelity import write_fidelity_musicxml
from src.pipelines.bass_output import BassOutputConfig


def clock_source(kind: str, track_index: int=0) -> bytes:
    raw=mido.MidiFile(type=1,ticks_per_beat=480)
    conductor=mido.MidiTrack([mido.MetaMessage("set_tempo",tempo=500000),mido.MetaMessage("time_signature",numerator=4,denominator=4)])
    notes=mido.MidiTrack([mido.Message("note_on",note=28,velocity=80),mido.Message("note_off",note=28,time=480)])
    raw.tracks=[conductor,notes]
    message=(mido.MetaMessage("set_tempo",tempo=600000,time=240) if kind=="tempo"
        else mido.MetaMessage("time_signature",numerator=3,denominator=4,time=240))
    if track_index==0: conductor.append(message)
    else: notes.insert(1,message);notes[-1].time=240
    stream=io.BytesIO();raw.save(file=stream);return stream.getvalue()


@pytest.mark.parametrize("kind",["tempo","meter"])
@pytest.mark.parametrize("track_index",[0,1])
def test_raw_clock_refused_before_normalizing_library(kind,track_index):
    data=clock_source(kind,track_index)
    with patch("pretty_midi.PrettyMIDI",side_effect=AssertionError("Do not normalize first")):
        with pytest.raises(UnsupportedMidiClockError):_load_unambiguous_midi(data)


@pytest.mark.parametrize("route",["saved","notation","fidelity"])
@pytest.mark.parametrize("kind",["tempo","meter"])
def test_all_local_export_routes_preserve_source_and_write_no_derivative(tmp_path:Path,route,kind):
    data=clock_source(kind,1);source=tmp_path/"source.mid";source.write_bytes(data)
    output=tmp_path/("result.xml" if route=="fidelity" else "result")
    config=BassOutputConfig(profile="bass4")
    with pytest.raises(UnsupportedMidiClockError):
        if route=="saved":run_saved_midi_trial(source,output,config=config,source_kind="local-midi",time_basis="storage-clock")
        elif route=="notation":run_bass_notation_trial(source,output,config=config,source_kind="local-midi")
        else:write_fidelity_musicxml(source,output)
    assert not output.exists() and source.read_bytes()==data


def test_redundant_conductor_declarations_are_not_false_changes():
    raw=mido.MidiFile(ticks_per_beat=480)
    raw.tracks=[mido.MidiTrack([mido.MetaMessage("set_tempo",tempo=600000),
        mido.MetaMessage("set_tempo",tempo=600000,time=240),
        mido.MetaMessage("time_signature",numerator=4,denominator=4,time=240)])]
    validate_fixed_storage_clock(raw)
    raw.tracks[0].append(mido.MetaMessage("set_tempo",tempo=500000,time=1))
    with pytest.raises(UnsupportedMidiClockError,match="Tempo changes"):validate_fixed_storage_clock(raw)


def test_tempo_change_after_default_or_after_notes_is_not_silently_removed():
    raw=mido.MidiFile(ticks_per_beat=480)
    raw.tracks=[mido.MidiTrack([mido.MetaMessage("set_tempo",tempo=600000,time=480)])]
    with pytest.raises(UnsupportedMidiClockError):validate_fixed_storage_clock(raw)


def test_fixed_non4_meter_and_extra_explicit_change_fields_rejected():
    from pydantic import ValidationError
    from src.pipelines.explicit_rhythm import ExplicitRhythmConfig
    raw=mido.MidiFile();raw.tracks=[mido.MidiTrack([mido.MetaMessage("time_signature",numerator=3,denominator=4)])]
    with pytest.raises(UnsupportedMidiClockError):validate_fixed_storage_clock(raw)
    for key in ("tempo_changes","meter_changes"):
        with pytest.raises(ValidationError):
            ExplicitRhythmConfig(tempo_bpm=120,numerator=4,denominator=4,origin_seconds=0,
                subdivisions_per_quarter=4,max_boundary_shift_ms=30,**{key:[{"time":1}]})
