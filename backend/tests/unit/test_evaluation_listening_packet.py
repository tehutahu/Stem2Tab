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


def _replace_notes(job, notes):
    """Write a small exact-clock polyphonic MIDI and refresh its source hash."""
    messages = []
    for pitch, start, end in notes:
        messages.extend([(round(start*1000), 1, mido.Message('note_on', note=pitch, velocity=80)),
                         (round(end*1000), 0, mido.Message('note_off', note=pitch, velocity=0))])
    track = mido.MidiTrack([mido.MetaMessage('set_tempo', tempo=1000000),
                           mido.Message('program_change', program=33)])
    previous = 0
    for tick, _, message in sorted(messages, key=lambda value: value[:2]):
        track.append(message.copy(time=tick-previous)); previous = tick
    score = mido.MidiFile(ticks_per_beat=1000); score.tracks.append(track)
    score.save(job/'transcribe/performance.mid')
    for filename in ('completion.json', 'request.json'):
        path = job/filename; value = json.loads(path.read_text())
        value['raw_midi_sha256'] = sha256(job/'transcribe/performance.mid')
        path.write_text(json.dumps(value))


def test_decay_opt_in_retains_legacy_bytes_and_all_source_artifacts(packet):
    job, output = packet
    _replace_notes(job, [(40, .2, 1.8)])
    (job/'source.musicxml').write_text('<score-partwise/>')
    before = {str(path.relative_to(job)): path.read_bytes() for path in job.rglob('*') if path.is_file()}
    legacy = prepare_listening_packet(job, output, label='legacy')
    legacy_bytes = (output/'synth.wav').read_bytes()
    variant = output.with_name(str(uuid4()))
    decay = prepare_listening_packet(job, variant, label='opt in', include_decay_preview=True)
    assert 'decay_preview' not in legacy and not (output/'synth-decay.wav').exists()
    assert decay['decay_preview'] == dict(policy='amplitude-half-life-v1', half_life_seconds=.35)
    assert decay['tracks'][:2] == legacy['tracks'][:2]
    assert (output/'synth.wav').read_bytes() == legacy_bytes
    # New FLOAT WAVs can contain different PEAK creation timestamps. Compare audio
    # samples exactly; hash checks protect each stored artifact's actual bytes.
    previous, previous_rate = sf.read(output/'synth.wav')
    current, current_rate = sf.read(variant/'synth.wav')
    assert previous_rate == current_rate
    np.testing.assert_array_equal(previous, current)
    assert decay['midi_sha256'] == legacy['midi_sha256'] == sha256(job/'transcribe/performance.mid')
    assert before == {str(path.relative_to(job)): path.read_bytes() for path in job.rglob('*') if path.is_file()}
    constant, sr = sf.read(variant/'synth.wav'); plucked, _ = sf.read(variant/'synth-decay.wav')
    early, late = slice(round(.3*sr), round(.4*sr)), slice(round(1.5*sr), round(1.6*sr))
    rms = lambda data: np.sqrt(np.mean(data**2))
    assert .95 < rms(constant[late])/rms(constant[early]) < 1.05
    assert rms(plucked[late]) < .15*rms(plucked[early])


@pytest.mark.parametrize('length', [.001, .004, .3, 1.6])
def test_decay_changes_only_amplitude_inside_original_note_boundaries(packet, length):
    job, output = packet
    _replace_notes(job, [(40, .2, .2+length)])
    result = prepare_listening_packet(job, output, label='synthetic', include_decay_preview=True)
    legacy, sr = sf.read(output/'synth.wav'); decay, rate = sf.read(output/'synth-decay.wav')
    a, b = round(.2*sr), round((.2+length)*sr)
    assert rate == sr and len(decay) == len(legacy) == 2*sr
    assert not np.any(decay[:a]) and not np.any(decay[b:])
    np.testing.assert_array_equal(decay != 0, legacy != 0)
    np.testing.assert_allclose(decay[a:b], legacy[a:b] * np.exp2(-np.arange(b-a)/sr/.35), atol=1e-8, rtol=2e-6)
    assert np.max(np.abs(decay)) > 0 and result['note_count'] == 1


def test_overlapping_notes_decay_from_their_own_onsets_and_repeated_attack_resets(packet):
    job, output = packet
    rows = [(40, .2, 1.5), (43, .7, 1.), (40, 1.5, 1.51)]
    _replace_notes(job, rows)
    result = prepare_listening_packet(job, output, label='chords', include_decay_preview=True)
    combined, sr = sf.read(output/'synth-decay.wav'); expected = np.zeros_like(combined)
    for row in rows:
        _replace_notes(job, [row])
        part = output.with_name(str(uuid4()))
        prepare_listening_packet(job, part, label='one note', include_decay_preview=True)
        samples, _ = sf.read(part/'synth-decay.wav'); expected += samples
    np.testing.assert_allclose(combined, expected, atol=1e-8, rtol=2e-6)
    assert result['note_count'] == 3 and not np.any(combined[round(1.51*sr):])
    assert np.max(np.abs(combined[round(1.5*sr):round(1.51*sr)])) > .02


def test_optional_decay_api_is_allowlisted_and_hash_checked(packet):
    job, output = packet
    prepare_listening_packet(job, output, label='new', include_decay_preview=True)
    client = TestClient(app); base = f'/api/v1/listening-trials/{output.name}'
    summary = client.get(base); assert summary.status_code == 200
    assert summary.json()['decay_preview']['half_life_seconds'] == .35
    assert len(summary.json()['tracks']) == 4 and str(job) not in summary.text
    assert client.get(base+'/files/synth-decay').content == (output/'synth-decay.wav').read_bytes()
    (output/'synth-decay.wav').write_bytes(b'changed')
    assert client.get(base+'/files/synth-decay').status_code == 409


@pytest.mark.parametrize('change', ['missing-policy', 'wrong-policy', 'missing-track', 'duplicate-track'])
def test_inconsistent_decay_metadata_is_rejected(packet, change):
    job, output = packet
    prepare_listening_packet(job, output, label='new', include_decay_preview=True)
    path = output/'summary.json'; value = json.loads(path.read_text())
    if change == 'missing-policy': del value['decay_preview']
    elif change == 'wrong-policy': value['decay_preview']['half_life_seconds'] = -.35
    elif change == 'missing-track': value['tracks'].pop()
    else: value['tracks'].append(value['tracks'][-1])
    path.write_text(json.dumps(value))
    assert TestClient(app).get(f'/api/v1/listening-trials/{output.name}').status_code == 409
