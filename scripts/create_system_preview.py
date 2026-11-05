"""Create fresh synthetic MIDI files for local UI review without models or audio."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import mido


def write_midi(path: Path, notes: list[tuple[float, float, int]]) -> None:
    """Write supplied events on a 60 BPM, 100-microsecond storage clock."""
    midi = mido.MidiFile(ticks_per_beat=10000)
    track = mido.MidiTrack()
    midi.tracks.append(track)
    track.extend([
        mido.MetaMessage("set_tempo", tempo=1000000),
        mido.MetaMessage("time_signature", numerator=4, denominator=4),
        mido.Message("program_change", program=33),
    ])
    events = []
    for start, end, pitch in notes:
        events.extend([
            (round(start * 10000), 1, mido.Message("note_on", note=pitch, velocity=90)),
            (round(end * 10000), 0, mido.Message("note_off", note=pitch, velocity=0)),
        ])
    previous = 0
    for tick, _, message in sorted(events, key=lambda event: event[:2]):
        track.append(message.copy(time=tick - previous))
        previous = tick
    track.append(mido.MetaMessage("end_of_track", time=0))
    midi.save(path)


def main() -> None:
    """Refuse existing directories so review fixtures never overwrite local work."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    write_midi(output / "synthetic-pulse-120.mid", [
        (.25, .5, 40), (.5, 1, 43), (1.5, 2.5, 45), (3, 5.5, 47),
        (6, 6.25, 40), (6.25, 6.5, 40), (7, 7.5, 43),
    ])
    write_midi(output / "synthetic-dense.mid", [
        (index * .0625, index * .0625 + .025, (40, 43, 45, 47)[index % 4])
        for index in range(64)
    ])
    description = {
        "synthetic_only": True,
        "inference": "not run",
        "storage_clock_bpm": 60,
        "pulse_fixture": {
            "file": "synthetic-pulse-120.mid",
            "musical_clock_example": {"bpm": 120, "numerator": 4, "denominator": 4, "origin_seconds": .5},
        },
        "instructions": "Open /midi-trial, choose local MIDI and 4-string bass. Import either file. No audio or model is needed.",
        "limits": "These fixtures verify UI/system behavior, not song transcription accuracy or inferred musical tempo.",
    }
    (output / "README.json").write_text(json.dumps(description, indent=2) + "\n", encoding="utf-8")
    print(f"Synthetic UI review files: {output}")


if __name__ == "__main__":
    main()
