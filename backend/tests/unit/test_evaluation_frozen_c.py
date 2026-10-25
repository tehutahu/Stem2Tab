"""Pure fixtures: arithmetic correctness is not proof of musical correctness."""
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch
import pytest
from src.evaluation.models import NoteEvent, NoteEventSet
from src.evaluation.frozen_c import additional_stage
from src.evaluation.compare_note_events import main


def events(*rows):
    return NoteEventSet(source={'fixture': True}, notes=tuple(
        NoteEvent(start=s, end=e, midi=m, velocity=.7) for s,e,m in rows))


def test_known_true_upper_counterexample_and_preservation():
    original = events((0, 1, 52), (.04, 1.1, 40))
    before = original.model_dump_json()
    result, decisions = additional_stage(original)
    assert result.notes == (original.notes[1],)
    assert result.source == original.source
    assert original.model_dump_json() == before
    assert decisions == [dict(rejected_index=0, support_index=1, harmonic=2,
        harmonic_error_cents=0.0, reason='exact12_earlier_upper_start')]
    # If both notes are real, this exact intended arithmetic deletes a true note.


@pytest.mark.parametrize('start,end,midi,deleted', [
    (.05,1,40,True), (.050000000002,1,40,False),
    (0,1,40,False), (.04,.9,40,False), (.04,1,41,False)])
def test_fixed_boundaries(start,end,midi,deleted):
    original=events((0,1,52),(start,end,midi))
    result, decisions=additional_stage(original)
    assert bool(decisions) == deleted
    assert len(result.notes) == len(original.notes)-int(deleted)


def test_duplicates_first_supporter_and_chain():
    original=events((0,1,64),(.02,1,52),(.04,1,40),(.04,1,40))
    result, decisions=additional_stage(original)
    assert [n.midi for n in result.notes] == [64,40,40]
    assert decisions[0]['support_index'] == 2
    assert decisions[0]['rejected_index'] == 1
    assert NoteEventSet.model_validate_json(result.model_dump_json()) == result


def test_cli_bytes_and_refusal(tmp_path):
    source=tmp_path/'input.json'
    source.write_bytes(b'  {"schema_version":"1.0","notes":[]}  ')
    before=source.read_bytes()
    out=tmp_path/'baseline.json'
    main(['--input',str(source),'--output',str(out)])
    assert out.read_bytes()==before==source.read_bytes()
    with pytest.raises(SystemExit):
        main(['--input',str(source),'--output',str(out)])
    with pytest.raises(SystemExit):
        main(['--input',str(source),'--output',str(source)])
    side=tmp_path/'blocked.json.decisions.json'; side.write_text('keep')
    with pytest.raises(SystemExit):
        main(['--input',str(source),'--output',str(tmp_path/'blocked.json')])
    assert side.read_text()=='keep'


def test_cli_subprocess_candidate_ack_and_sidecar(tmp_path):
    source=tmp_path/'notes.json'; original=events((0,1,52),(.04,1,40))
    source.write_text(original.model_dump_json())
    out=tmp_path/'result.json'
    command=[sys.executable,'-m','src.evaluation.compare_note_events','--input',str(source),
             '--output',str(out),'--mode','frozen-c']
    assert subprocess.run(command, capture_output=True).returncode==2
    assert not out.exists()
    assert subprocess.run(command+['--already-pr8'],capture_output=True).returncode==0
    assert NoteEventSet.model_validate_json(out.read_bytes())==additional_stage(original)[0]
    assert json.loads(out.with_name(out.name+'.decisions.json').read_text())['decisions']==additional_stage(original)[1]
    assert source.read_text()==original.model_dump_json()


def test_second_write_failure_removes_only_owned_output(tmp_path):
    source=tmp_path/'input.json'; source.write_text(events().model_dump_json())
    out=tmp_path/'new.json'; side=out.with_name(out.name+'.decisions.json')
    real_open=Path.open
    def racing_open(path,*args,**kwargs):
        if path==side and args==('xb',):
            side.write_text('other writer')
        return real_open(path,*args,**kwargs)
    with patch.object(Path,'open',racing_open),pytest.raises(FileExistsError):
        main(['--input',str(source),'--output',str(out),'--mode','frozen-c','--already-pr8'])
    assert not out.exists()
    assert side.read_text()=='other writer'
