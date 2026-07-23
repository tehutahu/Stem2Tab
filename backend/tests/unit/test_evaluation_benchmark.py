from __future__ import annotations

import json
from pathlib import Path

from src.evaluation import adapters
from src.evaluation.adapters import AdapterConfig, SeparationResult, TranscriptionResult
from src.evaluation.benchmark import main
from src.evaluation.io import write_midi
from src.evaluation.models import NoteEvent, NoteEventSet


class FakeTranscriber:
    name = "fake"

    def run(
        self,
        audio_path: Path,
        output_dir: Path,
        *,
        config: AdapterConfig,
    ) -> TranscriptionResult:
        del audio_path, config
        events = NoteEventSet(
            notes=(
                NoteEvent(start=0.0, end=0.5, midi=40, velocity=0.8),
                NoteEvent(start=0.5, end=1.0, midi=43, velocity=0.8),
            )
        )
        raw_path = write_midi(events, output_dir / "raw.mid")
        return TranscriptionResult(
            raw_midi_path=raw_path,
            events=events,
            metadata={"backend": self.name, "version": "test"},
        )


class BrokenTranscriber:
    name = "broken"

    def run(
        self,
        audio_path: Path,
        output_dir: Path,
        *,
        config: AdapterConfig,
    ) -> TranscriptionResult:
        del audio_path, output_dir, config
        raise RuntimeError("deliberate failure")


class CountingSeparator:
    name = "counting"

    def __init__(self) -> None:
        self.calls = 0

    def run(
        self,
        audio_path: Path,
        output_dir: Path,
        *,
        config: AdapterConfig,
    ) -> SeparationResult:
        del config
        self.calls += 1
        output_dir.mkdir(parents=True, exist_ok=True)
        return SeparationResult(audio_path=audio_path, metadata={"backend": self.name})


def _inputs(tmp_path: Path) -> tuple[Path, Path]:
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"synthetic audio placeholder")
    reference = NoteEventSet(
        notes=(
            NoteEvent(start=0.0, end=0.5, midi=40, velocity=0.8),
            NoteEvent(start=0.5, end=1.0, midi=43, velocity=0.8),
        )
    )
    reference_path = write_midi(reference, tmp_path / "reference.mid")
    return audio_path, reference_path


def test_cli_writes_complete_success_artifacts(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setitem(adapters.TRANSCRIBER_ADAPTERS, "fake", FakeTranscriber())
    audio_path, reference_path = _inputs(tmp_path)
    output_dir = tmp_path / "output"

    exit_code = main(
        [
            "--audio",
            str(audio_path),
            "--reference",
            str(reference_path),
            "--transcribers",
            "fake",
            "--output-dir",
            str(output_dir),
        ]
    )

    assert exit_code == 0
    for relative_path in (
        "manifest.json",
        "reference/events.json",
        "reference/events.csv",
        "reference/reference.mid",
        "runs/direct__fake/raw.mid",
        "runs/direct__fake/events.json",
        "runs/direct__fake/events.csv",
        "runs/direct__fake/performance.mid",
        "runs/direct__fake/metrics.json",
        "comparison.json",
        "comparison.csv",
        "report.md",
    ):
        assert (output_dir / relative_path).is_file(), relative_path

    comparison = json.loads((output_dir / "comparison.json").read_text(encoding="utf-8"))
    assert comparison["reference_available"] is True
    assert comparison["runs"][0]["status"] == "success"
    assert comparison["runs"][0]["metrics"]["onset_f1"] == 1.0
    manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["inputs"]["audio"]["sha256"]
    assert manifest["requested"]["separators"] == ["direct"]
    assert manifest["metric_config"]["frame_hop_ms"] == 10.0
    assert manifest["runs"][0]["adapter_metadata"]["backend"] == "fake"
    output = capsys.readouterr().out
    assert "direct__fake" in output
    assert f"Artifacts: {output_dir}" in output


def test_cli_without_reference_produces_listening_artifacts(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setitem(adapters.TRANSCRIBER_ADAPTERS, "fake", FakeTranscriber())
    monkeypatch.chdir(tmp_path)
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"synthetic audio placeholder")
    output_dir = tmp_path / "output"

    exit_code = main(
        [
            "--audio",
            str(audio_path),
            "--transcribers",
            "fake",
            "--output-dir",
            str(output_dir),
        ]
    )

    assert exit_code == 0
    assert not (output_dir / "reference").exists()
    assert (output_dir / "runs/direct__fake/raw.mid").is_file()
    assert (output_dir / "runs/direct__fake/performance.mid").is_file()
    comparison = json.loads((output_dir / "comparison.json").read_text(encoding="utf-8"))
    assert comparison["reference_available"] is False
    assert comparison["runs"][0]["metrics"] is None
    assert comparison["runs"][0]["estimated_note_count"] == 2
    manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["reference_available"] is False
    assert manifest["inputs"]["reference"] is None
    assert manifest["adapter_config"]["demucs_cache_dir"] == str(tmp_path / ".cache/demucs")
    report = (output_dir / "report.md").read_text(encoding="utf-8")
    assert "Reference MIDI: not provided" in report
    assert "| direct__fake | success | 2 |" in report
    assert "Accuracy metrics were not calculated" in capsys.readouterr().out


def test_cli_continues_after_transcriber_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setitem(adapters.TRANSCRIBER_ADAPTERS, "fake", FakeTranscriber())
    monkeypatch.setitem(adapters.TRANSCRIBER_ADAPTERS, "broken", BrokenTranscriber())
    audio_path, reference_path = _inputs(tmp_path)
    output_dir = tmp_path / "output"

    exit_code = main(
        [
            "--audio",
            str(audio_path),
            "--reference",
            str(reference_path),
            "--transcribers",
            "broken,fake",
            "--output-dir",
            str(output_dir),
        ]
    )

    assert exit_code == 1
    comparison = json.loads((output_dir / "comparison.json").read_text(encoding="utf-8"))
    assert [run["status"] for run in comparison["runs"]] == ["error", "success"]
    assert "deliberate failure" in comparison["runs"][0]["error"]
    assert (output_dir / "runs/broken").exists() is False
    assert (output_dir / "runs/direct__broken/metrics.json").is_file()
    assert (output_dir / "runs/direct__fake/performance.mid").is_file()


def test_cli_reuses_each_separator_for_all_transcribers(
    tmp_path: Path,
    monkeypatch,
) -> None:
    separator = CountingSeparator()
    monkeypatch.setitem(adapters.SEPARATOR_ADAPTERS, "counting", separator)
    monkeypatch.setitem(adapters.TRANSCRIBER_ADAPTERS, "fake", FakeTranscriber())
    monkeypatch.setitem(adapters.TRANSCRIBER_ADAPTERS, "fake_two", FakeTranscriber())
    audio_path, reference_path = _inputs(tmp_path)

    exit_code = main(
        [
            "--audio",
            str(audio_path),
            "--reference",
            str(reference_path),
            "--separators",
            "counting",
            "--transcribers",
            "fake,fake_two",
            "--output-dir",
            str(tmp_path / "output"),
        ]
    )

    assert exit_code == 0
    assert separator.calls == 1
    assert (tmp_path / "output/runs/counting__fake/performance.mid").is_file()
    assert (tmp_path / "output/runs/counting__fake_two/performance.mid").is_file()


def test_cli_refuses_nonempty_output_directory(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setitem(adapters.TRANSCRIBER_ADAPTERS, "fake", FakeTranscriber())
    audio_path, reference_path = _inputs(tmp_path)
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    sentinel = output_dir / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")

    exit_code = main(
        [
            "--audio",
            str(audio_path),
            "--reference",
            str(reference_path),
            "--transcribers",
            "fake",
            "--output-dir",
            str(output_dir),
        ]
    )

    assert exit_code == 2
    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert "not empty" in capsys.readouterr().err


def test_cli_rejects_reference_without_non_drum_notes(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setitem(adapters.TRANSCRIBER_ADAPTERS, "fake", FakeTranscriber())
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"audio")
    reference_path = write_midi(NoteEventSet(), tmp_path / "empty.mid")

    exit_code = main(
        [
            "--audio",
            str(audio_path),
            "--reference",
            str(reference_path),
            "--transcribers",
            "fake",
            "--output-dir",
            str(tmp_path / "output"),
        ]
    )

    assert exit_code == 2
    assert not (tmp_path / "output").exists()
    assert "no non-drum notes" in capsys.readouterr().err


def test_cli_auto_numbers_output_directory(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setitem(adapters.TRANSCRIBER_ADAPTERS, "fake", FakeTranscriber())
    monkeypatch.chdir(tmp_path)
    audio_path, reference_path = _inputs(tmp_path)

    exit_code = main(
        [
            "--audio",
            str(audio_path),
            "--reference",
            str(reference_path),
            "--transcribers",
            "fake",
        ]
    )

    assert exit_code == 0
    generated = list((tmp_path / "benchmark_results").iterdir())
    assert len(generated) == 1
    assert generated[0].name.startswith("audio-")
    assert (generated[0] / "report.md").is_file()
