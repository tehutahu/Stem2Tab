"""Reproducible CPU synthetic diagnostics; no real-music accuracy claims."""
from __future__ import annotations

import argparse
import csv
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import subprocess
import sys
from time import perf_counter

import numpy as np
import pretty_midi
import soundfile as sf

from src.evaluation.note_assembly import AssemblyConfig

SR = 22050
BACKEND = Path(__file__).resolve().parents[2]


def _dump(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n")


def _package_version(name: str) -> str | None:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def _case(name: str, notes: list[tuple], **kwargs) -> dict:
    return {"id": name, "notes": notes, "partials": [.76, .19, .05], "peak": .5,
            "attack": .005, "release": .04, "legato": False, "phase": 0., **kwargs}


def fixture_cases(suite: str) -> list[dict]:
    """Return fixed development or reserved validation definitions, independent of estimates."""
    if suite == "development":
        variants = [("anchor", {})]
        variants += [(f"duration_{d}", {"duration": d}) for d in [.2, .35, .8]]
        variants += [(f"peak_{p}", {"peak": p}) for p in [.1, .25, .8]]
        variants += [("bright", {"partials": [1, .5, .25, .125]}),
                     ("weak_fundamental", {"partials": [.2, 1, .5, .25]}),
                     ("odd_harmonics", {"partials": [1, 0, .5, 0, .25]}),
                     ("reverse_order", {"pitches": [43, 38, 33, 28, 23]})]
        result = []
        for name, override in variants:
            duration = override.get("duration", .5)
            cursor = round(.5 * SR)
            notes = []
            for pitch in override.get("pitches", [23, 28, 33, 38, 43]):
                notes.append((pitch, cursor / SR, round(duration * SR) / SR, 1.))
                cursor += round(duration * SR) + round(.25 * SR)
            result.append(_case(name, notes, **{k: v for k, v in override.items()
                                                if k in ("peak", "partials")}))
        return result
    if suite == "validation_a":
        return [
            _case("repeat_gapped", [(23,.37,.18,1),(23,.595,.18,1),(23,.82,.18,1),
                                    (28,1.11,.18,1),(28,1.335,.18,1)], peak=.42, partials=[1,.31,.11,.04]),
            _case("repeat_contiguous", [(23,.37,.24,1),(23,.61,.24,1),(23,.85,.24,1)], peak=.42),
            _case("short_notes", [(28,.37,.075,1),(23,.53,.095,1),(33,.71,.11,1)], peak=.42),
            _case("short_rests", [(23,.37,.27,1),(23,.665,.27,1),(28,.975,.27,1)], peak=.42),
            _case("weak_fundamental", [(23,.37,.43,1),(28,.98,.43,.7),(30,1.59,.43,.4)],
                  peak=.37, partials=[.15,1,.4,.2]),
            _case("legato_changes", [(23,.37,.28,1),(28,.65,.28,1),(30,.93,.28,1),(31,1.21,.28,1)],
                  peak=.42, legato=True),
            _case("mixed_dynamics", [(23,.37,.33,1),(28,.91,.33,.02),(33,1.45,.33,.08),(43,1.99,.33,.6)], peak=.7),
            _case("silence", [], seconds=2.),
        ]
    if suite == "validation_b":
        return [
            _case("dense_repicks", [(23,.29,.14,.8),(23,.448,.14,1),(23,.606,.14,.5)],
                  peak=.56, partials=[1,.27,.13]),
            _case("new_short_pitches", [(26,.29,.065,1),(30,.385,.12,.7),(35,.535,.17,1)], peak=.56),
            _case("quiet_after_loud", [(23,.29,.47,1),(23,.93,.47,.012),(28,1.57,.47,.05)], peak=.56),
            _case("new_weak_fundamental", [(23,.29,.61,1),(26,1.07,.29,.8),(31,1.53,.41,.6)],
                  peak=.56, partials=[.08,.9,.5,.27]),
            _case("new_legato", [(28,.29,.19,1),(29,.48,.23,1),(33,.71,.37,1)], peak=.56, legato=True),
            _case("new_boundaries", [(25,.29,.21,1),(28,.69,.31,.7),(35,1.19,.26,.8)],
                  peak=.56, partials=[1,.23,.07], phase=.9, attack=.009, release=.025),
            _case("mixed_long_short", [(23,.29,.62,1),(23,.965,.085,1),(28,1.21,.37,.6)], peak=.56),
            _case("silence", [], seconds=1.7),
        ]
    raise ValueError(f"Unknown suite: {suite}")


def generate_fixture(spec: dict, directory: Path) -> dict:
    """Synthesize a sample-aligned WAV and its generated-only reference MIDI."""
    directory.mkdir(parents=True)
    events = [{"midi": p, "start_sample": round(s*SR), "end_sample": round(s*SR)+round(d*SR), "gain": g}
              for p, s, d, g in spec["notes"]]
    length = round(spec.get("seconds", max((e["end_sample"]/SR for e in events), default=0)+.5)*SR)
    audio = np.zeros(length)
    phase = spec["phase"]
    for index, event in enumerate(events):
        count = event["end_sample"] - event["start_sample"]
        hz = pretty_midi.note_number_to_hz(event["midi"])
        time = np.arange(count) / SR
        wave = sum(a*np.sin((h+1)*(2*np.pi*hz*time+phase)) for h, a in enumerate(spec["partials"]))
        envelope = np.ones(count)
        attack = min(count//2, round(spec["attack"]*SR))
        release = min(count//2, round(spec["release"]*SR))
        if attack and (not spec["legato"] or index == 0):
            envelope[:attack] = np.linspace(0,1,attack,endpoint=False)
        if release and (not spec["legato"] or index == len(events)-1):
            envelope[-release:] = np.linspace(1,0,release)
        audio[event["start_sample"]:event["end_sample"]] += wave*envelope*event["gain"]
        if spec["legato"]:
            phase = (phase+2*np.pi*hz*count/SR) % (2*np.pi)
        event.update(start=event["start_sample"]/SR, end=event["end_sample"]/SR)
    peak = float(np.max(np.abs(audio), initial=0))
    if peak:
        audio *= spec["peak"]/peak
    sf.write(directory/"audio.wav", audio, SR, subtype="PCM_16")
    midi = pretty_midi.PrettyMIDI(initial_tempo=120, resolution=SR)
    bass = pretty_midi.Instrument(program=33)
    bass.notes = [pretty_midi.Note(80,e["midi"],e["start"],e["end"]) for e in events]
    midi.instruments.append(bass)
    midi.write(str(directory/"reference.mid"))
    decoded, _ = sf.read(directory/"audio.wav")
    fixture = {**spec, "notes": events, "seconds": length/SR,
               "rms_actual": float(np.sqrt(np.mean(decoded**2))), "sample_rate": SR,
               "channels": 1, "subtype": "PCM_16"}
    _dump(directory/"fixture.json", fixture)
    return fixture


def _diagnostic(ref: dict, estimated: list[dict]) -> dict:
    same = sorted([e for e in estimated if e["midi"] == ref["midi"] and
                   min(e["end"],ref["end"]) > max(e["start"],ref["start"])], key=lambda e:e["start"])
    onset = (same[0]["start"]-ref["start"])*1000 if same else None
    return {"reference": ref, "interval_missing": not same, "segments": len(same),
            "onset_error_ms": onset, "late": onset is not None and onset > 50,
            "events": same}


def summarize(root: Path) -> None:
    """Aggregate saved metrics, including regressions and empty-input false positives."""
    plan = json.loads((root/"plan.json").read_text())
    rows, details = [], []
    for case in plan["cases"]:
        for method in plan["transcribers"]:
            directory = root/"runs"/case["id"]/"runs"/f"direct__{method}"
            record = json.loads((directory/"metrics.json").read_text())
            events = json.loads((directory/"events.json").read_text())["notes"]
            diagnostics = [_diagnostic(n,events) for n in case["notes"]]
            rows.append({"suite": plan["suite"], "case": case["id"], "method": method,
                         "reference_notes": len(case["notes"]), "estimated_notes": len(events),
                         "onset_f1": (record["metrics"] or {}).get("onset_f1"),
                         "onset_offset_f1": (record["metrics"] or {}).get("onset_offset_f1"),
                         "frame_f1": (record["metrics"] or {}).get("frame_f1"),
                         "extra": (record["metrics"] or {}).get("extra_note_count",len(events)),
                         "missed": (record["metrics"] or {}).get("missed_note_count",0),
                         "b0_interval_missing": sum(d["interval_missing"] for d in diagnostics if d["reference"]["midi"]==23),
                         "b0_late": sum(d["late"] for d in diagnostics if d["reference"]["midi"]==23),
                         "overlap_multi_candidate_refs": sum(d["segments"]>=2 for d in diagnostics),
                         "transcription_seconds": record["transcription_seconds"]})
            details.append({"case":case["id"], "method":method,"events":events,"diagnostics":diagnostics})
    _dump(root/"summary.json",rows)
    _dump(root/"note_diagnostics.json",details)
    with (root/"summary.csv").open("w") as handle:
        writer=csv.DictWriter(handle,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    _dump(root/"provenance.json", {"packages":{p:_package_version(p) for p in ["librosa","basic-pitch","numpy","onnxruntime"]},
          "sha256":{str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in sorted(root.rglob("*")) if p.is_file() and p.name!="provenance.json"}})


def main(argv: list[str] | None = None) -> int:
    """Run a fixed suite without overwriting outputs or changing production defaults."""
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir",required=True,type=Path)
    parser.add_argument("--suite",choices=["development","validation_a","validation_b"],required=True)
    parser.add_argument("--transcribers",default="basic_pitch,pyin_raw,pyin")
    parser.add_argument("--generate-only",action="store_true")
    args=parser.parse_args(argv)
    root=args.output_dir.resolve()
    if root.exists() and any(root.iterdir()):
        parser.error("output directory must be new or empty")
    root.mkdir(parents=True,exist_ok=True)
    cases=[generate_fixture(c,root/"inputs"/c["id"]) for c in fixture_cases(args.suite)]
    _dump(root/"plan.json",{"suite":args.suite,"cases":cases,"transcribers":args.transcribers.split(","),
           "assembly":AssemblyConfig().model_dump(),"cpu_only":True,
           "source_commit":subprocess.check_output(["git","rev-parse","HEAD"],cwd=BACKEND,text=True).strip(),
           "source_sha256":{str(p.relative_to(BACKEND)):hashlib.sha256(p.read_bytes()).hexdigest()
                            for p in sorted((BACKEND/"src/evaluation").glob("*.py"))},
           "all_suite_definitions":{s:fixture_cases(s) for s in ["development","validation_a","validation_b"]}})
    if args.generate_only:
        return 0
    env={**os.environ,"CUDA_VISIBLE_DEVICES":"","BASIC_PITCH_MODEL_SERIALIZATION":"onnx"}
    for case in cases:
        command=[sys.executable,"-m","src.evaluation.benchmark","--audio",str(root/"inputs"/case["id"]/"audio.wav"),
                 "--separators","direct","--transcribers",args.transcribers,"--output-dir",str(root/"runs"/case["id"])]
        if case["notes"]:
            command += ["--reference",str(root/"inputs"/case["id"]/"reference.mid")]
        start=perf_counter()
        result=subprocess.run(command,cwd=BACKEND,env=env,capture_output=True,text=True)
        (root/f"{case['id']}.log").write_text(result.stdout+"\nSTDERR:\n"+result.stderr)
        _dump(root/"timing"/f"{case['id']}.json",{"command":command,"exit_code":result.returncode,
                                              "wall_seconds":perf_counter()-start})
        print(case["id"],result.returncode,round(perf_counter()-start,2),flush=True)
        if result.returncode:
            return result.returncode
    summarize(root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
