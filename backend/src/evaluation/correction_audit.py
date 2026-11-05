"""Freeze and audit separate offset/prefix candidates; never run a correction/model."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
from typing import Annotated, Literal

from mir_eval.transcription import N_DECIMALS
import numpy as np
from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from src.evaluation.metrics import MetricConfig, evaluate_note_events, match_onsets
from src.evaluation.models import NoteEvent, NoteEventSet


class AuditModel(BaseModel):
    """Strict, finite protocol data; unknown settings must not disappear."""
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class Artifact(AuditModel):
    """An existing local file whose bytes must remain unchanged."""
    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class VerifiedReference(AuditModel):
    """Human-attested independent reference provenance, not automatic certification."""
    status: Literal["verified"]
    artifact: Artifact
    provenance: str = Field(min_length=1)
    verification: str = Field(min_length=1)


class UnscoredReference(AuditModel):
    """Absent or hypothetical labels cannot produce accuracy scores."""
    status: Literal["unavailable", "hypothesis"]
    reason: str = Field(min_length=1)


class Target(AuditModel):
    """Predictions use source seconds inside this interval; reference may span it."""
    id: str = Field(min_length=1)
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    alignment: str = Field(min_length=1)
    baseline: Artifact
    reference: Annotated[VerifiedReference | UnscoredReference, Field(discriminator="status")]

    @model_validator(mode="after")
    def interval(self) -> Target:
        """Reject reversed intervals and oversized short-segment evaluations."""
        if not 0 < self.end_seconds-self.start_seconds <= 30:
            raise ValueError("Use a positive short interval of at most 30s")
        return self


class Recording(AuditModel):
    """The unit of holdout separation and aggregation; clips are not independent songs."""
    id: str = Field(min_length=1)
    usage: Literal["development_reused", "held_out_declared_unused", "synthetic"]
    usage_evidence: str = Field(min_length=1)
    targets: tuple[Target, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def nonoverlapping(self) -> Recording:
        """Avoid weighting the same local interval repeatedly."""
        ordered = sorted(self.targets, key=lambda target: target.start_seconds)
        if any(a.end_seconds > b.start_seconds for a, b in zip(ordered, ordered[1:])):
            raise ValueError("Do not double-count overlapping clips within a recording")
        return self


class Criteria(AuditModel):
    """Explicit prospective proposals, with no score-dependent default thresholds."""
    minimum_held_out_recordings: int = Field(ge=1)
    minimum_improved_held_out_recordings: int = Field(ge=1)
    minimum_primary_recoveries: int = Field(ge=1)
    maximum_broken_correct_offsets: int = Field(ge=0)
    maximum_worsened_offsets: int = Field(ge=0)
    maximum_newly_missed_onsets: int = Field(ge=0)
    maximum_extra_increase: int = Field(ge=0)
    maximum_octave_increase: int = Field(ge=0)
    maximum_bad_recovered_continuing_offsets: int = Field(ge=0)


class Candidate(AuditModel):
    """One isolated candidate against the raw baseline, never a chained combination."""
    id: str = Field(min_length=1)
    kind: Literal["offset_only", "prefix_only"]
    implementation_files: tuple[Artifact, ...] = Field(min_length=1)
    parameters: dict[str, JsonValue]


class AuditMetricConfig(MetricConfig):
    """Reuse benchmark tolerances while rejecting misspelled protocol settings."""
    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")


class Plan(AuditModel):
    """Targets, parameters, provenance and acceptance proposals fixed before inspection."""
    schema_version: Literal["correction-audit-v1"] = "correction-audit-v1"
    dataset_kind: Literal["synthetic", "local_recordings"]
    known_reused_recording_ids: tuple[str, ...]
    baseline_pipeline_identity: str = Field(min_length=1)
    candidate: Candidate
    metrics: AuditMetricConfig
    prefix_seconds: float = Field(gt=0, le=1)
    criteria: Criteria
    recordings: tuple[Recording, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def identities(self) -> Plan:
        """Keep adjustment data, synthetic data and declared holdouts distinct."""
        reused = self.known_reused_recording_ids
        if len(set(reused)) != len(reused):
            raise ValueError("Reused recording IDs must be unique")
        if self.dataset_kind == "local_recordings" and len(reused) < 17:
            raise ValueError("List all seventeen or more known reused recordings before freezing")
        if len({recording.id for recording in self.recordings}) != len(self.recordings):
            raise ValueError("A recording must appear once, containing all of its clips")
        targets = [target.id for recording in self.recordings for target in recording.targets]
        if len(set(targets)) != len(targets):
            raise ValueError("Target IDs must be unique")
        for recording in self.recordings:
            if (recording.usage == "synthetic") != (self.dataset_kind == "synthetic"):
                raise ValueError("Synthetic and real recordings require separate plans")
            if recording.usage == "held_out_declared_unused" and recording.id in reused:
                raise ValueError("An adjustment recording cannot be labelled held out")
            if recording.usage == "development_reused" and recording.id not in reused:
                raise ValueError("Add every development recording to the reused list")
        return self


class Completed(AuditModel):
    """A saved candidate artifact; this evaluator does not generate it."""
    target_id: str
    status: Literal["completed"]
    artifact: Artifact


class Unfinished(AuditModel):
    """Keep failed execution distinct from an unattempted/unscored result."""
    target_id: str
    status: Literal["failed", "not_evaluated"]
    reason: str = Field(min_length=1)


class Attempts(AuditModel):
    """Bind all outcomes to the frozen plan; absent target outcomes stay unscored."""
    plan_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    candidate_parameters: dict[str, JsonValue]
    outcomes: tuple[Annotated[Completed | Unfinished, Field(discriminator="status")], ...]


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _bytes(root: Path, asset: Artifact) -> bytes:
    path = root / asset.path
    if path.stat().st_size > 5*1024**2:
        raise ValueError("Artifact exceeds the 5MiB audit bound")
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != asset.sha256:
        raise ValueError("Artifact hash mismatch: " + asset.path)
    return data


def _events(root: Path, asset: Artifact) -> NoteEventSet:
    data = _bytes(root, asset)
    events = NoteEventSet.model_validate_json(data)
    if len(events.notes) > 20000:
        raise ValueError("Artifact exceeds 20k notes")
    return events


def _bounded(events: NoteEventSet, target: Target) -> None:
    if len(events.notes) > 500:
        raise ValueError("Use at most 500 prediction notes per short interval")
    if any(n.start < target.start_seconds or n.end > target.end_seconds for n in events.notes):
        raise ValueError("Prediction must use source seconds inside its frozen interval")


def freeze_plan(plan: Plan, root: Path) -> dict:
    """Hash inputs and code before candidates are evaluated; no inputs are rewritten."""
    for asset in plan.candidate.implementation_files:
        _bytes(root, asset)
    for recording in plan.recordings:
        for target in recording.targets:
            _bounded(_events(root, target.baseline), target)
            if target.reference.status == "verified":
                _events(root, target.reference.artifact)
    from src.evaluation import metrics
    import mir_eval
    payload = dict(plan=plan.model_dump(mode="json"), input_root=str(root.resolve()),
        frozen_at=datetime.now(timezone.utc).isoformat(),
        evaluator_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        metrics_sha256=hashlib.sha256(Path(metrics.__file__).read_bytes()).hexdigest(),
        models_sha256=hashlib.sha256(Path(__file__).with_name("models.py").read_bytes()).hexdigest(),
        dependency_versions={name: version(name) for name in ("pydantic", "numpy", "scipy")},
        mir_eval_version=mir_eval.__version__)
    return dict(**payload, plan_sha256=_digest(payload))


def _window(events: NoteEventSet, start: float, end: float) -> tuple[NoteEventSet, tuple[int, ...]]:
    rows = [(NoteEvent(start=max(n.start, start)-start, end=min(n.end, end)-start,
                      midi=n.midi, velocity=n.velocity), index)
            for index, n in enumerate(events.notes) if n.start < end and n.end > start]
    if len(rows) > 500:
        raise ValueError("Use at most 500 reference/prediction notes per window")
    rows.sort(key=lambda row: (row[0].start, row[0].midi, row[0].end))
    return NoteEventSet(notes=tuple(row[0] for row in rows)), tuple(row[1] for row in rows)


def _counts(reference: NoteEventSet, prediction: NoteEventSet, config: MetricConfig) -> dict:
    matches = match_onsets(reference, prediction, config=config)
    tp, count, estimated = len(matches), len(reference.notes), len(prediction.notes)
    # A verified silent interval is assessed, but it has no positive-reference F1.
    values = evaluate_note_events(reference, prediction, config=config, runtime_seconds=0).model_dump(mode="json") if count else None
    return dict(reference_notes=count, estimated_notes=estimated, tp=tp, missed=count-tp,
                extra=estimated-tp, onset_f1=values["onset_f1"] if values else None,
                metrics=values)


def _anchor(note: NoteEvent) -> tuple:
    return note.start, note.midi, note.velocity


def _signature(note: NoteEvent) -> tuple:
    return note.start, note.end, note.midi, note.velocity


def _offset_hit(error: float, tolerance: float) -> bool:
    return bool(np.around(error, decimals=N_DECIMALS) <= tolerance)


def _compare(plan: Plan, target: Target, ref: NoteEventSet, baseline: NoteEventSet, candidate: NoteEventSet) -> dict:
    before, before_map = _window(baseline, target.start_seconds, target.end_seconds)
    after, after_map = _window(candidate, target.start_seconds, target.end_seconds)
    truth, truth_map = _window(ref, target.start_seconds, target.end_seconds)
    violations = []
    if plan.candidate.kind == "offset_only":
        if Counter(map(_anchor, baseline.notes)) != Counter(map(_anchor, candidate.notes)):
            violations.append("offset_candidate_changed_pitch_onset_velocity_or_multiplicity")
    else:
        old, new = Counter(map(_signature, baseline.notes)), Counter(map(_signature, candidate.notes))
        if old-new:
            violations.append("prefix_candidate_removed_or_edited_original_notes")
        if any(abs(note[0]-target.start_seconds) > 1e-9 for note in (new-old).elements()):
            violations.append("prefix_addition_is_not_at_the_crop_boundary")
    bm = match_onsets(truth, before, config=plan.metrics)
    cm = match_onsets(truth, after, config=plan.metrics)
    baseline_hits, candidate_hits = {r for r, _ in bm}, {r for r, _ in cm}
    before_counts, after_counts = _counts(truth, before, plan.metrics), _counts(truth, after, plan.metrics)
    anchors = Counter(map(_anchor, baseline.notes)) | Counter(map(_anchor, candidate.notes))
    ambiguous = any(count > 1 for count in anchors.values())
    if ambiguous:
        violations.append("duplicate_anchor_identity_ambiguous")
    candidate_by_anchor = {_anchor(note): note for note in candidate.notes}
    offsets = dict(assessed=not ambiguous, eligible=0, right_censored=0, broken_correct=0, repaired=0, worsened=0,
                   absolute_error_before_ms=0., absolute_error_after_ms=0.)
    if not ambiguous:
        for ri, bi in bm:
            original_ref = ref.notes[truth_map[ri]]
            if original_ref.end > target.end_seconds:
                offsets["right_censored"] += 1
                continue
            old = baseline.notes[before_map[bi]]
            new = candidate_by_anchor.get(_anchor(old))
            if new is None:
                violations.append("original_anchor_missing_for_offset_assessment")
                continue
            tolerance = max(plan.metrics.offset_min_tolerance_seconds,
                            plan.metrics.offset_ratio*(original_ref.end-original_ref.start))
            a, b = abs(old.end-original_ref.end), abs(new.end-original_ref.end)
            offsets["eligible"] += 1
            offsets["broken_correct"] += int(_offset_hit(a, tolerance) and not _offset_hit(b, tolerance))
            offsets["repaired"] += int(not _offset_hit(a, tolerance) and _offset_hit(b, tolerance))
            offsets["worsened"] += int(b > a+1e-9)
            offsets["absolute_error_before_ms"] += a*1000
            offsets["absolute_error_after_ms"] += b*1000
    prefix_end = min(target.end_seconds, target.start_seconds+plan.prefix_seconds)
    pref, pref_map = _window(ref, target.start_seconds, prefix_end)
    bp, _ = _window(baseline, target.start_seconds, prefix_end)
    cp, _ = _window(candidate, target.start_seconds, prefix_end)
    phb = {r for r, _ in match_onsets(pref, bp, config=plan.metrics)}
    phc = {r for r, _ in match_onsets(pref, cp, config=plan.metrics)}
    carried = {i for i, original in enumerate(pref_map) if ref.notes[original].start < target.start_seconds}
    recovered = carried & (phc-phb)
    bad_recovered, censored_recovered = 0, 0
    full_matches = {truth_map[r]: after_map[c] for r, c in cm}
    for index in recovered:
        original = ref.notes[pref_map[index]]
        if original.end > target.end_seconds or pref_map[index] not in full_matches:
            censored_recovered += 1
            continue
        n = candidate.notes[full_matches[pref_map[index]]]
        tolerance = max(plan.metrics.offset_min_tolerance_seconds,
                        plan.metrics.offset_ratio*(original.end-original.start))
        bad_recovered += int(not _offset_hit(abs(n.end-original.end), tolerance))
    old_octaves = before_counts["metrics"]["octave_error_count"] if before_counts["metrics"] else 0
    new_octaves = after_counts["metrics"]["octave_error_count"] if after_counts["metrics"] else 0
    return dict(status="evaluated", violations=violations, before=before_counts, after=after_counts,
        newly_missed_onsets=sum(ref.notes[truth_map[i]].start >= target.start_seconds for i in baseline_hits-candidate_hits),
        extra_increase=max(0, after_counts["extra"]-before_counts["extra"]),
        octave_increase=max(0, new_octaves-old_octaves), offset_transitions=offsets,
        prefix=dict(before=_counts(pref, bp, plan.metrics), after=_counts(pref, cp, plan.metrics),
                    continuing_reference_notes=len(carried), continuing_before=len(carried & phb),
                    continuing_after=len(carried & phc), recovered=len(recovered),
                    lost=len(carried & (phb-phc)), bad_recovered_offsets=bad_recovered,
                    unassessed_recovered_offsets=censored_recovered))


def _locked_plan(locked: dict) -> Plan:
    payload = {key: value for key, value in locked.items() if key != "plan_sha256"}
    if _digest(payload) != locked["plan_sha256"]:
        raise ValueError("Frozen plan digest mismatch")
    return Plan.model_validate(locked["plan"])


def check_isolated_pair(offset: dict, prefix: dict) -> dict:
    """Require separate candidates on identical original baselines/targets and tolerances."""
    a, b = _locked_plan(offset), _locked_plan(prefix)
    if a.candidate.kind != "offset_only" or b.candidate.kind != "prefix_only":
        raise ValueError("Provide an offset-only lock and a prefix-only lock")
    for key in ("dataset_kind", "known_reused_recording_ids", "baseline_pipeline_identity", "recordings", "metrics", "prefix_seconds"):
        if getattr(a, key) != getattr(b, key):
            raise ValueError("Isolated plans must share original inputs and conditions: " + key)
    return dict(status="same_baseline_and_targets", offset_plan_sha256=offset["plan_sha256"], prefix_plan_sha256=prefix["plan_sha256"])


def evaluate_locked(locked: dict, attempts: Attempts, outcomes_root: Path) -> dict:
    """Assess saved artifacts, retaining failures/unscored cases and recording-level gates."""
    plan = _locked_plan(locked)
    if attempts.plan_sha256 != locked["plan_sha256"]:
        raise ValueError("Frozen plan digest mismatch")
    if _digest(attempts.candidate_parameters) != _digest(plan.candidate.parameters):
        raise ValueError("Candidate parameters differ from the predeclared plan")
    ids = [outcome.target_id for outcome in attempts.outcomes]
    targets = {target.id for recording in plan.recordings for target in recording.targets}
    if len(set(ids)) != len(ids) or not set(ids) <= targets:
        raise ValueError("Duplicate or unplanned target outcome")
    root = Path(locked["input_root"])
    outcomes = {outcome.target_id: outcome for outcome in attempts.outcomes}
    code_error = None
    try:
        from src.evaluation import metrics
        import mir_eval
        if (hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != locked["evaluator_sha256"]
            or hashlib.sha256(Path(metrics.__file__).read_bytes()).hexdigest() != locked["metrics_sha256"]
            or hashlib.sha256(Path(__file__).with_name("models.py").read_bytes()).hexdigest() != locked["models_sha256"]
            or {name: version(name) for name in ("pydantic", "numpy", "scipy")} != locked["dependency_versions"]
            or mir_eval.__version__ != locked["mir_eval_version"]):
            raise ValueError("Evaluator changed after freeze")
        for asset in plan.candidate.implementation_files:
            _bytes(root, asset)
    except (ValueError, OSError) as error:
        code_error = str(error)
    recordings = []
    for recording in plan.recordings:
        rows = []
        for target in recording.targets:
            row = dict(target_id=target.id, status="not_evaluated", reason="No outcome supplied", comparison=None)
            outcome = outcomes.get(target.id)
            try:
                if code_error:
                    raise ValueError(code_error)
                baseline = _events(root, target.baseline); _bounded(baseline, target)
                if isinstance(outcome, Unfinished):
                    row.update(status=outcome.status, reason=outcome.reason)
                elif outcome is not None:
                    candidate = _events(outcomes_root, outcome.artifact); _bounded(candidate, target)
                    if target.reference.status != "verified":
                        row.update(reason="Reference " + target.reference.status + ": " + target.reference.reason)
                    else:
                        ref = _events(root, target.reference.artifact)
                        row.update(status="evaluated", reason=None, comparison=_compare(plan, target, ref, baseline, candidate))
                        # Detect mutation during evaluation; original files are never rewritten.
                        _bytes(root, target.baseline); _bytes(root, target.reference.artifact)
                        _bytes(outcomes_root, outcome.artifact)
            except (ValueError, OSError) as error:
                row.update(status="failed", reason=str(error), comparison=None)
            rows.append(row)
        recordings.append(dict(recording_id=recording.id, usage=recording.usage, targets=rows,
                               summary=_aggregate(rows)))
    reasons = _gate(plan, recordings)
    return dict(schema_version="correction-audit-result-v1", plan_sha256=locked["plan_sha256"],
        frozen_plan=locked, recordings=recordings,
        decision="hold" if reasons else "ready_for_human_review", hold_reasons=reasons,
        metric_scope="Window intersections: held notes get a virtual crop-boundary onset; these are not acoustic attack scores. Offset transition tolerances use original reference durations; right-censored offsets are unassessed.",
        warning="Declared provenance/unused status is not independently certified. Never an automatic production promotion.")


def _aggregate(rows: list[dict]) -> dict:
    assessed = [row["comparison"] for row in rows if row["status"] == "evaluated"]
    summary = dict(planned=len(rows), evaluated=len(assessed), failed=sum(r["status"] == "failed" for r in rows),
                   not_evaluated=sum(r["status"] == "not_evaluated" for r in rows))
    for scope in ("", "prefix_"):
        for label in ("before", "after"):
            values = [row[label] if not scope else row["prefix"][label] for row in assessed]
            counts = {key: sum(value[key] for value in values) for key in ("reference_notes", "estimated_notes", "tp", "missed", "extra")}
            denominator = counts["reference_notes"]+counts["estimated_notes"]
            counts["onset_micro_f1"] = 2*counts["tp"]/denominator if counts["reference_notes"] else None
            summary[scope+label] = counts
    summary["violations"] = sum(len(row["violations"]) for row in assessed)
    summary["offset_unassessed_targets"] = sum(not row["offset_transitions"]["assessed"] for row in assessed)
    for key in ("newly_missed_onsets", "extra_increase", "octave_increase"):
        summary[key] = sum(row[key] for row in assessed)
    for key in ("eligible", "right_censored", "broken_correct", "repaired", "worsened"):
        summary[key] = sum(row["offset_transitions"][key] for row in assessed)
    for label in ("before", "after"):
        error = sum(row["offset_transitions"]["absolute_error_"+label+"_ms"] for row in assessed)
        summary["offset_mae_"+label+"_ms"] = error/summary["eligible"] if summary["eligible"] else None
    for key in ("recovered", "lost", "bad_recovered_offsets", "unassessed_recovered_offsets", "continuing_reference_notes", "continuing_before", "continuing_after"):
        summary[key] = sum(row["prefix"][key] for row in assessed)
    return summary


def _gate(plan: Plan, recordings: list[dict]) -> list[str]:
    reasons = []
    criteria = plan.criteria
    limits = dict(broken_correct=criteria.maximum_broken_correct_offsets,
                  worsened=criteria.maximum_worsened_offsets,
                  newly_missed_onsets=criteria.maximum_newly_missed_onsets,
                  extra_increase=criteria.maximum_extra_increase,
                  octave_increase=criteria.maximum_octave_increase,
                  bad_recovered_offsets=criteria.maximum_bad_recovered_continuing_offsets)
    for row in recordings:
        summary = row["summary"]
        if summary["evaluated"] != summary["planned"]:
            reasons.append(row["recording_id"] + ": incomplete_or_failed")
        if summary["violations"] or summary["lost"] or summary["unassessed_recovered_offsets"]:
            reasons.append(row["recording_id"] + ": invariant_or_boundary_assessment_problem")
        for key, maximum in limits.items():
            if summary[key] > maximum:
                reasons.append(row["recording_id"] + ": regression_" + key)
    held = [row for row in recordings if row["usage"] == "held_out_declared_unused"]
    primary = "repaired" if plan.candidate.kind == "offset_only" else "recovered"
    if len(held) < criteria.minimum_held_out_recordings:
        reasons.append("insufficient_declared_unused_recordings")
    if sum(row["summary"][primary] > 0 for row in held) < criteria.minimum_improved_held_out_recordings:
        reasons.append("insufficient_improved_recordings_no_effect_or_worse")
    if sum(row["summary"][primary] for row in held) < criteria.minimum_primary_recoveries:
        reasons.append("insufficient_primary_recoveries")
    return reasons


def _write_new(path: Path, value: dict) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def main(argv: list[str] | None = None) -> None:
    """Print schemas, freeze a protocol, or assess saved canonical JSON, without inference."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    schema = commands.add_parser("schema"); schema.add_argument("kind", choices=["plan", "attempts"])
    freeze = commands.add_parser("freeze"); freeze.add_argument("--plan", type=Path, required=True); freeze.add_argument("--out", type=Path, required=True)
    pair = commands.add_parser("check-pair"); pair.add_argument("--offset-lock", type=Path, required=True); pair.add_argument("--prefix-lock", type=Path, required=True)
    evaluate = commands.add_parser("evaluate"); evaluate.add_argument("--lock", type=Path, required=True); evaluate.add_argument("--attempts", type=Path, required=True); evaluate.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "schema":
        print(json.dumps((Plan if args.kind == "plan" else Attempts).model_json_schema(), indent=2))
    elif args.command == "freeze":
        plan = Plan.model_validate_json(args.plan.read_text())
        _write_new(args.out, freeze_plan(plan, args.plan.parent))
    elif args.command == "check-pair":
        print(json.dumps(check_isolated_pair(json.loads(args.offset_lock.read_text()), json.loads(args.prefix_lock.read_text())), indent=2))
    else:
        locked = json.loads(args.lock.read_text())
        attempts = Attempts.model_validate_json(args.attempts.read_text())
        _write_new(args.out, evaluate_locked(locked, attempts, args.attempts.parent))


if __name__ == "__main__":
    main()
