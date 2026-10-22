"""Small frame-only regressions; no audio model or reference MIDI is required."""

import numpy as np
import pytest
from pydantic import ValidationError

from src.evaluation.note_assembly import AssemblyConfig, assemble_notes


def assemble(pitches, *, hop=0.01, active=None, voiced=None, onsets=(), config=AssemblyConfig()):
    pitches = np.array(pitches, dtype=float)
    f0 = 440 * 2 ** ((pitches - 69) / 12)
    times = np.arange(len(pitches)) * hop
    return assemble_notes(f0, np.ones(len(pitches), dtype=bool) if voiced is None else voiced,
                          times, len(pitches) * hop, active=active, onset_times=onsets, config=config).notes


def test_sustain_discards_short_boundary_pitch_fragments():
    notes = assemble([29] + [28] * 10 + [27])
    assert [(n.midi, n.start, n.end) for n in notes] == [(28, 0.01, 0.11)]


def test_sustain_repairs_one_frame_interior_jitter():
    notes = assemble([28] * 5 + [29] + [28] * 6)
    assert [(n.midi, n.start, n.end) for n in notes] == [(28, 0.0, 0.12)]
    assert notes[0].velocity == 80 / 127


def test_stable_pitch_transition_is_kept():
    notes = assemble([28] * 6 + [33] * 6)
    assert [(n.midi, n.start, n.end) for n in notes] == [(28, 0.0, 0.06), (33, 0.06, 0.12)]


def test_independent_onset_keeps_same_pitch_repicking():
    notes = assemble([28] * 25, onsets=[0.02, 0.10, 0.20, 0.245])
    assert [(n.start, n.end) for n in notes] == [(0, 0.1), (0.1, 0.2), (0.2, 0.25)]


def test_onset_between_frames_retains_exact_supplied_time():
    notes = assemble([28] * 20, onsets=[0.075])
    assert [(n.start, n.end) for n in notes] == [(0, 0.075), (0.075, 0.2)]


@pytest.mark.parametrize("frames", [6, 7, 8, 9])
def test_60_to_90ms_notes_survive(frames):
    notes = assemble([28] * frames)
    assert len(notes) == 1
    assert notes[0].end == pytest.approx(frames * 0.01)


@pytest.mark.parametrize("gap_kind", ["voiced", "active"])
def test_even_one_frame_gap_is_not_bridged(gap_kind):
    mask = np.ones(15, dtype=bool)
    mask[7] = False
    notes = assemble([28] * 15, **{gap_kind: mask})
    assert [(n.start, n.end) for n in notes] == [(0, 0.07), (0.08, 0.15)]


def test_jitter_with_independent_onset_is_not_repaired():
    notes = assemble([28] * 7 + [29] + [28] * 7, onsets=[0.07])
    assert [(n.start, n.end) for n in notes] == [(0, 0.07), (0.08, 0.15)]


def test_invalid_f0_forms_gaps_and_audio_end_is_clipped():
    result = assemble_notes([110, 110, np.nan, 110, 110, 110], [True] * 6,
                            [0, 0.01, 0.02, 0.03, 0.04, 0.06], 0.05)
    assert [(n.start, n.end) for n in result.notes] == [(0, 0.02), (0.03, 0.05)]


def test_empty_frame_input_returns_no_notes():
    assert assemble_notes([], [], [], 1).notes == ()


@pytest.mark.parametrize("kwargs", [
    {"f0": [[110, 110]]}, {"voiced": [True]}, {"active": [True]},
    {"times": [0, 0]}, {"times": [0.1, 0]}, {"times": [-0.1, 0]},
    {"times": [0, np.nan]}, {"audio_duration": 0}, {"audio_duration": np.inf},
    {"onset_times": [np.nan]}, {"onset_times": [-0.1]}, {"onset_times": [1.1]},
    {"voiced": [1, 1]},
])
def test_invalid_inputs_are_rejected(kwargs):
    arguments = dict(f0=[110, 110], voiced=[True, True], times=[0, 0.01], audio_duration=1)
    arguments.update(kwargs)
    with pytest.raises(ValueError):
        assemble_notes(**arguments)


def test_config_is_frozen_and_validated():
    with pytest.raises(ValidationError):
        AssemblyConfig(min_pitch_run_frames=0)
    with pytest.raises(ValidationError):
        AssemblyConfig(min_onset_separation_seconds=np.nan)
    config = AssemblyConfig()
    with pytest.raises(ValidationError):
        config.min_pitch_run_frames = 3


def test_repeated_onsets_at_exact_50ms_spacing_survive_float_rounding():
    notes = assemble([28] * 25, onsets=[0.05, 0.10, 0.15, 0.20])
    assert [(n.start, n.end) for n in notes] == [
        (0, 0.05), (0.05, 0.10), (0.10, 0.15), (0.15, 0.20), (0.20, 0.25),
    ]


def test_half_semitone_rounding_matches_raw_librosa_conversion():
    import librosa

    frequencies = 440 * 2 ** ((np.arange(24, 68) + 0.5 - 69) / 12)
    for frequency, expected in zip(frequencies, np.rint(librosa.hz_to_midi(frequencies))):
        result = assemble_notes([frequency, frequency], [True, True], [0, 0.01], 0.02)
        assert [n.midi for n in result.notes] == [int(expected)]


def test_late_release_onset_does_not_create_23ms_tail():
    # The 23 ms remainder includes two frame starts, but is still too short.
    notes = assemble([28] * 20, onsets=[0.177])
    assert [(n.start, n.end) for n in notes] == [(0, 0.2)]


@pytest.mark.parametrize("tail_duration", [0.06, 0.07, 0.08, 0.09])
def test_same_pitch_repicking_retains_60_to_90ms_final_note(tail_duration):
    onset = 0.3 - tail_duration
    notes = assemble([28] * 30, onsets=[onset])
    assert [(n.start, n.end) for n in notes] == [(0, onset), (onset, 0.3)]
