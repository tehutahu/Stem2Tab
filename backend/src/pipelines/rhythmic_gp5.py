"""Strict GP5 of an explicit quantized derivative, with independent event readback."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path

import guitarpro

from src.pipelines.bass_output import BassOutputConfig, BassOutputEvent
from src.pipelines.bass_fingering import BassFingeringReport
from src.pipelines.explicit_rhythm import RhythmReport, plan_explicit_rhythm


def read_gp5_events(path: Path) -> tuple[Counter, dict]:
    """Read actual GP5 beats; merge only explicit contiguous same-string ties."""
    gp = guitarpro.models
    song = guitarpro.parse(str(path))
    if len(song.tracks) != 1:
        raise ValueError("Expected exactly one bass track")
    track = song.tracks[0]
    rows: list[list[int]] = []
    last: dict[int, list[int]] = {}
    rests = ties = segments = 0
    meters = []
    for measure in track.measures:
        signature = measure.header.timeSignature
        meters.append((signature.numerator, signature.denominator.value))
        expected_length = signature.numerator*4*gp.Duration.quarterTime//signature.denominator.value
        cursor = measure.header.start
        if any(voice.beats for voice in measure.voices[1:]):
            raise ValueError("Unexpected additional GP5 voice")
        for beat in measure.voices[0].beats:
            if beat.start != cursor or beat.duration.time <= 0:
                raise ValueError("Noncontiguous GP5 beat positions")
            a,b = beat.start-gp.Duration.quarterTime, beat.start-gp.Duration.quarterTime+beat.duration.time
            if beat.status == gp.BeatStatus.rest:
                if beat.notes: raise ValueError("Rest contains notes")
                rests += 1
            elif not beat.notes:
                raise ValueError("Empty non-rest beat")
            if len({n.string for n in beat.notes}) != len(beat.notes):
                raise ValueError("Simultaneous notes share a string")
            for note in beat.notes:
                segments += 1
                if note.type == gp.NoteType.tie:
                    previous = last.get(note.string)
                    if previous is None or previous[1] != a or previous[2:5] != [note.realValue,note.string,note.value]:
                        raise ValueError("Broken GP5 tie")
                    previous[1] = b; ties += 1
                elif note.type == gp.NoteType.normal:
                    row = [a,b,note.realValue,note.string,note.value,note.velocity]
                    rows.append(row); last[note.string] = row
                else:
                    raise ValueError("Unexpected GP5 note type")
            cursor += beat.duration.time
        if cursor-measure.header.start != expected_length:
            raise ValueError("GP5 measure length differs from explicit meter")
    return Counter(tuple(row) for row in rows), dict(logical_note_count=len(rows), note_segments=segments,
        tie_segments=ties, rest_beats=rests, bars=len(track.measures), meters=meters, tempo_bpm=song.tempo,
        tuning_midi=tuple(s.value for s in reversed(track.strings)), max_fret=track.fretCount,
        playback_program=track.channel.instrument, event_readback_exact=True)


def write_rhythmic_gp5(rhythm: RhythmReport, fingering: BassFingeringReport,
                      output_dir: Path, *, config: BassOutputConfig) -> dict:
    """Write all notes or an explicit refusal; original timing artifacts stay separate.

    A fixed integer BPM and fixed meter are supported. Notes are sliced at actual
    derived attacks/releases and bar boundaries; continuations use GP ties, while
    adjacent repeated attacks remain normal notes. Empty intervals become rests.
    """
    originals = tuple(BassOutputEvent(**{k:getattr(e,k) for k in BassOutputEvent.model_fields}) for e in rhythm.events)
    if plan_explicit_rhythm(originals,config=rhythm.config) != rhythm:
        raise ValueError("Rhythm report differs from its source")
    if rhythm.status != "planned" or fingering.status != "planned":
        raise ValueError("Complete rhythm and fingering plans required; use retained original/XML")
    if fingering.input_events_sha256 != rhythm.input_events_sha256 or len(fingering.events) != len(originals):
        raise ValueError("Fingering and rhythm sources differ")
    if fingering.tuning_midi != config.resolved_tuning or fingering.max_fret != config.resolved_max_fret:
        raise ValueError("Instrument settings differ")
    if not rhythm.config.tempo_bpm.is_integer():
        raise ValueError("GP5 requires integer BPM; explicit MusicXML remains available")
    gp = guitarpro.models
    quarter = gp.Duration.quarterTime
    clock = rhythm.config
    rows = []
    for original, timing, mapping in zip(originals,rhythm.events,fingering.events):
        if any(getattr(original,k) != getattr(mapping,k) for k in BassOutputEvent.model_fields):
            raise ValueError("Fingering changed an original event")
        if mapping.string is None or mapping.fret is None or config.resolved_tuning[-mapping.string]+mapping.fret != original.midi:
            raise ValueError("String/fret does not preserve sounding pitch")
        velocity = min(range(15,128,16),key=lambda v:(abs(v-original.velocity),v))
        rows.append((timing.score_start_units*quarter//clock.subdivisions_per_quarter,
            timing.score_end_units*quarter//clock.subdivisions_per_quarter,original.midi,mapping.string,mapping.fret,velocity))
    for i,a in enumerate(rows):
        for b in rows[i+1:]:
            if a[3]==b[3] and max(a[0],b[0])<min(a[1],b[1]):
                raise ValueError("Derived simultaneous notes share a string")
    song = gp.Song(tempo=int(clock.tempo_bpm),tempoName="Explicit user clock",tracks=[],measureHeaders=[])
    song.title = "Bass - explicit quantized derivative (not original timing)"
    track = gp.Track(song,name="Bass explicit rhythm",channel=gp.MidiChannel(instrument=33),measures=[],fretCount=config.resolved_max_fret)
    track.strings = [gp.GuitarString(number=i,value=p) for i,p in enumerate(reversed(config.resolved_tuning),1)]
    song.tracks.append(track)
    bar_ticks = clock.numerator*4*quarter//clock.denominator
    last = max((r[1] for r in rows),default=0)
    bars = max(1,(last+bar_ticks-1)//bar_ticks)
    if bars>128: raise ValueError("GP5 bar limit")
    durations = sorted([(gp.Duration(value=v,isDotted=d).time,v,d) for v in (1,2,4,8,16,32) for d in (False,True)],reverse=True)
    for bar in range(bars):
        left,right = bar*bar_ticks,(bar+1)*bar_ticks
        header = gp.MeasureHeader(number=bar+1,start=quarter+left,
            timeSignature=gp.TimeSignature(numerator=clock.numerator,denominator=gp.Duration(value=clock.denominator)))
        song.measureHeaders.append(header)
        measure = gp.Measure(track,header,clef=gp.MeasureClef.bass);track.measures.append(measure)
        voice = measure.voices[0]
        boundaries = sorted({left,right} | {t for row in rows for t in row[:2] if left<t<right})
        for start,end in zip(boundaries,boundaries[1:]):
            active = [r for r in rows if r[0]<=start<r[1]]
            cursor = start
            while cursor<end:
                options = [d for d in durations if d[0]<=end-cursor]
                if not options: raise ValueError("GP5 duration not representable; use MusicXML")
                length,value,dotted = options[0]
                beat = gp.Beat(voice,duration=gp.Duration(value=value,isDotted=dotted),start=quarter+cursor,
                    status=gp.BeatStatus.normal if active else gp.BeatStatus.rest)
                for row in active:
                    beat.notes.append(gp.Note(beat,value=row[4],string=row[3],velocity=row[5],
                        type=gp.NoteType.normal if cursor==row[0] else gp.NoteType.tie))
                voice.beats.append(beat);cursor+=length
    output_dir=Path(output_dir)
    output_dir.mkdir(parents=True,exist_ok=False)
    path=output_dir/"bass.explicit-rhythm.gp5"
    with path.open("xb") as stream: guitarpro.write(song,stream,version=(5,1,0))
    actual,back=read_gp5_events(path)
    if (actual != Counter(rows) or back["tuning_midi"]!=config.resolved_tuning or back["max_fret"]!=config.resolved_max_fret
        or back["tempo_bpm"]!=clock.tempo_bpm or back["playback_program"]!=33 or back["bars"]!=bars
        or back["meters"] != [(clock.numerator,clock.denominator)]*bars):
        raise ValueError("GP5 readback differs from complete explicit rhythm/mapping contract")
    result=dict(status="written",sha256=hashlib.sha256(path.read_bytes()).hexdigest(),readback=back,
        timing_basis="explicit-quantized-derivative",source_timing_preserved=False,origin_seconds=clock.origin_seconds,
        input_events_sha256=rhythm.input_events_sha256,velocity_changed_count=sum(e.velocity!=r[5] for e,r in zip(originals,rows)),
        warnings=["Original MIDI/XML retained separately. Add explicit origin to align score playback.",
            "Fixed integer BPM/meter only; no tuplets, swing or tempo/meter changes. No inferred acoustic correctness.",
            "Bass program33 and nearest GP5 velocity levels; original timbre/dynamics are not preserved."])
    (output_dir/"readback.json").write_text(json.dumps(result,indent=2)+'\n',encoding="utf-8")
    return result
