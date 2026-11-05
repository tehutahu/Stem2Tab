"""Cloud-only synthetic validation of the latest opt-in explicit GP5 path."""
from collections import Counter
from pathlib import Path

import pytest

from src.pipelines.bass_output import BassOutputConfig, BassOutputEvent
from src.pipelines.bass_fingering import plan_bass_fingering
from src.pipelines.explicit_rhythm import ExplicitRhythmConfig, plan_explicit_rhythm
from src.pipelines.rhythmic_gp5 import read_gp5_events, write_rhythmic_gp5


def _config() -> ExplicitRhythmConfig:
    return ExplicitRhythmConfig(tempo_bpm=120, numerator=4, denominator=4,
        origin_seconds=1, subdivisions_per_quarter=8, max_boundary_shift_ms=30)


def test_explicit_origin_rest_boundary_tie_and_real_pitch(tmp_path: Path) -> None:
    source = (BassOutputEvent(source_index=0, start=1.125, end=1.375, midi=28),
              BassOutputEvent(source_index=1, start=2.875, end=3.125, midi=43))
    original = tuple(event.model_dump() for event in source)
    instrument = BassOutputConfig(profile="bass4", max_fret=24)
    rhythm = plan_explicit_rhythm(source, config=_config())
    finger = plan_bass_fingering(source, config=instrument)
    assert rhythm.status == finger.status == "planned"
    result = write_rhythmic_gp5(rhythm, finger, tmp_path / "result", config=instrument)
    actual, report = read_gp5_events(tmp_path / "result/bass.explicit-rhythm.gp5")
    assert Counter((start, end, pitch) for start, end, pitch, _, _, _ in actual.elements()) == Counter(
        [(240, 720, 28), (3600, 4080, 43)])
    assert report["bars"] == 2 and report["tie_segments"] >= 1 and report["rest_beats"] >= 1
    assert result["origin_seconds"] == 1 and result["source_timing_preserved"] is False
    assert tuple(event.model_dump() for event in source) == original


def test_collapsed_short_event_is_not_written(tmp_path: Path) -> None:
    source = (BassOutputEvent(source_index=0, start=1.001, end=1.003, midi=28),)
    instrument = BassOutputConfig(profile="bass4", max_fret=24)
    rhythm = plan_explicit_rhythm(source, config=_config())
    assert rhythm.status == "blocked" and "short_note_collapsed" in rhythm.reasons
    with pytest.raises(ValueError, match="Complete"):
        write_rhythmic_gp5(rhythm, plan_bass_fingering(source, config=instrument),
                           tmp_path / "result", config=instrument)
    assert not (tmp_path / "result").exists()
