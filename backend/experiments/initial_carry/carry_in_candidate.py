"""Offline candidate: retain only explicitly closed first-prologue bass ties.

Existing notes stay byte-for-byte equal as data. A new note is a boundary-clipped
model declaration, never a measured onset or an independently verified note.
Later unknown ties, malformed headers, retriggers and inferred closures do not
create notes. No product decoder or pinned upstream source is modified.
"""
from __future__ import annotations
import math


def candidate(notes,rows):
    result=[dict(n) for n in notes]
    candidates=[];diagnostics=[]
    boundary=None;program=None;keys=[];end=None
    for i,r in enumerate(rows):
        if r['type']=='chunk_boundary':
            if boundary is not None:
                return result,[],[dict(reason='first_prologue_not_terminated')]
            boundary=r
            if r['seek_time']!=0:
                return result,[],[dict(reason='first_boundary_not_zero')]
            continue
        if r['type']!='token':continue
        etype,value=r['event_type'],r['value']
        if boundary is None:return result,[],[dict(reason='missing_initial_boundary')]
        if etype=='tie':end=i;break
        if etype=='program':program=value
        elif etype=='pitch' and program is not None:keys.append((program,value))
        else:return result,[],[dict(reason='unsupported_or_malformed_initial_prologue')]
    if end is None:return result,[],[dict(reason='no_complete_initial_prologue')]
    if len(set(keys))!=len(keys):return result,[],[dict(reason='duplicate_initial_tie_key')]
    pending={k:dict(program=k[0],pitch=k[1],prologue_end_row=end) for k in keys if k[0] in [32,33] and isinstance(k[1],int) and 0<=k[1]<=127}
    for k in keys:
        if k not in pending:diagnostics.append(dict(key=k,reason='not_supported_bass_key'))
    tick=0.;velocity=None
    for i,r in enumerate(rows[end+1:],end+1):
        if r['type']=='chunk_boundary':break
        if r['type']!='token':continue
        etype,value=r['event_type'],r['value']
        if etype=='program':program=value
        elif etype=='velocity':velocity=value
        elif etype=='shift':
            if not isinstance(value,(int,float)) or not math.isfinite(value) or value<0:
                return result,[],[dict(reason='invalid_shift')]
            if value>0:
                newtick=value/100
                if newtick<tick:return result,[],[dict(reason='backward_shift')]
                tick=newtick
        elif etype=='pitch' and program is not None and velocity is not None:
            key=(program,value)
            if key not in pending:continue
            initial=pending.pop(key)
            cutoff=boundary['next_seek_time']
            if velocity!=0:
                diagnostics.append(dict(key=key,reason='first_matching_action_is_retrigger_not_explicit_off',row=i))
                continue
            if tick<=0 or cutoff is not None and tick>=cutoff:
                diagnostics.append(dict(key=key,reason='explicit_off_outside_first_window',row=i))
                continue
            # Refuse to duplicate or intersect an already exported same-key note.
            if any(n['pitch']==key[1] and n['program']==key[0] and n['onset']<tick and n['offset']>0 for n in notes):
                diagnostics.append(dict(key=key,reason='already_represented_or_overlapping',row=i))
                continue
            initial.update(onset=0.,offset=tick,explicit_off_row=i,
                left_censored=True,true_onset_unknown=True,derived_from_model_tokens=True)
            candidates.append(initial)
    diagnostics.extend(dict(key=k,reason='no_explicit_off_in_first_chunk') for k in pending)
    first_index=max((n['index'] for n in notes),default=-1)+1
    for j,c in enumerate(candidates):
        c['output_ordinal']=len(result)
        result.append(dict(index=first_index+j,onset=c['onset'],offset=c['offset'],pitch=c['pitch'],
            program=c['program'],instrument='electric_bass' if c['program']==33 else 'acoustic_bass',is_drum=False))
    return result,candidates,diagnostics
