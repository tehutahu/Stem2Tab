"""Meaningful boundary/identity checks; synthetic tokens are not real-song truth."""
from carry_in_candidate import candidate


def tok(kind,value):return dict(type='token',event_type=kind,value=value)
def boundary(t=0,n=5):return dict(type='chunk_boundary',seek_time=t,next_seek_time=n)
def stream(body):return [boundary(),tok('program',33),tok('pitch',39),tok('tie',0),*body]
def close(t=180):return [tok('shift',t),tok('velocity',0),tok('pitch',39)]


def main():
    tests=[]
    base=[dict(index=0,onset=1.8,offset=2.38,pitch=34,program=33,instrument='electric_bass',is_drum=False)]
    out,added,diag=candidate(base,stream(close()))
    assert out[:1]==base and base[0]['onset']==1.8
    assert len(out)==2 and added[0]['left_censored'] and added[0]['true_onset_unknown']
    assert out[1]['onset']==0 and out[1]['offset']==1.8 and out[1]['pitch']==39
    tests.append('explicit initial tie and explicit off retain a clipped carry-in without changing existing note')
    assert candidate([],stream(close(0)))[1]==[]
    assert candidate([],stream([tok('shift',50),tok('velocity',1),tok('pitch',39),*close()]))[1]==[]
    assert candidate([],stream([boundary(5,None),*close()]))[1]==[]
    tests.append('zero length, retrigger-first and later chunk cannot invent initial carry-in')
    assert candidate([],stream([]))[1]==[]
    assert candidate([],[boundary(),tok('program',33),tok('pitch',39),tok('shift',180),*close()])[1]==[]
    assert candidate([],stream([tok('shift',200),*close(180)]))[1]==[]
    tests.append('missing off, malformed header and backwards time are diagnosed without repair')
    overlap=[dict(index=3,onset=0,offset=1.8,pitch=39,program=33,instrument='electric_bass',is_drum=False)]
    assert candidate(overlap,stream(close()))[1]==[]
    assert candidate([],[boundary(1,6),tok('program',33),tok('pitch',39),tok('tie',0),*close()])[1]==[]
    tests.append('already present note and nonzero initial seek never duplicate boundary notes')
    no_tie=[boundary(),tok('tie',0),tok('shift',20),tok('program',33),tok('velocity',1),tok('pitch',40)]
    assert candidate(base,no_tie)[0]==base
    tests.append('no declared initial pitch leaves every original note unchanged')
    return tests


if __name__=='__main__':
    import json
    print(json.dumps(dict(tests=main(),passed=True)))
