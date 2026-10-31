"""Prepare a bounded local listening packet from an existing completed trial."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
from uuid import UUID

import numpy as np
import soundfile as sf

from src.evaluation.saved_midi_trial import _load_unambiguous_midi


def sha256(path: Path) -> str:
    """Hash a bounded local artifact."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare_listening_packet(job_dir: Path, output: Path, *, label: str) -> dict:
    """Retain source audio and synthesize saved notes without models or normalization.

    Only completed, hash-linked opt-in trials are accepted. All outputs are fresh;
    the source seconds are common to each track, including leading/trailing silence.
    """
    job_dir, output = Path(job_dir), Path(output)
    identifier = UUID(output.name)
    if identifier.version != 4 or str(identifier) != output.name:
        raise ValueError("Use a canonical UUID4 for the new packet")
    state = json.loads((job_dir / "status.json").read_text())
    done = json.loads((job_dir / "completion.json").read_text())
    request = json.loads((job_dir / "request.json").read_text())
    if state["status"] != "completed":
        raise ValueError("Only a completed trial can be reviewed")
    original = next(job_dir.glob("input.*"))
    bass = job_dir / "separate/bass.wav"
    midi = job_dir / "transcribe/performance.mid"
    sources = [("original", original, done["input_sha256"]),
               ("bass", bass, done["bass_sha256"]),
               ("midi", midi, done["raw_midi_sha256"])]
    for _, path, expected in sources:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 20 * 1024**2 or sha256(path) != expected:
            raise ValueError("Source artifact missing, changed, linked or too large")
    if request["input_sha256"] != done["input_sha256"]:
        raise ValueError("Source identity mismatch")
    infos = [sf.info(p) for p in (original, bass)]
    duration = infos[0].frames / infos[0].samplerate
    if not 0 < duration <= 10.01 or any(i.channels > 2 or i.frames <= 0 or i.samplerate > 96000 for i in infos):
        raise ValueError("Only short, mono/stereo local clips are supported")
    if abs(infos[1].frames / infos[1].samplerate - duration) > 1 / min(i.samplerate for i in infos):
        raise ValueError("Original and bass durations do not align")
    _, notes = _load_unambiguous_midi(midi.read_bytes())
    if len(notes) > 500 or any(n["start"] < 0 or n["end"] > duration for n in notes):
        raise ValueError("Saved notes exceed the shared audio interval; no cropping")
    sr = 44100
    samples = np.zeros(round(duration * sr), dtype=np.float64)
    rounding = 0.0
    for note in notes:
        a, b = round(note["start"] * sr), round(note["end"] * sr)
        if b <= a:
            raise ValueError("A note is shorter than the synthesis sample resolution")
        t = np.arange(b - a) / sr
        f = 440 * 2 ** ((note["midi"] - 69) / 12)
        tone = (np.sin(2*np.pi*f*t) + .25*np.sin(4*np.pi*f*t) + .1*np.sin(6*np.pi*f*t)) / 1.35
        fade = min(round(.002 * sr), len(tone) // 2)
        if fade:
            tone[:fade] *= np.linspace(0, 1, fade)
            tone[-fade:] *= np.linspace(1, 0, fade)
        samples[a:b] += .12 * tone
        rounding = max(rounding, abs(a/sr-note["start"]), abs(b/sr-note["end"]))
    if not np.isfinite(samples).all() or np.max(np.abs(samples), initial=0) >= 1:
        raise ValueError("Synthesis would clip; no automatic normalization")
    output.mkdir(parents=True, exist_ok=False)
    tracks = []
    for key, path in (("original", original), ("bass", bass)):
        dest = output / (key + path.suffix.lower())
        shutil.copyfile(path, dest)
        tracks.append({"key": key, "filename": dest.name, "sha256": sha256(dest), "bytes": dest.stat().st_size})
    synth = output / "synth.wav"
    sf.write(synth, samples, sr, subtype="FLOAT")
    tracks.append({"key": "synth", "filename": synth.name, "sha256": sha256(synth), "bytes": synth.stat().st_size})
    summary = dict(trial_id=output.name, label=label, duration_seconds=duration,
        source_job_id=state["job_id"], midi_trial_id=state["midi_trial_id"],
        midi_sha256=sha256(midi), note_count=len(notes), tracks=tracks,
        synthesis_sample_rate=sr, synthesis_max_boundary_rounding_seconds=rounding,
        source_timing_origin_seconds=0, accuracy_verified=False,
        warning="Local private comparison. Synth uses preserved note times and pitches with a fixed additive tone; source timbre, dynamics and controllers are not reproduced. No loudness normalization or reference accuracy claim.")
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary
