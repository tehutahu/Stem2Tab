"""Validate generated diagnostic inputs without running transcription backends."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pretty_midi
import pytest
import soundfile as sf

from src.evaluation import synthetic

SUITE_SIZES = {"development": 11, "validation_a": 8, "validation_b": 8}
FIXTURES = [
    pytest.param(suite, spec, id=f"{suite}-{spec['id']}")
    for suite in SUITE_SIZES
    for spec in synthetic.fixture_cases(suite)
]


@pytest.mark.parametrize("suite,size", SUITE_SIZES.items())
def test_suite_definitions_have_unique_ids_and_nonoverlapping_notes(suite: str, size: int) -> None:
    specs = synthetic.fixture_cases(suite)
    assert len(specs) == size
    assert len({spec["id"] for spec in specs}) == size
    for spec in specs:
        previous_end = 0.0
        for pitch, start, duration, gain in spec["notes"]:
            assert start >= previous_end - 1 / synthetic.SR
            assert duration > 0
            assert 0 <= pitch <= 127
            assert gain > 0
            previous_end = start + duration


def test_reserved_validation_suites_cover_difficult_conditions_and_are_distinct() -> None:
    first = synthetic.fixture_cases("validation_a")
    second = synthetic.fixture_cases("validation_b")
    # Ignore labels: reserved conditions must differ in the actual synthesis data.
    signatures = lambda cases: {
        json.dumps({k: v for k, v in spec.items() if k != "id"}, sort_keys=True)
        for spec in cases
    }
    assert not signatures(first).intersection(signatures(second))
    for cases in (first, second):
        assert any(spec["legato"] for spec in cases)
        assert any(spec["partials"][0] < spec["partials"][1] for spec in cases)
        assert any(len({n[3] for n in spec["notes"]}) > 1 for spec in cases)
        assert any(n[2] < 0.12 for spec in cases for n in spec["notes"])
        assert any(not spec["notes"] for spec in cases)
        assert any(
            left[0] == right[0]
            for spec in cases
            for left, right in zip(spec["notes"], spec["notes"][1:])
        )


@pytest.mark.parametrize("suite,spec", FIXTURES)
def test_generated_audio_and_reference_are_sample_aligned(
    tmp_path: Path, suite: str, spec: dict,
) -> None:
    directory = tmp_path / suite / spec["id"]
    fixture = synthetic.generate_fixture(spec, directory)
    audio, sr = sf.read(directory / "audio.wav")
    info = sf.info(directory / "audio.wav")
    assert sr == 22050
    assert audio.ndim == 1
    assert info.channels == 1
    assert info.subtype == "PCM_16"
    assert len(audio) == round(fixture["seconds"] * sr)
    assert np.isfinite(audio).all()
    assert fixture["sample_rate"] == sr
    assert fixture["channels"] == 1
    assert fixture["rms_actual"] == pytest.approx(float(np.sqrt(np.mean(audio**2))))
    assert json.loads((directory / "fixture.json").read_text()) == fixture

    reference = pretty_midi.PrettyMIDI(str(directory / "reference.mid"))
    notes = sorted(
        (n for instrument in reference.instruments for n in instrument.notes),
        key=lambda n: n.start,
    )
    assert len(notes) == len(spec["notes"]) == len(fixture["notes"])
    for note, source, event in zip(notes, spec["notes"], fixture["notes"]):
        pitch, start, duration, _ = source
        start_sample = round(start * sr)
        end_sample = start_sample + round(duration * sr)
        assert note.pitch == pitch == event["midi"]
        assert event["start_sample"] == start_sample
        assert event["end_sample"] == end_sample
        assert note.start == pytest.approx(start_sample / sr, abs=1 / sr)
        assert note.end == pytest.approx(end_sample / sr, abs=1 / sr)
    if not notes:
        assert np.count_nonzero(audio) == 0
        assert fixture["rms_actual"] == 0
    else:
        assert np.max(np.abs(audio)) == pytest.approx(spec["peak"], abs=1 / 32768)
        assert np.count_nonzero(audio[:fixture["notes"][0]["start_sample"]]) == 0


def test_fixture_generation_is_deterministic_and_refuses_existing_directory(tmp_path: Path) -> None:
    spec = synthetic.fixture_cases("validation_b")[5]  # phase/envelope variant
    first, second = tmp_path / "first", tmp_path / "second"
    assert synthetic.generate_fixture(spec, first) == synthetic.generate_fixture(spec, second)
    for filename in ("audio.wav", "reference.mid", "fixture.json"):
        assert hashlib.sha256((first / filename).read_bytes()).digest() == hashlib.sha256(
            (second / filename).read_bytes()
        ).digest()
    saved = (first / "audio.wav").read_bytes()
    with pytest.raises(FileExistsError):
        synthetic.generate_fixture(spec, first)
    assert (first / "audio.wav").read_bytes() == saved


def test_generate_only_records_plan_without_calling_transcription(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = synthetic.fixture_cases("validation_a")[-1]
    monkeypatch.setattr(synthetic, "fixture_cases", lambda suite: [spec])

    def forbidden_inference(*args: object, **kwargs: object):
        raise AssertionError("generate-only must not invoke a transcription subprocess")

    monkeypatch.setattr(synthetic.subprocess, "run", forbidden_inference)
    monkeypatch.setattr(synthetic.subprocess, "check_output", lambda *args, **kwargs: "test-commit\n")
    directory = tmp_path / "generated"
    assert synthetic.main([
        "--output-dir", str(directory), "--suite", "validation_a", "--generate-only",
    ]) == 0
    plan = json.loads((directory / "plan.json").read_text())
    assert plan["cpu_only"] is True
    assert plan["source_commit"] == "test-commit"
    assert len(plan["cases"]) == 1
    assert plan["cases"][0]["notes"] == []
    assert not (directory / "runs").exists()


def test_main_refuses_nonempty_output_before_generating_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = tmp_path / "keep.txt"
    sentinel.write_text("preserve existing output")

    def forbidden_generation(*args: object, **kwargs: object):
        raise AssertionError("existing output must be rejected before generating fixtures")

    monkeypatch.setattr(synthetic, "generate_fixture", forbidden_generation)
    with pytest.raises(SystemExit) as exc:
        synthetic.main([
            "--output-dir", str(tmp_path), "--suite", "development", "--generate-only",
        ])
    assert exc.value.code == 2
    assert list(tmp_path.iterdir()) == [sentinel]
    assert sentinel.read_text() == "preserve existing output"


def test_unknown_fixture_suite_fails_explicitly() -> None:
    with pytest.raises(ValueError, match="Unknown suite"):
        synthetic.fixture_cases("unknown")


def test_pyin_only_summary_records_missing_optional_basic_pitch_as_null(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Summaries remain usable when Basic Pitch was never installed for a pYIN run."""
    plan = {
        "suite": "validation_a",
        "transcribers": ["pyin"],
        "cases": [{"id": "silence", "notes": []}],
    }
    (tmp_path / "plan.json").write_text(json.dumps(plan))
    run = tmp_path / "runs" / "silence" / "runs" / "direct__pyin"
    run.mkdir(parents=True)
    (run / "metrics.json").write_text(json.dumps({
        "metrics": None, "transcription_seconds": 0.01,
    }))
    (run / "events.json").write_text(json.dumps({"notes": []}))

    def optional_version(name: str) -> str:
        if name == "basic-pitch":
            raise synthetic.metadata.PackageNotFoundError(name)
        return "test-version"

    monkeypatch.setattr(synthetic.metadata, "version", optional_version)
    synthetic.summarize(tmp_path)

    packages = json.loads((tmp_path / "provenance.json").read_text())["packages"]
    assert packages["basic-pitch"] is None
    assert packages["librosa"] == "test-version"
    rows = json.loads((tmp_path / "summary.json").read_text())
    assert len(rows) == 1
    assert rows[0]["method"] == "pyin"
    assert rows[0]["estimated_notes"] == 0
    assert (tmp_path / "summary.csv").is_file()
    assert json.loads((tmp_path / "note_diagnostics.json").read_text())[0]["diagnostics"] == []
