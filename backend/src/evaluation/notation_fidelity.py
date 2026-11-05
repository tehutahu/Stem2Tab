"""Local evaluation-only MusicXML preserving loaded MIDI event boundaries.

This is a bounded nominal MIDI-note interchange export, not beat inference,
engraving, or TAB fingering. Existing pipeline/worker defaults are not called.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Literal
import xml.etree.ElementTree as ET

from pydantic import BaseModel, ConfigDict
from src.evaluation.midi_clock import validate_fixed_storage_clock


class FidelityReport(BaseModel):
    """Describe event-preservation scope without claiming acoustic accuracy."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)
    schema_version: str = "1.0"
    timing: Literal["ticks", "grid48"]
    input_sha256: str
    input_resolution: int
    divisions: int
    tempo_bpm: float
    notation_scale: Literal[1, 2] = 1
    original_tempo_bpm: float
    note_count: int
    voice_count: int
    minimum_duration_units: int | None
    symbolic_type_omissions: int
    same_pitch_overlap_note_ons: int
    input_pitch_bends: int
    input_control_changes: int
    overlap_audit_scope: str = "track-local channel/pitch overlaps; cross-track channel collisions are not detected"
    input_noteoff_policy: str = "pretty_midi loaded events; ambiguous source note-offs cannot be reconstructed"
    scope: str = "loaded non-drum MIDI note numbers/boundaries; fixed tempo 4/4; ignores bends/controllers/velocity/timbre; no inferred beats or fingering"


@dataclass(frozen=True)
class _Event:
    start: int
    end: int
    pitch: int


def _text(parent: ET.Element, tag: str, value: object) -> ET.Element:
    element = ET.SubElement(parent, tag)
    element.text = str(value)
    return element


def _symbolic_duration(duration: int, divisions: int) -> tuple[str, bool] | None:
    # Emit an unambiguous type only when it exactly agrees with duration.
    # Other positive integer divisions are legal but deliberately unengraved.
    types = [(4, "whole"), (2, "half"), (1, "quarter")]
    types += [(1 / (2 ** i), name) for i, name in enumerate(
        ["eighth", "16th", "32nd", "64th", "128th", "256th"], start=1
    )]
    for quarters, name in types:
        if duration == quarters * divisions:
            return name, False
        if duration == quarters * divisions * 1.5:
            return name, True
    return None


def _emit_note(
    measure: ET.Element, duration: int, divisions: int, voice: int,
    event: _Event | None = None, *, tie_stop: bool = False, tie_start: bool = False,
) -> bool:
    element = ET.SubElement(measure, "note")
    if event is None:
        ET.SubElement(element, "rest")
    else:
        pitch = ET.SubElement(element, "pitch")
        step, alter = [("C", 0), ("C", 1), ("D", 0), ("D", 1), ("E", 0),
                       ("F", 0), ("F", 1), ("G", 0), ("G", 1), ("A", 0),
                       ("A", 1), ("B", 0)][event.pitch % 12]
        _text(pitch, "step", step)
        if alter:
            _text(pitch, "alter", alter)
        _text(pitch, "octave", event.pitch // 12 - 1)
    _text(element, "duration", duration)
    for enabled, kind in [(tie_stop, "stop"), (tie_start, "start")]:
        if enabled:
            ET.SubElement(element, "tie", type=kind)
    _text(element, "voice", voice)
    symbolic = _symbolic_duration(duration, divisions)
    if symbolic is not None:
        name, dotted = symbolic
        _text(element, "type", name)
        if dotted:
            ET.SubElement(element, "dot")
    if tie_stop or tie_start:
        notations = ET.SubElement(element, "notations")
        for enabled, kind in [(tie_stop, "stop"), (tie_start, "start")]:
            if enabled:
                ET.SubElement(notations, "tied", type=kind)
    return symbolic is None


def write_fidelity_musicxml(
    midi_path: Path, output_path: Path, *, timing: Literal["ticks", "grid48"] = "ticks",
    notation_scale: Literal[1, 2] = 1,
) -> FidelityReport:
    """Write a new XML file, preserving each loaded attack as a distinct event.

    `ticks` preserves original MIDI tick boundaries, including positive notes
    shorter than 1/48 quarter. `grid48` rounds boundaries using the old grid
    and explicitly rejects collapsed notes. Neither mode repairs MIDI note-off
    ambiguity already introduced before this loader (e.g. same-pitch overlaps).
    `notation_scale=2` doubles tempo AND all integer durations, retaining the
    original full-bar horizon in seconds. This is notation rebarring, not
    quantization or tempo inference. Meter 4/4 and origin zero remain assumptions.
    """
    import pretty_midi
    import mido

    if timing not in {"ticks", "grid48"}:
        raise ValueError("timing must be ticks or grid48")
    if type(notation_scale) is not int or notation_scale not in {1, 2}:
        raise ValueError("notation_scale must be 1 or 2")
    if notation_scale != 1 and timing != "ticks":
        raise ValueError("Notation rebarring must preserve ticks; quantization is a separate mode")
    if output_path.exists():
        raise FileExistsError(output_path)
    raw = mido.MidiFile(str(midi_path))
    if raw.type == 2:
        raise ValueError("Asynchronous Type2 MIDI is outside this shared-timeline exporter")
    if raw.ticks_per_beat <= 0:
        raise ValueError("SMPTE time division is outside this PPQ exporter")
    validate_fixed_storage_clock(raw)
    ambiguous = 0
    for track in raw.tracks:
        active: dict[tuple[int, int], int] = {}
        for message in track:
            if message.type not in {"note_on", "note_off"}:
                continue
            key = message.channel, message.note
            if message.type == "note_on" and message.velocity > 0:
                ambiguous += active.get(key, 0) > 0
                active[key] = active.get(key, 0) + 1
            elif active.get(key, 0):
                active[key] -= 1
    midi = pretty_midi.PrettyMIDI(str(midi_path))
    _, tempi = midi.get_tempo_changes()
    bpm = float(tempi[0])
    if any(abs(float(t) - bpm) > 1e-6 for t in tempi):
        raise ValueError("Tempo changes are outside this fixed-tempo exporter")
    if any((t.numerator, t.denominator) != (4, 4) for t in midi.time_signature_changes):
        raise ValueError("Only fixed 4/4 MIDI is supported")
    divisions = midi.resolution if timing == "ticks" else 48
    events = []
    for instrument in midi.instruments:
        if instrument.is_drum:
            continue
        for note in instrument.notes:
            if note.pitch < 12:
                raise ValueError("Sounding MIDI below C0 is outside MusicXML octave 0..9")
            start = round(midi.time_to_tick(note.start) * divisions / midi.resolution)
            end = round(midi.time_to_tick(note.end) * divisions / midi.resolution)
            if start < 0 or end <= start:
                raise ValueError("Note collapses on selected timing grid; use ticks for short notes")
            events.append(_Event(start, end, note.pitch))
    events.sort(key=lambda n: (n.start, n.end, n.pitch))
    bar = divisions * 4
    # Retain the original export's trailing rests as well as every event time.
    total = max(bar, ((max((n.end for n in events), default=0) + bar - 1) // bar) * bar) * notation_scale
    events = [_Event(n.start * notation_scale, n.end * notation_scale, n.pitch) for n in events]
    original_bpm = bpm
    bpm *= notation_scale
    voices: list[list[_Event]] = []
    for event in events:
        index = next((i for i, voice in enumerate(voices) if voice[-1].end <= event.start), len(voices))
        if index == len(voices):
            voices.append([])
        voices[index].append(event)
    if not voices:
        voices.append([])

    root = ET.Element("score-partwise", version="4.0")
    part_list = ET.SubElement(root, "part-list")
    score_part = ET.SubElement(part_list, "score-part", id="P1")
    _text(score_part, "part-name", "Bass MIDI note numbers (no controllers)")
    part = ET.SubElement(root, "part", id="P1")
    omissions = 0
    for start in range(0, total, bar):
        end = start + bar
        measure = ET.SubElement(part, "measure", number=str(start // bar + 1))
        if start == 0:
            attributes = ET.SubElement(measure, "attributes")
            _text(attributes, "divisions", divisions)
            time = ET.SubElement(attributes, "time")
            _text(time, "beats", 4)
            _text(time, "beat-type", 4)
            clef = ET.SubElement(attributes, "clef")
            _text(clef, "sign", "F")
            _text(clef, "line", 4)
            direction = ET.SubElement(measure, "direction")
            metronome = ET.SubElement(ET.SubElement(direction, "direction-type"), "metronome")
            _text(metronome, "beat-unit", "quarter")
            _text(metronome, "per-minute", repr(bpm))
            ET.SubElement(direction, "sound", tempo=repr(bpm))
        for index, voice in enumerate(voices, start=1):
            if index > 1:
                _text(ET.SubElement(measure, "backup"), "duration", bar)
            cursor = start
            for event in voice:
                if event.end <= start:
                    continue
                if event.start >= end:
                    break
                a, b = max(start, event.start), min(end, event.end)
                if a > cursor:
                    omissions += _emit_note(measure, a-cursor, divisions, index)
                omissions += _emit_note(
                    measure, b-a, divisions, index, event,
                    tie_stop=event.start < start, tie_start=event.end > end,
                )
                cursor = b
            if cursor < end:
                omissions += _emit_note(measure, end-cursor, divisions, index)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(root)
    payload = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    with output_path.open("xb") as stream:
        stream.write(payload)
    return FidelityReport(
        timing=timing, input_sha256=hashlib.sha256(midi_path.read_bytes()).hexdigest(),
        input_resolution=midi.resolution, divisions=divisions, tempo_bpm=bpm,
        notation_scale=notation_scale, original_tempo_bpm=original_bpm,
        note_count=len(events), voice_count=len(voices),
        minimum_duration_units=min((n.end-n.start for n in events), default=None),
        symbolic_type_omissions=omissions,
        same_pitch_overlap_note_ons=ambiguous,
        input_pitch_bends=sum(len(i.pitch_bends) for i in midi.instruments if not i.is_drum),
        input_control_changes=sum(len(i.control_changes) for i in midi.instruments if not i.is_drum),
    )


def main() -> int:
    """Export saved MIDI locally without separation/transcription or relabeling."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--midi", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--timing", choices=["ticks", "grid48"], default="ticks")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    report = write_fidelity_musicxml(args.midi, args.output_dir / "bass.musicxml", timing=args.timing)
    (args.output_dir / "summary.json").write_text(report.model_dump_json(indent=2) + "\n")
    print(json.dumps(report.model_dump(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
