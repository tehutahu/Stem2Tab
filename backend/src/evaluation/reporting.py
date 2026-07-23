"""Machine-readable comparison outputs and compact Markdown reports."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from src.evaluation.metrics import BenchmarkMetrics
from src.evaluation.models import SourceValue


class RunRecord(BaseModel):
    """Status and measurements for one separator/transcriber combination."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    run_id: str
    separator: str
    transcriber: str
    status: str
    reference_available: bool
    separator_seconds: float = Field(ge=0.0)
    transcription_seconds: float = Field(ge=0.0)
    estimated_note_count: int | None = Field(default=None, ge=0)
    metrics: BenchmarkMetrics | None = None
    error: str | None = None
    adapter_metadata: dict[str, SourceValue] = Field(default_factory=dict)

    def flattened(self) -> dict[str, str | int | float | None]:
        """Flatten nested metrics for stable comparison CSV columns."""
        row: dict[str, str | int | float | None] = {
            "run_id": self.run_id,
            "separator": self.separator,
            "transcriber": self.transcriber,
            "status": self.status,
            "reference_available": self.reference_available,
            "error": self.error,
            "separator_seconds": self.separator_seconds,
            "transcription_seconds": self.transcription_seconds,
        }
        metric_values = self.metrics.model_dump() if self.metrics is not None else {}
        for field_name in BenchmarkMetrics.model_fields:
            if field_name == "estimated_notes" and self.metrics is None:
                row[field_name] = self.estimated_note_count
            elif field_name == "runtime_seconds" and self.metrics is None:
                row[field_name] = self.separator_seconds + self.transcription_seconds
            else:
                row[field_name] = metric_values.get(field_name)
        return row


def write_run_metrics(record: RunRecord, path: Path) -> Path:
    """Write one run's status, adapter metadata, and metrics."""
    payload = {
        "schema_version": "1.0",
        **record.model_dump(mode="json"),
    }
    return _write_json(payload, path)


def write_comparison_json(records: list[RunRecord], path: Path) -> Path:
    """Write all comparison results as structured JSON."""
    payload = {
        "schema_version": "1.0",
        "reference_available": records[0].reference_available if records else False,
        "runs": [record.model_dump(mode="json") for record in records],
    }
    return _write_json(payload, path)


def write_comparison_csv(records: list[RunRecord], path: Path) -> Path:
    """Write all comparison results as a flat CSV table."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "run_id",
        "separator",
        "transcriber",
        "status",
        "reference_available",
        "error",
        "separator_seconds",
        "transcription_seconds",
        *BenchmarkMetrics.model_fields,
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        for record in records:
            writer.writerow(record.flattened())
    return path


def render_markdown_report(
    records: list[RunRecord],
    *,
    generated_at: str,
    reference_available: bool,
) -> str:
    """Render a compact comparison table suitable for files and terminals."""
    lines = [
        "# Stem2Tab Benchmark",
        "",
        f"Generated: {generated_at}",
        "",
    ]
    if not reference_available:
        lines.extend(
            [
                "Reference MIDI: not provided. Accuracy metrics were not calculated; "
                "compare the generated audio/MIDI artifacts by listening.",
                "",
            ]
        )
    lines.extend(
        [
            "| Run | Status | Notes | Onset F1 | On+off F1 | Frame F1 | "
            "Octave | Extra | Missed | Duration MAE ms | Runtime s |",
            "|:--|:--|--:|--:|--:|--:|--:|--:|--:|--:|--:|",
        ]
    )
    for record in records:
        metrics = record.metrics
        if metrics is None:
            runtime_seconds = record.separator_seconds + record.transcription_seconds
            note_count = "-" if record.estimated_note_count is None else str(record.estimated_note_count)
            values = (note_count, "-", "-", "-", "-", "-", "-", "-", _format_number(runtime_seconds))
        else:
            values = (
                str(metrics.estimated_notes),
                _format_number(metrics.onset_f1),
                _format_number(metrics.onset_offset_f1),
                _format_number(metrics.frame_f1),
                str(metrics.octave_error_count),
                str(metrics.extra_note_count),
                str(metrics.missed_note_count),
                _format_optional(metrics.duration_mae_ms),
                _format_number(metrics.runtime_seconds),
            )
        lines.append(
            f"| {record.run_id} | {record.status} | "
            + " | ".join(values)
            + " |"
        )

    failures = [record for record in records if record.error]
    if failures:
        lines.extend(["", "## Failures", ""])
        for record in failures:
            lines.append(f"- `{record.run_id}`: {record.error}")
    return "\n".join(lines) + "\n"


def write_markdown_report(report: str, path: Path) -> Path:
    """Persist a rendered Markdown comparison report."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report, encoding="utf-8")
    return path


def _write_json(payload: dict[str, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def _format_number(value: float) -> str:
    return f"{value:.3f}"


def _format_optional(value: float | None) -> str:
    return "-" if value is None else _format_number(value)
