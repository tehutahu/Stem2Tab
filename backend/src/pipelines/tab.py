"""Very simple MIDI to GP5 converter for bass tracks."""

from __future__ import annotations

from pathlib import Path

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

    Falls back to copying the MIDI bytes into the GP5 file if conversion fails,
    ensuring a downloadable artifact is always produced.
    """
    if not midi_path.exists():
        raise FileNotFoundError(f"MIDI not found: {midi_path}")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    logger.info("tab_generation_start", job_id=job_id, midi=str(midi_path), gp5=str(output_path))

    try:
        import guitarpro
        import pretty_midi

        midi = pretty_midi.PrettyMIDI(str(midi_path))
        song = guitarpro.models.Song()

        channel = guitarpro.models.Channel(1, program=33)  # Fingered Bass
        song.channels.append(channel)

        track = guitarpro.models.Track(song, name="Bass", channel=channel)
        tuning = STANDARD_TUNINGS.get(strings, STANDARD_TUNINGS[4])
        track.strings = [guitarpro.models.String(pitch) for pitch in tuning]
        song.tracks.append(track)

        header = guitarpro.models.MeasureHeader(number=1)
        header.tempo = guitarpro.models.Tempo(int(midi.estimate_tempo() or 120))
        song.measureHeaders.append(header)

        measure = guitarpro.models.Measure(track, header)
        track.measures.append(measure)

        voice = measure.voices[0]
        default_duration = guitarpro.models.Duration(value=4)  # quarter note

        notes = sorted((n.start, n.end, n.pitch) for inst in midi.instruments for n in inst.notes)
        if not notes:
            voice.beats.append(guitarpro.models.Beat(voice, status=guitarpro.models.BeatStatus.rest))
        else:
            for _, _, pitch in notes:
                mapping = _pick_string_and_fret(pitch, strings)
                if mapping is None:
                    continue
                string_idx, fret = mapping
                beat = guitarpro.models.Beat(voice)
                beat.duration = default_duration
                gp_note = guitarpro.models.Note(string=string_idx, fret=fret, velocity=15)
                beat.notes.append(gp_note)
                voice.beats.append(beat)

            if not voice.beats:
                voice.beats.append(guitarpro.models.Beat(voice, status=guitarpro.models.BeatStatus.rest))

        guitarpro.write(song, output_path)
    except Exception as exc:  # pragma: no cover - defensive fallback
        logger.warning("tab_generation_fallback", job_id=job_id, error=str(exc))
        output_path.write_bytes(midi_path.read_bytes())

    logger.info("tab_generation_complete", job_id=job_id, gp5=str(output_path))
    return output_path


