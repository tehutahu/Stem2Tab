"""CPU-only notation regressions using synthesized MIDI, never reference audio."""
from __future__ import annotations

from math import ceil
from pathlib import Path

import guitarpro
import pretty_midi
import pytest
from music21 import converter, stream, tempo as m21tempo

from src.pipelines.tab import _pick_string_and_fret, _write_simple_musicxml, midi_to_gp5

# (sounding MIDI pitch, onset in quarter notes, duration in quarter notes)
Event = tuple[int, float, float]

BASS_TUNINGS = [
    (4, [28, 33, 38, 43]),
    (5, [23, 28, 33, 38, 43]),
    (6, [23, 28, 33, 38, 43, 48]),
]


def _write_midi(tmp_path: Path, events: list[Event], *, bpm: float = 120) -> Path:
    midi = pretty_midi.PrettyMIDI(initial_tempo=bpm, resolution=960)
    bass = pretty_midi.Instrument(program=33)
    for pitch, onset, duration in events:
        bass.notes.append(
            pretty_midi.Note(
                velocity=80, pitch=pitch,
                start=onset * 60 / bpm, end=(onset + duration) * 60 / bpm
            )
        )
    midi.instruments.append(bass)
    path = tmp_path / "synthetic.mid"
    midi.write(str(path))
    return path


def _gp_events(path: Path) -> tuple[list[Event], guitarpro.models.Song]:
    song = guitarpro.parse(str(path))
    assert len(song.tracks) == 1, "A conversion must not retain the default guitar track"
    track = song.tracks[0]
    origin = song.measureHeaders[0].start
    events: list[list[float | int]] = []
    previous_by_string: dict[int, int] = {}
    for measure in track.measures:
        voice = measure.voices[0]
        assert sum(beat.duration.time for beat in voice.beats) == measure.header.length
        for beat in voice.beats:
            onset = (beat.start - origin) / 960
            duration = beat.duration.time / 960
            for note in beat.notes:
                if note.type == guitarpro.models.NoteType.tie:
                    previous = events[previous_by_string[note.string]]
                    assert previous[0] == note.realValue
                    assert float(previous[1]) + float(previous[2]) == pytest.approx(onset)
                    previous[2] += duration
                else:
                    previous_by_string[note.string] = len(events)
                    events.append([note.realValue, onset, duration])
    return [(int(pitch), float(onset), float(duration)) for pitch, onset, duration in events], song


def _xml_events(path: Path) -> tuple[list[Event], stream.Part]:
    score = converter.parse(str(path))
    assert len(score.parts) == 1
    part = score.parts[0]
    # A tied note across a bar is one logical source note.
    merged = part.stripTies(inPlace=False)
    events = [
        (int(pitch.midi), float(n.offset), float(n.quarterLength))
        for n in merged.flatten().notes
        for pitch in n.pitches
    ]
    return events, part


def _assert_events(actual: list[Event], expected: list[Event]) -> None:
    assert len(actual) == len(expected), "Source notes must survive notation export"
    for result, source in zip(actual, expected):
        assert result[0] == source[0], "Compare sounding pitches, not octave-transposed labels"
        assert result[1:] == pytest.approx(source[1:], abs=1e-7)


@pytest.mark.parametrize("strings,pitches", BASS_TUNINGS)
def test_bass_open_strings_use_sounding_midi(strings: int, pitches: list[int]) -> None:
    for index, pitch in enumerate(pitches):
        assert _pick_string_and_fret(pitch, strings) == (strings - index, 0)


@pytest.mark.parametrize("strings,pitches", BASS_TUNINGS)
def test_gp5_preserves_open_bass_pitches(tmp_path: Path, strings: int, pitches: list[int]) -> None:
    expected = [(pitch, float(index), 0.5) for index, pitch in enumerate(pitches)]
    midi = _write_midi(tmp_path, expected)
    output = tmp_path / "bass.gp5"
    assert midi_to_gp5(midi, output, strings=strings) == output
    actual, song = _gp_events(output)
    assert [s.value for s in song.tracks[0].strings] == list(reversed(pitches))
    _assert_events(actual, expected)


# Include leading silence, eighth/sixteenth notes, an internal rest, multiple bars,
# a sustained note crossing a bar, and a final note after the tie.
RHYTHM_EVENTS: list[Event] = [
    (43, 0.5, 0.5),
    (45, 1.0, 0.25),
    (47, 1.5, 0.25),
    (48, 3.75, 1.0),
    (50, 5.0, 0.5),
]


RHYTHM_CASES = [
    pytest.param(RHYTHM_EVENTS, id="rests-and-ties"),
    pytest.param(
        [(28, 0.125, 0.125), (33, 0.375, 0.125), (38, 1.0, 0.25)],
        id="32nd-notes-and-rests",
    ),
    pytest.param([(28, 0.0, 9.0), (33, 9.5, 0.5)], id="long-note"),
    pytest.param([], id="empty"),
    pytest.param([(28, 0.0, 1.0)], id="single-note"),
]


@pytest.mark.parametrize("events", RHYTHM_CASES)
def test_gp5_preserves_rhythm_and_complete_measures(
    tmp_path: Path, events: list[Event]
) -> None:
    midi = _write_midi(tmp_path, events)
    output = tmp_path / "rhythm.gp5"
    assert midi_to_gp5(midi, output) == output
    actual, song = _gp_events(output)
    _assert_events(actual, events)
    measure_count = max(1, ceil(max((onset + duration for _, onset, duration in events), default=0) / 4))
    assert len(song.measureHeaders) == measure_count
    assert len(song.tracks[0].measures) == measure_count
    assert [h.start - song.measureHeaders[0].start for h in song.measureHeaders] == [
        index * 4 * 960 for index in range(measure_count)
    ]
    assert song.tempo == 120, "Use the MIDI tempo map rather than inferring tempo from note spacing"


@pytest.mark.parametrize("events", RHYTHM_CASES)
def test_musicxml_preserves_pitch_onset_and_duration(tmp_path: Path, events: list[Event]) -> None:
    midi = _write_midi(tmp_path, events)
    output = tmp_path / "rhythm.musicxml"
    _write_simple_musicxml(midi, output, tempo=120)
    actual, part = _xml_events(output)
    _assert_events(actual, events)
    for measure in part.getElementsByClass(stream.Measure):
        assert float(measure.duration.quarterLength) <= 4.0
    measures = list(part.getElementsByClass(stream.Measure))
    assert [float(m.offset) for m in measures] == [4.0 * i for i in range(len(measures))]


def test_gp5_companion_musicxml_uses_same_tempo_and_rhythm(tmp_path: Path) -> None:
    midi = _write_midi(tmp_path, RHYTHM_EVENTS)
    output = tmp_path / "companion.gp5"
    assert midi_to_gp5(midi, output) == output
    actual, _ = _xml_events(output.with_suffix(".musicxml"))
    _assert_events(actual, RHYTHM_EVENTS)


@pytest.mark.parametrize(
    "events",
    [
        pytest.param([(28, 0.0, 2.0), (33, 1.0, 3.0)], id="overlap"),
        pytest.param([(28, 0.0, 1.0), (33, 0.0, 1.0)], id="chord"),
        pytest.param([(27, 0.0, 1.0), (68, 1.0, 1.0)], id="outside-fret-range"),
    ],
)
def test_musicxml_fallback_preserves_unrepresentable_gp5_notes(
    tmp_path: Path, events: list[Event]
) -> None:
    midi = _write_midi(tmp_path, events)
    output = tmp_path / "unsupported.gp5"
    actual_path = midi_to_gp5(midi, output)
    assert actual_path == output.with_suffix(".musicxml")
    assert actual_path.exists()
    assert not output.exists()
    actual, _ = _xml_events(actual_path)
    _assert_events(sorted(actual, key=lambda n: (n[1], n[0])), events)


def test_fractional_tempo_falls_back_without_rescaling_notes(tmp_path: Path) -> None:
    expected = [(28, 0.5, 0.5), (33, 1.5, 1.0)]
    midi = _write_midi(tmp_path, expected, bpm=120.5)
    output = tmp_path / "fractional.gp5"
    actual_path = midi_to_gp5(midi, output)
    assert actual_path == output.with_suffix(".musicxml")
    actual, part = _xml_events(actual_path)
    _assert_events(actual, expected)
    marks = list(part.recurse().getElementsByClass(m21tempo.MetronomeMark))
    assert len(marks) == 1
    assert marks[0].number == pytest.approx(120.5, abs=1e-3)


@pytest.mark.parametrize("unsupported", ["tempo-change", "3/4"])
def test_unsupported_tempo_map_or_meter_fails_explicitly(
    tmp_path: Path, unsupported: str
) -> None:
    import mido

    midi = _write_midi(tmp_path, [(28, 0.0, 4.0)])
    if unsupported == "tempo-change":
        raw = mido.MidiFile(str(midi))
        raw.tracks[0].insert(1, mido.MetaMessage("set_tempo", tempo=600000, time=960))
        raw.save(str(midi))
        message = "Tempo changes"
    else:
        raw_midi = pretty_midi.PrettyMIDI(str(midi))
        raw_midi.time_signature_changes = [pretty_midi.TimeSignature(3, 4, 0)]
        raw_midi.write(str(midi))
        message = "4/4"
    output = tmp_path / "unsupported.gp5"
    with pytest.raises(ValueError, match=message):
        midi_to_gp5(midi, output)
    assert not output.exists()
    assert not output.with_suffix(".musicxml").exists()


def test_musicxml_write_failure_propagates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    midi = _write_midi(tmp_path, [(28, 0.0, 1.0)])

    def fail_write(*args: object, **kwargs: object) -> None:
        raise OSError("synthetic XML disk failure")

    monkeypatch.setattr(stream.Score, "write", fail_write)
    with pytest.raises(OSError, match="synthetic XML disk failure"):
        midi_to_gp5(midi, tmp_path / "failed.gp5")
