"""Synthetic protocol/gate tests, not independent recordings or quality evidence."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.evaluation.correction_audit import Attempts, Plan, check_isolated_pair, evaluate_locked, freeze_plan, main
from src.evaluation.io import write_note_events_json
from src.evaluation.models import NoteEvent, NoteEventSet


def _asset(path: Path) -> dict:
    return dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def _notes(path: Path, rows: list[tuple]) -> dict:
    write_note_events_json(NoteEventSet(source={"kind": "synthetic-test-only"}, notes=tuple(
        NoteEvent(start=a, end=b, midi=p) for a, b, p in rows)), path)
    return _asset(path)


def _bundle(root, ref, baseline, *, kind="offset_only", start=0., end=2., simulated_holdout=False):
    code = root/'candidate.py'; code.write_text('# synthetic fixture, never executed\n')
    target = dict(id='clip1', start_seconds=start, end_seconds=end, alignment='Shared synthetic source seconds',
                  baseline=_notes(root/'baseline.json', baseline),
                  reference=dict(status='verified', artifact=_notes(root/'reference.json', ref),
                                 provenance='Generated mathematical notes', verification='Synthetic test oracle only'))
    plan = Plan.model_validate(dict(dataset_kind='local_recordings' if simulated_holdout else 'synthetic',
        known_reused_recording_ids=[f'dev{i}' for i in range(17)] if simulated_holdout else [],
        baseline_pipeline_identity='Synthetic test, no audio/model',
        candidate=dict(id='toy', kind=kind, implementation_files=[_asset(code)], parameters={'amount': 1}),
        metrics={}, prefix_seconds=.25,
        criteria=dict(minimum_held_out_recordings=1, minimum_improved_held_out_recordings=1,
                      minimum_primary_recoveries=1, maximum_broken_correct_offsets=0,
                      maximum_worsened_offsets=0, maximum_newly_missed_onsets=0,
                      maximum_extra_increase=0, maximum_octave_increase=0,
                      maximum_bad_recovered_continuing_offsets=0),
        recordings=[dict(id='recording1', usage='held_out_declared_unused' if simulated_holdout else 'synthetic',
                         usage_evidence='Simulated role for gate unit test, not a real holdout', targets=[target])]))
    return plan, freeze_plan(plan, root)


def _run(root, plan, locked, candidate=None, outcome=None):
    if outcome is None:
        outcome = dict(target_id='clip1', status='completed', artifact=_notes(root/'candidate.json', candidate))
    attempts = Attempts.model_validate(dict(plan_sha256=locked['plan_sha256'],
                                          candidate_parameters=plan.candidate.parameters, outcomes=[outcome]))
    return evaluate_locked(locked, attempts, root)


def _comparison(result):
    return result['recordings'][0]['targets'][0]['comparison']


def test_offset_repair_reuses_metrics_and_preserves_input_bytes(tmp_path):
    plan, locked = _bundle(tmp_path, [(0., 1., 40)], [(0., 1.5, 40)])
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    result = _run(tmp_path, plan, locked, [(0., 1., 40)])
    c = _comparison(result)
    assert c['before']['onset_f1'] == c['after']['onset_f1'] == 1
    assert c['before']['metrics']['onset_offset_f1'] == 0
    assert c['after']['metrics']['onset_offset_f1'] == 1
    assert c['offset_transitions']['repaired'] == 1 and not c['violations']
    assert before == {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.name in before}
    assert result['decision'] == 'hold'  # Synthetic cannot establish a held-out result.


def test_average_f1_gain_cannot_hide_one_previously_correct_offset_being_broken(tmp_path):
    ref = [(i*.4, i*.4+.2, 40+i) for i in range(4)]
    baseline = [(a, b+(.1 if i < 3 else 0), p) for i, (a,b,p) in enumerate(ref)]
    candidate = [(a, b+(0 if i < 3 else .1), p) for i, (a,b,p) in enumerate(ref)]
    plan, locked = _bundle(tmp_path, ref, baseline, simulated_holdout=True)
    result = _run(tmp_path, plan, locked, candidate); c = _comparison(result)
    assert c['after']['metrics']['onset_offset_f1'] > c['before']['metrics']['onset_offset_f1']
    assert c['offset_transitions']['repaired'] == 3 and c['offset_transitions']['broken_correct'] == 1
    assert result['decision'] == 'hold' and any('regression_broken_correct' in s for s in result['hold_reasons'])


def test_offset_candidate_changing_pitch_or_onset_reports_lost_extra_and_octave(tmp_path):
    plan, locked = _bundle(tmp_path, [(0., 1., 40)], [(0., 1., 40)])
    result = _run(tmp_path, plan, locked, [(0., 1., 52)]); c = _comparison(result)
    assert c['violations'] and c['newly_missed_onsets'] == c['extra_increase'] == c['octave_increase'] == 1
    result = _run(tmp_path, plan, locked, [(.1, 1., 40)])
    assert _comparison(result)['after']['missed'] == _comparison(result)['after']['extra'] == 1


def test_boundary_continuation_recovery_is_separate_from_later_missing_note(tmp_path):
    plan, locked = _bundle(tmp_path, [(9.7, 10.7, 40), (11.4, 11.6, 43)], [],
                            kind='prefix_only', start=10., end=12.)
    c = _comparison(_run(tmp_path, plan, locked, [(10., 10.7, 40)]))
    assert c['prefix']['continuing_reference_notes'] == 1 and c['prefix']['recovered'] == 1
    assert c['prefix']['before']['missed'] == 1 and c['prefix']['after']['missed'] == 0
    assert c['after']['missed'] == 1 and c['prefix']['bad_recovered_offsets'] == 0


def test_prefix_retains_originals_and_simulated_gate_can_reach_review_only(tmp_path):
    original = [(10.6, 11.2, 43)]
    plan, locked = _bundle(tmp_path, [(9.7, 10.7, 40), *original], original,
                            kind='prefix_only', start=10., end=12., simulated_holdout=True)
    result = _run(tmp_path, plan, locked, [(10., 10.7, 40), *original])
    assert result['decision'] == 'ready_for_human_review' and not _comparison(result)['violations']
    assert 'not independently certified' in result['warning']
    changed = _run(tmp_path, plan, locked, [(10., 10.7, 40), (10.6, 11., 43)])
    assert changed['decision'] == 'hold' and _comparison(changed)['violations']


def test_prefix_bad_new_offset_or_interior_addition_is_held(tmp_path):
    plan, locked = _bundle(tmp_path, [(9.7, 10.7, 40)], [], kind='prefix_only', start=10., end=12., simulated_holdout=True)
    bad = _run(tmp_path, plan, locked, [(10., 11.9, 40)])
    assert _comparison(bad)['prefix']['recovered'] == 1
    assert _comparison(bad)['prefix']['bad_recovered_offsets'] == 1 and bad['decision'] == 'hold'
    interior = _run(tmp_path, plan, locked, [(10.01, 10.7, 40)])
    assert _comparison(interior)['violations'] and interior['decision'] == 'hold'


def test_prefix_negative_control_does_not_invent_continuing_reference(tmp_path):
    plan, locked = _bundle(tmp_path, [(10.1, 10.3, 43)], [(10.1, 10.3, 43)], kind='prefix_only', start=10., end=12.)
    c = _comparison(_run(tmp_path, plan, locked, [(10., 10.2, 40), (10.1, 10.3, 43)]))
    assert c['prefix']['continuing_reference_notes'] == c['prefix']['recovered'] == 0
    assert c['extra_increase'] == 1


@pytest.mark.parametrize('status', ['failed', 'not_evaluated'])
def test_failed_and_unattempted_outcomes_have_no_quality_metrics(tmp_path, status):
    plan, locked = _bundle(tmp_path, [(0., 1., 40)], [])
    result = _run(tmp_path, plan, locked, outcome=dict(target_id='clip1', status=status, reason='Synthetic test reason'))
    row = result['recordings'][0]['targets'][0]
    assert row['status'] == status and row['comparison'] is None
    assert result['recordings'][0]['summary'][status] == 1 and result['decision'] == 'hold'


def test_missing_outcome_stays_not_evaluated(tmp_path):
    plan, locked = _bundle(tmp_path, [(0., 1., 40)], [])
    attempts = Attempts(plan_sha256=locked['plan_sha256'], candidate_parameters=plan.candidate.parameters, outcomes=())
    row = evaluate_locked(locked, attempts, tmp_path)['recordings'][0]['targets'][0]
    assert row['status'] == 'not_evaluated' and row['comparison'] is None


@pytest.mark.parametrize('status', ['hypothesis', 'unavailable'])
def test_unverified_reference_never_creates_f1(tmp_path, status):
    plan, _ = _bundle(tmp_path, [(0., 1., 40)], [])
    data = plan.model_dump(mode='json'); data['recordings'][0]['targets'][0]['reference'] = dict(status=status, reason='Not independently checked')
    plan = Plan.model_validate(data); locked = freeze_plan(plan, tmp_path)
    row = _run(tmp_path, plan, locked, [])['recordings'][0]['targets'][0]
    assert row['status'] == 'not_evaluated' and row['comparison'] is None


def test_verified_silence_counts_false_notes_without_fabricating_positive_f1(tmp_path):
    plan, locked = _bundle(tmp_path, [], [], kind='prefix_only')
    c = _comparison(_run(tmp_path, plan, locked, [(0., .2, 40)]))
    assert c['after']['extra'] == 1 and c['after']['onset_f1'] is None and c['after']['metrics'] is None


def test_invalid_candidate_fails_instead_of_repairing_or_becoming_an_empty_score(tmp_path):
    plan, locked = _bundle(tmp_path, [(0., 1., 40)], [])
    path = tmp_path/'invalid.json'; path.write_text('{"notes":[{"start":0.5,"end":0.2,"midi":40}]}')
    original = path.read_bytes()
    result = _run(tmp_path, plan, locked, outcome=dict(target_id='clip1', status='completed', artifact=_asset(path)))
    assert result['recordings'][0]['targets'][0]['status'] == 'failed'
    assert path.read_bytes() == original


@pytest.mark.parametrize('file', ['baseline.json', 'candidate.py', 'reference.json'])
def test_mutated_frozen_input_or_code_is_failed_not_unscored_success(tmp_path, file):
    plan, locked = _bundle(tmp_path, [(0., 1., 40)], [])
    (tmp_path/file).write_text('changed')
    row = _run(tmp_path, plan, locked, [])['recordings'][0]['targets'][0]
    assert row['status'] == 'failed' and row['comparison'] is None


def test_parameters_targets_and_metric_names_cannot_silently_change(tmp_path):
    plan, locked = _bundle(tmp_path, [(0., 1., 40)], [])
    attempts = Attempts(plan_sha256=locked['plan_sha256'], candidate_parameters={'amount': True}, outcomes=())
    with pytest.raises(ValueError, match='parameters differ'): evaluate_locked(locked, attempts, tmp_path)
    attempts = Attempts(plan_sha256=locked['plan_sha256'], candidate_parameters=plan.candidate.parameters, outcomes=())
    changed = deepcopy(locked); changed['plan']['prefix_seconds'] = .5
    with pytest.raises(ValueError, match='digest mismatch'): evaluate_locked(changed, attempts, tmp_path)
    data = plan.model_dump(mode='json'); data['metrics']['offset_misspelling'] = 1
    with pytest.raises(ValidationError): Plan.model_validate(data)


@pytest.mark.parametrize('ids', [('clip1', 'clip1'), ('unplanned',)])
def test_repeated_or_unplanned_outcomes_cannot_hide_a_failed_target(tmp_path, ids):
    plan, locked = _bundle(tmp_path, [(0., 1., 40)], [])
    attempts = Attempts.model_validate(dict(plan_sha256=locked['plan_sha256'], candidate_parameters=plan.candidate.parameters,
        outcomes=[dict(target_id=value, status='failed', reason='Synthetic failure') for value in ids]))
    with pytest.raises(ValueError, match='Duplicate or unplanned'): evaluate_locked(locked, attempts, tmp_path)


def test_recording_aggregation_is_micro_and_two_clips_are_not_two_recordings(tmp_path):
    plan, _ = _bundle(tmp_path, [(0., .2, 40), (.4, .6, 43), (.8, 1., 45)], [], simulated_holdout=True)
    data = plan.model_dump(mode='json'); second = deepcopy(data['recordings'][0]['targets'][0])
    second.update(id='clip2', start_seconds=2., end_seconds=4., baseline=_notes(tmp_path/'base2.json', [(3., 3.5, 40)]))
    second['reference']['artifact'] = _notes(tmp_path/'ref2.json', [(3., 3.5, 40)])
    data['recordings'][0]['targets'].append(second); data['criteria']['minimum_held_out_recordings'] = 2
    plan = Plan.model_validate(data); locked = freeze_plan(plan, tmp_path)
    attempts = Attempts.model_validate(dict(plan_sha256=locked['plan_sha256'], candidate_parameters=plan.candidate.parameters,
        outcomes=[dict(target_id='clip1', status='completed', artifact=_notes(tmp_path/'c1.json', [])),
                  dict(target_id='clip2', status='completed', artifact=_notes(tmp_path/'c2.json', [(3., 3.5, 40)]))]))
    result = evaluate_locked(locked, attempts, tmp_path); summary = result['recordings'][0]['summary']
    assert summary['evaluated'] == 2 and summary['before']['reference_notes'] == 4
    assert summary['before']['onset_micro_f1'] == .4
    assert 'insufficient_declared_unused_recordings' in result['hold_reasons']
    assert any('no_effect' in reason for reason in result['hold_reasons'])


def test_reused_seventeen_cannot_be_called_independent_and_unknown_ids_block_freeze_plan(tmp_path):
    plan, _ = _bundle(tmp_path, [(0., 1., 40)], [], simulated_holdout=True)
    data = plan.model_dump(mode='json'); data['recordings'][0]['id'] = 'dev0'
    with pytest.raises(ValidationError, match='cannot be labelled held out'): Plan.model_validate(data)
    data = plan.model_dump(mode='json'); data['known_reused_recording_ids'] = []
    with pytest.raises(ValidationError, match='all seventeen'): Plan.model_validate(data)
    data['known_reused_recording_ids'] = [f'dev{i}' for i in range(8)]
    with pytest.raises(ValidationError, match='all seventeen'): Plan.model_validate(data)


def test_right_censored_offsets_and_duplicate_identities_are_not_scored_as_repairs(tmp_path):
    plan, locked = _bundle(tmp_path, [(0., 5., 40)], [(0., 1., 40)])
    result = _run(tmp_path, plan, locked, [(0., 2., 40)])
    assert _comparison(result)['offset_transitions']['right_censored'] == 1
    assert _comparison(result)['offset_transitions']['eligible'] == 0 and result['decision'] == 'hold'
    plan, locked = _bundle(tmp_path, [(0., 1., 40)], [(0., .9, 40), (0., 1.5, 40)])
    result = _run(tmp_path, plan, locked, [(0., 1., 40), (0., 1., 40)])
    assert 'duplicate_anchor_identity_ambiguous' in _comparison(result)['violations']
    assert result['decision'] == 'hold'


def test_offset_tolerance_uses_existing_mir_eval_rounding_and_inclusive_boundary(tmp_path):
    plan, locked = _bundle(tmp_path, [(0., .25, 40)], [(0., .3, 40)])
    result = _run(tmp_path, plan, locked, [(0., .2, 40)])
    assert _comparison(result)['offset_transitions']['broken_correct'] == 0


def test_offset_diagnostics_use_original_reference_duration_not_truncated_boundary_duration(tmp_path):
    plan, locked = _bundle(tmp_path, [(9., 10.2, 40)], [(10., 10.4, 40)], start=10., end=12.)
    result = _run(tmp_path, plan, locked, [(10., 10.5, 40)])
    assert _comparison(result)['offset_transitions']['broken_correct'] == 1  # 0.24s tolerance, not 0.05s.
    assert 'virtual crop-boundary' in result['metric_scope']


def test_cli_freeze_evaluate_and_no_overwrite(tmp_path):
    plan, _ = _bundle(tmp_path, [(0., 1., 40)], [(0., 1.5, 40)])
    source = tmp_path/'plan.json'; source.write_text(plan.model_dump_json())
    lock = tmp_path/'lock.json'; main(['freeze', '--plan', str(source), '--out', str(lock)])
    with pytest.raises(FileExistsError): main(['freeze', '--plan', str(source), '--out', str(lock)])
    value = json.loads(lock.read_text())
    attempts = Attempts.model_validate(dict(plan_sha256=value['plan_sha256'], candidate_parameters=plan.candidate.parameters,
        outcomes=[dict(target_id='clip1', status='completed', artifact=_notes(tmp_path/'candidate.json', [(0., 1., 40)]))]))
    path = tmp_path/'attempts.json'; path.write_text(attempts.model_dump_json())
    result = tmp_path/'result.json'
    main(['evaluate', '--lock', str(lock), '--attempts', str(path), '--out', str(result)])
    assert json.loads(result.read_text())['recordings'][0]['summary']['repaired'] == 1
    with pytest.raises(FileExistsError): main(['evaluate', '--lock', str(lock), '--attempts', str(path), '--out', str(result)])


def test_two_corrections_share_the_original_baseline_and_cannot_be_silently_chained(tmp_path):
    plan, offset = _bundle(tmp_path, [(0., 1., 40)], [(0., 1.5, 40)])
    data = plan.model_dump(mode='json'); data['candidate']['kind'] = 'prefix_only'
    prefix = freeze_plan(Plan.model_validate(data), tmp_path)
    assert check_isolated_pair(offset, prefix)['status'] == 'same_baseline_and_targets'
    data['recordings'][0]['targets'][0]['baseline'] = _notes(tmp_path/'already-corrected.json', [(0., 1., 40)])
    chained = freeze_plan(Plan.model_validate(data), tmp_path)
    with pytest.raises(ValueError, match='original inputs'): check_isolated_pair(offset, chained)
