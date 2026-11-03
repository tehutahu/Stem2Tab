"""Explicit musical notation clocks, separate from immutable MIDI storage clocks."""
from __future__ import annotations

from fractions import Fraction
import hashlib
from math import lcm
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal
import xml.etree.ElementTree as ET

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from src.evaluation.notation_fidelity import _Event, _emit_note, _text, write_fidelity_musicxml


class MusicalClock(BaseModel):
    """User-declared fixed quarter-note clock, never a tempo estimate."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    bpm: float = Field(ge=20, le=300, strict=True)
    numerator: StrictInt = Field(ge=1, le=12)
    denominator: Literal[2, 4, 8, 16]
    origin_seconds: float = Field(ge=0, le=600, strict=True)
    evidence: str = Field(default="", max_length=500)
    provenance: Literal["explicit-user-input"] = "explicit-user-input"

    @model_validator(mode="after")
    def bounded_precision(self) -> "MusicalClock":
        if (Fraction(str(self.bpm))*1000).denominator != 1:
            raise ValueError("BPM supports at most three decimal places")
        if (Fraction(str(self.origin_seconds))*1_000_000).denominator != 1:
            raise ValueError("Origin supports at most six decimal places")
        return self


class MusicalClockReport(BaseModel):
    """Provenance and exact preservation scope of one independent derivative."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    clock: MusicalClock
    source_sha256: str
    storage_bpm: float
    storage_meter: Literal["4/4"] = "4/4"
    estimation_status: Literal["not-estimated"] = "not-estimated"
    confidence: None = None
    ambiguity_bpm: list[float]
    note_count: int
    divisions: int
    horizon_seconds: str
    quantization: Literal["none"] = "none"
    preview_eligible: bool
    preview_events: list[tuple[int, int, int]] | None
    preview_horizon_ticks: int | None
    preview_reason: str | None
    scope: str = "Fixed user-declared clock only. MIDI attacks cannot establish musical pulse, meter, downbeat, or constant tempo. No audio analysis. Original MIDI bytes and velocities remain unchanged; XML renders pitches and boundaries, not controllers, velocity or timbre."


def write_musical_clock(midi_path: Path, output_path: Path, clock: MusicalClock) -> MusicalClockReport:
    """Rebar exact rational source times without moving an attack or release.

    Origin is a user-declared downbeat in the source seconds coordinate system.
    Bars extend backwards from it to zero. Partial first/final measures preserve
    the original storage XML horizon, including its trailing rests. Excessive
    integer precision is rejected rather than rounded.
    """
    import mido
    import pretty_midi

    if output_path.exists():
        raise FileExistsError(output_path)
    with TemporaryDirectory(prefix="stem2tab-clock-base-") as temporary:
        base = Path(temporary)/"source.musicxml"
        report = write_fidelity_musicxml(midi_path, base)
        root_base = ET.parse(base).getroot()
    raw = mido.MidiFile(str(midi_path))
    # The strict clock permits multiple declarations at tick zero. The last
    # effective tempo wins, matching the MIDI loader and storage XML.
    tempo_us = 500000
    for message in raw.tracks[0]:
        if message.type == "set_tempo":
            tempo_us = message.tempo
    seconds_per_tick = Fraction(tempo_us, raw.ticks_per_beat * 1_000_000)
    bpm = Fraction(str(clock.bpm))
    quarters_per_tick = seconds_per_tick * bpm / 60
    origin = Fraction(str(clock.origin_seconds)) * bpm / 60
    bar_quarters = Fraction(clock.numerator * 4, clock.denominator)
    divisions = lcm(quarters_per_tick.denominator, origin.denominator, bar_quarters.denominator)
    if divisions > 1_000_000_000:
        raise ValueError("Exact clock requires divisions above 1000000000; use fewer BPM/origin decimal places")
    units_per_tick = int(quarters_per_tick * divisions)
    bar = int(bar_quarters * divisions)
    phase = int(origin * divisions) % bar
    # The existing storage XML defines its full-bar tail. Preserve that exact horizon.
    horizon_ticks = 0
    for measure in root_base.findall("./part/measure"):
        cursor = maximum = 0
        for element in measure:
            duration = int(element.findtext("duration", "0"))
            if element.tag == "backup":
                cursor -= duration
            elif element.tag in {"note", "forward"}:
                cursor += duration
                maximum = max(maximum, cursor)
        horizon_ticks += maximum
    total = horizon_ticks * units_per_tick
    if total > 9_000_000_000_000:
        raise ValueError("Exact clock exceeds the bounded integer duration range")
    midi = pretty_midi.PrettyMIDI(str(midi_path))
    events = sorted((_Event(int(midi.time_to_tick(n.start))*units_per_tick,
        int(midi.time_to_tick(n.end))*units_per_tick, n.pitch)
        for instrument in midi.instruments if not instrument.is_drum for n in instrument.notes),
        key=lambda n: (n.start, n.end, n.pitch))
    voices: list[list[_Event]] = []
    for event in events:
        index = next((i for i, voice in enumerate(voices) if voice[-1].end <= event.start), len(voices))
        if index == len(voices):
            voices.append([])
        voices[index].append(event)
    voices = voices or [[]]
    cuts = [0]
    boundary = phase or bar
    while boundary < total:
        cuts.append(boundary)
        boundary += bar
    cuts.append(total)
    if len(cuts) > 4001:
        raise ValueError("Musical clock exceeds the 4000-measure limit")
    root = ET.Element("score-partwise", version="4.0")
    part_list = ET.SubElement(root, "part-list")
    score_part = ET.SubElement(part_list, "score-part", id="P1")
    _text(score_part, "part-name", "Explicit musical clock, original MIDI times")
    part = ET.SubElement(root, "part", id="P1")
    for index, (start, end) in enumerate(zip(cuts, cuts[1:])):
        partial = end-start != bar
        measure = ET.SubElement(part, "measure", number=str(index + (0 if phase else 1)), **({"implicit": "yes"} if partial else {}))
        if index == 0:
            attributes = ET.SubElement(measure, "attributes")
            _text(attributes, "divisions", divisions)
            time = ET.SubElement(attributes, "time")
            _text(time, "beats", clock.numerator)
            _text(time, "beat-type", clock.denominator)
            clef = ET.SubElement(attributes, "clef")
            _text(clef, "sign", "F")
            _text(clef, "line", 4)
            direction = ET.SubElement(measure, "direction")
            metronome = ET.SubElement(ET.SubElement(direction, "direction-type"), "metronome")
            _text(metronome, "beat-unit", "quarter")
            _text(metronome, "per-minute", clock.bpm)
            ET.SubElement(direction, "sound", tempo=str(clock.bpm))
        for voice_number, voice in enumerate(voices, 1):
            if voice_number > 1:
                _text(ET.SubElement(measure, "backup"), "duration", end-start)
            cursor = start
            for event in voice:
                if event.end <= start:
                    continue
                if event.start >= end:
                    break
                a, b = max(start, event.start), min(end, event.end)
                if a > cursor:
                    _emit_note(measure, a-cursor, divisions, voice_number)
                _emit_note(measure, b-a, divisions, voice_number, event,
                    tie_stop=event.start < start, tie_start=event.end > end)
                cursor = b
            if cursor < end:
                _emit_note(measure, end-cursor, divisions, voice_number)
    # alphaTab uses integer BPM and a 960-tick quarter; refuse approximate playback.
    tick_exact = all((Fraction(value*960, divisions)).denominator == 1
        for value in [*cuts, *(v for e in events for v in (e.start, e.end))])
    eligible = bpm.denominator == 1 and tick_exact
    reason = None if eligible else "The browser renderer cannot represent this clock exactly at integer BPM and 960 ticks/quarter. Exact XML and report remain downloadable."
    output_path.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(root)
    with output_path.open("xb") as stream:
        stream.write(ET.tostring(root, encoding="utf-8", xml_declaration=True))
    return MusicalClockReport(clock=clock, source_sha256=hashlib.sha256(midi_path.read_bytes()).hexdigest(),
        storage_bpm=report.tempo_bpm, ambiguity_bpm=[clock.bpm/2, clock.bpm*2],
        note_count=len(events), divisions=divisions, horizon_seconds=str(horizon_ticks*seconds_per_tick),
        preview_eligible=eligible, preview_reason=reason,
        preview_events=[(e.pitch, e.start*960//divisions, e.end*960//divisions) for e in events] if eligible else None,
        preview_horizon_ticks=total*960//divisions if eligible else None)
