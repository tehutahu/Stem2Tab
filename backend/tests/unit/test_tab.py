"""Synthetic MIDI -> real GP5 readback regressions; no audio/model fixtures."""
from __future__ import annotations

import hashlib
from pathlib import Path

import guitarpro
import pretty_midi

from src.pipelines import tab


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_midi(path: Path, instruments: list[tuple[int, list[tuple[float, float, int]]]]) -> None:
    midi = pretty_midi.PrettyMIDI(initial_tempo=120.0)
    for program, rows in instruments:
        instrument = pretty_midi.Instrument(program=program)
        for start, end, pitch in rows:
            instrument.notes.append(pretty_midi.Note(velocity=100, pitch=pitch, start=start, end=end))
        midi.instruments.append(instrument)
    midi.write(str(path))


def _convert_and_read(midi_path: Path, output_path: Path) -> guitarpro.models.Song:
    original_sha = _sha256(midi_path)
    returned = tab.midi_to_gp5(midi_path, output_path, strings=4)
    assert _sha256(midi_path) == original_sha, "conversion must not rewrite the input MIDI"
    assert returned == output_path, "the GP5 path must complete without fallback"
    assert returned.is_file()
    return guitarpro.parse(str(returned))


def _notes(song: guitarpro.models.Song) -> list[guitarpro.models.Note]:
    return [note for track in song.tracks for measure in track.measures
            for voice in measure.voices for beat in voice.beats for note in beat.notes]


def _assert_one_bass_container(song: guitarpro.models.Song) -> guitarpro.models.Track:
    assert len(song.tracks) == 1, "no default blank track may precede the intended Bass track"
    assert len(song.measureHeaders) == 1
    track = song.tracks[0]
    assert track.name == "Bass"
    assert track.channel.instrument == 33
    assert len(track.measures) == 1
    assert [string.value for string in track.strings] == [43, 38, 33, 28]
    return track


def test_gp5_preserves_multiple_mapped_notes_and_cross_instrument_order(tmp_path: Path) -> None:
    midi_path = tmp_path / "interleaved.mid"
    _write_midi(midi_path, [
        (33, [(1.0, 1.25, 43), (0.5, 0.75, 28), (1.5, 1.75, 45)]),
        (32, [(0.0, 0.25, 33), (1.25, 1.5, 40), (1.75, 2.0, 33)]),
    ])

    song = _convert_and_read(midi_path, tmp_path / "interleaved.gp5")
    notes = _notes(song)
    assert [note.realValue for note in notes] == [33, 28, 43, 40, 45, 33]
    track = _assert_one_bass_container(song)
    beats = track.measures[0].voices[0].beats
    sounding = [beat for beat in beats if beat.notes]
    origin = song.measureHeaders[0].start
    assert [(beat.start - origin) / 960 for beat in sounding] == [0, 1, 2, 2.5, 3, 3.5]
    assert all(beat.duration.time == 480 for beat in sounding)
    assert any(beat.status == guitarpro.models.BeatStatus.rest for beat in beats)
    assert sum(beat.duration.time for beat in beats) == 3840
    assert all(note.type == guitarpro.models.NoteType.normal for note in notes)
    assert [(note.string, note.value) for note in notes] == [(3, 0), (4, 0), (1, 0), (2, 2), (1, 2), (3, 0)]


def test_gp5_mapping_boundaries_use_sounding_bass_pitch(tmp_path: Path) -> None:
    midi_path = tmp_path / "boundaries.mid"
    _write_midi(midi_path, [(33, [(0.0, .25, 28), (.5, .75, 67)])])
    song = _convert_and_read(midi_path, tmp_path / "boundaries.gp5")
    assert [note.realValue for note in _notes(song)] == [28, 67]
    _assert_one_bass_container(song)
    assert tab._pick_string_and_fret(27, 4) is None
    assert tab._pick_string_and_fret(68, 4) is None


def test_gp5_preserves_low_register_notes(tmp_path: Path) -> None:
    midi_path = tmp_path / "low-register.mid"
    _write_midi(midi_path, [(33, [(0.0, .25, 28), (.5, .75, 33), (1.0, 1.25, 38)])])
    song = _convert_and_read(midi_path, tmp_path / "low-register.gp5")
    assert [note.realValue for note in _notes(song)] == [28, 33, 38]
    _assert_one_bass_container(song)


def test_gp5_empty_note_container_uses_midi_tempo(tmp_path: Path) -> None:
    midi_path = tmp_path / "empty.mid"
    _write_midi(midi_path, [])
    song = _convert_and_read(midi_path, tmp_path / "empty.gp5")
    assert song.tempo == 120
    assert _notes(song) == []
    track = _assert_one_bass_container(song)
    beats = track.measures[0].voices[0].beats
    assert [beat.status for beat in beats] == [guitarpro.models.BeatStatus.rest]
    assert beats[0].duration.time == 3840
