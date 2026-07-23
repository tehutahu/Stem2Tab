from __future__ import annotations

import json
from pathlib import Path

import pretty_midi
import pytest
from pydantic import ValidationError

from src.evaluation.io import (
    read_midi,
    read_note_events_csv,
    read_note_events_json,
    write_midi,
    write_note_events_csv,
    write_note_events_json,
)
from src.evaluation.models import NoteEvent, NoteEventSet


def test_note_event_validation_and_deterministic_sorting() -> None:
    later = NoteEvent(start=1.0, end=1.5, midi=40, velocity=0.5)
    higher = NoteEvent(start=0.0, end=0.5, midi=45, velocity=1.0)
    lower = NoteEvent(start=0.0, end=0.4, midi=40, velocity=0.75)

    events = NoteEventSet(notes=(later, higher, lower))

    assert events.notes == (lower, higher, later)
    with pytest.raises(ValidationError):
        NoteEvent(start=-0.1, end=0.5, midi=40)
    with pytest.raises(ValidationError):
        NoteEvent(start=0.5, end=0.5, midi=40)
    with pytest.raises(ValidationError):
        NoteEvent(start=0.0, end=0.5, midi=128)
    with pytest.raises(ValidationError):
        NoteEvent(start=0.0, end=0.5, midi=40, velocity=0.0)


def test_json_and_csv_round_trip(tmp_path: Path) -> None:
    events = NoteEventSet(
        source={"kind": "reference", "name": "fixture"},
        notes=(
            NoteEvent(start=0.0, end=0.5, midi=40, velocity=0.8),
            NoteEvent(start=0.5, end=1.0, midi=43, velocity=1.0),
        ),
    )
    json_path = write_note_events_json(events, tmp_path / "events.json")
    csv_path = write_note_events_csv(events, tmp_path / "events.csv")

    assert read_note_events_json(json_path) == events
    csv_events = read_note_events_csv(csv_path, source=events.source)
    assert csv_events == events
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "1.0"
    assert csv_path.read_text(encoding="utf-8").splitlines()[0] == "start,end,midi,velocity"


def test_csv_rejects_nonstandard_columns(tmp_path: Path) -> None:
    csv_path = tmp_path / "invalid.csv"
    csv_path.write_text("onset,offset,pitch\n0,1,40\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Unexpected CSV columns"):
        read_note_events_csv(csv_path)


def test_midi_merges_non_drum_tracks_and_ignores_drums(tmp_path: Path) -> None:
    midi_data = pretty_midi.PrettyMIDI(initial_tempo=120)
    bass_one = pretty_midi.Instrument(program=32, name="Bass one")
    bass_one.notes.append(pretty_midi.Note(velocity=64, pitch=40, start=0.0, end=0.5))
    bass_two = pretty_midi.Instrument(program=33, name="Bass two")
    bass_two.notes.append(pretty_midi.Note(velocity=127, pitch=43, start=0.5, end=1.0))
    drums = pretty_midi.Instrument(program=0, is_drum=True, name="Drums")
    drums.notes.append(pretty_midi.Note(velocity=100, pitch=36, start=0.0, end=0.1))
    midi_data.instruments.extend((bass_two, drums, bass_one))
    input_path = tmp_path / "input.mid"
    midi_data.write(str(input_path))

    events = read_midi(input_path, source={"kind": "reference"})

    assert [note.midi for note in events.notes] == [40, 43]
    assert events.notes[0].velocity == pytest.approx(64 / 127)
    assert events.notes[1].velocity == pytest.approx(1.0)
    assert events.source["non_drum_tracks"] == 2


def test_midi_write_read_round_trip_and_velocity_clamp(tmp_path: Path) -> None:
    events = NoteEventSet(
        notes=(
            NoteEvent(start=0.0, end=0.25, midi=40, velocity=0.001),
            NoteEvent(start=0.25, end=0.75, midi=47, velocity=1.0),
        )
    )
    midi_path = write_midi(events, tmp_path / "performance.mid")

    reloaded = read_midi(midi_path)

    assert [note.midi for note in reloaded.notes] == [40, 47]
    assert reloaded.notes[0].velocity == pytest.approx(1 / 127)
    assert reloaded.notes[1].velocity == pytest.approx(1.0)
    assert reloaded.notes[0].start == pytest.approx(0.0, abs=0.002)
    assert reloaded.notes[1].end == pytest.approx(0.75, abs=0.002)
