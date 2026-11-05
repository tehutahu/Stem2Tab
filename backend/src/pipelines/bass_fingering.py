"""Bounded opt-in bass fingering on unchanged performed-time events.

This constrains a discrete hand-window proxy, not human ergonomics or movement
speed. It does not infer tempo, modify notes, write files or change defaults.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.pipelines.bass_output import BassOutputConfig, BassOutputEvent, _mapping as _legacy_mapping

MAX_EVENTS = 500
MAX_STATES = 2048
MAX_EXPANSIONS = 100_000


class BassFingeringPosition(BaseModel):
    """A sounding pitch's candidate string (highest=1) and fret."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    string: int = Field(ge=1, le=7)
    fret: int = Field(ge=0, le=36)


class BassFingeringEvent(BassOutputEvent):
    """Original event identity/data plus an all-or-none planning decision."""

    input_ordinal: int = Field(ge=0)
    string: int | None = None
    fret: int | None = None
    candidate_positions: tuple[BassFingeringPosition, ...]
    unmappable_reason: str | None = None


class BassFingeringCost(BaseModel):
    """Fixed lexicographic objective: anchor travel, then total assigned frets."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    position_movement: int = Field(ge=0)
    total_fret: int = Field(ge=0)


class BassFingeringReport(BaseModel):
    """JSON-serializable complete plan or explicit bounded-search failure."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)
    schema_version: Literal["1.0"] = "1.0"
    status: Literal["planned", "unplayable", "too_complex"]
    profile: str
    strings: int
    tuning_midi: tuple[int, ...]
    max_fret: int
    max_position_shift: int
    max_hand_span: int
    input_event_count: int
    mapped_event_count: int
    input_events_sha256: str
    events: tuple[BassFingeringEvent, ...]
    cost: BassFingeringCost | None
    metrics: dict[str, Any]
    timeline: tuple[dict[str, Any], ...]
    legacy: dict[str, Any]
    movement_comparison: dict[str, Any]
    search: dict[str, Any]
    warnings: tuple[str, ...]
    reasons: tuple[dict[str, Any], ...]
    pitches_times_indices_preserved: Literal[True] = True
    transposition_semitones: Literal[0] = 0
    partial_plan: Literal[False] = False

    @model_validator(mode="after")
    def validate_complete_result(self) -> "BassFingeringReport":
        """Reject partial or internally inconsistent event-row reports."""
        if len(self.events) != self.input_event_count:
            raise ValueError("Every original event must remain in the report")
        if [event.input_ordinal for event in self.events] != list(range(len(self.events))):
            raise ValueError("Original event order/ordinals must be retained")
        mapped = sum(event.string is not None and event.fret is not None for event in self.events)
        if mapped != self.mapped_event_count:
            raise ValueError("Mapped count differs from event rows")
        if self.status == "planned":
            if mapped != self.input_event_count or self.cost is None or self.reasons:
                raise ValueError("A planned result must be complete")
            for event in self.events:
                if (not 1 <= event.string <= self.strings or not 0 <= event.fret <= self.max_fret or
                        self.tuning_midi[-event.string] + event.fret != event.midi):
                    raise ValueError("Assigned string/fret must preserve the original sounding pitch")
        elif mapped or self.cost is not None:
            raise ValueError("Failed searches must not expose a partial plan as usable")
        return self


@dataclass(frozen=True, slots=True)
class _Node:
    active: tuple[tuple[int, int, int], ...] = ()
    anchor: int | None = None
    movement: int = 0
    fret_sum: int = 0
    assignments: tuple[tuple[int, int, int], ...] = ()
    timeline: tuple[tuple[float, int | None, tuple[int, ...]], ...] = ()
    signature: tuple[tuple[int, int], ...] = ()

    @property
    def score(self) -> tuple[Any, ...]:
        return self.movement, self.fret_sum, self.signature


class _BudgetExceeded(Exception):
    pass


def _options(event: BassOutputEvent, config: BassOutputConfig) -> tuple[tuple[int, int], ...]:
    return tuple((string, event.midi - pitch)
                 for string, pitch in enumerate(reversed(config.resolved_tuning), 1)
                 if 0 <= event.midi - pitch <= config.resolved_max_fret)


def _groups(events: tuple[BassOutputEvent, ...]) -> list[tuple[float, tuple[int, ...]]]:
    times: dict[float, list[int]] = {}
    for index, event in enumerate(events):
        times.setdefault(event.start, []).append(index)
    return [(time, tuple(sorted(indices, key=lambda i: (events[i].midi, events[i].end, events[i].source_index, i))))
            for time, indices in sorted(times.items())]


def _anchors(active: tuple[tuple[int, int, int], ...], span: int) -> range:
    frets = [fret for _, _, fret in active if fret > 0]
    if not frets:
        return range(0)
    return range(max(1, max(frets) - span), min(frets) + 1)


def _search(events: tuple[BassOutputEvent, ...], options: tuple[tuple[tuple[int, int], ...], ...],
            shift: int, span: int, stats: dict[str, Any]) -> _Node | None:
    states = { ((), None): _Node() }
    def charge() -> None:
        if stats["expansions"] >= MAX_EXPANSIONS:
            stats["cap_reached"] = "expansions"
            raise _BudgetExceeded
        stats["expansions"] += 1
    for time, group in _groups(events):
        next_states: dict[tuple[Any, ...], _Node] = {}
        stats["onset_groups_processed"] += 1
        for node in states.values():
            active = tuple(row for row in node.active if events[row[0]].end > time)
            occupied = {string for _, string, _ in active}
            if len(active) + len(group) > stats["strings"]:
                continue
            def extend(offset: int, added: tuple[tuple[int, int, int], ...], used: set[int]) -> None:
                if offset < len(group):
                    index = group[offset]
                    for string, fret in options[index]:
                        charge()
                        if string in used:
                            continue
                        partial = active + added + ((index, string, fret),)
                        frets = [row[2] for row in partial if row[2] > 0]
                        if frets and max(frets) - min(frets) > span:
                            continue
                        extend(offset + 1, added + ((index, string, fret),), used | {string})
                    return
                combined = tuple(sorted(active + added))
                # Open-only attacks do not invent hand-position resets or free
                # intermediate shifts. Retain the previous anchor across rests.
                anchors = _anchors(combined, span) if any(fret > 0 for _, _, fret in added) else (node.anchor,)
                for anchor in anchors:
                    charge()
                    distance = abs(anchor - node.anchor) if anchor is not None and node.anchor is not None else 0
                    if distance > shift:
                        continue
                    candidate = _Node(combined, anchor, node.movement + distance,
                        node.fret_sum + sum(row[2] for row in added), node.assignments + added,
                        node.timeline + ((time, anchor, tuple(row[0] for row in combined)),),
                        node.signature + tuple((row[1], row[2]) for row in added))
                    key = combined, anchor
                    previous = next_states.get(key)
                    if previous is None and len(next_states) >= MAX_STATES:
                        stats["cap_reached"] = "states"
                        raise _BudgetExceeded
                    if previous is None or candidate.score < previous.score:
                        next_states[key] = candidate
                    stats["peak_states"] = max(stats["peak_states"], len(next_states))
            extend(0, (), occupied)
        stats["peak_states"] = max(stats["peak_states"], len(next_states))
        if not next_states:
            stats["failed_onset"] = time
            stats["failed_input_ordinals"] = list(group)
            return None
        states = next_states
    return min(states.values(), key=lambda node: node.score)


def _measure(events: tuple[BassOutputEvent, ...], mapping: dict[int, tuple[int, int]],
             span: int) -> tuple[dict[str, Any], list[dict[str, Any]], list[tuple[float, tuple[int, ...], range | None]]]:
    conflicts, spans, max_polyphony = set(), [], 0
    constraints, anchor_groups = [], []
    for time, group in _groups(events):
        active = tuple(index for index, event in enumerate(events) if event.start <= time < event.end and index in mapping)
        max_polyphony = max(max_polyphony, len(active))
        for offset, left in enumerate(active):
            for right in active[offset + 1:]:
                if mapping[left][0] == mapping[right][0]:
                    conflicts.add((left, right))
        frets = [mapping[index][1] for index in active if mapping[index][1] > 0]
        observed = max(frets) - min(frets) if frets else 0
        spans.append(observed)
        if observed > span:
            constraints.append(dict(code="hand_span", time=time, input_ordinals=list(active), observed=observed, limit=span))
        new_fretted = any(index in mapping and mapping[index][1] > 0 for index in group)
        active_rows = tuple((index, *mapping[index]) for index in active)
        anchor_groups.append((time, active, _anchors(active_rows, span) if new_fretted else None))
    for pair in sorted(conflicts):
        constraints.append(dict(code="same_string_overlap", input_ordinals=list(pair)))
    return dict(same_string_overlap_pairs=len(conflicts), max_fretted_span=max(spans, default=0),
                max_simultaneous_notes=max_polyphony, total_fret=sum(fret for _, fret in mapping.values())), constraints, anchor_groups


def _fixed_anchors(groups: list[tuple[float, tuple[int, ...], range | None]],
                   limit: int | None) -> tuple[int, tuple[tuple[float, int | None, tuple[int, ...]], ...]] | None:
    states: dict[int | None, tuple[int, tuple[Any, ...]]] = {None: (0, ())}
    for time, active, choices in groups:
        next_states = {}
        for previous, (cost, timeline) in states.items():
            for anchor in choices if choices is not None else (previous,):
                distance = abs(anchor - previous) if anchor is not None and previous is not None else 0
                if limit is not None and distance > limit:
                    continue
                candidate = cost + distance, timeline + ((time, anchor, active),)
                old = next_states.get(anchor)
                # Ascending anchor enumeration and stable onset order settle
                # equal-cost ties without repeated whole-path comparisons.
                if old is None or candidate[0] < old[0]:
                    next_states[anchor] = candidate
        states = next_states
        if not states:
            return None
    return min(states.values(), key=lambda value: (value[0], tuple(row[1] or 0 for row in value[1])))


def _timeline_rows(timeline: tuple[Any, ...]) -> tuple[dict[str, Any], ...]:
    return tuple(dict(time=time, hand_position=anchor, active_input_ordinals=list(active)) for time, anchor, active in timeline)


def _max_shift(timeline: tuple[Any, ...]) -> int:
    positions = [row[1] for row in timeline if row[1] is not None]
    return max((abs(a - b) for a, b in zip(positions, positions[1:])), default=0)


def _legacy(events: tuple[BassOutputEvent, ...], config: BassOutputConfig, shift: int, span: int) -> dict[str, Any]:
    rows = _legacy_mapping(events, config)
    mapping = {row.input_ordinal: (row.string, row.fret) for row in rows if row.string is not None}
    metrics, violations, groups = _measure(events, mapping, span)
    unmapped = [row.input_ordinal for row in rows if row.string is None]
    bounded = _fixed_anchors(groups, shift) if not unmapped else None
    relaxed = bounded or (_fixed_anchors(groups, None) if not unmapped else None)
    if unmapped:
        violations.append(dict(code="pitch_out_of_range", input_ordinals=unmapped))
    elif bounded is None and relaxed is not None:
        violations.append(dict(code="position_shift", limit=shift, observed_on_minimum_travel_path=_max_shift(relaxed[1])))
    if relaxed is not None:
        metrics.update(position_movement=relaxed[0], max_position_shift_observed=_max_shift(relaxed[1]))
    else:
        metrics.update(position_movement=None, max_position_shift_observed=None)
    return dict(policy="Existing first-playable string mapper; unchanged original pitches/times", events=[dict(
        **event.model_dump(), input_ordinal=index, string=rows[index].string, fret=rows[index].fret)
        for index, event in enumerate(events)], metrics=metrics, violations=violations,
        feasible_under_constraints=not violations, anchor_path_respects_movement_limit=bounded is not None,
        timeline=list(_timeline_rows(relaxed[1])) if relaxed is not None else [],
        cost=dict(position_movement=relaxed[0], total_fret=metrics["total_fret"]) if relaxed is not None else None)


def plan_bass_fingering(events: Sequence[BassOutputEvent], *, config: BassOutputConfig,
                        max_position_shift: int = 5, max_hand_span: int = 4) -> BassFingeringReport:
    """Plan every original note jointly under explicit string/hand-window limits.

    Intervals are half-open [start,end); same-onset notes are one unordered
    assignment group. A positive fret must lie in [hand_position,
    hand_position+max_hand_span]. Open-only attacks preserve the previous hand
    position, including over rests. The first fretted group has no assumed
    initial hand location. Movement is in fret units per fretted attack group,
    never speed per second. Exhaustion yields too_complex, never a claim that
    an unsearched fingering is impossible. No file or input object is modified.
    """
    events = tuple(events)
    if not isinstance(config, BassOutputConfig) or any(not isinstance(event, BassOutputEvent) for event in events):
        raise TypeError("Use validated BassOutputConfig and BassOutputEvent values")
    if any(type(value) is not int or not 0 <= value <= 36 for value in (max_position_shift, max_hand_span)):
        raise ValueError("Movement and hand-span limits must be explicit integers in0..36")
    options = tuple(_options(event, config) for event in events)
    warnings = ["Hand-window/string constraints are not an ergonomic guarantee or a fingering-finger assignment.",
                "Movement bounds also apply across rests; no tempo or movement speed is inferred.",
                "Original timing is used for conflicts; GP5 quarter-note preview timing is unchanged by this planner."]
    duplicates = [index for index, count in Counter(event.source_index for event in events).items() if count > 1]
    if duplicates:
        warnings.append("Duplicate source indices retained; input_ordinal distinguishes event instances.")
    stats = dict(caps=dict(events=MAX_EVENTS, states=MAX_STATES, expansions=MAX_EXPANSIONS),
                 strings=len(config.resolved_tuning), peak_states=1, expansions=0, onset_groups_processed=0,
                 complete=False, optimality="No partial plan returned; exact-state dominance only, no beam pruning")
    reasons: list[dict[str, Any]] = []
    node = None
    status = "planned"
    if len(events) > MAX_EVENTS:
        status = "too_complex"
        reasons.append(dict(code="event_cap", observed=len(events), limit=MAX_EVENTS))
    else:
        missing = [index for index, choices in enumerate(options) if not choices]
        if missing:
            status = "unplayable"
            reasons.append(dict(code="pitch_out_of_range", input_ordinals=missing,
                                source_indices=[events[index].source_index for index in missing]))
            stats["complete"] = True
        else:
            try:
                node = _search(events, options, max_position_shift, max_hand_span, stats)
                stats["complete"] = True
                if node is None:
                    status = "unplayable"
                    reasons.append(dict(code="constraints_unsatisfied", time=stats.get("failed_onset"),
                                        input_ordinals=stats.get("failed_input_ordinals", []),
                                        meaning="No complete plan under these explicit constraints; no claim of physical impossibility"))
            except _BudgetExceeded:
                status = "too_complex"
                reasons.append(dict(code="search_cap", meaning="Search stopped without proving feasibility or infeasibility"))
    mapping = {index: (string, fret) for index, string, fret in node.assignments} if node is not None else {}
    metrics, violations, _ = _measure(events, mapping, max_hand_span) if status == "planned" else ({}, [], [])
    if node is not None:
        metrics.update(position_movement=node.movement, max_position_shift_observed=_max_shift(node.timeline))
        if violations or metrics["max_position_shift_observed"] > max_position_shift:
            raise RuntimeError("Internal planner constraint verification failed")
    legacy = _legacy(events, config, max_position_shift, max_hand_span) if len(events) <= MAX_EVENTS else dict(
        policy="Existing first-playable mapper", not_evaluated="Event cap reached", cost=None, metrics={})
    legacy_movement = legacy.get("metrics", {}).get("position_movement")
    comparable = node is not None and legacy_movement is not None
    comparison = dict(available=comparable, planned_position_movement=node.movement if node is not None else None,
                      legacy_position_movement=legacy_movement,
                      reduction=legacy_movement - node.movement if comparable else None,
                      legacy_feasible_under_constraints=legacy.get("feasible_under_constraints"),
                      interpretation="Same discrete hand-window travel definition; legacy may violate hard constraints. No ergonomic/quality improvement claim.")
    rows = tuple(BassFingeringEvent(**event.model_dump(), input_ordinal=index,
        string=mapping.get(index, (None, None))[0], fret=mapping.get(index, (None, None))[1],
        candidate_positions=tuple(BassFingeringPosition(string=string, fret=fret) for string, fret in options[index]),
        unmappable_reason=None if status == "planned" else ("Pitch outside configured tuning/fret range" if not options[index]
            else "No complete plan returned: " + status)) for index, event in enumerate(events))
    digest = hashlib.sha256(json.dumps([event.model_dump(mode="json") for event in events], sort_keys=True,
                                      separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    return BassFingeringReport(status=status, profile=config.profile, strings=len(config.resolved_tuning),
        tuning_midi=config.resolved_tuning, max_fret=config.resolved_max_fret,
        max_position_shift=max_position_shift, max_hand_span=max_hand_span,
        input_event_count=len(events), mapped_event_count=len(mapping), input_events_sha256=digest, events=rows,
        cost=BassFingeringCost(position_movement=node.movement, total_fret=node.fret_sum) if node is not None else None,
        metrics=metrics, timeline=_timeline_rows(node.timeline) if node is not None else (), legacy=legacy,
        movement_comparison=comparison, search=stats, warnings=tuple(warnings), reasons=tuple(reasons))
