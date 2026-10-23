"""Synthetic offset diagnostics; RMS-rise onset gate is a rejected hypothesis.

No adapter or production configuration is changed. References are used only
after frame extraction/assembly. Generated audio and note artifacts stay private.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import numpy as np


def rms_rise_onsets(times: np.ndarray, energy: np.ndarray, onsets: np.ndarray) -> np.ndarray:
    """Offline hypothesis: keep a split when mean RMS rises across fixed 50ms windows."""
    if times.ndim != 1 or energy.shape != times.shape or onsets.ndim != 1:
        raise ValueError("times, energy and onsets must be aligned one-dimensional arrays")
    kept = []
    for onset in onsets:
        before = energy[(times >= onset - .05) & (times < onset)]
        after = energy[(times >= onset) & (times < onset + .05)]
        if len(before) and len(after) and np.mean(after) > np.mean(before):
            kept.append(float(onset))
    return np.asarray(kept, dtype=float)


def synthetic_cases() -> list[dict]:
    """Fixed new diagnostic conditions; no threshold selection or even-recording access."""
    return [
        dict(id="rest_70ms", notes=[(33,.23,.18,1),(33,.48,.20,1)], mode="normal"),
        dict(id="rest_25ms", notes=[(33,.23,.18,1),(33,.435,.20,1)], mode="normal"),
        dict(id="short_notes", notes=[(28,.23,.075,1),(33,.39,.095,1),(38,.59,.11,1)], mode="normal"),
        dict(id="equal_contiguous_repicks", notes=[(33,.23,.24,1),(33,.47,.24,1),(33,.71,.24,1)], mode="normal"),
        dict(id="quieter_contiguous_repicks", notes=[(33,.23,.28,1),(33,.51,.28,.07),(33,.79,.28,.30)], mode="normal"),
        dict(id="legato_pitch_changes", notes=[(28,.23,.24,1),(30,.47,.19,1),(33,.66,.33,1)], mode="legato"),
        dict(id="weak_decay", notes=[(28,.23,.80,1)], mode="decay", tail_gain=.006),
        dict(id="near_floor_decay", notes=[(28,.23,.80,1)], mode="decay", tail_gain=.00002),
        dict(id="decaying_vibrato", notes=[(33,.23,.81,1)], mode="vibrato", tail_gain=.12),
        dict(id="silence", notes=[], mode="normal"),
    ]


def run_synthetic(output: Path) -> list[dict]:
    """Extract frames once per new audio, preserving raw data for both assemblies."""
    import pretty_midi
    import soundfile as sf
    from src.evaluation.adapters import AdapterConfig
    from src.evaluation.pyin import PyinTranscriber, _write_precise_midi
    from src.evaluation.note_assembly import assemble_notes
    from src.evaluation.io import read_midi, write_note_events_json
    from src.evaluation.metrics import evaluate_note_events, MetricConfig
    from src.evaluation.models import NoteEvent, NoteEventSet

    output.mkdir(parents=True, exist_ok=False)
    cases = synthetic_cases()
    (output / "plan.json").write_text(json.dumps({"cases": cases, "sample_rate": 22050,
        "partials": [1,.27,.09], "peak": .43, "phase": .51, "attack_seconds": .006,
        "release_seconds": .035, "hypothesis": "fixed symmetric 50ms local RMS rise; no search",
        "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}, indent=2))
    rows = []
    sr = 22050
    for spec in cases:
        folder = output / spec["id"]
        folder.mkdir()
        length = round((max((s+d for p,s,d,g in spec["notes"]), default=.9) + .33) * sr)
        audio = np.zeros(length)
        references = []
        phase = .51
        for index, (pitch,start,duration,gain) in enumerate(spec["notes"]):
            first, count = round(start*sr), round(duration*sr)
            time = np.arange(count)/sr
            hz = 440*2**((pitch-69)/12)
            phase_curve = phase + 2*np.pi*hz*time
            if spec["mode"] == "vibrato":
                frequency = hz*2**(.3*np.sin(2*np.pi*4.3*time)/12)
                phase_curve = phase+2*np.pi*np.cumsum(frequency)/sr
            sound = sum(a*np.sin((h+1)*phase_curve) for h,a in enumerate([1,.27,.09]))
            envelope = np.ones(count)
            if spec["mode"] in ["decay","vibrato"]:
                envelope *= np.exp(np.linspace(0,np.log(spec["tail_gain"]),count))
            attack = min(count//2,round(.006*sr));release=min(count//2,round(.035*sr))
            if spec["mode"] != "legato" or index == 0:
                envelope[:attack] *= np.linspace(0,1,attack,endpoint=False)
            if spec["mode"] != "legato" or index == len(spec["notes"])-1:
                envelope[-release:] *= np.linspace(1,0,release)
            audio[first:first+count] += sound*envelope*gain
            phase = float((phase+2*np.pi*hz*count/sr)%(2*np.pi))
            references.append(NoteEvent(start=first/sr,end=(first+count)/sr,midi=pitch,velocity=80/127))
        peak = float(np.max(abs(audio),initial=0))
        if peak: audio *= .43/peak
        audio_path=folder/"audio.wav";sf.write(audio_path,audio,sr,subtype="PCM_16")
        reference=NoteEventSet(notes=tuple(references))
        reference_path=folder/"reference.mid";_write_precise_midi(reference,reference_path)
        reference=read_midi(reference_path)
        result=PyinTranscriber().run(audio_path,folder/"preserved-raw",config=AdapterConfig(demucs_model="unused",demucs_cache_dir=folder/"unused"))
        frames_path=folder/"preserved-raw/pyin_frames.npz"
        before=hashlib.sha256(frames_path.read_bytes()).hexdigest()
        with np.load(frames_path) as frame:
            t=frame["time"];kept=rms_rise_onsets(t,frame["energy"],frame["onset_times"])
            guarded=assemble_notes(frame["f0"],frame["voiced_flag"],t,len(audio)/sr,active=frame["active"],onset_times=kept)
            gate_path=folder/"offline-gate.mid";_write_precise_midi(guarded,gate_path);guarded=read_midi(gate_path)
            counts={"detected_onsets":len(frame["onset_times"]),"gate_kept_onsets":len(kept)}
        for method,estimate in [("baseline_pyin",result.events),("offline_rms_rise_gate",guarded)]:
            write_note_events_json(estimate,folder/f"{method}.json")
            row={"case":spec["id"],"method":method,"reference_notes":len(reference.notes),"estimated_notes":len(estimate.notes),**counts}
            if reference.notes:row.update(evaluate_note_events(reference,estimate,config=MetricConfig(),runtime_seconds=0).model_dump())
            else:row["metrics_defined"]=False
            rows.append(row)
        assert hashlib.sha256(frames_path.read_bytes()).hexdigest()==before
        print("Completed",spec["id"],flush=True)
    (output/"summary.json").write_text(json.dumps(rows,indent=2))
    return rows


def main() -> int:
    """Create a new private diagnostic suite without real-recording arguments."""
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir",type=Path,required=True)
    args=parser.parse_args()
    run_synthetic(args.output_dir)
    return 0


if __name__=="__main__":
    raise SystemExit(main())
