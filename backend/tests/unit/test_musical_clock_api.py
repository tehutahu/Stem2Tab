"""Exercise explicit clock API with synthetic bytes and unchanged source storage."""
import hashlib

import pytest

from tests.unit.test_midi_trials_api import client, _post, _midi
from tests.unit.test_evaluation_musical_clock import read_musical_xml


def request_for(summary, **settings):
    return dict(input_sha256=summary["input_sha256"],clock=dict(bpm=120,numerator=3,denominator=4,origin_seconds=.25,**settings))


def test_exact_derivative_is_separate_repeatable_and_hash_verified(client,tmp_path):
    summary=_post(client,data=_midi([(0,240,28),(481,482,28),(1900,4300,34)])).json()
    folder=tmp_path/"midi-trials"/summary["trial_id"]
    before={str(p.relative_to(folder)):p.read_bytes() for p in folder.rglob("*") if p.is_file()}
    url=f"/api/v1/musical-clock/{summary['trial_id']}/derive"
    body=request_for(summary)
    response=client.post(url,json=body)
    assert response.status_code==200,response.text
    result=response.json()
    assert client.post(url,json=body).json()==result
    assert result["report"]["clock"]["provenance"]=="explicit-user-input"
    assert result["report"]["estimation_status"]=="not-estimated"
    assert result["report"]["quantization"]=="none"
    assert hashlib.sha256(result["musicxml"].encode()).hexdigest()==result["musicxml_sha256"]
    assert result["report"]["source_sha256"]==summary["input_sha256"]
    actual=read_musical_xml(result["musicxml"].encode())
    original=next(a for a in summary["artifacts"] if a["key"]=="storage-clock-xml")
    assert actual["events"]==read_musical_xml(client.get(original["url"]).content)["events"]
    assert before=={str(p.relative_to(folder)):p.read_bytes() for p in folder.rglob("*") if p.is_file()}


@pytest.mark.parametrize("change",["request_hash","source_bytes","symlink"])
def test_rejects_changed_or_unsafe_source(client,tmp_path,change):
    summary=_post(client).json()
    path=tmp_path/"midi-trials"/summary["trial_id"]/"input.mid"
    body=request_for(summary)
    if change=="request_hash": body["input_sha256"]="f"*64
    elif change=="source_bytes": path.write_bytes(path.read_bytes()+b"changed")
    else:
        data=path.read_bytes(); path.unlink(); target=tmp_path/"copy.mid";target.write_bytes(data);path.symlink_to(target)
    response=client.post(f"/api/v1/musical-clock/{summary['trial_id']}/derive",json=body)
    assert response.status_code in {404,409}


def test_missing_or_invalid_clock_never_receives_default_bpm(client):
    summary=_post(client).json()
    url=f"/api/v1/musical-clock/{summary['trial_id']}/derive"
    assert client.post(url,json={"input_sha256":summary["input_sha256"]}).status_code==422
    body=request_for(summary);body["clock"]["bpm"]=120.0001
    assert client.post(url,json=body).status_code==422
    body=request_for(summary);body["clock"]["quantize"]=True
    assert client.post(url,json=body).status_code==422
