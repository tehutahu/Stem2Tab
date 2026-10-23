"""Optional audit of this project's fixed-tempo generated GP5/MusicXML files.

Tie continuation is decoded from its preceding voice/string event, never the
placeholder fret on a GP5 tie or a cross-voice stripTies heuristic. This reader
is deliberately limited to notation emitted by pipelines.tab, not arbitrary scores.
"""
from __future__ import annotations

import argparse
from collections import Counter
from fractions import Fraction
import hashlib
import json
from pathlib import Path
from time import perf_counter
import xml.etree.ElementTree as ET

from src.evaluation.io import read_midi, write_note_events_json
from src.evaluation.metrics import MetricConfig, evaluate_note_events
from src.evaluation.models import NoteEvent, NoteEventSet
from src.pipelines.tab import _load_score, midi_to_gp5


def read_generated_notation(path: Path) -> NoteEventSet:
    """Read sounding pitches and seconds, preserving distinct rearticulations."""
    if path.suffix.lower()=='.gp5':
        return _read_gp5(path)
    if path.suffix.lower()=='.musicxml':
        return _read_musicxml(path)
    raise ValueError('Only generated .gp5 and .musicxml files are supported')


def _events(notes: list[list]) -> NoteEventSet:
    return NoteEventSet(notes=tuple(NoteEvent(midi=int(p),start=float(a),end=float(b),velocity=80/127) for p,a,b in notes))


def _read_gp5(path: Path) -> NoteEventSet:
    import guitarpro
    song=guitarpro.parse(str(path))
    if len(song.tracks)!=1 or song.tempo<=0:
        raise ValueError('Expected one generated bass track with positive fixed tempo')
    origin=song.measureHeaders[0].start
    notes=[];previous={}
    for measure in song.tracks[0].measures:
        voice=measure.voices[0]
        if sum(b.duration.time for b in voice.beats)!=measure.header.length:
            raise ValueError('Incomplete generated GP5 measure')
        for beat in voice.beats:
            start=Fraction(beat.start-origin,960);duration=Fraction(beat.duration.time,960)
            for note in beat.notes:
                if note.type==guitarpro.models.NoteType.tie:
                    index=previous.get(note.string)
                    if index is None or notes[index][2]!=start:
                        raise ValueError('Unmatched or noncontiguous GP5 tie')
                    # GP5 serializes a tie fret as zero; its pitch comes from the
                    # previous sounding note on that string, even across measures.
                    notes[index][2]=start+duration
                elif note.type==guitarpro.models.NoteType.normal:
                    previous[note.string]=len(notes)
                    notes.append([note.realValue,start,start+duration])
                else:
                    raise ValueError('Unsupported generated GP5 note type')
    seconds=Fraction(60,song.tempo)
    return _events([[p,a*seconds,b*seconds] for p,a,b in notes])


def _read_musicxml(path: Path) -> NoteEventSet:
    root=ET.parse(path).getroot();parts=root.findall('part')
    if len(parts)!=1:raise ValueError('Expected one generated MusicXML bass part')
    part=parts[0];tempos=[float(x.get('tempo')) for x in part.iter('sound') if x.get('tempo')]
    if not tempos or tempos[0]<=0 or any(abs(t-tempos[0])>1e-6 for t in tempos):
        raise ValueError('Expected an explicit positive fixed MusicXML tempo')
    seconds=60/tempos[0];notes=[];active={};divisions=1;bar=Fraction(4);measure_start=Fraction(0)
    for measure in part.findall('measure'):
        attributes=measure.find('attributes')
        if attributes is not None:
            if attributes.find('transpose') is not None:
                raise ValueError('Generated sounding-pitch XML must not transpose')
            if attributes.find('divisions') is not None:divisions=int(attributes.findtext('divisions'))
            if attributes.find('time') is not None:
                bar=Fraction(int(attributes.findtext('time/beats'))*4,int(attributes.findtext('time/beat-type')))
            if divisions<=0 or bar!=4:raise ValueError('Expected generated 4/4 with positive divisions')
        cursor=Fraction(0);chord_start=Fraction(0)
        for element in measure:
            if element.tag in ('backup','forward'):
                duration=Fraction(int(element.findtext('duration')),divisions)
                cursor+=duration if element.tag=='forward' else -duration
                if cursor<0:raise ValueError('MusicXML cursor precedes measure')
            if element.tag!='note':continue
            if element.find('grace') is not None:raise ValueError('Generated score must not contain grace notes')
            duration=Fraction(int(element.findtext('duration','0')),divisions)
            chord=element.find('chord') is not None
            onset=measure_start+(chord_start if chord else cursor)
            if not chord:chord_start=cursor;cursor+=duration
            if cursor>bar or duration<=0:raise ValueError('Invalid generated note duration')
            if element.find('rest') is not None:continue
            pitch=element.find('pitch')
            if pitch is None:raise ValueError('Expected a pitched generated note')
            midi=12*(int(pitch.findtext('octave'))+1)+dict(C=0,D=2,E=4,F=5,G=7,A=9,B=11)[pitch.findtext('step')]+int(pitch.findtext('alter','0'))
            key=(element.findtext('voice','1'),midi);types={x.get('type') for x in element.findall('tie')}
            if 'stop' in types:
                index=active.get(key)
                if index is None or notes[index][2]!=onset:raise ValueError('Unmatched or noncontiguous MusicXML tie')
                notes[index][2]=onset+duration
                if 'start' not in types:active.pop(key)
            else:
                if key in active:raise ValueError('Unclosed MusicXML tie before new attack')
                notes.append([midi,onset,onset+duration])
                if 'start' in types:active[key]=len(notes)-1
        measure_start+=bar
    if active:raise ValueError('Unclosed generated MusicXML tie')
    return _events([[p,float(a)*seconds,float(b)*seconds] for p,a,b in notes])


def _identities(events: NoteEventSet) -> Counter:
    return Counter((n.midi,round(n.start,7),round(n.end,7)) for n in events.notes)


def audit_notation(midi_path: Path, output_dir: Path, *, reference: NoteEventSet|None=None,
                   metric_config: MetricConfig=MetricConfig(), strings: int=4) -> dict:
    """Export once, compare to rounded target, and retain errors as stage outcomes.

    MIDI tempo metadata is authoritative. A performance MIDI's default 120 BPM
    is a container assumption, not inferred musical tempo or beat truth.
    """
    if not midi_path.is_file():raise FileNotFoundError(midi_path)
    if output_dir.exists() and any(output_dir.iterdir()):raise ValueError('Notation output must be new or empty')
    output_dir.mkdir(parents=True,exist_ok=True)
    started=perf_counter();summary=dict(schema_version='1.0',status='error',strings=strings,
        input_sha256=hashlib.sha256(midi_path.read_bytes()).hexdigest(),
        scope='fixed-tempo 4/4 notation integrity; MIDI tempo is not audio rhythm inference',
        reference_available=reference is not None)
    try:
        performed=read_midi(midi_path);write_note_events_json(performed,output_dir/'input_events.json')
        bpm,notes=_load_score(midi_path)
        target=_events([[n.pitch,n.start/48*60/bpm,n.end/48*60/bpm] for n in notes])
        write_note_events_json(target,output_dir/'quantized_events.json')
        if reference is not None:
            summary['input_metrics']=evaluate_note_events(reference,performed,config=metric_config,runtime_seconds=0).model_dump()
            summary['quantized_metrics']=evaluate_note_events(reference,target,config=metric_config,runtime_seconds=0).model_dump()
        selected=midi_to_gp5(midi_path,output_dir/'bass.gp5',strings=strings)
        loaded=read_generated_notation(selected);write_note_events_json(loaded,output_dir/'reloaded_events.json')
        expected,actual=_identities(target),_identities(loaded)
        ordered_expected=sorted(target.notes,key=lambda n:(n.start,n.end,n.midi))
        ordered_actual=sorted(loaded.notes,key=lambda n:(n.start,n.end,n.midi))
        same_pitches=[n.midi for n in ordered_expected]==[n.midi for n in ordered_actual]
        max_drift=max((max(abs(a.start-b.start),abs(a.end-b.end)) for a,b in zip(ordered_expected,ordered_actual)),default=0.0) if same_pitches else None
        summary.update(ordered_pitch_sequence_equal=same_pitches,max_boundary_drift_seconds=max_drift)
        summary.update(status='success' if expected==actual else 'integrity_error',tempo_bpm=bpm,
            notation_path=selected.name,notation_format=selected.suffix,expected_notes=len(target.notes),reloaded_notes=len(loaded.notes),
            exact=expected==actual,unmatched_exact_boundaries_actual=sum((actual-expected).values()),unmatched_exact_boundaries_expected=sum((expected-actual).values()))
        if reference is not None:summary['notation_metrics']=evaluate_note_events(reference,loaded,config=metric_config,runtime_seconds=0).model_dump()
    except Exception as error:
        summary.update(error_type=type(error).__name__,error=str(error))
    summary['seconds']=perf_counter()-started
    (output_dir/'summary.json').write_text(json.dumps(summary,indent=2)+'\n',encoding='utf-8')
    return summary


def main() -> int:
    """Audit a saved performance MIDI without rerunning separation/transcription."""
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--midi',type=Path,required=True);parser.add_argument('--reference',type=Path);parser.add_argument('--output-dir',type=Path,required=True);parser.add_argument('--strings',type=int,choices=[4,5,6],default=4)
    args=parser.parse_args();reference=read_midi(args.reference) if args.reference else None
    summary=audit_notation(args.midi,args.output_dir,reference=reference,strings=args.strings)
    print(json.dumps(summary,indent=2));return 0 if summary['status']=='success' else 1


if __name__=='__main__':raise SystemExit(main())
