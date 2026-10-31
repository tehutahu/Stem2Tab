"""Explicit, bounded rhythmic derivatives; source performance events stay untouched."""
from __future__ import annotations

from collections import Counter
from fractions import Fraction
import hashlib
import json
from pathlib import Path
from typing import Literal
import xml.etree.ElementTree as ET

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from src.pipelines.bass_output import BassOutputEvent


class ExplicitRhythmConfig(BaseModel):
    """User-specified quarter-note tempo, meter, origin and quantization grid."""
    model_config = ConfigDict(frozen=True,extra="forbid",allow_inf_nan=False)
    tempo_bpm: float = Field(ge=20,le=300)
    numerator: StrictInt = Field(ge=1,le=12)
    denominator: Literal[2,4,8]
    origin_seconds: float = Field(ge=0)
    subdivisions_per_quarter: Literal[1,2,4,8]
    max_boundary_shift_ms: float = Field(ge=0,le=250)

    @model_validator(mode="before")
    @classmethod
    def reject_boolean_settings(cls,value):
        """Booleans are not explicit musical clock or grid settings."""
        if isinstance(value,dict) and any(isinstance(v,bool) for v in value.values()):
            raise ValueError("Use numeric clock/grid values, not booleans")
        return value


class RhythmEvent(BassOutputEvent):
    """An original event plus its proposed, explicitly measured derivative."""
    input_ordinal: int
    score_start_units: int
    score_end_units: int
    proposed_start_seconds: float
    proposed_end_seconds: float
    start_change_ms: float
    end_change_ms: float
    reasons: tuple[str,...] = ()


class RhythmReport(BaseModel):
    """Complete proposal, including blocked events; never an accuracy result."""
    model_config = ConfigDict(frozen=True,extra="forbid",allow_inf_nan=False)
    status: Literal["planned","blocked"]
    config: ExplicitRhythmConfig
    input_event_count: int
    input_events_sha256: str
    events: tuple[RhythmEvent,...]
    reasons: tuple[str,...]
    grid_step_seconds: float
    max_absolute_start_change_ms: float
    max_absolute_end_change_ms: float
    changed_event_count: int
    musical_tempo_meter_origin_verified: Literal[False] = False
    source_timing_preserved_in_derivative: Literal[False] = False
    warnings: tuple[str,...]


def _fraction(value: float) -> Fraction:
    return Fraction(str(value))


def _nearest(value: Fraction) -> int:
    # Exact decimal rational arithmetic; halfway values choose the later grid point.
    return (2*value.numerator+value.denominator)//(2*value.denominator)


def plan_explicit_rhythm(events: tuple[BassOutputEvent,...] | list[BassOutputEvent], *,
                         config: ExplicitRhythmConfig) -> RhythmReport:
    """Quantize only by explicit choice, rejecting loss and changed overlap topology.

    Bounds are part of the experimental format. A blocked plan retains every
    original event and proposed delta; it never emits a partial score or repairs
    a collapsed note, merged attack, or newly created/removed overlap.
    """
    events=tuple(events)
    if any(not isinstance(e,BassOutputEvent) for e in events) or not isinstance(config,ExplicitRhythmConfig):
        raise TypeError("Use validated event and explicit rhythm models")
    if len(events)>500 or len({e.source_index for e in events})!=len(events):
        raise ValueError("Use at most500 events with unique source indices")
    origin=_fraction(config.origin_seconds)
    step=Fraction(60,config.subdivisions_per_quarter)/_fraction(config.tempo_bpm)
    limit=_fraction(config.max_boundary_shift_ms)
    proposed=[]
    issues: list[set[str]]=[]
    global_issues=set()
    for i,event in enumerate(events):
        a,b=_fraction(event.start),_fraction(event.end)
        start,end=_nearest((a-origin)/step),_nearest((b-origin)/step)
        qa,qb=origin+start*step,origin+end*step
        start_delta,end_delta=(qa-a)*1000,(qb-b)*1000
        problems=set()
        if event.midi<12:
            problems.add("unsupported_musicxml_pitch")
        if a<origin or start<0:
            problems.add("before_explicit_origin")
        if end<=start:
            problems.add("short_note_collapsed")
        if abs(start_delta)>limit or abs(end_delta)>limit:
            problems.add("boundary_shift_limit")
        proposed.append(dict(**event.model_dump(),input_ordinal=i,score_start_units=start,score_end_units=end,
            proposed_start_seconds=float(qa),proposed_end_seconds=float(qb),
            start_change_ms=float(start_delta),end_change_ms=float(end_delta)))
        issues.append(problems)
    for i,a in enumerate(events):
        for j in range(i+1,len(events)):
            b=events[j]
            qa,qb=proposed[i],proposed[j]
            if a.start!=b.start and qa["score_start_units"]==qb["score_start_units"]:
                issues[i].add("distinct_attacks_merged")
                issues[j].add("distinct_attacks_merged")
            original_overlap=max(a.start,b.start)<min(a.end,b.end)
            derived_overlap=max(qa["score_start_units"],qb["score_start_units"])<min(qa["score_end_units"],qb["score_end_units"])
            if original_overlap!=derived_overlap:
                issues[i].add("overlap_topology_changed")
                issues[j].add("overlap_topology_changed")
    # Limit score allocation, independently of event count. No clipping to fit.
    max_quarters=Fraction(max((p["score_end_units"] for p in proposed),default=0),config.subdivisions_per_quarter)
    if max_quarters>128*Fraction(config.numerator*4,config.denominator):
        global_issues.add("score_bar_limit")
    active=maximum=0
    boundaries=sorted((point,kind) for p in proposed if p["score_end_units"]>p["score_start_units"]
        for point,kind in [(p["score_start_units"],1),(p["score_end_units"],-1)])
    for _,kind in boundaries:
        active+=kind
        maximum=max(maximum,active)
    if maximum>7:
        global_issues.add("notation_voice_limit")
    rows=tuple(RhythmEvent(**row,reasons=tuple(sorted(problem))) for row,problem in zip(proposed,issues))
    reasons=tuple(sorted(global_issues.union(*(issue for issue in issues))))
    digest=hashlib.sha256(json.dumps([e.model_dump(mode="json") for e in events],sort_keys=True,
        separators=(",",":"),allow_nan=False).encode()).hexdigest()
    return RhythmReport(status="blocked" if reasons else "planned",config=config,input_event_count=len(events),
        input_events_sha256=digest,events=rows,reasons=reasons,grid_step_seconds=float(step),
        max_absolute_start_change_ms=max((abs(e.start_change_ms) for e in rows),default=0),
        max_absolute_end_change_ms=max((abs(e.end_change_ms) for e in rows),default=0),
        changed_event_count=sum(e.start_change_ms!=0 or e.end_change_ms!=0 for e in rows),
        warnings=("User-specified quarter-note BPM, meter and time origin; no musical correctness or beat detection claim.",
          "This quantized score is separate from original MIDI and storage-clock MusicXML; every source event remains in this report.",
          "Score time zero is the explicit origin; add origin_seconds to align score playback with the original performance.",
          "No tuplets, swing, changing tempo/meter, pickup notes before origin, controllers or source timbre are inferred."))


def _text(parent: ET.Element,tag: str,value: object) -> ET.Element:
    node=ET.SubElement(parent,tag)
    node.text=str(value)
    return node


def _durations(divisions: int) -> list[tuple[int,str,bool]]:
    values=[]
    for quarter_length,name in [(Fraction(4),"whole"),(Fraction(2),"half"),(Fraction(1),"quarter"),
        (Fraction(1,2),"eighth"),(Fraction(1,4),"16th"),(Fraction(1,8),"32nd")]:
        for dotted,factor in [(False,Fraction(1)),(True,Fraction(3,2))]:
            length=quarter_length*divisions*factor
            if length.denominator==1:
                values.append((int(length),name,dotted))
    return sorted(values,reverse=True)


def _emit(measure: ET.Element,*,start: int,end: int,bar_end: int,divisions: int,
          voice: int,pitch: int | None,event_start: int,event_end: int) -> int:
    segments=0
    while start<end:
        duration,name,dotted=next(item for item in _durations(divisions) if item[0]<=min(end,bar_end)-start)
        note=ET.SubElement(measure,"note")
        if pitch is None:
            ET.SubElement(note,"rest")
        else:
            p=ET.SubElement(note,"pitch")
            step,alter=[("C",0),("C",1),("D",0),("D",1),("E",0),("F",0),
                ("F",1),("G",0),("G",1),("A",0),("A",1),("B",0)][pitch%12]
            _text(p,"step",step)
            if alter: _text(p,"alter",alter)
            _text(p,"octave",pitch//12-1)
        _text(note,"duration",duration)
        tie_types=[]
        if pitch is not None:
            if start>event_start: tie_types.append("stop")
            if start+duration<event_end: tie_types.append("start")
            for kind in tie_types: ET.SubElement(note,"tie",type=kind)
        _text(note,"voice",voice)
        _text(note,"type",name)
        if dotted: ET.SubElement(note,"dot")
        if tie_types:
            notations=ET.SubElement(note,"notations")
            for kind in tie_types: ET.SubElement(notations,"tied",type=kind)
        start+=duration
        segments+=pitch is not None
    return segments


def read_rhythm_xml_events(path: Path) -> Counter:
    """Read exact voice positions and merge ties, for event-preservation checks."""
    output=[]
    for part in ET.parse(path).getroot().findall("part"):
        base=Fraction(0)
        divisions=1
        active={}
        for measure in part.findall("measure"):
            divisions=int(measure.findtext("attributes/divisions",str(divisions)))
            cursor=maximum=Fraction(0)
            for item in measure:
                if item.tag in {"backup","forward"}:
                    cursor+=Fraction(int(item.findtext("duration")),divisions)*(-1 if item.tag=="backup" else 1)
                    maximum=max(maximum,cursor)
                elif item.tag=="note":
                    duration=Fraction(int(item.findtext("duration")),divisions)
                    a,b=base+cursor,base+cursor+duration
                    p=item.find("pitch")
                    if p is not None:
                        pitch={"C":0,"D":2,"E":4,"F":5,"G":7,"A":9,"B":11}[p.findtext("step")]
                        pitch+=int(p.findtext("alter","0"))+12*(int(p.findtext("octave"))+1)
                        key=(item.findtext("voice","1"),pitch)
                        ties={t.attrib["type"] for t in item.findall("tie")}
                        if "stop" in ties:
                            first,previous_end=active.pop(key)
                            if previous_end!=a: raise ValueError("Noncontiguous tie")
                        else: first=a
                        if "start" in ties: active[key]=(first,b)
                        else: output.append((first,b,pitch))
                    cursor+=duration
                    maximum=max(maximum,cursor)
            base+=maximum
        if active: raise ValueError("Unclosed tie")
    return Counter(output)


def write_explicit_rhythm(report: RhythmReport,output_dir: Path) -> dict:
    """Save a complete fresh plan and, only when valid, a readable score derivative."""
    config=report.config
    # Recompute from immutable originals so a caller cannot forge a success report.
    original=[BassOutputEvent(**{key:getattr(e,key) for key in BassOutputEvent.model_fields}) for e in report.events]
    verified=plan_explicit_rhythm(original,config=config)
    if verified!=report:
        raise ValueError("Rhythm plan does not match its unchanged source events and explicit configuration")
    output_dir=Path(output_dir).resolve()
    output_dir.mkdir(parents=True,exist_ok=False)
    with (output_dir/"rhythm-plan.json").open("x",encoding="utf-8") as stream:
        stream.write(report.model_dump_json(indent=2)+"\n")
    if report.status!="planned":
        return {"status":"blocked","xml":None,"reasons":report.reasons,"input_event_count":report.input_event_count}
    divisions=max(2,config.subdivisions_per_quarter)
    scale=divisions//config.subdivisions_per_quarter
    bar_units=config.numerator*4*divisions//config.denominator
    voices: list[list[tuple[int,int,int]]]=[]
    for event in sorted(report.events,key=lambda e:(e.score_start_units,e.score_end_units,e.midi,e.input_ordinal)):
        a,b=event.score_start_units*scale,event.score_end_units*scale
        voice=next((i for i,v in enumerate(voices) if v[-1][1]<=a),len(voices))
        if voice==len(voices): voices.append([])
        voices[voice].append((a,b,event.midi))
    if not voices: voices=[[]]
    if len(voices)>7:
        raise ValueError("At most seven simultaneous notation voices are supported; no event dropping")
    root=ET.Element("score-partwise",version="4.0")
    work=ET.SubElement(root,"work")
    _text(work,"work-title","Explicit quantized derivative — not original performance timing")
    parts=ET.SubElement(root,"part-list")
    score_part=ET.SubElement(parts,"score-part",id="P1")
    _text(score_part,"part-name","Bass — user-specified rhythm")
    score_instrument=ET.SubElement(score_part,"score-instrument",id="I1")
    _text(score_instrument,"instrument-name","Electric bass preview")
    instrument=ET.SubElement(score_part,"midi-instrument",id="I1")
    _text(instrument,"midi-channel",1)
    _text(instrument,"midi-program",34)  # Explicit bass preview instrument, not inferred source timbre.
    part=ET.SubElement(root,"part",id="P1")
    last=max((e.score_end_units*scale for e in report.events),default=0)
    bars=max(1,(last+bar_units-1)//bar_units)
    segments=0
    for bar in range(bars):
        start,end=bar*bar_units,(bar+1)*bar_units
        measure=ET.SubElement(part,"measure",number=str(bar+1))
        if bar==0:
            attrs=ET.SubElement(measure,"attributes")
            _text(attrs,"divisions",divisions)
            meter=ET.SubElement(attrs,"time")
            _text(meter,"beats",config.numerator)
            _text(meter,"beat-type",config.denominator)
            clef=ET.SubElement(attrs,"clef")
            _text(clef,"sign","F");_text(clef,"line",4)
            direction=ET.SubElement(measure,"direction")
            kind=ET.SubElement(direction,"direction-type")
            metronome=ET.SubElement(kind,"metronome")
            _text(metronome,"beat-unit","quarter");_text(metronome,"per-minute",config.tempo_bpm)
            ET.SubElement(direction,"sound",tempo=str(config.tempo_bpm))
        for i,voice in enumerate(voices,start=1):
            if i>1: _text(ET.SubElement(measure,"backup"),"duration",bar_units)
            cursor=start
            for a,b,pitch in voice:
                if b<=start or a>=end: continue
                if a>cursor:
                    _emit(measure,start=cursor,end=a,bar_end=end,divisions=divisions,voice=i,pitch=None,event_start=cursor,event_end=a)
                left,right=max(a,start),min(b,end)
                segments+=_emit(measure,start=left,end=right,bar_end=end,divisions=divisions,voice=i,pitch=pitch,event_start=a,event_end=b)
                cursor=right
            if cursor<end:
                _emit(measure,start=cursor,end=end,bar_end=end,divisions=divisions,voice=i,pitch=None,event_start=cursor,event_end=end)
    path=output_dir/"score-explicit-quantized.musicxml"
    ET.indent(root,space="  ")
    with path.open("xb") as stream:
        ET.ElementTree(root).write(stream,encoding="utf-8",xml_declaration=True)
    expected=Counter((Fraction(e.score_start_units,config.subdivisions_per_quarter),
        Fraction(e.score_end_units,config.subdivisions_per_quarter),e.midi) for e in report.events)
    if read_rhythm_xml_events(path)!=expected:
        raise ValueError("Quantized score readback lost or changed events")
    result={"status":"written","xml":str(path),"sha256":hashlib.sha256(path.read_bytes()).hexdigest(),
        "logical_note_count":len(report.events),"note_segments":segments,"voices":len(voices),"bars":bars,
        "divisions":divisions,"event_readback_exact":True,"relative_to_explicit_origin":True,
        "source_timbre_preserved":False,"explicit_preview_program_zero_based":33}
    with (output_dir/"export.json").open("x",encoding="utf-8") as stream:
        json.dump(result,stream,indent=2);stream.write("\n")
    return result
