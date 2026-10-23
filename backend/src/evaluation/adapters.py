"""Pluggable separator and transcriber adapters for benchmark runs."""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path
from typing import Protocol

from src.evaluation.io import read_midi
from src.evaluation.models import NoteEventSet, SourceValue
from src.pipelines.separation import separate_stems
from src.pipelines.transcription import transcribe_midi
from src.evaluation.pyin import PyinTranscriber


@dataclass(frozen=True)
class AdapterConfig:
    """Runtime settings shared with benchmark adapters."""

    demucs_model: str
    demucs_cache_dir: Path


@dataclass(frozen=True)
class SeparationResult:
    """Audio selected for transcription plus artifacts produced by separation."""

    audio_path: Path
    artifacts: dict[str, Path] = field(default_factory=dict)
    metadata: dict[str, SourceValue] = field(default_factory=dict)


@dataclass(frozen=True)
class TranscriptionResult:
    """Raw backend MIDI and its canonical note-event representation."""

    raw_midi_path: Path
    events: NoteEventSet
    metadata: dict[str, SourceValue] = field(default_factory=dict)


class SeparatorAdapter(Protocol):
    """Interface implemented by benchmark audio separators."""

    name: str

    def run(
        self,
        audio_path: Path,
        output_dir: Path,
        *,
        config: AdapterConfig,
    ) -> SeparationResult:
        """Prepare one audio input for all selected transcribers."""
        ...


class TranscriberAdapter(Protocol):
    """Interface implemented by benchmark note transcribers."""

    name: str

    def run(
        self,
        audio_path: Path,
        output_dir: Path,
        *,
        config: AdapterConfig,
    ) -> TranscriptionResult:
        """Transcribe audio into raw MIDI and canonical note events."""
        ...


class DirectSeparator:
    """Pass input audio through without copying or processing it."""

    name = "direct"

    def run(
        self,
        audio_path: Path,
        output_dir: Path,
        *,
        config: AdapterConfig,
    ) -> SeparationResult:
        del config
        output_dir.mkdir(parents=True, exist_ok=True)
        if not audio_path.is_file():
            raise FileNotFoundError(f"Input audio not found: {audio_path}")
        return SeparationResult(
            audio_path=audio_path,
            metadata={"backend": "direct"},
        )


class DemucsSeparator:
    """Wrap the existing Demucs pipeline and select its bass stem."""

    name = "demucs"

    def run(
        self,
        audio_path: Path,
        output_dir: Path,
        *,
        config: AdapterConfig,
    ) -> SeparationResult:
        config.demucs_cache_dir.mkdir(parents=True, exist_ok=True)
        stems = separate_stems(
            input_audio=audio_path,
            output_dir=output_dir,
            model_name=config.demucs_model,
            cache_dir=config.demucs_cache_dir,
            job_id="benchmark",
        )
        bass_path = stems.get("bass")
        if bass_path is None:
            raise RuntimeError("Demucs did not produce a bass stem")
        return SeparationResult(
            audio_path=bass_path,
            artifacts=dict(stems),
            metadata={
                "backend": "demucs",
                "model": config.demucs_model,
                "cache_dir": str(config.demucs_cache_dir),
            },
        )


class BasicPitchTranscriber:
    """Wrap the project's existing Basic Pitch ONNX transcription path."""

    name = "basic_pitch"

    def run(
        self,
        audio_path: Path,
        output_dir: Path,
        *,
        config: AdapterConfig,
    ) -> TranscriptionResult:
        del config
        output_dir.mkdir(parents=True, exist_ok=True)
        generated_path = transcribe_midi(audio_path, output_dir, job_id="benchmark")
        raw_midi_path = output_dir / "raw.mid"
        if generated_path.resolve() != raw_midi_path.resolve():
            shutil.move(str(generated_path), raw_midi_path)
        events = read_midi(
            raw_midi_path,
            source={
                "kind": "estimate",
                "transcriber": self.name,
            },
        )
        return TranscriptionResult(
            raw_midi_path=raw_midi_path,
            events=events,
            metadata={
                "backend": self.name,
                "model": "ICASSP_2022",
                "serialization": "onnx",
                "package_version": _package_version("basic-pitch"),
            },
        )


class BasicPitchMonoTranscriber(BasicPitchTranscriber):
    """Optional mono-bass partial suppression, preserving the raw model result."""

    name = "basic_pitch_mono"

    def run(self, audio_path: Path, output_dir: Path, *, config: AdapterConfig) -> TranscriptionResult:
        """Apply a reference-free filter without changing the default backend."""
        import json
        from src.evaluation.harmonic_suppression import HarmonicSuppressionConfig, suppress_contained_harmonics
        from src.evaluation.io import write_midi, write_note_events_json, write_note_events_csv

        raw = super().run(audio_path, output_dir, config=config)
        settings = HarmonicSuppressionConfig()
        filtered, decisions = suppress_contained_harmonics(raw.events, config=settings)
        write_note_events_json(raw.events, output_dir / "raw_events.json")
        write_note_events_csv(raw.events, output_dir / "raw_events.csv")
        selected = output_dir / "filtered.mid"
        write_midi(filtered, selected)
        decisions_path = output_dir / "harmonic_suppression.json"
        decisions_path.write_text(json.dumps({"parameters": settings.model_dump(), "rejected": decisions}, indent=2) + "\n", encoding="utf-8")
        return TranscriptionResult(raw_midi_path=raw.raw_midi_path, events=filtered,
            metadata={**raw.metadata, "processing": "contained_harmonic_suppression", "uses_reference": False,
                      "parameters": settings.model_dump_json(), "selected_midi_path": str(selected),
                      "decisions_path": str(decisions_path), "suppressed_note_count": len(decisions)})


SEPARATOR_ADAPTERS: dict[str, SeparatorAdapter] = {
    DirectSeparator.name: DirectSeparator(),
    DemucsSeparator.name: DemucsSeparator(),
}
TRANSCRIBER_ADAPTERS: dict[str, TranscriberAdapter] = {
    BasicPitchTranscriber.name: BasicPitchTranscriber(),
    BasicPitchMonoTranscriber.name: BasicPitchMonoTranscriber(),
    "pyin_raw": PyinTranscriber(assembled=False),
    "pyin": PyinTranscriber(),
}


def get_separator_adapter(name: str) -> SeparatorAdapter:
    """Resolve a registered separator or report all available names."""
    try:
        return SEPARATOR_ADAPTERS[name]
    except KeyError as exc:
        available = ", ".join(sorted(SEPARATOR_ADAPTERS))
        raise ValueError(f"Unknown separator {name!r}; available: {available}") from exc


def get_transcriber_adapter(name: str) -> TranscriberAdapter:
    """Resolve a registered transcriber or report all available names."""
    try:
        return TRANSCRIBER_ADAPTERS[name]
    except KeyError as exc:
        available = ", ".join(sorted(TRANSCRIBER_ADAPTERS))
        raise ValueError(f"Unknown transcriber {name!r}; available: {available}") from exc


def _package_version(distribution: str) -> str | None:
    try:
        return metadata.version(distribution)
    except metadata.PackageNotFoundError:
        return None
