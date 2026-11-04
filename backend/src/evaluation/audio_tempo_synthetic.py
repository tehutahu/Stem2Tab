"""Reproducible synthetic audio evaluation; no binary fixtures or real recordings."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import io
import json
from pathlib import Path
from typing import Iterator

import numpy as np
import soundfile as sf

from src.evaluation.audio_tempo import estimate_audio_tempo


@dataclass(frozen=True)
class SyntheticAudio:
    """An independent known pulse or explicit negative-control audio signal."""

    name: str
    samples: np.ndarray
    sample_rate: int
    expected_bpm: float | None
    expected_status: str

    def wav_bytes(self) -> bytes:
        """Encode generated floating-point samples without writing audio files."""
        stream = io.BytesIO()
        sf.write(stream, self.samples, self.sample_rate, format="WAV", subtype="FLOAT")
        return stream.getvalue()


def pulse_audio(*, bpm: float, seconds: float = 24, rate: int = 16000,
                phase: float = .173, pattern: str = "quarter", seed: int = 7) -> np.ndarray:
    """Synthesize transient percussion and bass tones from a declared beat grid."""
    rng = np.random.default_rng(seed)
    samples = np.zeros(round(seconds*rate), dtype=np.float32)
    subdivisions = 3 if pattern == "triplets" else 2 if pattern in {"subdivision", "syncopation"} else 1
    times = phase+np.arange(int(seconds*bpm*subdivisions/60)+1)*60/(bpm*subdivisions)
    for index, start in enumerate(times):
        if pattern == "dropouts" and index % 7 in {2, 5}:
            continue
        if pattern == "syncopation" and index % 8 in {0, 3, 6}:
            continue
        amplitude = .7
        if pattern in {"accents", "subdivision"}:
            amplitude *= 1 if index % (4*subdivisions) == 0 else .55 if index % subdivisions == 0 else .25
        length = int(.18*rate)
        t = np.arange(length)/rate
        tone = np.sin(2*np.pi*55*t)+.35*np.sin(2*np.pi*110*t)
        click = rng.standard_normal(length)*np.exp(-t/.012)
        attack = 1-np.exp(-t/.0015)
        wave = amplitude*attack*(.55*tone*np.exp(-t/.07)+.4*click)
        a = round(float(start)*rate)
        b = min(len(samples), a+len(wave))
        if a < len(samples):
            samples[a:b] += wave[:b-a].astype(np.float32)
    if pattern == "noise":
        samples += rng.standard_normal(len(samples)).astype(np.float32)*.025
    if pattern == "antiphase":
        return np.column_stack((samples, -samples))
    return samples


def synthetic_audio_cases() -> Iterator[SyntheticAudio]:
    """Yield diverse known audio rhythms and negative/unstable controls."""
    rate = 16000
    for bpm in (60, 75, 90, 100, 120, 140, 180, 210):
        yield SyntheticAudio(f"quarter_{bpm}", pulse_audio(bpm=bpm), rate, bpm, "candidates")
    for pattern in ("accents", "subdivision", "syncopation", "dropouts", "noise", "antiphase"):
        yield SyntheticAudio(pattern+"_120", pulse_audio(bpm=120, pattern=pattern), rate, 120, "candidates")
    yield SyntheticAudio("triplets_80", pulse_audio(bpm=80, pattern="triplets"), rate, 80, "candidates")
    count = 24*rate
    time = np.arange(count)/rate
    yield SyntheticAudio("silence", np.zeros(count, dtype=np.float32), rate, None, "insufficient_evidence")
    yield SyntheticAudio("drone", (.2*np.sin(2*np.pi*110*time)).astype(np.float32), rate, None, "insufficient_evidence")
    yield SyntheticAudio("dc", np.full(count, .2, dtype=np.float32), rate, None, "insufficient_evidence")
    yield SyntheticAudio("noise", np.random.default_rng(18).normal(0,.05,count).astype(np.float32), rate, None, "insufficient_evidence")
    yield SyntheticAudio("short", pulse_audio(bpm=120, seconds=3), rate, None, "insufficient_evidence")
    change = np.r_[pulse_audio(bpm=90,seconds=12), pulse_audio(bpm=150,seconds=12)]
    yield SyntheticAudio("tempo_change_90_150", change, rate, None, "unstable")
    rng = np.random.default_rng(144)
    rubato = np.zeros(count, dtype=np.float32)
    start = .173
    while start < 24:
        a = round(start*rate)
        n = min(round(.07*rate),len(rubato)-a)
        t = np.arange(n)/rate
        rubato[a:a+n] += (rng.normal(0,.3,n)*np.exp(-t/.012)).astype(np.float32)
        start += rng.uniform(.22,.92)
    yield SyntheticAudio("irregular_rubato", rubato, rate, None, "noncandidate")


def evaluate_synthetic() -> dict:
    """Return exact and octave-family outcomes without claiming real-song accuracy."""
    rows = []
    for case in synthetic_audio_cases():
        result = estimate_audio_tempo(io.BytesIO(case.wav_bytes()))
        values = [candidate.bpm for candidate in result.candidates]
        family_ok = case.expected_bpm is not None and any(abs(value-case.expected_bpm)/case.expected_bpm <= .025 for value in values)
        primary_family_ok = case.expected_bpm is not None and any(abs(candidate.bpm-case.expected_bpm)/case.expected_bpm <= .025 for candidate in result.candidates if candidate.family_id == "pulse-1")
        primary = next((candidate.bpm for candidate in result.candidates if candidate.relation == "primary"), None)
        exact_ok = case.expected_bpm is not None and primary is not None and abs(primary-case.expected_bpm)/case.expected_bpm <= .025
        status_ok = result.status != "candidates" if case.expected_status == "noncandidate" else result.status == case.expected_status
        rows.append(dict(name=case.name, expected_bpm=case.expected_bpm, expected_status=case.expected_status,
            status=result.status, candidates=values, primary=primary, family_within_2_5_percent=family_ok,
            primary_within_2_5_percent=exact_ok, primary_family_within_2_5_percent=primary_family_ok, status_matches=status_ok,
            window_bpms=[window.bpm for window in result.windows],
            window_periodicity=[window.periodicity for window in result.windows]))
    positive = [row for row in rows if row["expected_bpm"] is not None]
    negative = [row for row in rows if row["expected_bpm"] is None]
    return dict(scope="Generated synthetic audio only; not calibrated real-song accuracy", cases=len(rows),
        positive_cases=len(positive), primary_matches=sum(row["primary_within_2_5_percent"] for row in positive),
        primary_octave_matches=sum(row["primary_family_within_2_5_percent"] for row in positive),
        candidate_set_matches=sum(row["family_within_2_5_percent"] for row in positive),
        negative_cases=len(negative), negative_status_matches=sum(row["status_matches"] for row in negative), rows=rows)


def main() -> int:
    """Write a rerunnable synthetic aggregate without storing binary audio."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output exists; select a new aggregate path")
    report = evaluate_synthetic()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2,allow_nan=False)+"\n")
    print(json.dumps({key:value for key,value in report.items() if key != "rows"},indent=2))
    for row in report["rows"]:
        print(row["name"],row["status"],row["candidates"],row["window_periodicity"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
