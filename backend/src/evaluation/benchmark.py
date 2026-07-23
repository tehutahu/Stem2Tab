"""Command-line benchmark for separator and transcriber comparisons."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from time import perf_counter
from typing import Sequence

from src.core.config import settings
from src.evaluation.adapters import (
    AdapterConfig,
    SeparatorAdapter,
    TranscriberAdapter,
    get_separator_adapter,
    get_transcriber_adapter,
)
from src.evaluation.io import (
    read_midi,
    write_midi,
    write_note_events_csv,
    write_note_events_json,
)
from src.evaluation.metrics import MetricConfig, evaluate_note_events
from src.evaluation.models import NoteEventSet
from src.evaluation.reporting import (
    RunRecord,
    render_markdown_report,
    write_comparison_csv,
    write_comparison_json,
    write_markdown_report,
    write_run_metrics,
)


class BenchmarkConfigurationError(ValueError):
    """An invalid input or output configuration detected before execution."""


def build_parser() -> argparse.ArgumentParser:
    """Build the public Phase A benchmark CLI parser."""
    parser = argparse.ArgumentParser(
        description="Generate comparable audio-to-note artifacts, with optional reference metrics.",
    )
    parser.add_argument("--audio", required=True, type=Path, help="Input audio file")
    parser.add_argument(
        "--reference",
        type=Path,
        help="Optional bass-only reference MIDI; omit it for listening comparison",
    )
    parser.add_argument(
        "--separators",
        default="direct",
        help="Comma-separated separator adapters (default: direct)",
    )
    parser.add_argument(
        "--transcribers",
        default="basic_pitch",
        help="Comma-separated transcriber adapters (default: basic_pitch)",
    )
    parser.add_argument("--output-dir", type=Path, help="New or empty artifact directory")
    parser.add_argument(
        "--onset-tolerance-ms",
        type=_positive_float,
        default=50.0,
    )
    parser.add_argument(
        "--pitch-tolerance-cents",
        type=_positive_float,
        default=50.0,
    )
    parser.add_argument("--offset-ratio", type=_positive_float, default=0.2)
    parser.add_argument(
        "--offset-min-tolerance-ms",
        type=_positive_float,
        default=50.0,
    )
    parser.add_argument("--frame-hop-ms", type=_positive_float, default=10.0)
    parser.add_argument("--demucs-model", default=settings.demucs_model)
    parser.add_argument(
        "--demucs-cache-dir",
        type=Path,
        help="Demucs model cache (default: .cache/demucs under the current directory)",
    )
    return parser


def run_benchmark(args: argparse.Namespace) -> tuple[Path, list[RunRecord], str]:
    """Execute all requested adapter combinations and write their artifacts."""
    audio_path = _require_file(args.audio, "Audio")
    reference_path = (
        _require_file(args.reference, "Reference MIDI")
        if args.reference is not None
        else None
    )
    separator_names = _parse_adapter_names(args.separators, kind="separator")
    transcriber_names = _parse_adapter_names(args.transcribers, kind="transcriber")
    separators = [(name, _resolve_separator(name)) for name in separator_names]
    transcribers = [(name, _resolve_transcriber(name)) for name in transcriber_names]

    reference: NoteEventSet | None = None
    if reference_path is not None:
        try:
            reference = read_midi(
                reference_path,
                source={
                    "kind": "reference",
                    "path": str(reference_path.resolve()),
                },
            )
        except Exception as exc:
            raise BenchmarkConfigurationError(f"Could not read reference MIDI: {exc}") from exc
        if not reference.notes:
            raise BenchmarkConfigurationError(
                "Reference MIDI contains no non-drum notes; provide a bass-only reference MIDI"
            )

    metric_config = MetricConfig(
        onset_tolerance_ms=args.onset_tolerance_ms,
        pitch_tolerance_cents=args.pitch_tolerance_cents,
        offset_ratio=args.offset_ratio,
        offset_min_tolerance_ms=args.offset_min_tolerance_ms,
        frame_hop_ms=args.frame_hop_ms,
    )
    demucs_cache_dir = args.demucs_cache_dir or Path.cwd() / ".cache" / "demucs"
    adapter_config = AdapterConfig(
        demucs_model=args.demucs_model,
        demucs_cache_dir=demucs_cache_dir.resolve(),
    )
    output_dir = _prepare_output_dir(args.output_dir, audio_path=audio_path)
    started_at = _utc_now()

    if reference is not None:
        reference_dir = output_dir / "reference"
        write_note_events_json(reference, reference_dir / "events.json")
        write_note_events_csv(reference, reference_dir / "events.csv")
        write_midi(
            reference,
            reference_dir / "reference.mid",
            instrument_name="Stem2Tab Reference Bass",
        )

    records: list[RunRecord] = []
    separator_details: dict[str, dict[str, object]] = {}
    for separator_name, separator in separators:
        separator_dir = output_dir / "separators" / separator_name
        separator_started = perf_counter()
        try:
            separated = separator.run(
                audio_path,
                separator_dir,
                config=adapter_config,
            )
            separator_seconds = perf_counter() - separator_started
            separator_details[separator_name] = {
                "status": "success",
                "seconds": separator_seconds,
                "audio_path": str(separated.audio_path),
                "artifacts": {
                    name: str(path)
                    for name, path in sorted(separated.artifacts.items())
                },
                "metadata": separated.metadata,
            }
        except Exception as exc:
            separator_seconds = perf_counter() - separator_started
            error = _format_error(exc)
            separator_details[separator_name] = {
                "status": "error",
                "seconds": separator_seconds,
                "error": error,
            }
            for transcriber_name, _ in transcribers:
                record = RunRecord(
                    run_id=_run_id(separator_name, transcriber_name),
                    separator=separator_name,
                    transcriber=transcriber_name,
                    status="error",
                    reference_available=reference is not None,
                    separator_seconds=separator_seconds,
                    transcription_seconds=0.0,
                    error=error,
                )
                records.append(record)
                run_dir = output_dir / "runs" / record.run_id
                write_run_metrics(record, run_dir / "metrics.json")
            continue

        for transcriber_name, transcriber in transcribers:
            run_id = _run_id(separator_name, transcriber_name)
            run_dir = output_dir / "runs" / run_id
            transcription_started = perf_counter()
            try:
                transcription = transcriber.run(
                    separated.audio_path,
                    run_dir,
                    config=adapter_config,
                )
                estimated = NoteEventSet(
                    source={
                        "kind": "estimate",
                        "separator": separator_name,
                        "transcriber": transcriber_name,
                    },
                    notes=transcription.events.notes,
                )
                write_note_events_json(estimated, run_dir / "events.json")
                write_note_events_csv(estimated, run_dir / "events.csv")
                write_midi(
                    estimated,
                    run_dir / "performance.mid",
                    instrument_name=f"Stem2Tab {run_id}",
                )
                transcription_seconds = perf_counter() - transcription_started
                metrics = (
                    evaluate_note_events(
                        reference,
                        estimated,
                        config=metric_config,
                        runtime_seconds=separator_seconds + transcription_seconds,
                    )
                    if reference is not None
                    else None
                )
                record = RunRecord(
                    run_id=run_id,
                    separator=separator_name,
                    transcriber=transcriber_name,
                    status="success",
                    reference_available=reference is not None,
                    separator_seconds=separator_seconds,
                    transcription_seconds=transcription_seconds,
                    estimated_note_count=len(estimated.notes),
                    metrics=metrics,
                    adapter_metadata={
                        **separated.metadata,
                        **transcription.metadata,
                        "raw_midi_path": str(transcription.raw_midi_path),
                    },
                )
            except Exception as exc:
                transcription_seconds = perf_counter() - transcription_started
                record = RunRecord(
                    run_id=run_id,
                    separator=separator_name,
                    transcriber=transcriber_name,
                    status="error",
                    reference_available=reference is not None,
                    separator_seconds=separator_seconds,
                    transcription_seconds=transcription_seconds,
                    error=_format_error(exc),
                    adapter_metadata=separated.metadata,
                )
            records.append(record)
            write_run_metrics(record, run_dir / "metrics.json")

    completed_at = _utc_now()
    write_comparison_json(records, output_dir / "comparison.json")
    write_comparison_csv(records, output_dir / "comparison.csv")
    report = render_markdown_report(
        records,
        generated_at=completed_at,
        reference_available=reference is not None,
    )
    write_markdown_report(report, output_dir / "report.md")
    _write_manifest(
        output_dir / "manifest.json",
        started_at=started_at,
        completed_at=completed_at,
        audio_path=audio_path,
        reference_path=reference_path,
        separator_names=separator_names,
        transcriber_names=transcriber_names,
        metric_config=metric_config,
        adapter_config=adapter_config,
        separator_details=separator_details,
        records=records,
    )
    return output_dir, records, report


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        output_dir, records, report = run_benchmark(args)
    except BenchmarkConfigurationError as exc:
        print(f"benchmark: error: {exc}", file=sys.stderr)
        return 2

    print(report, end="")
    print(f"Artifacts: {output_dir}")
    return 1 if any(record.status != "success" for record in records) else 0


def _parse_adapter_names(value: str, *, kind: str) -> list[str]:
    names = list(dict.fromkeys(part.strip() for part in value.split(",") if part.strip()))
    if not names:
        raise BenchmarkConfigurationError(f"At least one {kind} adapter is required")
    return names


def _resolve_separator(name: str) -> SeparatorAdapter:
    try:
        return get_separator_adapter(name)
    except ValueError as exc:
        raise BenchmarkConfigurationError(str(exc)) from exc


def _resolve_transcriber(name: str) -> TranscriberAdapter:
    try:
        return get_transcriber_adapter(name)
    except ValueError as exc:
        raise BenchmarkConfigurationError(str(exc)) from exc


def _require_file(path: Path, label: str) -> Path:
    if not path.is_file():
        raise BenchmarkConfigurationError(f"{label} file not found: {path}")
    return path.resolve()


def _prepare_output_dir(output_dir: Path | None, *, audio_path: Path) -> Path:
    if output_dir is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        output_dir = Path.cwd() / "benchmark_results" / f"{audio_path.stem}-{stamp}"
    output_dir = output_dir.resolve()
    if output_dir.exists():
        if not output_dir.is_dir():
            raise BenchmarkConfigurationError(f"Output path is not a directory: {output_dir}")
        if any(output_dir.iterdir()):
            raise BenchmarkConfigurationError(f"Output directory is not empty: {output_dir}")
    else:
        output_dir.mkdir(parents=True)
    return output_dir


def _run_id(separator: str, transcriber: str) -> str:
    return f"{separator}__{transcriber}"


def _positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0.0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def _format_error(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _file_metadata(path: Path) -> dict[str, str | int]:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return {
        "path": str(path),
        "size_bytes": path.stat().st_size,
        "sha256": digest.hexdigest(),
    }


def _write_manifest(
    path: Path,
    *,
    started_at: str,
    completed_at: str,
    audio_path: Path,
    reference_path: Path | None,
    separator_names: list[str],
    transcriber_names: list[str],
    metric_config: MetricConfig,
    adapter_config: AdapterConfig,
    separator_details: dict[str, dict[str, object]],
    records: list[RunRecord],
) -> None:
    package_names = (
        "stem2tab",
        "basic-pitch",
        "pretty-midi",
        "mir-eval",
        "demucs",
        "pydantic",
        "numpy",
    )
    payload = {
        "schema_version": "1.0",
        "started_at": started_at,
        "completed_at": completed_at,
        "inputs": {
            "audio": _file_metadata(audio_path),
            "reference": _file_metadata(reference_path) if reference_path is not None else None,
        },
        "reference_available": reference_path is not None,
        "requested": {
            "separators": separator_names,
            "transcribers": transcriber_names,
        },
        "metric_config": metric_config.model_dump(mode="json"),
        "adapter_config": {
            "demucs_model": adapter_config.demucs_model,
            "demucs_cache_dir": str(adapter_config.demucs_cache_dir),
        },
        "separators": separator_details,
        "runs": [
            {
                "run_id": record.run_id,
                "status": record.status,
                "error": record.error,
                "separator_seconds": record.separator_seconds,
                "transcription_seconds": record.transcription_seconds,
                "adapter_metadata": record.adapter_metadata,
            }
            for record in records
        ],
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "packages": {name: _package_version(name) for name in package_names},
        },
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _package_version(name: str) -> str | None:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


if __name__ == "__main__":
    raise SystemExit(main())
