"""Very simple MIDI to GP5 converter for bass tracks."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Tuple

import structlog

logger = structlog.get_logger()


# MIDI numbers for standard bass tunings (4/5/6 strings)
STANDARD_TUNINGS = {
    4: [40, 45, 50, 55],  # E1 A1 D2 G2
    5: [35, 40, 45, 50, 55],  # B0 E1 A1 D2 G2
    6: [28, 33, 38, 43, 47, 52],  # E0 A0 D1 G1 B1 E2 (approx)
}


def _pick_string_and_fret(midi_pitch: int, strings: int) -> tuple[int, int] | None:
    """Naively pick a string/fret for a given MIDI pitch."""
    tuning = STANDARD_TUNINGS.get(strings, STANDARD_TUNINGS[4])
    # Strings are numbered from 1 (highest) in PyGuitarPro, so reverse for bass perspective.
    for string_idx, open_pitch in enumerate(reversed(tuning), start=1):
        fret = midi_pitch - open_pitch
        if 0 <= fret <= 24:
            return string_idx, fret
    return None


def midi_to_gp5(
    midi_path: Path,
    output_path: Path,
    *,
    strings: int = 4,
    job_id: str | None = None,
) -> Path:
    """Convert a MIDI file to a minimal GP5 bass tab.

    On failure, falls back to emitting a MusicXML file so that AlphaTab can still import the result.
    """
    if not midi_path.exists():
        raise FileNotFoundError(f"MIDI not found: {midi_path}")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    logger.info("tab_generation_start", job_id=job_id, midi=str(midi_path), gp5=str(output_path))

    try:
        import guitarpro  # type: ignore
        import pretty_midi  # type: ignore
        midi = pretty_midi.PrettyMIDI(str(midi_path))
        tempo = int(midi.estimate_tempo() or 120)
        song = guitarpro.models.Song(tempo=tempo, tempoName="Bass")
        time_signature = guitarpro.models.TimeSignature(
            numerator=4, denominator=guitarpro.models.Duration(value=4)
        )
        header = guitarpro.models.MeasureHeader(number=1, start=0, timeSignature=time_signature)
        song.measureHeaders.append(header)

        channel = guitarpro.models.MidiChannel(instrument=33)  # Fingered Bass
        track = guitarpro.models.Track(song, name="Bass", channel=channel)
        tuning = STANDARD_TUNINGS.get(strings, STANDARD_TUNINGS[4])
        track.strings = [
            guitarpro.models.GuitarString(number=idx + 1, value=pitch)
            for idx, pitch in enumerate(reversed(tuning))
        ]
        song.tracks.append(track)

        measure = guitarpro.models.Measure(track, header, clef=guitarpro.models.MeasureClef.bass)
        track.measures.append(measure)

        voice = measure.voices[0]
        default_duration = guitarpro.models.Duration(value=4)  # quarter note

        notes = sorted((n.start, n.end, n.pitch) for inst in midi.instruments for n in inst.notes)
        if not notes:
            rest = guitarpro.models.Beat(
                voice,
                status=guitarpro.models.BeatStatus.rest,
                duration=default_duration,
            )
            voice.beats.append(rest)
        else:
            for _, _, pitch in notes:
                mapping = _pick_string_and_fret(pitch, strings)
                if mapping is None:
                    continue
                string_idx, fret = mapping
                beat = guitarpro.models.Beat(
                    voice,
                    status=guitarpro.models.BeatStatus.normal,
                    duration=default_duration,
                )
                gp_note = guitarpro.models.Note(
                    beat,
                    value=fret,
                    string=string_idx,
                    velocity=80,
                    type=guitarpro.models.NoteType.normal,
                )
                beat.notes.append(gp_note)
                voice.beats.append(beat)

            if not voice.beats:
                rest = guitarpro.models.Beat(
                    voice,
                    status=guitarpro.models.BeatStatus.rest,
                    duration=default_duration,
                )
                voice.beats.append(rest)

        guitarpro.write(song, output_path)
        logger.info("tab_generation_complete", job_id=job_id, gp5=str(output_path))

        _write_simple_musicxml(midi_path, output_path.with_suffix(".musicxml"), tempo=tempo, job_id=job_id)

        return output_path
    except Exception as exc:  # pragma: no cover - defensive fallback
        logger.warning("tab_generation_fallback", job_id=job_id, error=str(exc))

        fallback_path = output_path.with_suffix(".musicxml")
        _write_simple_musicxml(midi_path, fallback_path, tempo=None, job_id=job_id)
        return fallback_path


def _write_simple_musicxml(
    midi_path: Path,
    output_path: Path,
    *,
    tempo: int | None = None,
    job_id: str | None = None,
) -> None:
    """Emit a single-voice MusicXML that AlphaTab can safely render while keeping timing."""
    try:
        import pretty_midi  # type: ignore
        from music21 import instrument, meter, note, stream, tempo as m21tempo  # type: ignore
    except Exception as exc:  # pragma: no cover - dependency guard
        logger.warning("musicxml_dependencies_missing", job_id=job_id, error=str(exc))
        return

    if not midi_path.exists():
        logger.warning("musicxml_midi_missing", job_id=job_id, midi=str(midi_path))
        return

    midi = pretty_midi.PrettyMIDI(str(midi_path))
    notes_raw: list[Tuple[float, float, int]] = []
    for inst in midi.instruments:
        for n in inst.notes:
            if n.end > n.start:
                notes_raw.append((n.start, n.end, n.pitch))
    notes_raw.sort(key=lambda x: x[0])

    if tempo is not None and tempo > 0:
        bpm = tempo
    else:
        tempo_times, tempi = midi.get_tempo_changes()
        bpm = float(tempi[0]) if len(tempi) > 0 and tempi[0] > 0 else float(midi.estimate_tempo() or 120)
    if bpm <= 0:
        bpm = 120.0

    score = stream.Score()
    part = stream.Part()
    part.insert(0, m21tempo.MetronomeMark(number=bpm))
    part.insert(0, meter.TimeSignature("4/4"))
    part.insert(0, instrument.ElectricBass())

    if not notes_raw:
        part.append(note.Rest(quarterLength=4.0))
    else:
        seconds_to_quarter = bpm / 60.0
        current_time = 0.0

        for start, end, pitch in notes_raw:
            # fill rest if there is a gap
            if start > current_time:
                rest_q = max(0.25, (start - current_time) * seconds_to_quarter)
                rest_q = _quantize_quarter(rest_q)
                part.append(note.Rest(quarterLength=rest_q))
                current_time = start

            duration_q = max(0.25, (end - start) * seconds_to_quarter)
            # clamp extremely long durations to avoid rendering issues
            duration_q = min(duration_q, 8.0)
            duration_q = _quantize_quarter(duration_q)

            m_note = note.Note()
            m_note.pitch.midi = pitch
            m_note.quarterLength = duration_q
            part.append(m_note)

            current_time = start + (duration_q / seconds_to_quarter)

    part.makeMeasures(inPlace=True)
    part.makeNotation(inPlace=True)
    score.append(part)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    score.write("musicxml", str(output_path))
    logger.info("musicxml_simple_written", job_id=job_id, musicxml=str(output_path), notes=len(notes_raw), bpm=bpm)


def _quantize_quarter(value: float) -> float:
    """Quantize quarterLength to standard divisions (1/48 step) to keep triplets and finer timing expressible."""
    step = 1 / 48  # ~32nd-triplet resolution
    quantized = round(value / step) * step
    if quantized <= 0:
        quantized = step
    return quantized


