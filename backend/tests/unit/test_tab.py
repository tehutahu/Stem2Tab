"""Synthetic MIDI -> real GP5 readback regressions; no audio/model fixtures."""
from __future__ import annotations

import hashlib
from pathlib import Path

import guitarpro
import pretty_midi
import pytest

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


@pytest.fixture
def gp5_only(monkeypatch: pytest.MonkeyPatch) -> None:
    # This regression concerns GP5 container construction. MusicXML has its
    # own timing/voice limitations and is deliberately not part of this fix.
    monkeypatch.setattr(tab, "_write_simple_musicxml", lambda *args, **kwargs: None)


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
    assert [string.value for string in track.strings] == [55, 50, 45, 40]
    return track


def test_gp5_preserves_multiple_mapped_notes_and_cross_instrument_order(tmp_path: Path, gp5_only: None) -> None:
    midi_path = tmp_path / "interleaved.mid"
    _write_midi(midi_path, [
        (33, [(1.0, 1.25, 55), (0.4, 0.65, 40), (1.8, 2.0, 57)]),
        (32, [(0.1, 0.3, 45), (1.0, 1.4, 52), (2.1, 2.3, 45)]),
    ])

    song = _convert_and_read(midi_path, tmp_path / "interleaved.gp5")

    notes = _notes(song)
    # Includes simultaneous onsets ordered by original end, plus a repeated
    # pitch. Pitch sequence is preserved; original rhythm is intentionally not.
    assert [note.realValue for note in notes] == [45, 40, 55, 52, 57, 45]
    track = _assert_one_bass_container(song)
    beats = track.measures[0].voices[0].beats
    assert len(beats) == 6
    assert all(beat.duration.value == 4 for beat in beats)
    assert all(note.type == guitarpro.models.NoteType.normal for note in notes)
    assert [(note.string, note.value) for note in notes] == [(3, 0), (4, 0), (1, 0), (2, 2), (1, 2), (3, 0)]


def test_gp5_retains_existing_mapping_boundaries_without_octave_repair(tmp_path: Path, gp5_only: None) -> None:
    midi_path = tmp_path / "boundaries.mid"
    _write_midi(midi_path, [(33, [(0.0, .2, 39), (.4, .6, 40), (.8, 1.0, 79), (1.2, 1.4, 80)])])

    song = _convert_and_read(midi_path, tmp_path / "boundaries.gp5")

    assert [note.realValue for note in _notes(song)] == [40, 79]
    _assert_one_bass_container(song)
    # These are the current four-string helper limits, not corrected bass tuning.
    assert tab._pick_string_and_fret(39, 4) is None
    assert tab._pick_string_and_fret(80, 4) is None


def test_gp5_all_unmappable_notes_preserve_the_intended_rest(tmp_path: Path, gp5_only: None) -> None:
    midi_path = tmp_path / "low-register.mid"
    _write_midi(midi_path, [(33, [(0.0, .2, 28), (.4, .6, 33), (.8, 1.0, 38)])])

    song = _convert_and_read(midi_path, tmp_path / "low-register.gp5")

    assert _notes(song) == []
    track = _assert_one_bass_container(song)
    beats = track.measures[0].voices[0].beats
    assert len(beats) == 1
    assert beats[0].status == guitarpro.models.BeatStatus.rest
    assert beats[0].duration.value == 4


def test_gp5_empty_note_container_when_tempo_is_available(
    tmp_path: Path, gp5_only: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    midi_path = tmp_path / "empty.mid"
    _write_midi(midi_path, [])
    # Empty/single-onset estimate_tempo can fail before container construction.
    # Keep that existing fallback behavior out of this narrowly scoped regression.
    monkeypatch.setattr(pretty_midi.PrettyMIDI, "estimate_tempo", lambda self: 120.0)

    song = _convert_and_read(midi_path, tmp_path / "empty.gp5")

    assert _notes(song) == []
    track = _assert_one_bass_container(song)
    assert [beat.status for beat in track.measures[0].voices[0].beats] == [guitarpro.models.BeatStatus.rest]
