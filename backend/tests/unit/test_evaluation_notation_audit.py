"""Generated notation semantics across ties, voices, tempos and failure paths."""
from pathlib import Path
import hashlib,json
import pretty_midi
import pytest
from src.evaluation.notation_audit import audit_notation,read_generated_notation
from src.evaluation.models import NoteEvent,NoteEventSet
from src.pipelines.tab import midi_to_gp5

def midi(tmp_path,events,bpm=120):
    score=pretty_midi.PrettyMIDI(initial_tempo=bpm,resolution=960);bass=pretty_midi.Instrument(program=33)
    bass.notes=[pretty_midi.Note(80,p,a*60/bpm,(a+d)*60/bpm) for p,a,d in events];score.instruments.append(bass);path=tmp_path/'input.mid';score.write(str(path));return path

def assert_events(actual,expected,bpm=120):
    notes=sorted(actual.notes,key=lambda n:(n.start,n.end,n.midi));target=sorted([(p,a*60/bpm,(a+d)*60/bpm) for p,a,d in expected],key=lambda n:(n[1],n[2],n[0]))
    assert len(notes)==len(target)
    for n,(p,a,b) in zip(notes,target):assert n.midi==p;assert n.start==pytest.approx(a,abs=2e-6);assert n.end==pytest.approx(b,abs=2e-6)

def test_gp5_tie_on_fretted_string_in_nonfinal_measure(tmp_path):
    events=[(41,1.5,1.75),(43,5,.5),(45,9,.5)];path=midi(tmp_path,events);selected=midi_to_gp5(path,tmp_path/'out.gp5')
    assert selected.suffix=='.gp5';assert_events(read_generated_notation(selected),events)

def test_musicxml_voice_specific_ties_preserve_repeated_pitch_attacks(tmp_path):
    events=[(28,0,1.5),(40,.5,4.5),(41,.75,.3125),(40,5.25,.25)]
    path=midi(tmp_path,events);selected=midi_to_gp5(path,tmp_path/'out.gp5')
    assert selected.suffix=='.musicxml';assert_events(read_generated_notation(selected),events)

@pytest.mark.parametrize('bpm',[90,120.5])
def test_notation_seconds_use_midi_tempo_including_xml_fractional_fallback(tmp_path,bpm):
    events=[(28,.5,.5),(33,3.75,1),(38,6,.25)];path=midi(tmp_path,events,bpm)
    result=audit_notation(path,tmp_path/'audit')
    # GP5 rounds the MIDI's microsecond tempo to an integer BPM; report even
    # that small seconds drift instead of silently relaxing exactness.
    if bpm == 90:
        assert result['status']=='integrity_error' and not result['exact']
        assert result['ordered_pitch_sequence_equal']
        assert 0 < result['max_boundary_drift_seconds'] < 0.00001
    else:
        assert result['status']=='success' and result['exact']
    assert_events(read_generated_notation(tmp_path/'audit'/result['notation_path']),events,bpm)

def test_audit_separates_reference_metrics_from_format_integrity(tmp_path):
    path=midi(tmp_path,[(28,0,1)])
    reference=NoteEventSet(notes=(NoteEvent(start=0,end=.5,midi=40,velocity=.7),))
    result=audit_notation(path,tmp_path/'audit',reference=reference)
    assert result['exact'] and result['input_metrics']['onset_f1']==0
    assert result['notation_metrics']['onset_f1']==0

def test_audit_records_export_failure_without_modifying_input(tmp_path,monkeypatch):
    path=midi(tmp_path,[(28,0,1)]);before=hashlib.sha256(path.read_bytes()).hexdigest()
    def fail(*args,**kwargs):raise OSError('simulated write failure')
    monkeypatch.setattr('src.evaluation.notation_audit.midi_to_gp5',fail)
    result=audit_notation(path,tmp_path/'audit')
    assert result['status']=='error' and result['error_type']=='OSError'
    assert hashlib.sha256(path.read_bytes()).hexdigest()==before
    assert json.loads((tmp_path/'audit/summary.json').read_text())['status']=='error'

def test_new_output_required_and_unknown_format_rejected(tmp_path):
    path=midi(tmp_path,[(28,0,1)]);out=tmp_path/'audit';audit_notation(path,out)
    with pytest.raises(ValueError,match='new or empty'):audit_notation(path,out)
    with pytest.raises(ValueError,match='Only generated'):read_generated_notation(path)

def test_empty_score_is_valid_integrity_without_accuracy_claim(tmp_path):
    result=audit_notation(midi(tmp_path,[]),tmp_path/'audit')
    assert result['status']=='success' and result['expected_notes']==result['reloaded_notes']==0
    assert result['reference_available'] is False and 'notation_metrics' not in result

def test_xml_bad_tie_is_not_silently_dropped(tmp_path):
    path=tmp_path/'bad.musicxml';path.write_text('''<score-partwise><part><measure><attributes><divisions>1</divisions><time><beats>4</beats><beat-type>4</beat-type></time></attributes><direction><sound tempo="120"/></direction><note><pitch><step>E</step><octave>1</octave></pitch><duration>1</duration><tie type="stop"/></note></measure></part></score-partwise>''')
    with pytest.raises(ValueError,match='Unmatched'):read_generated_notation(path)

def test_benchmark_opt_in_and_default_off(tmp_path,monkeypatch):
    import numpy as np,soundfile as sf
    from src.evaluation import benchmark
    from src.evaluation.adapters import TranscriptionResult
    from src.evaluation.io import write_midi
    audio=tmp_path/'input.wav';sf.write(audio,np.zeros(2205),22050)
    notes=NoteEventSet(notes=(NoteEvent(start=0,end=.5,midi=28,velocity=.7),))
    class Fake:
        name='fake'
        def run(self,audio_path,output_dir,*,config):
            return TranscriptionResult(raw_midi_path=write_midi(notes,output_dir/'raw.mid'),events=notes)
    monkeypatch.setattr(benchmark,'_resolve_transcriber',lambda name:Fake())
    for enabled in [False,True]:
        out=tmp_path/str(enabled);args=['--audio',str(audio),'--transcribers','fake','--output-dir',str(out)]
        if enabled:args+=['--notation-audit']
        _,records,_=benchmark.run_benchmark(benchmark.build_parser().parse_args(args))
        assert records[0].status=='success'
        assert (out/'runs/direct__fake/notation/summary.json').exists()==enabled
        assert ('notation_status' in records[0].adapter_metadata)==enabled


def test_benchmark_requested_notation_failure_retains_transcription_metrics(tmp_path, monkeypatch):
    import numpy as np
    import soundfile as sf
    from src.evaluation import benchmark
    from src.evaluation.adapters import TranscriptionResult
    from src.evaluation.io import write_midi

    audio = tmp_path / 'input.wav'
    sf.write(audio, np.zeros(2205), 22050)
    notes = NoteEventSet(notes=(NoteEvent(start=0, end=.5, midi=28, velocity=.7),))
    reference = write_midi(notes, tmp_path / 'reference.mid')

    class Fake:
        name = 'fake'

        def run(self, audio_path, output_dir, *, config):
            return TranscriptionResult(
                raw_midi_path=write_midi(notes, output_dir / 'raw.mid'), events=notes,
            )

    monkeypatch.setattr(benchmark, '_resolve_transcriber', lambda name: Fake())
    monkeypatch.setattr('src.evaluation.notation_audit.audit_notation',
                        lambda *a, **kw: {'status': 'error', 'seconds': 0, 'error': 'fixture failure'})
    args = benchmark.build_parser().parse_args([
        '--audio', str(audio), '--reference', str(reference), '--transcribers', 'fake',
        '--output-dir', str(tmp_path / 'run'), '--notation-audit',
    ])
    _, records, _ = benchmark.run_benchmark(args)
    assert records[0].status == 'error'
    assert records[0].metrics.onset_f1 == 1
    assert records[0].estimated_note_count == 1
    assert records[0].error == 'Notation audit: fixture failure'
