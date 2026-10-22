"""Export a small, explicitly bounded score-MIDI bass notation baseline."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import structlog

logger = structlog.get_logger()

# Sounding MIDI pitches, low to high (bass notation is conventionally transposed).
STANDARD_TUNINGS = {
    4: [28, 33, 38, 43],  # E1 A1 D2 G2
    5: [23, 28, 33, 38, 43],  # B0 E1 A1 D2 G2
    6: [23, 28, 33, 38, 43, 48],  # B0 E1 A1 D2 G2 C3
}
_GRID = 48  # subdivisions per quarter note; includes straight and triplet values
_BAR = 4 * _GRID


@dataclass(frozen=True)
class _ScoreNote:
    start: int
    end: int
    pitch: int
    velocity: int


def _pick_string_and_fret(midi_pitch: int, strings: int) -> tuple[int, int] | None:
    """Pick the highest available string within the supported 24-fret range."""
    if strings not in STANDARD_TUNINGS:
        raise ValueError(f"Unsupported bass string count: {strings}")
    for string_idx, open_pitch in enumerate(reversed(STANDARD_TUNINGS[strings]), start=1):
        fret = midi_pitch - open_pitch
        if 0 <= fret <= 24:
            return string_idx, fret
    return None


def _load_score(midi_path: Path) -> tuple[float, list[_ScoreNote]]:
    """Read fixed-tempo 4/4 MIDI and round absolute boundaries to a 1/48 beat grid."""
    import pretty_midi

    midi = pretty_midi.PrettyMIDI(str(midi_path))
    _, tempi = midi.get_tempo_changes()
    bpm = float(tempi[0])
    if any(abs(float(t) - bpm) > 1e-6 for t in tempi):
        raise ValueError("Tempo changes require a score-MIDI tempo-map exporter")
    if any((t.numerator, t.denominator) != (4, 4) for t in midi.time_signature_changes):
        raise ValueError("Only 4/4 score MIDI is currently supported")
    notes = []
    for inst in midi.instruments:
        if inst.is_drum:
            continue
        for n in inst.notes:
            start = round(midi.time_to_tick(n.start) * _GRID / midi.resolution)
            end = round(midi.time_to_tick(n.end) * _GRID / midi.resolution)
            if end <= start:
                raise ValueError("MIDI note is shorter than the supported 1/48 beat grid")
            notes.append(_ScoreNote(start, end, n.pitch, n.velocity))
    return bpm, sorted(notes, key=lambda n: (n.start, n.end, n.pitch))


def midi_to_gp5(
    midi_path: Path,
    output_path: Path,
    *,
    strings: int = 4,
    job_id: str | None = None,
) -> Path:
    """Export fixed-tempo 4/4 score MIDI with rests, measures and tied durations.

    Absolute note boundaries are rounded to 1/48 of a quarter note. This is a
    notation baseline, not performance-to-score rhythm inference. Unsupported
    GP5 polyphony, pitches or fractional tempos fall back to MusicXML with a
    warning. Invalid input and MusicXML failures propagate to the caller.
    """
    if not midi_path.exists():
        raise FileNotFoundError(f"MIDI not found: {midi_path}")
    if strings not in STANDARD_TUNINGS:
        raise ValueError(f"Unsupported bass string count: {strings}")
    bpm, notes = _load_score(midi_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    logger.info("tab_generation_start", job_id=job_id, midi=str(midi_path), gp5=str(output_path))
    xml_path = output_path.with_suffix(".musicxml")
    # Always produce the companion XML; never return a path that was not written.
    _write_musicxml_score(notes, xml_path, bpm, job_id)
    try:
        _write_gp5_score(notes, output_path, bpm, strings)
    except (ImportError, ValueError) as exc:
        logger.warning("tab_generation_fallback", job_id=job_id, error=str(exc))
        return xml_path
    logger.info("tab_generation_complete", job_id=job_id, gp5=str(output_path))
    return output_path


def _write_gp5_score(notes: list[_ScoreNote], output_path: Path, bpm: float, strings: int) -> None:
    import guitarpro

    models = guitarpro.models
    if abs(bpm - round(bpm)) > 1e-3:
        raise ValueError("GP5 requires an integer tempo; preserving fractional tempo in MusicXML")
    previous_end = 0
    for n in notes:
        if n.start < previous_end:
            raise ValueError("Overlapping MIDI notes require polyphonic notation; preserving in MusicXML")
        if _pick_string_and_fret(n.pitch, strings) is None:
            raise ValueError(f"Pitch {n.pitch} is outside the {strings}-string 24-fret range")
        previous_end = n.end
    song = models.Song(tempo=round(bpm), tempoName="Bass")
    song.measureHeaders.clear()
    song.tracks.clear()
    track = models.Track(song, name="Bass", channel=models.MidiChannel(instrument=33))
    track.measures.clear()
    track.strings = [models.GuitarString(number=i, value=p) for i, p in enumerate(
        reversed(STANDARD_TUNINGS[strings]), start=1)]
    song.tracks.append(track)
    total = max(_BAR, ((max((n.end for n in notes), default=0) + _BAR - 1) // _BAR) * _BAR)
    for index in range(total // _BAR):
        header = models.MeasureHeader(number=index + 1, start=960 + index * 3840,
            timeSignature=models.TimeSignature(numerator=4, denominator=models.Duration(value=4)))
        song.measureHeaders.append(header)
        track.measures.append(models.Measure(track, header, clef=models.MeasureClef.bass))

    # Each candidate is an exact integer number of grid units, including 128th triplets.
    durations = []
    for value in (1, 2, 4, 8, 16, 32, 64):
        durations.append((192 // value, value, False))
    for value in (1, 2, 4, 8, 16, 32, 64, 128):
        durations.append((128 // value, value, True))
    durations.sort(reverse=True)

    def emit(start: int, end: int, n: _ScoreNote | None) -> None:
        first = True
        while start < end:
            measure = track.measures[start // _BAR]
            voice = measure.voices[0]
            remaining = min(end - start, _BAR - start % _BAR)
            units, value, triplet = next(d for d in durations if d[0] <= remaining)
            duration = models.Duration(value=value)
            if triplet:
                duration.tuplet = models.Tuplet(enters=3, times=2)
            beat = models.Beat(voice, start=960 + start * 20, duration=duration,
                status=models.BeatStatus.rest if n is None else models.BeatStatus.normal)
            if n is not None:
                string_idx, fret = _pick_string_and_fret(n.pitch, strings)
                beat.notes.append(models.Note(beat, value=fret, string=string_idx, velocity=n.velocity,
                    type=models.NoteType.normal if first else models.NoteType.tie))
            voice.beats.append(beat)
            start += units
            first = False

    cursor = 0
    for n in notes:
        emit(cursor, n.start, None)
        emit(n.start, n.end, n)
        cursor = n.end
    emit(cursor, total, None)
    guitarpro.write(song, str(output_path))


def _write_simple_musicxml(
    midi_path: Path,
    output_path: Path,
    *,
    tempo: int | None = None,
    job_id: str | None = None,
) -> None:
    """Export MIDI timing without minimum-duration or maximum-duration clamps.

    The legacy tempo argument is accepted for compatibility; MIDI tempo metadata
    remains authoritative so a caller cannot accidentally rescale note timing.
    """
    bpm, notes = _load_score(midi_path)
    _write_musicxml_score(notes, output_path, bpm, job_id)


def _write_musicxml_score(
    notes: list[_ScoreNote], output_path: Path, bpm: float, job_id: str | None,
) -> None:
    from fractions import Fraction
    from music21 import clef, instrument, meter, note, stream, tempo

    score = stream.Score()
    part = stream.Part()
    part.insert(0, tempo.MetronomeMark(number=bpm))
    part.insert(0, meter.TimeSignature("4/4"))
    part.insert(0, instrument.ElectricBass())
    part.insert(0, clef.BassClef())
    total = max(_BAR, ((max((n.end for n in notes), default=0) + _BAR - 1) // _BAR) * _BAR)
    # XML requires explicit voices for overlapping events; otherwise its writer
    # serializes them sequentially despite the offsets in the source stream.
    voices: list[tuple[stream.Voice, int]] = []
    for n in notes:
        index = next((i for i, (_, end) in enumerate(voices) if end <= n.start), len(voices))
        if index == len(voices):
            voices.append((stream.Voice(id=index + 1), 0))
        voice, _ = voices[index]
        event = note.Note(n.pitch, quarterLength=Fraction(n.end - n.start, _GRID))
        event.volume.velocity = n.velocity
        voice.insert(Fraction(n.start, _GRID), event)
        voices[index] = (voice, n.end)
    if not voices:
        voices.append((stream.Voice(id=1), 0))
    for voice, _ in voices:
        voice.makeRests(refStreamOrTimeRange=(0, Fraction(total, _GRID)), fillGaps=True, inPlace=True)
        part.insert(0, voice)
    part.makeMeasures(inPlace=True)
    part.makeNotation(inPlace=True)
    score.append(part)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    score.write("musicxml", str(output_path))
    logger.info("musicxml_simple_written", job_id=job_id, musicxml=str(output_path), notes=len(notes), bpm=bpm)


def _quantize_quarter(value: float) -> float:
    """Round a positive duration to the shared 1/48-quarter-note grid."""
    return max(1, round(value * _GRID)) / _GRID
