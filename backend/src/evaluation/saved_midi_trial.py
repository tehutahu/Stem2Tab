"""Opt-in local bass postprocessing of saved MIDI; never runs a model.

Performance bytes/events are retained. XML uses the input storage clock, not
inferred musical beats. Optional GP5 is a clearly labelled fingering preview.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import sys
from typing import Any, Literal

import mido
import pretty_midi

from src.evaluation.io import write_note_events_csv, write_note_events_json
from src.evaluation.models import NoteEvent, NoteEventSet
from src.evaluation.notation_fidelity import write_fidelity_musicxml
from src.evaluation.midi_clock import validate_fixed_storage_clock
from src.pipelines.bass_output import BassOutputConfig, BassOutputEvent, write_bass_gp5_preview


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_json(path: Path, payload: Any) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")


def _load_unambiguous_midi(data: bytes) -> tuple[pretty_midi.PrettyMIDI, list[dict[str, Any]]]:
    """Reject note loss/ambiguity and unsupported clocks before creating output."""
    raw = mido.MidiFile(file=io.BytesIO(data))
    if raw.type == 2 or raw.ticks_per_beat <= 0:
        raise ValueError("Only synchronous type0/1 positive-PPQ MIDI is supported")
    validate_fixed_storage_clock(raw)
    active: dict[tuple[int, int], int] = {}
    completed = 0
    tick = 0
    for message in mido.merge_tracks(raw.tracks):
        tick += message.time
        if message.type not in {"note_on", "note_off"}:
            continue
        if message.channel == 9:
            raise ValueError("Drum note events are unsupported; no implicit filtering is allowed")
        key = message.channel, message.note
        if message.type == "note_on" and message.velocity > 0:
            if key in active:
                raise ValueError("Ambiguous same-channel/pitch overlapping note-ons; source is retained unchanged")
            active[key] = tick
        else:
            if key not in active:
                raise ValueError("Unmatched note-off; refusing silent note-stream repair")
            if tick <= active.pop(key):
                raise ValueError("Zero/negative duration note would be dropped by the MIDI loader")
            completed += 1
    if active:
        raise ValueError("Unclosed note-ons would be lost by the MIDI loader")
    midi = pretty_midi.PrettyMIDI(io.BytesIO(data))
    _, tempi = midi.get_tempo_changes()
    if any(abs(float(t) - float(tempi[0])) > 1e-6 for t in tempi):
        raise ValueError("Variable tempo is outside this fixed-storage-clock trial")
    if any((t.numerator, t.denominator) != (4, 4) for t in midi.time_signature_changes):
        raise ValueError("Only absent or fixed4/4 storage meter is supported; no musical meter is inferred")
    instruments = [instrument for instrument in midi.instruments if instrument.notes]
    if len(instruments) > 1:
        raise ValueError("Multiple populated instruments require an explicit external selection; no automatic merging")
    rows: list[dict[str, Any]] = []
    for instrument in instruments:
        if instrument.is_drum:
            raise ValueError("Drum notes are unsupported")
        for n in instrument.notes:
            if n.pitch < 12:
                raise ValueError("MIDI below12 is outside the bounded MusicXML exporter; source is unchanged")
            rows.append(dict(start=float(n.start), end=float(n.end), midi=int(n.pitch),
                             velocity=int(n.velocity), program=int(instrument.program),
                             start_tick=int(round(midi.time_to_tick(n.start))),
                             end_tick=int(round(midi.time_to_tick(n.end)))))
    rows.sort(key=lambda n: (n["start"], n["end"], n["midi"]))
    for index, row in enumerate(rows):
        row["source_index"] = index
    if len(rows) != completed:
        raise ValueError("Loaded note count differs from paired source events; refusing silent loss")
    return midi, rows


def run_saved_midi_trial(
    midi_path: Path, output_dir: Path, *, config: BassOutputConfig,
    source_kind: Literal["muscriptor-small", "local-midi"],
    time_basis: Literal["storage-clock"], gp5_preview: bool = False,
) -> dict[str, Any]:
    """Create new local artifacts and a complete, explicit export outcome.

The required profile/time basis prevents implicit changes to existing defaults.
Input validation errors occur before output creation. Later export errors are
recorded as failed outcomes; the CLI returns nonzero, never silent success.
"""
    if source_kind not in {"muscriptor-small", "local-midi"} or time_basis != "storage-clock":
        raise ValueError("Explicit supported source kind and storage-clock basis are required")
    config = BassOutputConfig.model_validate(config)
    midi_path, output_dir = Path(midi_path).resolve(), Path(output_dir).resolve()
    if output_dir.exists():
        raise FileExistsError(output_dir)
    data = midi_path.read_bytes()
    input_hash = _sha(data)
    midi, rows = _load_unambiguous_midi(data)
    note_events = NoteEventSet(
        source={"source_kind": source_kind, "source_sha256": input_hash,
                "event_origin": "saved MIDI loaded events; not original model raw JSON",
                "noteoff_policy": "ambiguous/unclosed/zero-duration note streams rejected"},
        notes=tuple(NoteEvent(start=n["start"], end=n["end"], midi=n["midi"],
                              velocity=n["velocity"] / 127.0) for n in rows),
    )
    report: dict[str, Any] = dict(
        schema_version="1.0", status="started", input_path=str(midi_path),
        input_sha256=input_hash, source_kind=source_kind,
        source_label_policy="caller-declared provenance, not independent model authentication",
        source_event_origin="loaded saved MIDI; original model JSON has independent provenance/timing",
        input_note_count=len(rows), output_dir=str(output_dir), config=config.model_dump(mode="json"),
        time_basis=time_basis, musical_tempo_and_meter_verified=False,
        input_ppq=midi.resolution, storage_tempo_bpm=float(midi.get_tempo_changes()[1][0]),
        XML_role="nominal performance-event interchange using storage-clock4/4 containers; not inferred Score MIDI",
        GP5_role="optional sequential-quarter fingering preview; no performed rhythm/rest/overlap fidelity",
        gp5_requested=gp5_preview, model_calls=0, separator_calls=0, source_weights_bundled=False,
        usage_scope="local noncommercial trial; retain MuScriptor small existing license/extra conditions; no weights redistribution"
                    if source_kind == "muscriptor-small" else "local MIDI; caller remains responsible for applicable source rights",
        warnings=["STORAGE_CLOCK_ONLY: XML bar grid and tempo do not establish musical beats/meter.",
                  "XML_SCOPE: loaded note pitch/boundaries only; controllers, bends, velocity and timbre are not rendered."],
    )
    output_dir.mkdir(parents=True, exist_ok=False)
    try:
        performance_path = output_dir / "performance.mid"
        with performance_path.open("xb") as stream:
            stream.write(data)
        assert _sha(performance_path.read_bytes()) == input_hash
        write_note_events_json(note_events, output_dir / "canonical.json")
        write_note_events_csv(note_events, output_dir / "canonical.csv")
        _write_json(output_dir / "source-events.json", rows)
        xml_report = write_fidelity_musicxml(performance_path, output_dir / "performance-clock.musicxml", timing="ticks")
        report["musicxml"] = xml_report.model_dump(mode="json")
        if xml_report.note_count != len(rows) or xml_report.same_pitch_overlap_note_ons:
            raise RuntimeError("Unexpected event count or ambiguity in XML export")
        if gp5_preview:
            preview = write_bass_gp5_preview(
                [BassOutputEvent(source_index=n["source_index"], start=n["start"], end=n["end"],
                                 midi=n["midi"], velocity=n["velocity"]) for n in rows],
                output_dir / "gp5-preview", config=config,
            )
            report["gp5"] = preview.model_dump(mode="json")
            report["warnings"].append("GP5_PREVIEW_ONLY: every mapped attack becomes one quarter beat; source rhythm is not retained.")
            if preview.status == "unmappable":
                report["warnings"].append("GP5_BLOCKED_OUT_OF_RANGE: no partial GP5 was written; inspect gp5-preview/bass-output.json. All events remain in MIDI/canonical/XML.")
                report["status"] = "gp5_blocked_out_of_range"
            else:
                report["status"] = "completed"
        else:
            report["gp5"] = None
            report["status"] = "completed"
        report["source_unchanged"] = _sha(midi_path.read_bytes()) == input_hash
        if not report["source_unchanged"]:
            raise RuntimeError("Source MIDI changed during processing; retained snapshot hash is reported")
    except Exception as exc:
        report["status"] = "failed"
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["source_unchanged"] = midi_path.is_file() and _sha(midi_path.read_bytes()) == input_hash
    report["artifacts"] = [dict(path=str(p.relative_to(output_dir)), bytes=p.stat().st_size,
                                sha256=_sha(p.read_bytes())) for p in sorted(output_dir.rglob("*")) if p.is_file()]
    _write_json(output_dir / "trial.json", report)
    return report


def main(argv: list[str] | None = None) -> int:
    """Run the opt-in saved-MIDI route and return an explicit export exit status."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--midi", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--source-kind", choices=["muscriptor-small", "local-midi"], required=True)
    parser.add_argument("--time-basis", choices=["storage-clock"], required=True)
    parser.add_argument("--bass-profile", choices=["bass4", "bass5", "custom"], required=True)
    parser.add_argument("--strings", type=int)
    parser.add_argument("--tuning-midi", help="Low-to-high sounding MIDI integers, comma separated; required for custom")
    parser.add_argument("--max-fret", type=int)
    parser.add_argument("--gp5-preview", action="store_true", help="Optional quarter-note fingering preview, not performed rhythm")
    args = parser.parse_args(argv)
    try:
        config = BassOutputConfig(profile=args.bass_profile, strings=args.strings,
                                  tuning_midi=tuple(int(x) for x in args.tuning_midi.split(",")) if args.tuning_midi else None,
                                  max_fret=args.max_fret)
        result = run_saved_midi_trial(args.midi, args.output_dir, config=config,
                                     source_kind=args.source_kind, time_basis=args.time_basis, gp5_preview=args.gp5_preview)
    except (ValueError, OSError) as exc:
        print(f"SAVED_MIDI_TRIAL_REJECTED: {exc}", file=sys.stderr)
        return 1
    for warning in result["warnings"]:
        print(warning, file=sys.stderr)
    if result.get("error"):
        print(result["error"], file=sys.stderr)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["status"] == "completed" else 2 if result["status"] == "gp5_blocked_out_of_range" else 1


if __name__ == "__main__":
    raise SystemExit(main())
