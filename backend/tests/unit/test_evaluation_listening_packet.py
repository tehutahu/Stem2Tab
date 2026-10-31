"""Synthetic, local listening packets; no private fixtures or inference."""
import json
from uuid import uuid4
import mido
import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient
from src.api.main import app
from src.core.config import settings
from src.evaluation.listening_packet import prepare_listening_packet, sha256


@pytest.fixture
def packet(tmp_path, monkeypatch):
    job=tmp_path/'job'; (job/'separate').mkdir(parents=True); (job/'transcribe').mkdir()
    source=job/'input.wav'; bass=job/'separate/bass.wav'
    sf.write(source,np.zeros((44100,2)),22050); sf.write(bass,np.zeros(88200),44100)
    midi=job/'transcribe/performance.mid'
    score=mido.MidiFile(ticks_per_beat=1000)
    score.tracks.append(mido.MidiTrack([mido.MetaMessage('set_tempo',tempo=1000000),mido.Message('program_change',program=33),
        mido.Message('note_on',note=28,velocity=80,time=200),mido.Message('note_off',note=28,velocity=0,time=300)]))
    score.save(midi)
    (job/'status.json').write_text(json.dumps(dict(status='completed',job_id=str(uuid4()),midi_trial_id=str(uuid4()))))
    done=dict(input_sha256=sha256(source),bass_sha256=sha256(bass),raw_midi_sha256=sha256(midi))
    (job/'completion.json').write_text(json.dumps(done)); (job/'request.json').write_text(json.dumps(done))
    monkeypatch.setattr(settings,'file_bucket_path',tmp_path)
    output=tmp_path/'listening-trials'/str(uuid4())
    return job,output


def test_preserves_source_samples_and_synthesis_boundaries(packet):
    job,output=packet; before=sha256(job/'input.wav')
    result=prepare_listening_packet(job,output,label='Synthetic fixture')
    assert sha256(output/'original.wav')==before==sha256(job/'input.wav')
    synth,sr=sf.read(output/'synth.wav'); assert len(synth)==2*sr and result['note_count']==1
    assert np.max(np.abs(synth[:round(.2*sr)]))==0 and np.max(np.abs(synth[round(.5*sr):]))==0
    assert np.max(np.abs(synth))>0 and result['synthesis_max_boundary_rounding_seconds']<1/sr
    with pytest.raises(FileExistsError): prepare_listening_packet(job,output,label='Again')


def test_rejects_changed_incomplete_or_unaligned_inputs(packet):
    job,output=packet
    with (job/'input.wav').open('ab') as f:f.write(b'x')
    with pytest.raises(ValueError,match='changed'):prepare_listening_packet(job,output,label='bad')
    assert not output.exists()


def test_api_only_serves_named_unchanged_files(packet):
    job,output=packet; prepare_listening_packet(job,output,label='safe')
    client=TestClient(app); base=f'/api/v1/listening-trials/{output.name}'
    result=client.get(base); assert result.status_code==200
    assert str(job) not in result.text
    assert client.get(base+'/files/original').content==(job/'input.wav').read_bytes()
    assert client.get(base+'/files/summary.json').status_code==404
    (output/'bass.wav').write_bytes(b'changed')
    assert client.get(base+'/files/bass').status_code==409


def test_rejects_symlink_and_path_metadata(packet):
    job,output=packet; prepare_listening_packet(job,output,label='safe'); client=TestClient(app)
    original=output/'original.wav'; original.unlink(); original.symlink_to(job/'input.wav')
    assert client.get(f'/api/v1/listening-trials/{output.name}/files/original').status_code==409
    p=output/'summary.json'; value=json.loads(p.read_text());value['tracks'][0]['filename']='../../secret';p.write_text(json.dumps(value))
    assert client.get(f'/api/v1/listening-trials/{output.name}').status_code==409
    assert client.get('/api/v1/listening-trials/not-uuid').status_code==404
