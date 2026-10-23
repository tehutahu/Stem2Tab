"""Private odd-only development evaluation; fixed candidate, no tuning."""
from pathlib import Path
import sys,json,time,hashlib,collections,argparse
import numpy as np
import librosa,soundfile as sf
import mir_eval.transcription as mt
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--baseline-root',type=Path,required=True,help='Private validated IDMT baseline root; only odd tracks read')
parser.add_argument('--results',type=Path,required=True,help='Existing fresh directory with a frozen plan.json')
args=parser.parse_args()
R=Path(__file__).resolve().parents[2]
B=args.baseline_root.resolve()
O=args.results.resolve()
sys.path[:0]=[str(R/'backend'),str(R/'backend/scripts')]
from pyin_periodicity_diagnostics import continuity_onsets,generate_audio,cases,SR
from src.evaluation.note_assembly import assemble_notes
from src.evaluation.pyin import _write_precise_midi,PyinTranscriber
from src.evaluation.io import read_midi,read_note_events_json,write_note_events_json
from src.evaluation.models import NoteEvent,NoteEventSet
from src.evaluation.metrics import evaluate_note_events,MetricConfig
from src.evaluation.adapters import AdapterConfig
def pairs(ref,est):
    """Return onset+pitch reference identities; offsets do not influence matching."""
    if not ref.notes or not est.notes:return {}
    def ar(x):return np.array([[n.start,n.end] for n in x.notes]),np.array([440*2**((n.midi-69)/12) for n in x.notes])
    ri,rp=ar(ref);ei,ep=ar(est)
    return dict(mt.match_notes(ri,rp,ei,ep,onset_tolerance=.05,pitch_tolerance=50,offset_ratio=None))
def summarize(rows):
    """Aggregate micro note metrics, retaining a separate frame-F1 macro."""
    result={}
    for method in ['baseline','periodicity_veto']:
        items=[r for r in rows if r['method']==method]
        nr=sum(r['reference_notes'] for r in items);ne=sum(r['estimated_notes'] for r in items)
        tp=sum(r['reference_notes']-r['missed_note_count'] for r in items)
        op=sum(round(r['onset_offset_recall']*r['reference_notes']) for r in items)
        result[method]=dict(reference_notes=nr,estimated_notes=ne,matched=tp,extra=ne-tp,missed=nr-tp,precision=tp/ne if ne else 0,recall=tp/nr if nr else None,onset_f1=2*tp/(nr+ne) if nr+ne else None,onset_offset_f1=2*op/(nr+ne) if nr+ne else None,frame_f1_macro=sum(r['frame_f1'] for r in items)/len(items))
    return result
def process(key,audio,frame,reference,baseline,folder):
    """Evaluate one fixed candidate and preserve private reference identity comparisons."""
    folder.mkdir(exist_ok=False,parents=True)
    start=time.perf_counter();onsets,decisions=continuity_onsets(audio,frame)
    estimated=assemble_notes(frame['f0'],frame['voiced_flag'],frame['time'],len(audio)/SR,active=frame['active'],onset_times=onsets)
    _write_precise_midi(estimated,folder/'candidate.mid');estimated=read_midi(folder/'candidate.mid')
    elapsed=time.perf_counter()-start
    (folder/'private-onset-decisions.json').write_text(json.dumps(decisions,indent=2))
    write_note_events_json(estimated,folder/'candidate.json')
    rows=[];pairsets={};strata=[]
    for method,events in [('baseline',baseline),('periodicity_veto',estimated)]:
        row=dict(case=key,method=method,reference_notes=len(reference.notes),estimated_notes=len(events.notes),vetoed=sum(d['veto'] for d in decisions),detected_onsets=len(decisions),assembly_seconds=elapsed if method=='periodicity_veto' else None)
        if reference.notes:row.update(evaluate_note_events(reference,events,config=MetricConfig(),runtime_seconds=0).model_dump())
        else:row['metrics_defined']=False
        rows.append(row);pairsets[method]=pairs(reference,events)
    bp,cp=pairsets['baseline'],pairsets['periodicity_veto'];common=sorted(bp.keys()&cp.keys())
    comparison=dict(case=key,lost_reference_ids=[int(i) for i in sorted(bp.keys()-cp.keys())],rescued_reference_ids=[int(i) for i in sorted(cp.keys()-bp.keys())],common=len(common))
    for method,events in [('baseline',baseline),('periodicity_veto',estimated)]:
        errors=[];onerrors=[]
        for i in common:
            r=reference.notes[i];e=events.notes[pairsets[method][i]]
            errors.append((e.end-r.end)*1000);onerrors.append((e.start-r.start)*1000)
        comparison[method+'_paired_offset_errors_ms']=errors
        comparison[method+'_paired_onset_errors_ms']=onerrors
    changed=[dict(pitch=n.midi,start=n.start,end=n.end) for n in estimated.notes if n not in baseline.notes]
    (folder/'private-changed-boundaries.json').write_text(json.dumps(changed,indent=2))
    return rows,comparison,estimated
plan=json.loads((O/'plan.json').read_text())
assert plan['source_sha256']==hashlib.sha256((R/'backend/scripts/pyin_periodicity_diagnostics.py').read_bytes()).hexdigest()
assert not (O/'odd-summary.json').exists() and not (O/'new-synthetic').exists()
rows=[];comparisons=[];private_strata=[]
for number in range(1,18,2):
    key=f'{number:03}';f=B/f'baseline/{key}/runs/direct__pyin';fp=f/'pyin_frames.npz';digest=hashlib.sha256(fp.read_bytes()).hexdigest()
    audio,_=librosa.load(B/f'dataset/audio/{key}.wav',sr=SR,mono=True)
    with np.load(fp) as loaded:frames={k:loaded[k] for k in loaded.files}
    reference=read_midi(B/f'references/{key}.mid');baseline=read_note_events_json(f/'events.json')
    rebuilt=assemble_notes(frames['f0'],frames['voiced_flag'],frames['time'],len(audio)/SR,active=frames['active'],onset_times=frames['onset_times'])
    temporary=O/f'reconstruction-{key}.mid';_write_precise_midi(rebuilt,temporary);assert read_midi(temporary).notes==baseline.notes
    rr,comparison,candidate=process(key,audio,frames,reference,baseline,O/'odd'/key)
    rows+=rr;comparisons.append(comparison)
    annotations=json.loads((B/f'references/{key}-source-annotations.json').read_text());annotations.sort(key=lambda a:(float(a['onsetSec']),int(a['pitch']),float(a['offsetSec'])))
    for method,events in [('baseline',baseline),('periodicity_veto',candidate)]:
        matched=pairs(reference,events)
        for index,(r,a) in enumerate(zip(reference.notes,annotations)):
            d=dict(track=key,reference_id=index,method=method,expression=a['expressionStyle'],excitation=a['excitationStyle'],duration_group='<150ms' if r.end-r.start<.15 else '150-350ms' if r.end-r.start<.35 else '>=350ms',matched=index in matched)
            if index in matched:
                e=events.notes[matched[index]];d.update(onset_error_ms=(e.start-r.start)*1000,offset_error_ms=(e.end-r.end)*1000)
            private_strata.append(d)
    assert hashlib.sha256(fp.read_bytes()).hexdigest()==digest
    print('Odd completed',key,flush=True)
(O/'odd-per-track.json').write_text(json.dumps(rows,indent=2));(O/'private-odd-comparisons.json').write_text(json.dumps(comparisons,indent=2));(O/'private-strata.json').write_text(json.dumps(private_strata,indent=2))
summary=summarize(rows)
for method in summary:
    for field in ['onset_error_ms','offset_error_ms']:
        values=[x[field] for x in private_strata if x['method']==method and x['matched']]
        summary[method][field+'_mean']=float(np.mean(values));summary[method][field+'_mae']=float(np.mean(np.abs(values)))
paired=dict(lost=sum(len(c['lost_reference_ids']) for c in comparisons),rescued=sum(len(c['rescued_reference_ids']) for c in comparisons),common=sum(c['common'] for c in comparisons))
for method in summary:
    for typ in ['offset','onset']:
        values=[x for c in comparisons for x in c[method+f'_paired_{typ}_errors_ms']]
        paired[method+f'_{typ}_mean_ms']=float(np.mean(values));paired[method+f'_{typ}_mae_ms']=float(np.mean(np.abs(values)))
result=dict(metrics=summary,paired=paired,vetoed_onsets=sum(r['vetoed'] for r in rows if r['method']=='baseline'))
(O/'odd-summary.json').write_text(json.dumps(result,indent=2));print(json.dumps(result),flush=True)
synrows=[];syncomparisons=[]
for spec in cases():
    folder=O/'new-synthetic'/spec['id'];folder.mkdir(parents=True)
    audio,refs=generate_audio(spec);sf.write(folder/'audio.wav',audio,SR,subtype='PCM_16')
    # Match the actual adapter input samples including PCM16 quantization.
    audio,_=librosa.load(folder/'audio.wav',sr=SR,mono=True)
    ref=NoteEventSet(notes=tuple(NoteEvent(start=s,end=e,midi=p,velocity=80/127) for p,s,e in refs))
    _write_precise_midi(ref,folder/'reference.mid');ref=read_midi(folder/'reference.mid')
    result=PyinTranscriber().run(folder/'audio.wav',folder/'preserved-raw',config=AdapterConfig(demucs_model='unused',demucs_cache_dir=folder/'unused'))
    fp=folder/'preserved-raw/pyin_frames.npz';digest=hashlib.sha256(fp.read_bytes()).hexdigest()
    with np.load(fp) as loaded:frames={k:loaded[k] for k in loaded.files}
    rr,comparison,_=process(spec['id'],audio,frames,ref,result.events,folder/'analysis');synrows+=rr;syncomparisons.append(comparison)
    assert hashlib.sha256(fp.read_bytes()).hexdigest()==digest
    (O/'synthetic-progress.json').write_text(json.dumps(synrows,indent=2))
    print('Synthetic completed',spec['id'],flush=True)
(O/'synthetic-summary.json').write_text(json.dumps(synrows,indent=2));(O/'private-synthetic-comparisons.json').write_text(json.dumps(syncomparisons,indent=2))
print('Study completed',flush=True)
