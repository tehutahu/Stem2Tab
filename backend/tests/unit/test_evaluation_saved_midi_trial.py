"""Synthetic local-MIDI CLI/artifact checks; no models or private fixtures."""
from collections import Counter
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import guitarpro
import mido
import pytest

from src.evaluation.saved_midi_trial import main, run_saved_midi_trial
from src.pipelines.bass_output import BassOutputConfig


def make_midi(path: Path, events: list[tuple[int, int, int]], *, channel: int = 0) -> None:
    midi = mido.MidiFile(type=1, ticks_per_beat=480)
    midi.tracks.append(mido.MidiTrack([mido.MetaMessage("set_tempo", tempo=500000),
                                       mido.MetaMessage("time_signature", numerator=4, denominator=4)]))
    track = mido.MidiTrack([mido.Message("program_change", channel=channel, program=33)])
    messages = []
    for start, end, pitch in events:
        messages.extend([(start, 1, mido.Message("note_on", channel=channel, note=pitch, velocity=96)),
                         (end, 0, mido.Message("note_off", channel=channel, note=pitch, velocity=0))])
    previous = 0
    for tick, _, message in sorted(messages, key=lambda row: row[:2]):
        message.time = tick - previous
        track.append(message)
        previous = tick
    midi.tracks.append(track)
    midi.save(path)


def xml_event_ticks(path: Path, ppq: int) -> Counter:
    """Independently interpret duration/divisions, backup and ties exactly."""
    root = ET.parse(path).getroot()
    output = []
    for part in root.findall("part"):
        base, divisions = Fraction(0), 1
        active = {}
        for measure in part.findall("measure"):
            divisions = int(measure.findtext("attributes/divisions", divisions))
            cursor = maximum = Fraction(0)
            for item in measure:
                if item.tag in {"backup", "forward"}:
                    duration = Fraction(int(item.findtext("duration")), divisions)
                    cursor += duration * (-1 if item.tag == "backup" else 1)
                    maximum = max(maximum, cursor)
                elif item.tag == "note":
                    duration = Fraction(int(item.findtext("duration")), divisions)
                    start, end = base + cursor, base + cursor + duration
                    pitch_node = item.find("pitch")
                    if pitch_node is not None:
                        semitone = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}[pitch_node.findtext("step")]
                        pitch = semitone + int(pitch_node.findtext("alter", "0")) + 12 * (int(pitch_node.findtext("octave")) + 1)
                        key = (item.findtext("voice", "1"), pitch)
                        ties = {tie.attrib["type"] for tie in item.findall("tie")}
                        if "stop" in ties:
                            first, previous_end = active.pop(key)
                            assert previous_end == start
                        else:
                            first = start
                        if "start" in ties:
                            active[key] = first, end
                        else:
                            output.append((first * ppq, end * ppq, pitch))
                    cursor += duration
                    maximum = max(maximum, cursor)
            base += maximum
        assert not active
    return Counter(output)


def cli_args(midi: Path, out: Path, profile: str = "bass4") -> list[str]:
    return ["--midi", str(midi), "--output-dir", str(out), "--source-kind", "muscriptor-small",
            "--time-basis", "storage-clock", "--bass-profile", profile]


def test_cli_preserves_low_notes_ticks_repeats_and_input_bytes(tmp_path: Path, capsys) -> None:
    events = [(0, 120, 28), (300, 301, 33), (301, 600, 40), (400, 650, 45),
              (1915, 1925, 28), (1925, 1935, 28)]
    midi, out = tmp_path / "source.mid", tmp_path / "output"
    make_midi(midi, events)
    original = midi.read_bytes()
    assert main(cli_args(midi, out) + ["--gp5-preview"]) == 0
    report = json.loads((out / "trial.json").read_text())
    assert midi.read_bytes() == (out / "performance.mid").read_bytes() == original
    assert report["input_sha256"] == hashlib.sha256(original).hexdigest()
    assert report["source_unchanged"] and report["model_calls"] == 0
    assert report["input_note_count"] == report["musicxml"]["note_count"] == 6
    assert report["musical_tempo_and_meter_verified"] is False
    assert "noncommercial" in report["usage_scope"] and report["source_weights_bundled"] is False
    assert xml_event_ticks(out / "performance-clock.musicxml", 480) == Counter(events)
    canonical = json.loads((out / "canonical.json").read_text())
    assert Counter((round(n["start"] * 960), round(n["end"] * 960), n["midi"]) for n in canonical["notes"]) == Counter(events)
    gp5 = next((out / "gp5-preview").glob("*.gp5"))
    song = guitarpro.parse(str(gp5))
    notes = [note for track in song.tracks for measure in track.measures for voice in measure.voices for beat in voice.beats for note in beat.notes]
    assert [n.realValue for n in notes] == [e[2] for e in events]
    assert [s.value for s in song.tracks[0].strings] == [43, 38, 33, 28]
    assert len(song.tracks[0].measures) == 2
    warnings = capsys.readouterr().err
    assert "STORAGE_CLOCK_ONLY" in warnings and "GP5_PREVIEW_ONLY" in warnings


def test_out_of_range_blocks_gp5_but_retains_every_event(tmp_path: Path, capsys) -> None:
    midi, out = tmp_path / "low_b.mid", tmp_path / "bass4"
    events = [(0, 120, 23), (240, 360, 28)]
    make_midi(midi, events)
    assert main(cli_args(midi, out) + ["--gp5-preview"]) == 2
    report = json.loads((out / "trial.json").read_text())
    assert report["status"] == "gp5_blocked_out_of_range"
    assert report["gp5"]["status"] == "unmappable"
    assert not list(out.rglob("*.gp5"))
    assert len(json.loads((out / "canonical.json").read_text())["notes"]) == 2
    assert xml_event_ticks(out / "performance-clock.musicxml", 480) == Counter(events)
    assert "GP5_BLOCKED_OUT_OF_RANGE" in capsys.readouterr().err
    assert main(cli_args(midi, tmp_path / "bass5", "bass5") + ["--gp5-preview"]) == 0


def test_no_preview_is_default_and_legacy_tuning_stays_unchanged(tmp_path: Path) -> None:
    from src.pipelines.tab import STANDARD_TUNINGS
    midi, out = tmp_path / "single.mid", tmp_path / "no-preview"
    make_midi(midi, [(480, 481, 28)])
    assert main(cli_args(midi, out)) == 0
    report = json.loads((out / "trial.json").read_text())
    assert report["gp5"] is None and not (out / "gp5-preview").exists()
    assert STANDARD_TUNINGS[4] == [40, 45, 50, 55]
    assert xml_event_ticks(out / "performance-clock.musicxml", 480) == Counter([(480, 481, 28)])


def test_no_overwrite_and_failed_export_are_visible(tmp_path: Path, monkeypatch) -> None:
    midi, out = tmp_path / "in.mid", tmp_path / "existing"
    make_midi(midi, [(0, 1, 28)])
    out.mkdir()
    marker = out / "keep.txt"
    marker.write_text("keep")
    assert main(cli_args(midi, out)) == 1
    assert marker.read_text() == "keep" and len(list(out.iterdir())) == 1
    def fail(*args, **kwargs):
        raise RuntimeError("synthetic exporter failure")
    monkeypatch.setattr("src.evaluation.saved_midi_trial.write_fidelity_musicxml", fail)
    broken = tmp_path / "broken"
    assert main(cli_args(midi, broken)) == 1
    report = json.loads((broken / "trial.json").read_text())
    assert report["status"] == "failed" and "synthetic exporter failure" in report["error"]
    assert (broken / "performance.mid").read_bytes() == midi.read_bytes()


@pytest.mark.parametrize("kind", ["overlap", "unclosed", "drum", "mixed", "tempo", "meter", "zero"])
def test_unsupported_inputs_fail_before_creating_outputs(tmp_path: Path, kind: str) -> None:
    midi, out = tmp_path / "unsupported.mid", tmp_path / "out"
    make_midi(midi, [(0, 100, 28), (50, 150, 28)] if kind == "overlap" else [(0, 100, 28)],
              channel=9 if kind == "drum" else 0)
    raw = mido.MidiFile(midi)
    if kind == "unclosed":
        raw.tracks[1] = mido.MidiTrack(m for m in raw.tracks[1] if m.type != "note_off")
    elif kind == "mixed":
        raw.tracks.append(mido.MidiTrack([mido.Message("program_change", channel=1, program=32),
                                          mido.Message("note_on", channel=1, note=33, velocity=90),
                                          mido.Message("note_off", channel=1, note=33, time=100)]))
    elif kind == "tempo":
        raw.tracks[0].insert(1, mido.MetaMessage("set_tempo", tempo=600000, time=50))
    elif kind == "meter":
        raw.tracks[0][1] = mido.MetaMessage("time_signature", numerator=3, denominator=4)
    elif kind == "zero":
        raw.tracks[1] = mido.MidiTrack([mido.Message("note_on", note=28, velocity=90), mido.Message("note_off", note=28)])
    raw.save(midi)
    original = midi.read_bytes()
    assert main(cli_args(midi, out) + ["--gp5-preview"]) == 1
    assert not out.exists() and midi.read_bytes() == original


def test_empty_and_custom_config_are_supported(tmp_path: Path) -> None:
    midi, out = tmp_path / "empty.mid", tmp_path / "custom"
    make_midi(midi, [])
    config = BassOutputConfig(profile="custom", strings=4, tuning_midi=(26, 33, 38, 43), max_fret=20)
    result = run_saved_midi_trial(midi, out, config=config, source_kind="local-midi",
                                  time_basis="storage-clock", gp5_preview=True)
    assert result["status"] == "completed" and result["input_note_count"] == 0
    assert xml_event_ticks(out / "performance-clock.musicxml", 480) == Counter()


def test_selection_and_time_basis_are_required(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        main(["--midi", str(tmp_path / "in.mid"), "--output-dir", str(tmp_path / "out")])
