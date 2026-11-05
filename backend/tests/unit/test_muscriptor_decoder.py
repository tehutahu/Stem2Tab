"""Synthetic decoder contract tests; no models, recordings or CUDA."""
from copy import deepcopy

import pytest

from src.pipelines.muscriptor_decoder import inspect_decoder_integrity


def token(kind: str, value: int) -> dict:
    return dict(type="token", event_type=kind, value=value)


def test_backward_absolute_shift_is_diagnostic_and_source_is_unchanged() -> None:
    tokens = [dict(type="chunk_boundary", seek_time=0, next_seek_time=5), token("tie", 0),
              token("shift", 167), token("pitch", 40), token("shift", 156), token("pitch", 40)]
    notes = [dict(onset=1.67, offset=1.56, pitch=40)]
    before = deepcopy((notes, tokens))
    result = inspect_decoder_integrity(notes, tokens)
    assert result.status == "invalid" and result.notes_modified is False
    assert result.note_violations[0].kind == "negative_duration"
    assert (result.shift_regressions[0].previous_frame, result.shift_regressions[0].current_frame) == (167, 156)
    assert (notes, tokens) == before


def test_valid_notes_cannot_hide_a_regressing_token_stream() -> None:
    result = inspect_decoder_integrity([dict(onset=.1, offset=.2)],
        [token("tie", 0), token("shift", 20), token("shift", 10)])
    assert result.status == "invalid" and not result.note_violations


def test_new_chunk_and_zero_shift_are_not_time_regressions() -> None:
    tokens = [dict(type="chunk_boundary"), token("tie", 0), token("shift", 450), token("shift", 0),
              dict(type="chunk_boundary"), token("pitch", 40), token("tie", 0), token("shift", 10)]
    assert inspect_decoder_integrity([dict(onset=4.5, offset=5.1)], tokens).status == "valid"


@pytest.mark.parametrize(("onset", "offset", "kind"), [
    (.3, .3, "zero_duration"), (.3, .2, "negative_duration"),
    (-.1, .2, "invalid_boundary"), (float("nan"), .2, "invalid_boundary"),
    (True, .2, "invalid_boundary"), (".1", .2, "invalid_boundary"),
])
def test_invalid_duration_never_returns_a_repaired_note(onset, offset, kind) -> None:
    result = inspect_decoder_integrity([dict(onset=onset, offset=offset)], [])
    assert result.status == "invalid" and result.note_violations[0].kind == kind
    assert result.raw_note_count == 1 and result.notes_modified is False
