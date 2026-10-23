"""Offline periodic-continuity onset-veto hypothesis; no production defaults change."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

SR = 22050
CORRELATION_MIN = .98


def periodic_correlation(audio: np.ndarray, center: float, period: float) -> float:
    """Compare two one-period-shifted windows without changing amplitude or phase."""
    first = round((center-period)*SR)
    last = round((center+period)*SR)
    if first-period*SR < 0 or last > len(audio) or last-first < 3:
        return float('nan')
    positions = np.arange(first,last,dtype=float)
    current = audio[first:last].astype(float)
    shifted=positions-period*SR
    lower=np.floor(shifted).astype(int);fraction=shifted-lower
    previous=audio[lower]*(1-fraction)+audio[lower+1]*fraction
    current -= current.mean();previous -= previous.mean()
    norm = np.linalg.norm(current)*np.linalg.norm(previous)
    return float(np.dot(current,previous)/norm) if norm > 1e-20 else float('nan')


def continuity_onsets(audio: np.ndarray, frames: dict) -> tuple[np.ndarray,list[dict]]:
    """Veto only stable, continuously periodic onsets; preserve all uncertain cases."""
    times=frames['time'];f0=frames['f0'];voiced=frames['voiced_flag'];active=frames['active']
    if audio.ndim != 1 or not (times.ndim==1 and f0.shape==voiced.shape==active.shape==times.shape):
        raise ValueError('mono audio and aligned frame arrays required')
    kept=[];decisions=[]
    for onset in frames['onset_times']:
        index=int(np.argmin(abs(times-onset)))
        frequency=f0[index]
        reject=False;scores=[];stable=False;period=None;reason='invalid_f0'
        if np.isfinite(frequency) and frequency>0:
            period=1/float(frequency)
            local=(times>=onset-period)&(times<=onset+period)
            frequencies=f0[local]
            stable=(bool(sum(local)>=3) and bool(np.all(voiced[local]&active[local]))
                    and bool(np.all(np.isfinite(frequencies)&(frequencies>0))))
            if stable:
                stable=bool(np.all(abs(12*np.log2(frequencies/frequency))<=.5))
            if stable:
                scores=[periodic_correlation(audio,float(onset)+shift*period,period) for shift in [-2,0,2]]
                reject=all(np.isfinite(x) and x>=CORRELATION_MIN for x in scores)
                reason='periodic_veto' if reject else 'uncertain_or_nonperiodic'
            else:reason='pitch_voicing_or_activity_change'
        if not reject:kept.append(float(onset))
        decisions.append(dict(onset=float(onset),period_seconds=period,stable=stable,correlations=[x if np.isfinite(x) else None for x in scores],veto=reject,reason=reason))
    return np.array(kept),decisions


def cases() -> list[dict]:
    """New fixed counterexamples, with independent pitches, timings and envelopes."""
    return [
      dict(id='rest_45ms',notes=[(31,.21,.21,1),(31,.465,.23,1)],mode='normal'),
      dict(id='rest_90ms',notes=[(36,.21,.17,1),(36,.47,.22,.4)],mode='normal'),
      dict(id='short_low_notes',notes=[(26,.21,.08,1),(31,.36,.10,.65),(36,.55,.12,.8)],mode='normal'),
      dict(id='equal_repicks',notes=[(36,.21,.26,1),(36,.47,.26,1),(36,.73,.26,1)],mode='normal'),
      dict(id='softer_repicks',notes=[(31,.21,.29,1),(31,.50,.29,.09),(31,.79,.29,.4)],mode='normal'),
      dict(id='phase_continuous_repicks',notes=[(33,.21,.27,1),(33,.48,.27,.9),(33,.75,.27,.8)],mode='continuous'),
      dict(id='phase_continuous_rising_repicks',notes=[(33,.21,.27,.8),(33,.48,.27,.9),(33,.75,.27,1)],mode='continuous'),
      dict(id='legato_changes',notes=[(31,.21,.28,1),(33,.49,.22,.8),(36,.71,.27,.9)],mode='legato'),
      dict(id='weak_release',notes=[(31,.21,.86,1)],mode='decay'),
      dict(id='vibrato_sustain',notes=[(36,.21,.89,1)],mode='vibrato'),
      dict(id='silent',notes=[],mode='normal'),
    ]


def generate_audio(spec: dict) -> tuple[np.ndarray,list[tuple]]:
    """Render new deterministic harmonic signals; endpoints are nominal, not perceptual."""
    duration=max((s+d for p,s,d,g in spec['notes']),default=.8)+.35
    audio=np.zeros(round(duration*SR));refs=[];phase=.73
    for i,(pitch,start,duration,gain) in enumerate(spec['notes']):
        first=round(start*SR);count=round(duration*SR);t=np.arange(count)/SR
        hz=440*2**((pitch-69)/12)
        if spec['mode']=='vibrato':
            curve=phase+2*np.pi*np.cumsum(hz*2**(.22*np.sin(2*np.pi*5.1*t)/12))/SR
        else:curve=phase+2*np.pi*hz*t
        sound=sum(a*np.sin(h*curve+.07*h) for h,a in [(1,1),(2,.33),(3,.11),(5,.035)])
        env=np.ones(count)
        if spec['mode'] in ['decay','vibrato']:env*=np.exp(np.linspace(0,np.log(.003 if spec['mode']=='decay' else .18),count))
        attack=round(.008*SR);release=round(.028*SR)
        if spec['mode'] not in ['legato','continuous'] or i==0:env[:attack]*=np.linspace(0,1,attack)
        if spec['mode'] not in ['legato','continuous'] or i==len(spec['notes'])-1:env[-release:]*=np.linspace(1,0,release)
        audio[first:first+count]+=sound*env*gain
        phase=float((phase+2*np.pi*hz*count/SR)%(2*np.pi))
        refs.append((pitch,first/SR,(first+count)/SR))
    peak=np.max(abs(audio),initial=0)
    if peak:audio*=.39/peak
    return audio,refs


def plan(output: Path) -> None:
    """Freeze one hypothesis and criteria before reading development outcomes."""
    output.mkdir(parents=True,exist_ok=False)
    data=dict(hypothesis='veto stable onset only when lag-one-period NCC >=.98 before/at/after onset',
      correlation_min=CORRELATION_MIN,pitch_stability_cents=50,local_pitch_window_periods=1,
      correlation_centers_periods=[-2,0,2],correlation_window_periods=2,sample_rate=SR,
      correlation_definition='mean removed NCC; fractional lag linear interpolation; sample bounds rounded to nearest; norm <=1e-20 or missing samples preserve onset',
      criteria=['no lost baseline reference matches overall or per odd track','recall nondecreasing',
        'onset+offset F1 strictly improves','paired offset MAE nonincreasing',
        'each synthetic group has no increased miss/extra or reduced onset+offset F1',
        'no bridging rests or raw-run limits; no production/default changes'],
      scope='odd 001..017 only; even excluded; one candidate, no threshold tuning',
      new_synthetic=cases(),source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    (output/'plan.json').write_text(json.dumps(data,indent=2))


def main() -> int:
    """Freeze a private plan; evaluation driver consumes only this fixed candidate."""
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--freeze-plan',type=Path,required=True)
    args=p.parse_args();plan(args.freeze_plan);return 0


if __name__=='__main__':raise SystemExit(main())
