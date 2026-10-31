"""Synthetic tests for explicit all-or-none sounding-bass GP5 previews."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import guitarpro
import pretty_midi
from pydantic import ValidationError
import pytest

from src.pipelines.bass_output import BassOutputConfig, BassOutputEvent, write_bass_gp5_preview


def _event(index: int, pitch: int, velocity: int = 95) -> BassOutputEvent:
    return BassOutputEvent(source_index=index, start=index*.37, end=index*.37+.12,
                           midi=pitch, velocity=velocity)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _gp_notes(path: str) -> list[guitarpro.models.Note]:
    song = guitarpro.parse(path)
    return [n for t in song.tracks for m in t.measures for v in m.voices for b in v.beats for n in b.notes]


@pytest.mark.parametrize(("profile", "pitches", "tuning"), [
    ("bass4", [28, 33, 38, 43], (28, 33, 38, 43)),
    ("bass5", [23, 28, 33, 38, 43], (23, 28, 33, 38, 43)),
])
def test_named_profiles_preserve_low_sounding_pitches(
    tmp_path: Path, profile: str, pitches: list[int], tuning: tuple[int, ...],
) -> None:
    events = [_event(i, pitch) for i, pitch in enumerate(pitches)]
    report = write_bass_gp5_preview(events, tmp_path/profile, config=BassOutputConfig(profile=profile))

    assert report.status == "written"
    assert [n.realValue for n in _gp_notes(report.gp5_path)] == pitches
    assert report.readback["tuning_midi"] == tuning
    assert report.readback["strings"] == len(tuning)
    assert report.readback["max_fret"] == 24
    assert report.transposition_semitones == 0
    assert report.timing_preserved is False


def test_custom_drop_d_and_explicit_zero_fret_limit(tmp_path: Path) -> None:
    config = BassOutputConfig(profile="custom", strings=4, tuning_midi=(26,33,38,43), max_fret=0)
    report = write_bass_gp5_preview([_event(7,26)], tmp_path/"drop-d", config=config)

    assert report.status == "written"
    assert report.events[0].midi == 26
    assert report.events[0].string == 4
    assert report.events[0].fret == 0
    assert report.readback["max_fret"] == 0
    assert [n.realValue for n in _gp_notes(report.gp5_path)] == [26]


def test_any_unmappable_event_suppresses_entire_gp5_and_reports_every_index(tmp_path: Path) -> None:
    events = [_event(12,27), _event(3,28), _event(9,67), _event(20,68), _event(21,27)]
    report = write_bass_gp5_preview(events, tmp_path/"strict", config=BassOutputConfig(profile="bass4"))

    assert report.status == "unmappable"
    assert report.unmappable_indices == (12,20,21)
    assert report.input_event_count == 5
    assert report.mapped_event_count == 2
    assert [row.source_index for row in report.events] == [12,3,9,20,21]
    assert [row.midi for row in report.events] == [27,28,67,68,27]
    assert all(row.unmappable_reason for row in report.events if row.source_index in (12,20,21))
    assert report.gp5_path is None
    assert report.readback is None
    assert list((tmp_path/"strict").iterdir()) == [Path(report.manifest_path)]
    assert report.partial_preview is False
    assert "12, 20, 21" in report.warnings[-1]


def test_bounds_are_playable_without_transposition(tmp_path: Path) -> None:
    events = [_event(0,28), _event(1,67)]
    report = write_bass_gp5_preview(events, tmp_path/"bounds", config=BassOutputConfig(profile="bass4"))
    assert [n.realValue for n in _gp_notes(report.gp5_path)] == [28,67]
    assert [(e.string,e.fret) for e in report.events] == [(4,0),(1,24)]


def test_fifteen_notes_fill_four_real_bars_preserving_order_and_reattacks(tmp_path: Path) -> None:
    pitches = [28,28,43,33,38,28,43,43,33,38,28,33,43,38,28]
    events = [_event(100-i,pitch,velocity=15+(i%8)*16) for i,pitch in enumerate(pitches)]
    originals = [e.model_dump() for e in events]
    report = write_bass_gp5_preview(events, tmp_path/"four-bars", config=BassOutputConfig(profile="bass4"))

    song = guitarpro.parse(report.gp5_path)
    assert len(song.tracks) == 1
    assert len(song.measureHeaders) == len(song.tracks[0].measures) == 4
    assert report.readback["beats_per_measure"] == [4,4,4,4]
    assert report.readback["padding_rest_count"] == 1
    assert report.readback["note_count"] == 15
    assert report.readback["velocity_changed_count"] == 0
    assert [n.realValue for n in _gp_notes(report.gp5_path)] == pitches
    assert all(n.type == guitarpro.models.NoteType.normal for n in _gp_notes(report.gp5_path))
    assert [row.source_index for row in report.events] == [100-i for i in range(15)]
    assert [e.model_dump() for e in events] == originals
    assert all(beat.duration.time == 960 for measure in song.tracks[0].measures for beat in measure.voices[0].beats)


@pytest.mark.parametrize("pitches", [[], [28]])
def test_empty_and_singleton_use_explicit_preview_clock_without_tempo_estimation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pitches: list[int],
) -> None:
    def forbidden_estimate(self: pretty_midi.PrettyMIDI) -> float:
        raise AssertionError("preview clock must never estimate tempo")
    monkeypatch.setattr(pretty_midi.PrettyMIDI,"estimate_tempo",forbidden_estimate)
    report = write_bass_gp5_preview([_event(i,p) for i,p in enumerate(pitches)], tmp_path/"explicit-clock",
                                   config=BassOutputConfig(profile="bass4",preview_tempo_bpm=137))
    assert report.status == "written"
    assert report.readback["preview_tempo_bpm"] == 137
    assert report.readback["note_count"] == len(pitches)
    assert report.readback["measure_count"] == 1
    assert report.readback["padding_rest_count"] == 4-len(pitches)


def test_native_velocity_and_endpoints_remain_explicit_when_gp5_dynamics_quantize(tmp_path: Path) -> None:
    events = [_event(4,28,1),_event(5,33,100),_event(6,38,127)]
    report = write_bass_gp5_preview(events,tmp_path/"dynamics",config=BassOutputConfig(profile="bass4"))
    assert [e.velocity for e in report.events] == [1,100,127]
    assert [n.velocity for n in _gp_notes(report.gp5_path)] == [15,95,127]
    assert report.readback["velocity_changed_count"] == 2
    assert [(e.start,e.end) for e in report.events] == [(e.start,e.end) for e in events]
    assert "actual serialized dynamics" in report.warnings[1]


def test_source_midi_bytes_and_manifest_events_remain_unchanged(tmp_path: Path) -> None:
    source = tmp_path/"generated.mid"
    midi = pretty_midi.PrettyMIDI()
    instrument = pretty_midi.Instrument(program=32)
    instrument.notes.extend([pretty_midi.Note(95,28,.1,.4),pretty_midi.Note(79,33,.2,.6)])
    midi.instruments.append(instrument)
    midi.write(str(source))
    digest = _sha(source)
    loaded = pretty_midi.PrettyMIDI(str(source))
    events = [BassOutputEvent(source_index=i,start=n.start,end=n.end,midi=n.pitch,velocity=n.velocity)
              for i,n in enumerate(loaded.instruments[0].notes)]
    report = write_bass_gp5_preview(events,tmp_path/"from-midi",config=BassOutputConfig(profile="bass4"))
    assert _sha(source) == digest
    manifest = json.loads(Path(report.manifest_path).read_text())
    assert manifest == report.model_dump(mode="json")
    assert [(row["start"],row["end"],row["midi"],row["velocity"]) for row in manifest["events"]] == [
        (e.start,e.end,e.midi,e.velocity) for e in events]


def test_no_overwrite_including_manifest_only_failure_output(tmp_path: Path) -> None:
    config = BassOutputConfig(profile="bass4")
    for name,pitch in [("written",28),("unmappable",27)]:
        folder = tmp_path/name
        write_bass_gp5_preview([_event(0,pitch)],folder,config=config)
        hashes = {p.name:_sha(p) for p in folder.iterdir()}
        with pytest.raises(FileExistsError):
            write_bass_gp5_preview([_event(1,33)],folder,config=config)
        assert {p.name:_sha(p) for p in folder.iterdir()} == hashes


@pytest.mark.parametrize("values", [
    {}, {"profile":"bass4","strings":5}, {"profile":"bass5","tuning_midi":[28,33,38,43]},
    {"profile":"custom"}, {"profile":"custom","strings":4,"tuning_midi":[26,33,38,43]},
    {"profile":"custom","strings":4,"tuning_midi":[26,33,38],"max_fret":24},
    {"profile":"custom","strings":4,"tuning_midi":[26,38,33,43],"max_fret":24},
    {"profile":"custom","strings":4,"tuning_midi":[26,33,33,43],"max_fret":24},
    {"profile":"custom","strings":1,"tuning_midi":[128],"max_fret":24},
    {"profile":"custom","strings":8,"tuning_midi":[1,2,3,4,5,6,7,8],"max_fret":24},
    {"profile":"bass4","max_fret":37}, {"profile":"bass4","max_fret":-1},
    {"profile":"bass4","preview_tempo_bpm":0}, {"profile":"bass4","preview_tempo_bpm":1001},
    {"profile":"bass4","preview_tempo_bpm":120.5}, {"profile":"bass4","strings":True},
])
def test_invalid_or_implicit_configuration_is_rejected(values: dict) -> None:
    with pytest.raises(ValidationError):
        BassOutputConfig(**values)


def test_duplicate_source_indices_are_rejected_before_creating_output(tmp_path: Path) -> None:
    folder=tmp_path/"duplicate"
    with pytest.raises(ValueError,match="unique"):
        write_bass_gp5_preview([_event(1,28),_event(1,33)],folder,config=BassOutputConfig(profile="bass4"))
    assert not folder.exists()


def test_negative_start_is_rejected_without_endpoint_repair() -> None:
    with pytest.raises(ValidationError):
        BassOutputEvent(source_index=0, start=-.01, end=.1, midi=28)


def test_legacy_defaults_remain_a_separate_unchanged_route() -> None:
    from src.pipelines import tab
    assert tab.STANDARD_TUNINGS[4] == [40,45,50,55]
    assert tab.STANDARD_TUNINGS[5] == [35,40,45,50,55]
