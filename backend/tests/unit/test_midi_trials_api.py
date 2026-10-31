"""Generated-MIDI API checks; no models, separation, Celery or external HTTP."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
import mido
import pytest

from src.api import midi_trials
from src.api.main import app
from src.core.config import settings
from src.worker import tasks
from tests.unit.test_evaluation_midi_clock import clock_source
from tests.unit.test_evaluation_notation_clock import read_clock


def _midi(events: list[tuple[int,int,int]] | None = None, *, tail: int = 0) -> bytes:
    midi = mido.MidiFile(type=1,ticks_per_beat=480)
    track = mido.MidiTrack([mido.MetaMessage("set_tempo",tempo=500000,time=0),
                           mido.Message("program_change",program=33,time=0)])
    items = []
    for start,end,pitch in events if events is not None else [(0,240,28),(480,960,33)]:
        items += [(start,1,mido.Message("note_on",note=pitch,velocity=95)),
                  (end,0,mido.Message("note_off",note=pitch,velocity=0))]
    previous=0
    for tick,_,message in sorted(items,key=lambda item:item[:2]):
        message.time=tick-previous
        track.append(message)
        previous=tick
    track.append(mido.MetaMessage("end_of_track",time=tail))
    midi.tracks.append(track)
    stream=io.BytesIO()
    midi.save(file=stream)
    return stream.getvalue()


@pytest.fixture
def client(tmp_path: Path,monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(settings,"file_bucket_path",tmp_path)
    def forbidden(*args,**kwargs):
        raise AssertionError("Saved-MIDI import must never call jobs/models/separation/transcription")
    for name in ("ensure_model","separate_stems","transcribe_midi","midi_to_gp5"):
        monkeypatch.setattr(tasks,name,forbidden)
    monkeypatch.setattr(tasks.process_job,"apply_async",forbidden)
    return TestClient(app,raise_server_exceptions=False)


def _post(client: TestClient,data: bytes | None = None,filename: str = "saved.mid",**values):
    form={"profile":"bass4","source_kind":"local-midi",**values}
    return client.post("/api/v1/midi-trials",files={"file":(filename,_midi() if data is None else data,"audio/midi")},data=form)


def _artifact(summary: dict,key: str) -> dict:
    return next(a for a in summary["artifacts"] if a["key"]==key)


def test_notation_clock_is_read_only_exact_and_repeatable(client,tmp_path):
    summary=_post(client,data=_midi([(13,481,28),(481,482,28),(1900,4300,34)])).json()
    folder=tmp_path/"midi-trials"/summary["trial_id"]
    before={str(p.relative_to(folder)):p.read_bytes() for p in folder.rglob("*") if p.is_file()}
    base=client.get(_artifact(summary,"storage-clock-xml")["url"])
    url=f"/api/v1/midi-trials/{summary['trial_id']}/notation-clock/double.musicxml"
    candidate=client.get(url)
    assert candidate.status_code==200 and candidate.content==client.get(url).content
    a,b=read_clock(base.content),read_clock(candidate.content)
    assert a["events"]==b["events"] and a["seconds"]==b["seconds"]
    assert a["bars"]*2==b["bars"] and a["tempo"]*2==b["tempo"]
    assert candidate.headers["x-source-midi-sha256"]==summary["input_sha256"]
    assert before=={str(p.relative_to(folder)):p.read_bytes() for p in folder.rglob("*") if p.is_file()}


@pytest.mark.parametrize("key",["original-midi","storage-clock-xml"])
def test_notation_clock_rejects_changed_source_artifacts(client,tmp_path,key):
    summary=_post(client).json()
    folder=tmp_path/"midi-trials"/summary["trial_id"]
    path=folder/midi_trials.ARTIFACTS[key][0]
    path.write_bytes(path.read_bytes()+b"changed")
    result=client.get(f"/api/v1/midi-trials/{summary['trial_id']}/notation-clock/double.musicxml")
    assert result.status_code==409 and result.json()["detail"]["code"]=="artifact_changed"


def test_notation_clock_rejects_other_exporter_even_with_valid_hash(client,tmp_path):
    summary=_post(client).json()
    folder=tmp_path/"midi-trials"/summary["trial_id"]
    path=folder/midi_trials.ARTIFACTS["storage-clock-xml"][0]
    path.write_bytes(path.read_bytes()+b"\n")
    artifact=_artifact(summary,"storage-clock-xml")
    artifact.update(bytes=path.stat().st_size,sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    (folder/"summary.json").write_text(json.dumps(summary))
    result=client.get(f"/api/v1/midi-trials/{summary['trial_id']}/notation-clock/double.musicxml")
    assert result.status_code==409 and result.json()["detail"]["code"]=="unsupported_source_export"


@pytest.mark.parametrize("kind",["tempo","meter"])
@pytest.mark.parametrize("track_index",[0,1])
def test_clock_changes_have_explicit_422_and_no_normalized_copy(client,tmp_path,kind,track_index):
    data=clock_source(kind,track_index)
    original=tmp_path/"user-original.mid";original.write_bytes(data)
    response=_post(client,data=data)
    assert response.status_code==422
    assert response.json()["detail"]["code"]=="unsupported_midi_clock"
    assert "original midi is unchanged" in response.json()["detail"]["message"].lower()
    assert original.read_bytes()==data and not (tmp_path/"midi-trials").exists()


def test_import_persists_safe_summary_original_bytes_and_distinct_roles(client: TestClient,tmp_path: Path) -> None:
    data=_midi(tail=480)
    response=_post(client,data=data,filename="../../private/saved.MIDI")
    assert response.status_code==201
    summary=response.json()
    assert summary["status"]=="completed"
    assert summary["original_filename"]=="saved.MIDI"
    assert summary["input_note_count"]==2
    assert summary["input_sha256"]==hashlib.sha256(data).hexdigest()
    assert summary["source_duration_seconds"]==1.5  # includes source end_of_track tail
    assert summary["last_note_end_seconds"]==1.0
    assert summary["source_duration_is_storage_midi"] is True
    assert summary["storage_clock"]=={"ppq":480,"tempo_bpm":120.0,"meter":"4/4","musical_tempo_and_meter_verified":False}
    assert summary["gp5"]["status"]=="not_requested"
    assert not any(a["key"]=="gp5-preview" for a in summary["artifacts"])
    assert _artifact(summary,"storage-clock-xml")["kind"]=="storage-clock-musicxml"
    assert str(tmp_path) not in json.dumps(summary) and "/mnt/" not in json.dumps(summary)
    assert all(a["url"].startswith("/api/v1/midi-trials/") for a in summary["artifacts"])
    assert client.get(f"/api/v1/midi-trials/{summary['trial_id']}").json()==summary
    original=client.get(_artifact(summary,"original-midi")["url"])
    assert original.status_code==200 and original.content==data
    assert client.get(_artifact(summary,"storage-clock-xml")["url"]).status_code==200


@pytest.mark.parametrize("value",["false","False","0","off","no"])
def test_false_form_strings_do_not_request_gp5(client: TestClient,value: str) -> None:
    response=_post(client,gp5_preview=value)
    assert response.status_code==201
    assert response.json()["gp5"]=={"requested":False,"status":"not_requested","mapped_count":None,"unmappable":[]}


def test_gp5_preview_roles_and_real_low_bass_profile(client: TestClient) -> None:
    response=_post(client,data=_midi([(0,240,23),(480,960,28)]),profile="bass5",gp5_preview="true")
    assert response.status_code==201
    summary=response.json()
    assert summary["gp5"]["status"]=="written"
    assert summary["gp5"]["mapped_count"]==2
    assert _artifact(summary,"gp5-preview")["kind"]=="quarter-note-gp5-preview"
    assert client.get(_artifact(summary,"gp5-preview")["url"]).status_code==200


def test_unmappable_gp5_returns201_and_retains_all_source_events(client: TestClient) -> None:
    data=_midi([(0,240,23),(480,960,28)])
    response=_post(client,data=data,gp5_preview="true")
    assert response.status_code==201
    summary=response.json()
    assert summary["status"]=="gp5_blocked_out_of_range"
    assert summary["gp5"]["status"]=="unmappable" and summary["gp5"]["mapped_count"]==1
    assert summary["gp5"]["unmappable"]==[{"source_index":0,"midi":23,"start":0.0,"end":.25,
        "reason":"No string/fret mapping in the selected tuning and max fret; event retained, no transposition."}]
    assert not any(a["key"]=="gp5-preview" for a in summary["artifacts"])
    assert client.get(_artifact(summary,"original-midi")["url"]).content==data
    canonical=client.get(_artifact(summary,"canonical-json")["url"]).json()
    assert [n["midi"] for n in canonical["notes"]]==[23,28]
    assert client.get(_artifact(summary,"storage-clock-xml")["url"]).status_code==200
    assert client.get(f"/api/v1/midi-trials/{summary['trial_id']}/files/gp5-preview").status_code==404


@pytest.mark.parametrize(("filename","data","code","reason"),[
    ("audio.wav",b"abc",415,"unsupported_extension"),
    ("broken.mid",b"garbage bytes",422,"invalid_midi"),
    ("empty.mid",b"",422,"invalid_midi"),
    ("big.mid",b"x"*(midi_trials.MAX_UPLOAD_BYTES+1),413,"upload_limit"),
])
def test_rejects_extension_garbage_empty_and_oversize_without_trial(
    client: TestClient,tmp_path: Path,filename: str,data: bytes,code: int,reason: str,
) -> None:
    response=_post(client,data=data,filename=filename)
    assert response.status_code==code
    assert response.json()["detail"]["code"]==reason
    assert not (tmp_path/"midi-trials").exists()


@pytest.mark.parametrize("form",[
    {"profile":"unknown"},{"profile":"custom"},{"profile":"custom","strings":"4","tuning_midi":"26,38,33,43","max_fret":"24"},
    {"max_fret":"37"},{"strings":"4.5"},{"source_kind":"guessed-model"},{"gp5_preview":"garbage"},
])
def test_configuration_errors_are_stable_and_do_not_export(client: TestClient,tmp_path: Path,form: dict) -> None:
    response=_post(client,**form)
    assert response.status_code==422
    assert response.json()["detail"]["code"]=="invalid_config"
    assert not (tmp_path/"midi-trials").exists()


def test_custom_drop_d_form_is_explicit(client: TestClient) -> None:
    response=_post(client,data=_midi([(0,240,26)]),profile="custom",strings="4",tuning_midi="26,33,38,43",max_fret="24",gp5_preview="true")
    assert response.status_code==201
    assert response.json()["config"]["tuning_midi"]==[26,33,38,43]
    assert response.json()["gp5"]["status"]=="written"


def test_reimport_allocates_new_uuid_and_keeps_previous_bytes(client: TestClient,tmp_path: Path) -> None:
    first=_post(client).json()
    first_folder=tmp_path/"midi-trials"/first["trial_id"]
    hashes={p.relative_to(first_folder).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in first_folder.rglob("*") if p.is_file()}
    second=_post(client).json()
    assert first["trial_id"]!=second["trial_id"]
    assert {p.relative_to(first_folder).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in first_folder.rglob("*") if p.is_file()}==hashes


@pytest.mark.parametrize("identifier",["not-a-uuid","00000000-0000-1000-8000-000000000000",str(uuid4()).upper()])
def test_noncanonical_or_nonv4_uuid_rejected(client: TestClient,identifier: str) -> None:
    response=client.get(f"/api/v1/midi-trials/{identifier}")
    assert response.status_code==404
    assert response.json()["detail"]["code"]=="trial_not_found"


def test_unknown_uuid_and_arbitrary_file_lookup_are_rejected(client: TestClient) -> None:
    assert client.get(f"/api/v1/midi-trials/{uuid4()}").status_code==404
    summary=_post(client).json()
    prefix=f"/api/v1/midi-trials/{summary['trial_id']}/files/"
    for key in ("summary.json","trial.json","input.mid","../../secret","%2e%2e%2fsecret","gp5-preview"):
        assert client.get(prefix+key).status_code==404


def test_missing_changed_and_symlink_artifacts_are_not_served(client: TestClient,tmp_path: Path) -> None:
    for change in ("missing","changed","symlink"):
        summary=_post(client).json()
        path=tmp_path/"midi-trials"/summary["trial_id"]/"input.mid"
        url=_artifact(summary,"original-midi")["url"]
        if change=="missing":
            path.unlink()
            expected=404
        elif change=="changed":
            path.write_bytes(b"changed")
            expected=409
        else:
            secret=tmp_path/"outside.txt"
            secret.write_text("not an artifact")
            path.unlink()
            path.symlink_to(secret)
            expected=404
        assert client.get(url).status_code==expected


def test_unexpected_export_exception_has_safe_persisted500_summary(
    client: TestClient,tmp_path: Path,monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*args,**kwargs):
        raise RuntimeError("internal private path /mnt/private/secret")
    monkeypatch.setattr(midi_trials,"_run_trial",fail)
    data=_midi()
    response=_post(client,data=data,gp5_preview="true")
    assert response.status_code==500
    summary=response.json()
    assert summary["status"]=="failed" and summary["error"]["code"]=="export_failed"
    assert "/mnt/private" not in response.text and str(tmp_path) not in response.text
    assert summary["gp5"]["status"]=="failed"
    assert [a["key"] for a in summary["artifacts"]]==["original-midi"]
    assert client.get(_artifact(summary,"original-midi")["url"]).content==data
    assert client.get(f"/api/v1/midi-trials/{summary['trial_id']}").json()==summary


@pytest.mark.parametrize(("data","reason"),[
    (_midi([(0,600,28)]*2001),"note_limit"),
    (_midi([(0,576001,28)]),"duration_limit"),
    (_midi([(0,1152001,28)]),"storage_clock_limit"),
    (_midi([(0,480,20+i) for i in range(33)]),"voice_limit"),
])
def test_bounded_note_duration_clock_and_voice_work(client: TestClient,data: bytes,reason: str) -> None:
    response=_post(client,data=data)
    assert response.status_code==422
    assert response.json()["detail"]["code"]==reason
