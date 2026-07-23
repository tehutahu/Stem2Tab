"""JSON, CSV, and MIDI input/output for canonical note events."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pretty_midi

from src.evaluation.models import NoteEvent, NoteEventSet, SourceValue

CSV_FIELDS = ("start", "end", "midi", "velocity")
DEFAULT_BASS_PROGRAM = pretty_midi.instrument_name_to_program("Electric Bass (finger)")


def read_note_events_json(path: Path) -> NoteEventSet:
    """Load and validate a canonical note-event JSON artifact."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    return NoteEventSet.model_validate(payload)


def write_note_events_json(events: NoteEventSet, path: Path) -> Path:
    """Write canonical note events as deterministic, human-readable JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = events.model_dump(mode="json")
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def read_note_events_csv(
    path: Path,
    *,
    source: dict[str, SourceValue] | None = None,
) -> NoteEventSet:
    """Load canonical note events from a CSV artifact."""
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != CSV_FIELDS:
            expected = ",".join(CSV_FIELDS)
            actual = ",".join(reader.fieldnames or ())
            raise ValueError(f"Unexpected CSV columns: {actual!r}; expected {expected!r}")
        notes = [
            NoteEvent(
                start=float(row["start"]),
                end=float(row["end"]),
                midi=int(row["midi"]),
                velocity=float(row["velocity"]),
            )
            for row in reader
        ]
    return NoteEventSet(source=source or {}, notes=tuple(notes))


def write_note_events_csv(events: NoteEventSet, path: Path) -> Path:
    """Write canonical note events using the stable Phase A CSV schema."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, lineterminator="\n")
        writer.writeheader()
        for note in events.notes:
            writer.writerow(
                {
                    "start": _format_float(note.start),
                    "end": _format_float(note.end),
                    "midi": note.midi,
                    "velocity": _format_float(note.velocity),
                }
            )
    return path


def read_midi(
    path: Path,
    *,
    source: dict[str, SourceValue] | None = None,
) -> NoteEventSet:
    """Read all non-drum MIDI tracks into one canonical note-event collection."""
    midi_data = pretty_midi.PrettyMIDI(str(path))
    instruments = [instrument for instrument in midi_data.instruments if not instrument.is_drum]
    notes = tuple(
        NoteEvent(
            start=float(note.start),
            end=float(note.end),
            midi=int(note.pitch),
            velocity=float(note.velocity) / 127.0,
        )
        for instrument in instruments
        for note in instrument.notes
    )
    metadata = dict(source or {})
    metadata.setdefault("non_drum_tracks", len(instruments))
    return NoteEventSet(source=metadata, notes=notes)


def write_midi(
    events: NoteEventSet,
    path: Path,
    *,
    instrument_name: str = "Stem2Tab Performance Bass",
) -> Path:
    """Write canonical note events as a single-track performance MIDI."""
    path.parent.mkdir(parents=True, exist_ok=True)
    midi_data = pretty_midi.PrettyMIDI(initial_tempo=120.0)
    instrument = pretty_midi.Instrument(
        program=DEFAULT_BASS_PROGRAM,
        is_drum=False,
        name=instrument_name,
    )
    for note in events.notes:
        velocity = min(127, max(1, round(note.velocity * 127.0)))
        instrument.notes.append(
            pretty_midi.Note(
                velocity=velocity,
                pitch=note.midi,
                start=note.start,
                end=note.end,
            )
        )
    midi_data.instruments.append(instrument)
    midi_data.write(str(path))
    return path


def _format_float(value: float) -> str:
    return format(value, ".12g")
