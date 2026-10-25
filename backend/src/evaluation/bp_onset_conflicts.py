"""Frozen opt-in B decoder for Basic Pitch 0.4.0 caches; no model inference.

The upstream decoder retains duration, neighbouring-pitch and Melodia behavior.
A weaker genuine reattack can be removed; this is not a default or safety claim.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Literal, Mapping

import numpy as np
from pydantic import BaseModel, ConfigDict
from src.evaluation.models import NoteEvent, NoteEventSet


class OnsetConflictConfig(BaseModel):
    """Explicitly choose the frozen B rule, without threshold fitting."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    method: Literal["dominant_gap_nms"]


class PeakDecision(BaseModel):
    """Record a removed onset peak and the retained same-pitch competitor."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    frame: int
    midi: int
    strength: float
    retained_frame: int
    retained_strength: float
    fixed_margin: float


def suppress_short_gap_peaks(
    eligible_peaks: np.ndarray,
    *,
    minimum_note_frames: int,
    config: OnsetConflictConfig,
) -> tuple[np.ndarray, list[PeakDecision]]:
    """Apply strength-priority NMS only within each pitch's minimum-length gap.

    Equal or weakly separated peaks are retained by dominant_gap_nms. This is
    limited abstention, not proof that a weaker onset is false. A real weaker
    reattack can have exactly the same activations as a spurious late peak.
    """
    if eligible_peaks.ndim != 2 or eligible_peaks.shape[1] != 88:
        raise ValueError("Expected Basic Pitch (frames, 88) eligible onset matrix")
    if minimum_note_frames < 1 or not np.isfinite(eligible_peaks).all():
        raise ValueError("Expected a positive minimum duration and finite peaks")
    if np.any(eligible_peaks < 0):
        raise ValueError("Onset strengths must be nonnegative")
    selected = np.zeros_like(eligible_peaks)
    decisions: list[PeakDecision] = []
    margin = 0.1
    for pitch in range(88):
        indices = np.flatnonzero(eligible_peaks[:, pitch] > 0)
        ordered = sorted(indices, key=lambda i: (-float(eligible_peaks[i, pitch]), int(i)))
        retained: list[int] = []
        for index in ordered:
            strength = float(eligible_peaks[index, pitch])
            stronger = [
                i for i in retained
                if abs(int(i) - int(index)) <= minimum_note_frames
                and float(eligible_peaks[i, pitch]) - strength + 1e-7 >= margin
            ]
            if stronger:
                winner = stronger[0]
                decisions.append(PeakDecision(
                    frame=int(index), midi=pitch + 21, strength=strength,
                    retained_frame=int(winner),
                    retained_strength=float(eligible_peaks[winner, pitch]),
                    fixed_margin=margin,
                ))
            else:
                retained.append(int(index))
                selected[index, pitch] = eligible_peaks[index, pitch]
    return selected, decisions


def decode_cached_activations(
    model_output: Mapping[str, np.ndarray], *, config: OnsetConflictConfig,
) -> tuple[NoteEventSet, list[PeakDecision]]:
    """Decode a copied cache using fixed BP .5/.3/127.7ms settings and no model.

    Raw arrays remain unchanged. The returned event set is an alternative;
    callers must retain the original decoder output. Pitch-range constraints
    and inference providers are not changed or inferred here.
    """
    from importlib.metadata import version

    if version("basic-pitch") != "0.4.0":
        raise ValueError("Frozen B requires basic-pitch==0.4.0")
    from basic_pitch import note_creation
    from basic_pitch.constants import AUDIO_SAMPLE_RATE, FFT_HOP
    from scipy.signal import argrelmax

    if set(model_output) != {"note", "onset", "contour"}:
        raise ValueError("Expected note, onset and contour arrays")
    frames = np.asarray(model_output["note"])
    onsets = np.asarray(model_output["onset"])
    contour = np.asarray(model_output["contour"])
    if (frames.ndim != 2 or frames.shape[1] != 88 or frames.shape != onsets.shape
            or contour.shape != (len(frames), 264) or len(frames) < 2):
        raise ValueError("Incompatible Basic Pitch cache shapes")
    if any(not np.isfinite(array).all() for array in (frames, onsets, contour)):
        raise ValueError("Nonfinite Basic Pitch cache")
    if np.any(onsets < 0) or np.any(onsets > 1) or np.any(frames < 0) or np.any(frames > 1):
        raise ValueError("Expected probabilities in [0, 1]")
    minimum_frames = int(np.round(127.7 / 1000.0 * (AUDIO_SAMPLE_RATE / FFT_HOP)))
    # Same infer-onsets operation as upstream, before eligibility/local maxima.
    inferred = (onsets.copy() if not np.any(frames) and not np.any(onsets)
                else note_creation.get_infered_onsets(onsets.copy(), frames.copy()))
    peak_matrix = np.zeros_like(inferred)
    peaks = argrelmax(inferred, axis=0)
    peak_matrix[peaks] = inferred[peaks]
    peak_matrix[peak_matrix < 0.5] = 0
    sparse, decisions = suppress_short_gap_peaks(
        peak_matrix, minimum_note_frames=minimum_frames, config=config,
    )
    # Sparse original maxima avoid creating new shoulder maxima after pruning.
    # infer_onsets=False avoids regenerating deliberately removed peaks.
    note_frames = note_creation.output_to_notes_polyphonic(
        frames.copy(), sparse, onset_thresh=0.5, frame_thresh=0.3,
        min_note_len=minimum_frames, infer_onsets=False, min_freq=None,
        max_freq=None, melodia_trick=True,
    )
    times = note_creation.model_frames_to_time(len(frames))
    events = NoteEventSet(
        source={"processing": "bp_onset_conflicts", "method": config.method},
        notes=tuple(NoteEvent(
            start=float(times[a]), end=float(times[b]), midi=int(pitch),
            velocity=max(1 / 127, min(1, float(amplitude))),
        ) for a, b, pitch, amplitude in note_frames),
    )
    return events, decisions


def main() -> int:
    """Write a separate processed copy, exact raw cache copy and decision log."""
    import importlib.metadata
    import shutil
    from src.evaluation.io import write_note_events_json

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", required=True, type=Path)
    parser.add_argument("--method", required=True, choices=["dominant_gap_nms"])
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    if importlib.metadata.version("basic-pitch") != "0.4.0":
        raise ValueError("This experiment was validated with Basic Pitch 0.4.0 only")
    with np.load(args.cache, allow_pickle=False) as saved:
        cache = {key: saved[key] for key in saved.files}
    config = OnsetConflictConfig(method=args.method)
    events, decisions = decode_cached_activations(cache, config=config)
    args.output_dir.mkdir(parents=True, exist_ok=False)
    shutil.copy2(args.cache, args.output_dir / "raw_activations.npz")
    write_note_events_json(events, args.output_dir / "alternative_events.json")
    summary = dict(
        config=config.model_dump(), raw_cache_sha256=hashlib.sha256(args.cache.read_bytes()).hexdigest(),
        notes=len(events.notes), decisions=[x.model_dump() for x in decisions],
        limitation="Research alternative only; weaker genuine reattacks may be suppressed. Raw decoder is retained as the baseline comparison.",
    )
    (args.output_dir / "decisions.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
