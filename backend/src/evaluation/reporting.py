"""Machine-readable comparison outputs and compact Markdown reports."""

from __future__ import annotations

import csv
import html
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


class AudioTrack(BaseModel):
    """One browser-playable track exposed by the interactive report."""

    model_config = ConfigDict(frozen=True)

    track_id: str
    label: str
    kind: str
    relative_path: str


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


def render_html_report(
    records: list[RunRecord],
    tracks: list[AudioTrack],
    *,
    generated_at: str,
    reference_available: bool,
) -> str:
    """Render a self-contained synchronized listening comparison page."""
    tracks_json = json.dumps(
        [track.model_dump(mode="json") for track in tracks],
        ensure_ascii=False,
    ).replace("<", "\\u003c")
    rows = "\n".join(_html_run_row(record) for record in records)
    reference_note = (
        "Reference MIDI was provided; accuracy metrics are shown below."
        if reference_available
        else "No reference MIDI was provided. Judge quality by switching tracks at the same playback position."
    )
    template = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="icon" href="data:,">
  <title>Stem2Tab Listening Comparison</title>
  <style>
    :root { color-scheme: dark; font-family: system-ui, sans-serif; }
    body { max-width: 980px; margin: 0 auto; padding: 2rem; background: #111827; color: #e5e7eb; }
    h1, h2 { color: #f9fafb; }
    .panel { padding: 1.25rem; margin: 1rem 0; border: 1px solid #374151; border-radius: .75rem; background: #1f2937; }
    #track-buttons { display: flex; flex-wrap: wrap; gap: .5rem; margin-bottom: 1rem; }
    button, a.button { border: 1px solid #4b5563; border-radius: .5rem; padding: .55rem .8rem; color: #e5e7eb; background: #111827; cursor: pointer; text-decoration: none; }
    button.active { border-color: #34d399; background: #064e3b; }
    button:hover, a.button:hover { border-color: #9ca3af; }
    audio { width: 100%; margin: .75rem 0; }
    .loop-controls { display: flex; flex-wrap: wrap; align-items: center; gap: .5rem; }
    .hint, #now-playing { color: #9ca3af; }
    table { width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; }
    th, td { padding: .6rem; border-bottom: 1px solid #374151; text-align: right; }
    th:first-child, td:first-child { text-align: left; }
    code { color: #a7f3d0; }
    @media (max-width: 700px) { body { padding: 1rem; } .table-wrap { overflow-x: auto; } }
  </style>
</head>
<body>
  <h1>Stem2Tab Listening Comparison</h1>
  <p>Generated: __GENERATED_AT__</p>
  <p>__REFERENCE_NOTE__</p>

  <section class="panel">
    <h2>A/B player</h2>
    <p class="hint">Start playback, then switch tracks. The player keeps the same timestamp. Keys 1–9 select tracks.</p>
    <div id="track-buttons"></div>
    <div id="now-playing"></div>
    <audio id="player" controls preload="metadata"></audio>
    <div class="loop-controls">
      <button id="set-a" type="button">Set A</button>
      <button id="set-b" type="button">Set B</button>
      <button id="clear-loop" type="button">Clear loop</button>
      <label><input id="loop-enabled" type="checkbox"> Loop A–B</label>
      <span id="loop-status" class="hint">A 0:00.000 / B unset</span>
    </div>
  </section>

  <section class="panel">
    <h2>Run summary and artifacts</h2>
    <div class="table-wrap">
      <table>
        <thead>
          <tr><th>Run</th><th>Status</th><th>Notes</th><th>Onset F1</th><th>Frame F1</th><th>Runtime s</th><th>Artifacts</th></tr>
        </thead>
        <tbody>
__RUN_ROWS__
        </tbody>
      </table>
    </div>
  </section>

  <script>
    const tracks = __TRACKS_JSON__;
    const player = document.querySelector("#player");
    const buttons = document.querySelector("#track-buttons");
    const nowPlaying = document.querySelector("#now-playing");
    const loopEnabled = document.querySelector("#loop-enabled");
    const loopStatus = document.querySelector("#loop-status");
    let markerA = 0;
    let markerB = null;
    let activeIndex = -1;

    function formatTime(value) {
      const minutes = Math.floor(value / 60);
      const seconds = (value % 60).toFixed(3).padStart(6, "0");
      return `${minutes}:${seconds}`;
    }

    function updateLoopStatus() {
      loopStatus.textContent = `A ${formatTime(markerA)} / B ${markerB === null ? "unset" : formatTime(markerB)}`;
    }

    function selectTrack(index) {
      const currentTime = Number.isFinite(player.currentTime) ? player.currentTime : 0;
      const shouldResume = !player.paused && !player.ended;
      activeIndex = index;
      player.src = tracks[index].relative_path;
      player.load();
      player.addEventListener("loadedmetadata", () => {
        player.currentTime = Math.min(currentTime, player.duration || currentTime);
        if (shouldResume) player.play().catch(() => {});
      }, { once: true });
      nowPlaying.textContent = `Now playing: ${tracks[index].label}`;
      [...buttons.children].forEach((button, buttonIndex) => {
        button.classList.toggle("active", buttonIndex === index);
      });
    }

    tracks.forEach((track, index) => {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = `${index + 1}. ${track.label}`;
      button.addEventListener("click", () => selectTrack(index));
      buttons.appendChild(button);
    });

    document.querySelector("#set-a").addEventListener("click", () => {
      markerA = player.currentTime || 0;
      if (markerB !== null && markerB <= markerA) markerB = null;
      updateLoopStatus();
    });
    document.querySelector("#set-b").addEventListener("click", () => {
      const value = player.currentTime || 0;
      markerB = value > markerA ? value : null;
      updateLoopStatus();
    });
    document.querySelector("#clear-loop").addEventListener("click", () => {
      markerA = 0;
      markerB = null;
      loopEnabled.checked = false;
      updateLoopStatus();
    });
    player.addEventListener("timeupdate", () => {
      if (loopEnabled.checked && markerB !== null && player.currentTime >= markerB) {
        player.currentTime = markerA;
        player.play().catch(() => {});
      }
    });
    document.addEventListener("keydown", (event) => {
      if (event.target instanceof HTMLInputElement) return;
      const index = Number(event.key) - 1;
      if (index >= 0 && index < tracks.length) selectTrack(index);
    });

    updateLoopStatus();
    if (tracks.length) selectTrack(0);
  </script>
</body>
</html>
"""
    return (
        template.replace("__GENERATED_AT__", html.escape(generated_at))
        .replace("__REFERENCE_NOTE__", html.escape(reference_note))
        .replace("__RUN_ROWS__", rows)
        .replace("__TRACKS_JSON__", tracks_json)
    )


def write_html_report(report: str, path: Path) -> Path:
    """Persist a self-contained listening comparison page."""
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


def _html_run_row(record: RunRecord) -> str:
    metrics = record.metrics
    note_count = (
        metrics.estimated_notes
        if metrics is not None
        else record.estimated_note_count
    )
    onset_f1 = _format_number(metrics.onset_f1) if metrics is not None else "—"
    frame_f1 = _format_number(metrics.frame_f1) if metrics is not None else "—"
    runtime_seconds = (
        metrics.runtime_seconds
        if metrics is not None
        else record.separator_seconds + record.transcription_seconds
    )
    if record.status == "success":
        run_path = f"runs/{record.run_id}"
        links = " ".join(
            (
                f'<a href="{run_path}/preview.wav">WAV</a>',
                f'<a href="{run_path}/performance.mid">MIDI</a>',
                f'<a href="{run_path}/events.csv">CSV</a>',
            )
        )
    else:
        links = html.escape(record.error or "Unavailable")
    return (
        "          <tr>"
        f"<td><code>{html.escape(record.run_id)}</code></td>"
        f"<td>{html.escape(record.status)}</td>"
        f"<td>{'—' if note_count is None else note_count}</td>"
        f"<td>{onset_f1}</td>"
        f"<td>{frame_f1}</td>"
        f"<td>{_format_number(runtime_seconds)}</td>"
        f"<td>{links}</td>"
        "</tr>"
    )
