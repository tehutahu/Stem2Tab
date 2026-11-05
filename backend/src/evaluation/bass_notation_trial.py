"""Opt-in saved-MIDI fingering/rhythm derivatives; never runs separation or a model."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import sys
from typing import Literal

import mido

from src.evaluation.saved_midi_trial import _load_unambiguous_midi, run_saved_midi_trial
from src.pipelines.bass_output import (BassEventMapping,BassOutputConfig,BassOutputEvent,
    _readback,_write_preview,write_bass_gp5_preview)
from src.pipelines.bass_fingering import BassFingeringReport,plan_bass_fingering
from src.pipelines.explicit_rhythm import ExplicitRhythmConfig,plan_explicit_rhythm,write_explicit_rhythm


def _json(path: Path,value: dict) -> None:
    with path.open("x",encoding="utf-8") as stream:
        json.dump(value,stream,indent=2,ensure_ascii=False,allow_nan=False)
        stream.write("\n")


def _planned_gp5(events: tuple[BassOutputEvent,...],plan: BassFingeringReport,
                 config: BassOutputConfig,folder: Path) -> dict:
    """Reuse the tested GP5 container/readback with the complete constrained mapping.

    Mapping affects strings/frets only. It does not lift the quarter-note preview
    limitation, and is never offered after a failed or capped search.
    """
    if plan.status!="planned" or plan.input_event_count!=len(events):
        raise ValueError("A complete constrained fingering plan is required")
    if plan.tuning_midi!=config.resolved_tuning or plan.max_fret!=config.resolved_max_fret:
        raise ValueError("Fingering settings differ from the output instrument")
    mapped=[]
    for ordinal,(original,row) in enumerate(zip(events,plan.events)):
        if row.input_ordinal!=ordinal or any(getattr(original,k)!=getattr(row,k) for k in BassOutputEvent.model_fields):
            raise ValueError("Fingering changed an original event")
        if row.string is None or row.fret is None or config.resolved_tuning[-row.string]+row.fret!=original.midi:
            raise ValueError("Invalid sounding pitch in planned string/fret")
        if not 0<=row.fret<=config.resolved_max_fret:
            raise ValueError("Planned fret outside selected range")
        mapped.append(BassEventMapping(**original.model_dump(),input_ordinal=ordinal,string=row.string,fret=row.fret,
            preview_velocity_midi=min(range(15,128,16),key=lambda v:(abs(v-original.velocity),v))))
    for i,a in enumerate(mapped):
        for b in mapped[i+1:]:
            if a.string==b.string and max(a.start,b.start)<min(a.end,b.end):
                raise ValueError("Planned simultaneous notes share a string")
    folder.mkdir(parents=True,exist_ok=False)
    path=folder/"bass.constrained.preview.gp5"
    rows=tuple(mapped)
    _write_preview(path,rows,config)
    back=_readback(path,rows,config)
    result=dict(status="written",path=str(path),mapping_mode="constrained",input_events_sha256=plan.input_events_sha256,
        readback=back,events=[r.model_dump(mode="json") for r in rows],timing_preserved=False,
        warning="GP5 remains a sequential quarter-note fingering preview. Original rhythm, rests and overlaps are not serialized.")
    _json(folder/"preview.json",result)
    return result


def run_bass_notation_trial(midi_path: Path,output_dir: Path,*,config: BassOutputConfig,
    source_kind: Literal["muscriptor-small","local-midi"],fingering_mode: Literal["none","constrained"]="none",
    max_position_shift: int | None=None,max_hand_span: int | None=None,
    rhythm: ExplicitRhythmConfig | None=None,gp5_preview: bool=False) -> dict:
    """Retain source/time-preserving artifacts and add only explicitly requested derivatives."""
    if fingering_mode not in {"none","constrained"}:
        raise ValueError("Unsupported fingering mode")
    if fingering_mode=="constrained":
        if any(type(v) is not int or not 0<=v<=36 for v in (max_position_shift,max_hand_span)):
            raise ValueError("Constrained fingering requires explicit movement and hand-span limits in0..36")
    elif max_position_shift is not None or max_hand_span is not None:
        raise ValueError("Fingering limits require constrained mode; unused parameters are not ignored")
    if rhythm is not None and not isinstance(rhythm,ExplicitRhythmConfig):
        raise TypeError("Use an explicit validated rhythm configuration")
    midi_path=Path(midi_path).resolve()
    output_dir=Path(output_dir).resolve()
    if output_dir.exists(): raise FileExistsError(output_dir)
    if midi_path.stat().st_size>2*1024*1024: raise ValueError("Input MIDI limit is2MiB")
    data=midi_path.read_bytes()
    raw=mido.MidiFile(file=io.BytesIO(data))
    if len(raw.tracks)>32 or sum(len(t) for t in raw.tracks)>20000:
        raise ValueError("Input MIDI complexity limit is32tracks/20000messages")
    _,loaded=_load_unambiguous_midi(data)
    if len(loaded)>500 or max((r["end"] for r in loaded),default=0)>600:
        raise ValueError("Notation trial limit is500notes/600seconds; no cropping or dropping")
    events=tuple(BassOutputEvent(**{k:r[k] for k in BassOutputEvent.model_fields}) for r in loaded)
    digest=hashlib.sha256(data).hexdigest()
    output_dir.mkdir(parents=True,exist_ok=False)
    report=dict(status="started",input_path=str(midi_path),input_sha256=digest,input_note_count=len(events),
        config=config.model_dump(mode="json"),source_kind=source_kind,model_calls=0,separator_calls=0,
        fingering_mode=fingering_mode,gp5_requested=gp5_preview,rhythm_requested=rhythm is not None,
        fingering={"status":"not_requested"},rhythm={"status":"not_requested"},gp5={"status":"not_requested"},
        warnings=["Optional derivatives do not replace the original Performance MIDI or storage-clock XML.",
          "No acoustic correctness/accuracy, beat detection, ergonomic guarantee or automatic tempo/meter inference.",
          "GP5 remains a quarter-note fingering preview; the explicit rhythm derivative is MusicXML only."])
    try:
        preserved=run_saved_midi_trial(midi_path,output_dir/"preserved",config=config,
            source_kind=source_kind,time_basis="storage-clock",gp5_preview=False)
        report["preserved"]=preserved
        if preserved["status"]!="completed": raise RuntimeError("Original time-preserving export failed")
        blocked=False
        plan=None
        if fingering_mode=="constrained":
            plan=plan_bass_fingering(events,config=config,max_position_shift=max_position_shift,max_hand_span=max_hand_span)
            report["fingering"]=plan.model_dump(mode="json")
            _json(output_dir/"fingering-plan.json",report["fingering"])
            blocked=plan.status!="planned"
        if gp5_preview:
            if plan is None:
                legacy=write_bass_gp5_preview(events,output_dir/"gp5-preview",config=config)
                report["gp5"]=legacy.model_dump(mode="json")
                blocked|=legacy.status!="written"
            elif plan.status=="planned":
                report["gp5"]=_planned_gp5(events,plan,config,output_dir/"gp5-preview")
            else:
                report["gp5"]={"status":"blocked","reason":"No complete constrained fingering plan; no partial GP5"}
        if rhythm is not None:
            proposal=plan_explicit_rhythm(events,config=rhythm)
            report["rhythm"]=write_explicit_rhythm(proposal,output_dir/"rhythm")
            report["rhythm"]["plan"]=proposal.model_dump(mode="json")
            blocked|=proposal.status!="planned"
        report["status"]="blocked_optional_derivative" if blocked else "completed"
    except Exception as exc:
        report["status"]="failed"
        report["error"]=f"{type(exc).__name__}: {exc}"
    report["source_unchanged"]=hashlib.sha256(midi_path.read_bytes()).hexdigest()==digest
    if not report["source_unchanged"]:
        report["status"]="failed";report["error"]="Source MIDI changed; retained snapshot hash is reported"
    report["artifacts"]=[dict(path=str(p.relative_to(output_dir)),bytes=p.stat().st_size,
        sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in sorted(output_dir.rglob("*")) if p.is_file()]
    _json(output_dir/"notation-trial.json",report)
    return report


def main(argv: list[str] | None=None) -> int:
    """CLI with explicit opt-in modes; unused or incomplete clock inputs are rejected."""
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--midi",type=Path,required=True)
    parser.add_argument("--output-dir",type=Path,required=True)
    parser.add_argument("--source-kind",choices=["muscriptor-small","local-midi"],required=True)
    parser.add_argument("--bass-profile",choices=["bass4","bass5","custom"],required=True)
    parser.add_argument("--strings",type=int)
    parser.add_argument("--tuning-midi")
    parser.add_argument("--max-fret",type=int)
    parser.add_argument("--fingering-mode",choices=["none","constrained"],default="none")
    parser.add_argument("--max-position-shift",type=int)
    parser.add_argument("--max-hand-span",type=int)
    parser.add_argument("--gp5-preview",action="store_true",help="Quarter-note preview only; no rhythmic GP5")
    parser.add_argument("--rhythm-mode",choices=["none","explicit-grid"],default="none")
    parser.add_argument("--tempo-bpm",type=float,help="Explicit quarter-note BPM, not inferred")
    parser.add_argument("--meter",help="Explicit numerator/denominator, e.g.3/4")
    parser.add_argument("--origin-seconds",type=float,help="Explicit bar/grid origin in source seconds")
    parser.add_argument("--subdivisions-per-quarter",type=int)
    parser.add_argument("--max-boundary-shift-ms",type=float)
    args=parser.parse_args(argv)
    values=[args.tempo_bpm,args.meter,args.origin_seconds,args.subdivisions_per_quarter,args.max_boundary_shift_ms]
    try:
        rhythm=None
        if args.rhythm_mode=="explicit-grid":
            if any(v is None for v in values): raise ValueError("Explicit rhythm requires tempo, meter, origin, grid and maximum shift")
            numerator,denominator=(int(v) for v in args.meter.split("/"))
            rhythm=ExplicitRhythmConfig(tempo_bpm=args.tempo_bpm,numerator=numerator,denominator=denominator,
                origin_seconds=args.origin_seconds,subdivisions_per_quarter=args.subdivisions_per_quarter,
                max_boundary_shift_ms=args.max_boundary_shift_ms)
        elif any(v is not None for v in values):
            raise ValueError("Clock/grid parameters require explicit-grid mode; they are not silently ignored")
        config=BassOutputConfig(profile=args.bass_profile,strings=args.strings,max_fret=args.max_fret,
            tuning_midi=tuple(int(p) for p in args.tuning_midi.split(",")) if args.tuning_midi else None)
        report=run_bass_notation_trial(args.midi,args.output_dir,config=config,source_kind=args.source_kind,
            fingering_mode=args.fingering_mode,max_position_shift=args.max_position_shift,max_hand_span=args.max_hand_span,
            rhythm=rhythm,gp5_preview=args.gp5_preview)
    except (ValueError,OSError,TypeError) as exc:
        print(f"NOTATION_TRIAL_REJECTED: {exc}",file=sys.stderr)
        return 1
    print(json.dumps({"status":report["status"],"output_dir":str(args.output_dir),
        "input_note_count":report["input_note_count"],"fingering":report["fingering"]["status"],
        "rhythm":report["rhythm"]["status"],"gp5":report["gp5"]["status"]},ensure_ascii=False))
    return 0 if report["status"]=="completed" else 2 if report["status"]=="blocked_optional_derivative" else 1


if __name__=="__main__":
    raise SystemExit(main())
