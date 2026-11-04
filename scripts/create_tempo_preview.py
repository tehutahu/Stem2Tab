"""Create paired synthetic WAV/MIDI for local BPM UI review, without models."""
from __future__ import annotations

import argparse
from array import array
import hashlib
import json
import math
from pathlib import Path
import random
import sys
import wave

from create_system_preview import write_midi


def main() -> None:
    """Refuse an existing destination so local recordings/results stay intact."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    root = args.output_dir.resolve()
    root.mkdir(parents=True, exist_ok=False)
    rate, seconds, bpm, phase = 16000, 24, 120, .25
    samples = array("h", [0]) * (rate * seconds)
    rng = random.Random(20261104)
    notes = []
    for index in range(48):
        start = phase + index * .5
        pitch = (40, 43, 45, 47)[index % 4]
        notes.append((start, start + .2, pitch))
        first = round(start * rate)
        frequency = 440 * 2 ** ((pitch - 69) / 12)
        accent = 1 if index % 4 == 0 else .65
        for offset in range(round(.2 * rate)):
            t = offset / rate
            envelope = min(1, t / .004) * math.exp(-t / .06)
            value = .18 * math.sin(2 * math.pi * frequency * t)
            value += .22 * accent * rng.uniform(-1, 1) * math.exp(-t / .012)
            samples[first + offset] = round(32767 * envelope * value)
    if sys.byteorder != "little":
        samples.byteswap()
    audio = root / "synthetic-bpm-120.wav"
    with wave.open(str(audio), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes(samples.tobytes())
    midi = root / "synthetic-bpm-120.mid"
    write_midi(midi, notes)
    metadata = {
        "synthetic_only": True, "model_calls": 0,
        "seconds": seconds, "sample_rate": rate, "notes": len(notes),
        "musical_clock_example": {"bpm": bpm, "numerator": 4, "denominator": 4, "origin_seconds": phase},
        "storage_clock_bpm": 60,
        "sha256": {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in (audio, midi)},
        "instructions": "Import MIDI at /midi-trial, choose local MIDI and bass4. Explicitly select the WAV for audio BPM suggestions. Choose a candidate; meter/origin remain manual. Standalone upload correspondence remains unverified by the server.",
        "scope": "Generated pulse example only, not evidence of real-song tempo accuracy.",
    }
    (root / "README.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    print(f"Synthetic BPM review files: {root}")


if __name__ == "__main__":
    main()
