"""Fail-closed diagnostics for the unchanged official model event stream."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class NoteTimingViolation(BaseModel):
    """Identify the raw row without replacing its original boundary values."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    ordinal: int
    kind: Literal["negative_duration", "zero_duration", "invalid_boundary"]
    onset_repr: str
    offset_repr: str


class ShiftRegression(BaseModel):
    """A model timestamp that moves backward within one decoded chunk."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    token_ordinal: int
    chunk_ordinal: int
    previous_frame: int
    current_frame: int


class DecoderIntegrity(BaseModel):
    """Complete validity decision. No repaired or filtered note list is exposed."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    policy: Literal["strict-time-order-v1"] = "strict-time-order-v1"
    status: Literal["valid", "invalid"]
    raw_note_count: int
    note_violations: tuple[NoteTimingViolation, ...]
    shift_regressions: tuple[ShiftRegression, ...]
    notes_modified: Literal[False] = False
    automatic_retry: Literal[False] = False


def inspect_decoder_integrity(
    notes: Sequence[Mapping[str, Any]], tokens: Sequence[Mapping[str, Any]],
) -> DecoderIntegrity:
    """Reject nonpositive notes and backward absolute shifts without correction.

    The official decoder resets frame positions at chunk boundaries and ignores
    zero shift tokens. Tie prologues declare held pitches without advancing time.
    Raw notes and tokens remain authoritative, including invalid rows.
    """
    violations = []
    for ordinal, note in enumerate(notes):
        onset, offset = note.get("onset"), note.get("offset")
        numeric = all(type(value) in (int, float) and math.isfinite(value) for value in (onset, offset))
        kind = ("invalid_boundary" if not numeric or onset < 0 else
                "negative_duration" if offset < onset else "zero_duration" if offset == onset else None)
        if kind is not None:
            violations.append(NoteTimingViolation(ordinal=ordinal, kind=kind,
                                                  onset_repr=repr(onset), offset_repr=repr(offset)))
    regressions = []
    frame, chunk, prologue = 0, -1, True
    for ordinal, token in enumerate(tokens):
        if token.get("type") == "chunk_boundary":
            frame, prologue, chunk = 0, True, chunk + 1
        elif token.get("type") == "token":
            kind, value = token.get("event_type"), token.get("value")
            if kind == "tie":
                prologue = False
            elif kind == "shift" and not prologue and type(value) is int and value > 0:
                if value < frame:
                    regressions.append(ShiftRegression(token_ordinal=ordinal, chunk_ordinal=chunk,
                                                       previous_frame=frame, current_frame=value))
                frame = value
    return DecoderIntegrity(status="invalid" if violations or regressions else "valid",
                            raw_note_count=len(notes), note_violations=tuple(violations),
                            shift_regressions=tuple(regressions))
