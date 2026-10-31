"""Explicit bass-fingering GP5 previews, separate from performed-time artifacts.

This opt-in writer never transposes or silently drops supplied events. GP5 uses
an explicit preview clock and four quarter-note slots per storage bar; it does
not represent performed rhythm, inferred meter, native programs or timbre.
"""
from __future__ import annotations

from collections.abc import Sequence
import hashlib
import json
from pathlib import Path
from types import MappingProxyType
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator
import structlog

logger = structlog.get_logger()

BASS_TUNINGS = MappingProxyType({"bass4": (28, 33, 38, 43), "bass5": (23, 28, 33, 38, 43)})
MidiPitch = Annotated[StrictInt, Field(ge=0, le=127)]


class BassOutputEvent(BaseModel):
    """One supplied event with a stable source index and unchanged note data."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    source_index: Annotated[StrictInt, Field(ge=0)]
    start: float = Field(ge=0.0)
    end: float
    midi: MidiPitch
    velocity: Annotated[StrictInt, Field(ge=1, le=127)] = 80

    @model_validator(mode="after")
    def validate_interval(self) -> BassOutputEvent:
        """Accept original finite endpoints only when duration is positive."""
        if self.end <= self.start:
            raise ValueError("end must be greater than start; no endpoint repair is performed")
        return self


class BassOutputConfig(BaseModel):
    """Explicit named/custom tuning and bounded GP5 preview settings."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    profile: Literal["bass4", "bass5", "custom"]
    strings: Annotated[StrictInt, Field(ge=1, le=7)] | None = None
    tuning_midi: tuple[MidiPitch, ...] | None = None
    max_fret: Annotated[StrictInt, Field(ge=0, le=36)] | None = None
    preview_tempo_bpm: Annotated[StrictInt, Field(ge=1, le=1000)] = 120

    @model_validator(mode="after")
    def validate_tuning(self) -> BassOutputConfig:
        """Reject implicit custom settings and alterations of named profiles."""
        if self.profile == "custom":
            if self.strings is None or self.tuning_midi is None or self.max_fret is None:
                raise ValueError("custom requires explicit strings, tuning_midi and max_fret")
            if len(self.tuning_midi) != self.strings:
                raise ValueError("custom tuning length must equal strings")
            if any(a >= b for a, b in zip(self.tuning_midi, self.tuning_midi[1:])):
                raise ValueError("custom tuning must be strictly increasing low to high; no sorting is performed")
        else:
            named = BASS_TUNINGS[self.profile]
            if self.strings is not None and self.strings != len(named):
                raise ValueError("strings cannot change the named bass profile")
            if self.tuning_midi is not None and self.tuning_midi != named:
                raise ValueError("tuning_midi cannot change the named bass profile")
        return self

    @property
    def resolved_tuning(self) -> tuple[int, ...]:
        """Return the explicit low-to-high sounding MIDI tuning."""
        return self.tuning_midi if self.profile == "custom" else BASS_TUNINGS[self.profile]

    @property
    def resolved_max_fret(self) -> int:
        """Use the named-profile 24-fret default, within supported range0..36."""
        return self.max_fret if self.max_fret is not None else 24


class BassEventMapping(BassOutputEvent):
    """A supplied event and its mapping decision, including every failure."""

    input_ordinal: int
    string: int | None = None
    fret: int | None = None
    preview_velocity_midi: int | None = None
    unmappable_reason: str | None = None


class BassOutputReport(BaseModel):
    """Manifest of the complete input, strict mapping and actual GP5 readback."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    schema_version: Literal["1.0"] = "1.0"
    status: Literal["written", "unmappable", "failed"]
    profile: Literal["bass4", "bass5", "custom"]
    strings: int
    tuning_midi: tuple[int, ...]
    max_fret: int
    preview_tempo_bpm: int
    input_event_count: int
    mapped_event_count: int
    input_events_sha256: str
    events: tuple[BassEventMapping, ...]
    unmappable_indices: tuple[int, ...]
    gp5_path: str | None
    manifest_path: str
    readback: dict[str, Any] | None = None
    warnings: tuple[str, ...]
    error: str | None = None
    timing_preserved: Literal[False] = False
    transposition_semitones: Literal[0] = 0
    partial_preview: Literal[False] = False


def _mapping(events: Sequence[BassOutputEvent], config: BassOutputConfig) -> tuple[BassEventMapping, ...]:
    rows = []
    for ordinal, event in enumerate(events):
        options = [(string, event.midi - open_pitch)
                   for string, open_pitch in enumerate(reversed(config.resolved_tuning), start=1)
                   if 0 <= event.midi - open_pitch <= config.resolved_max_fret]
        if options:
            string, fret = options[0]
            # GP5 has eight dynamic levels, not arbitrary MIDI velocity. Retain
            # native velocity above and explicitly report this preview derivative.
            velocity = min(range(15, 128, 16), key=lambda value: (abs(value-event.velocity), value))
            rows.append(BassEventMapping(**event.model_dump(), input_ordinal=ordinal,
                                         string=string, fret=fret, preview_velocity_midi=velocity))
        else:
            rows.append(BassEventMapping(**event.model_dump(), input_ordinal=ordinal,
                unmappable_reason=f"Pitch {event.midi} has no string/fret in0..{config.resolved_max_fret}; no transposition or omission is permitted."))
    return tuple(rows)


def _write_preview(path: Path, rows: tuple[BassEventMapping, ...], config: BassOutputConfig) -> None:
    import guitarpro

    gp = guitarpro.models
    song = gp.Song(tempo=config.preview_tempo_bpm, tempoName="Preview clock", tracks=[], measureHeaders=[])
    song.title = "Bass fingering preview - quarter notes only"
    track = gp.Track(song, name="Bass preview", channel=gp.MidiChannel(instrument=33), measures=[],
                     fretCount=config.resolved_max_fret)
    track.strings = [gp.GuitarString(number=index, value=pitch)
                     for index, pitch in enumerate(reversed(config.resolved_tuning), start=1)]
    song.tracks.append(track)
    bar_count = max(1, (len(rows)+3)//4)
    for bar in range(bar_count):
        header = gp.MeasureHeader(number=bar+1, start=gp.Duration.quarterTime*(1+4*bar),
                                  timeSignature=gp.TimeSignature(numerator=4, denominator=gp.Duration(value=4)))
        song.measureHeaders.append(header)
        measure = gp.Measure(track, header, clef=gp.MeasureClef.bass)
        track.measures.append(measure)
        voice = measure.voices[0]
        for slot in range(4):
            index = bar*4+slot
            is_note = index < len(rows)
            beat = gp.Beat(voice, duration=gp.Duration(value=4),
                           start=header.start+slot*gp.Duration.quarterTime,
                           status=gp.BeatStatus.normal if is_note else gp.BeatStatus.rest)
            if is_note:
                row = rows[index]
                beat.notes.append(gp.Note(beat, value=row.fret, string=row.string,
                                          velocity=row.preview_velocity_midi, type=gp.NoteType.normal))
            voice.beats.append(beat)
    with path.open("xb") as stream:
        guitarpro.write(song, stream, version=(5, 1, 0))


def _readback(path: Path, rows: tuple[BassEventMapping, ...], config: BassOutputConfig) -> dict[str, Any]:
    import guitarpro

    song = guitarpro.parse(str(path))
    if len(song.tracks) != 1:
        raise ValueError("GP5 preview must contain exactly one intended track")
    track = song.tracks[0]
    notes = []
    quarter_only = True
    beats_per_bar = []
    for measure in track.measures:
        beats = measure.voices[0].beats
        beats_per_bar.append(len(beats))
        for beat in beats:
            quarter_only = quarter_only and beat.duration.time == guitarpro.models.Duration.quarterTime
            for note in beat.notes:
                notes.append(dict(midi=note.realValue, string=note.string, fret=note.value, velocity=note.velocity,
                                  type=note.type.name, raw_start_ticks=beat.start))
    expected = [(r.midi, r.string, r.fret, r.preview_velocity_midi) for r in rows]
    actual = [(r["midi"], r["string"], r["fret"], r["velocity"]) for r in notes]
    expected_bars = max(1, (len(rows)+3)//4)
    tuning = tuple(string.value for string in reversed(track.strings))
    if (actual != expected or tuning != config.resolved_tuning or len(track.strings) != len(config.resolved_tuning)
            or track.name != "Bass preview" or track.channel.instrument != 33 or track.fretCount != config.resolved_max_fret
            or song.tempo != config.preview_tempo_bpm or len(song.measureHeaders) != expected_bars
            or len(track.measures) != expected_bars or any(n != 4 for n in beats_per_bar)
            or not quarter_only or any(n["type"] != "normal" for n in notes)):
        raise ValueError("GP5 readback differs from complete pitch/mapping/dynamic/container contract")
    for row, event in zip(notes, rows):
        row.update(source_index=event.source_index, input_ordinal=event.input_ordinal,
                   input_velocity=event.velocity, velocity_exact=row["velocity"] == event.velocity)
    return dict(track_count=1, track_name=track.name, playback_program=track.channel.instrument,
                strings=len(track.strings), tuning_midi=tuning, max_fret=track.fretCount,
                preview_tempo_bpm=song.tempo, measure_count=len(track.measures), measure_header_count=len(song.measureHeaders),
                beats_per_measure=beats_per_bar, quarter_note_only=True, note_count=len(notes), notes=notes,
                padding_rest_count=expected_bars*4-len(rows), velocity_changed_count=sum(not n["velocity_exact"] for n in notes),
                pitch_count_mapping_preserved=True)


def write_bass_gp5_preview(
    events: Sequence[BassOutputEvent], output_dir: Path, *, config: BassOutputConfig,
) -> BassOutputReport:
    """Write a fresh all-or-none fingering preview and complete JSON manifest.

    Named tunings are immutable; custom tunings require explicit string count
    and max fret0..36. Input order, multiplicities, source indices, original
    pitch/times/velocity are preserved in the manifest. Any unmappable event
    suppresses the entire GP5; canonical/MIDI/timing XML remain caller-owned.
    Runtime/write/readback failures are manifested and re-raised, never hidden.
    """
    events = tuple(events)
    if not isinstance(config, BassOutputConfig) or any(not isinstance(e, BassOutputEvent) for e in events):
        raise TypeError("Use validated BassOutputConfig and BassOutputEvent models")
    if len({event.source_index for event in events}) != len(events):
        raise ValueError("source_index values must be unique")
    rows = _mapping(events, config)
    unmappable = tuple(row.source_index for row in rows if row.unmappable_reason is not None)
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    manifest = output_dir/"bass-output.json"
    gp5_path = output_dir/"bass.preview.gp5"
    digest = hashlib.sha256(json.dumps([e.model_dump(mode="json") for e in events], sort_keys=True,
                                      separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    warnings = (
        "GP5 is a sequential quarter-note fingering preview at the explicit clock, not performed timing or inferred musical meter; 4/4 bars are storage only.",
        "GP5 dynamics use nearest supported velocity15,31,47,63,79,95,111,127 (tie lower). Original event velocity is retained here; actual serialized dynamics are read back.",
        "String selection is first playable string from highest to lowest; no fingering optimization, native instrument-program preservation or transcription-accuracy claim.",
    )
    if unmappable:
        warnings += (f"No GP5 generated: unmappable source_index values {list(unmappable)}. All input events remain in this manifest; no partial preview is allowed.",)
    report = BassOutputReport(status="unmappable" if unmappable else "written", profile=config.profile,
        strings=len(config.resolved_tuning), tuning_midi=config.resolved_tuning, max_fret=config.resolved_max_fret,
        preview_tempo_bpm=config.preview_tempo_bpm, input_event_count=len(events), mapped_event_count=len(events)-len(unmappable),
        input_events_sha256=digest, events=rows, unmappable_indices=unmappable,
        gp5_path=None if unmappable else str(gp5_path), manifest_path=str(manifest), warnings=warnings)
    try:
        if not unmappable:
            _write_preview(gp5_path, rows, config)
            report = report.model_copy(update={"readback": _readback(gp5_path, rows, config)})
    except Exception as exc:
        failed = report.model_copy(update={"status": "failed", "error": f"{type(exc).__name__}: {exc}"})
        with manifest.open("x", encoding="utf-8") as stream:
            stream.write(failed.model_dump_json(indent=2)+"\n")
        logger.exception("bass_preview_failed", output_dir=str(output_dir), error=str(exc))
        raise
    with manifest.open("x", encoding="utf-8") as stream:
        stream.write(report.model_dump_json(indent=2)+"\n")
    logger.info("bass_preview_completed", status=report.status, input_events=len(events), unmappable=len(unmappable),
                output_dir=str(output_dir))
    return report
