"""Bounded CPU tempo evidence from audio, without musical-clock or note edits."""
from __future__ import annotations

from dataclasses import dataclass
import math
import time
from typing import BinaryIO, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field
from scipy import ndimage, signal
import soundfile as sf

ANALYSIS_RATE = 8000
HOP = 80
FRAME = 512
BPM_MIN = 40
BPM_MAX = 240
MIN_SECONDS = 8.0
MAX_ANALYZED_SECONDS = 60.0
MAX_INTERVAL_SECONDS = 20.0
MAX_WINDOW_SECONDS = 12.0
ANALYSIS_BUDGET_SECONDS = 15.0
# These are heuristic evidence cutoffs, not calibrated confidence thresholds.
MIN_PERIODICITY = 0.22
MIN_ONSETS = 5
MAX_WINDOW_DEVIATION = 0.08


class TempoAudioError(ValueError):
    """A safe public refusal without paths, decoder stderr or source names."""

    def __init__(self, code: str, message: str, status: int = 422):
        self.code, self.message, self.status = code, message, status
        super().__init__(message)


@dataclass(frozen=True)
class AudioInfo:
    """Validated native PCM/FLAC metadata before bounded allocation."""

    sample_rate: int
    channels: int
    frames: int
    format: Literal["WAV", "FLAC"]

    @property
    def duration_seconds(self) -> float:
        """Return source duration in seconds, before any sampling."""
        return self.frames/self.sample_rate


class Interval(BaseModel):
    """An interval in source-selection seconds, never a musical downbeat."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)


class TempoWindow(Interval):
    """Local periodicity evidence without a claim of global constant tempo."""

    status: Literal["periodic", "weak", "insufficient"]
    bpm: float | None = Field(ge=BPM_MIN, le=BPM_MAX)
    periodicity: float | None = Field(ge=0, le=1)
    onset_count: int = Field(ge=0, le=3000)


class TempoCandidate(BaseModel):
    """An unresolved pulse or arithmetic half/double alternative."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)
    bpm: float = Field(ge=20, le=480)
    relation: Literal["primary", "alternative", "half", "double"]
    family_id: Literal["pulse-1", "pulse-2"] = "pulse-1"
    periodicity: float | None = Field(ge=0, le=1)
    supporting_windows: int = Field(ge=0, le=6)
    adoptable: bool


class TempoMethod(BaseModel):
    """Versioned method and search range, not a probability calibration."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    name: Literal["spectral-flux-autocorrelation-v1"] = "spectral-flux-autocorrelation-v1"
    analysis_sample_rate: Literal[8000] = ANALYSIS_RATE
    hop_seconds: Literal[0.01] = HOP/ANALYSIS_RATE
    bpm_min: Literal[40] = BPM_MIN
    bpm_max: Literal[240] = BPM_MAX
    quality_calibration: Literal["uncalibrated"] = "uncalibrated"


class TempoAnalysis(BaseModel):
    """Audio evidence only; adoption and notation are separate explicit actions."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)
    status: Literal["candidates", "insufficient_evidence", "unstable"]
    reason_codes: list[str]
    method: TempoMethod = Field(default_factory=TempoMethod)
    analyzed_intervals: list[Interval]
    coverage: Literal["full", "sampled"]
    candidates: list[TempoCandidate]
    windows: list[TempoWindow]
    warnings: list[str]


def _check_wave_length(stream: BinaryIO) -> None:
    """Reject a truncated RIFF/RIFX container before a decoder can shorten it."""
    stream.seek(0)
    header = stream.read(12)
    if header[:4] not in {b"RIFF", b"RIFX"} or header[8:12] != b"WAVE":
        raise TempoAudioError("invalid_audio", "The native WAV container is malformed or unsupported.")
    order = "little" if header[:4] == b"RIFF" else "big"
    stream.seek(0, 2)
    size = stream.tell()
    declared = int.from_bytes(header[4:8], order)+8
    if declared > size or declared < 12:
        raise TempoAudioError("invalid_audio", "The WAV container is truncated; no partial audio was analyzed.")
    cursor, chunks = 12, 0
    while cursor+8 <= declared:
        chunks += 1
        if chunks > 4096:
            raise TempoAudioError("invalid_audio", "The WAV container has too many metadata chunks.")
        stream.seek(cursor)
        chunk = stream.read(8)
        length = int.from_bytes(chunk[4:8], order)
        if len(chunk) != 8 or cursor+8+length > declared:
            raise TempoAudioError("invalid_audio", "The WAV container is truncated; no partial audio was analyzed.")
        cursor += 8+length+(length % 2)
    stream.seek(0)


def inspect_audio(stream: BinaryIO, *, max_seconds: float) -> AudioInfo:
    """Validate decoder metadata before reading samples or allocating arrays."""
    try:
        stream.seek(0)
        with sf.SoundFile(stream, closefd=False) as audio:
            if audio.format not in {"WAV", "WAVEX", "FLAC"}:
                raise TempoAudioError("unsupported_audio", "Use native WAV or FLAC audio; no decoder or model is installed automatically.", 415)
            if audio.format in {"WAV", "WAVEX"}:
                _check_wave_length(stream)
            if audio.channels not in {1, 2} or not 8000 <= audio.samplerate <= 96000:
                raise TempoAudioError("unsupported_audio", "Use one or two audio channels at 8000 to 96000 Hz.")
            info = AudioInfo(audio.samplerate, audio.channels, len(audio), "FLAC" if audio.format == "FLAC" else "WAV")
            if not 0 < info.duration_seconds <= max_seconds:
                raise TempoAudioError("duration_limit", f"Select nonempty audio of at most {max_seconds:g} seconds. No hidden crop was applied.")
            return info
    except (sf.LibsndfileError, RuntimeError, OSError) as exc:
        raise TempoAudioError("invalid_audio", "The native audio file is malformed or unsupported.") from exc


def _check_budget(deadline: float) -> None:
    if time.monotonic() > deadline:
        raise TempoAudioError("analysis_budget", "Tempo analysis exceeded its cooperative CPU budget. Retry explicitly with shorter audio.", 503)


def analysis_intervals(info: AudioInfo) -> list[Interval]:
    """Cover short inputs; disclose three separated intervals on longer inputs."""
    duration = info.duration_seconds
    if duration <= MAX_ANALYZED_SECONDS:
        count = max(1, math.ceil(duration/MAX_INTERVAL_SECONDS))
        boundaries = [round(i*info.frames/count) for i in range(count+1)]
        return [Interval(start_seconds=a/info.sample_rate, end_seconds=b/info.sample_rate)
            for a, b in zip(boundaries, boundaries[1:])]
    length = int(MAX_INTERVAL_SECONDS*info.sample_rate)
    starts = [0, (info.frames-length)//2, info.frames-length]
    return [Interval(start_seconds=start/info.sample_rate, end_seconds=(start+length)/info.sample_rate) for start in starts]


def _onset_envelope(samples: np.ndarray, rate: int) -> np.ndarray:
    divisor = math.gcd(rate, ANALYSIS_RATE)
    if rate != ANALYSIS_RATE:
        samples = signal.resample_poly(samples, ANALYSIS_RATE//divisor, rate//divisor, axis=0)
    # Each channel contributes spectral magnitudes. Opposite-polarity stereo
    # must not disappear through waveform downmixing.
    frames = np.lib.stride_tricks.sliding_window_view(samples, FRAME, axis=0)[::HOP]
    spectrum = np.abs(np.fft.rfft(frames*np.hanning(FRAME), axis=-1)).mean(axis=1)
    spectrum = spectrum[:, 3:225]  # approximately 47..3500 Hz
    scale = float(np.max(spectrum))
    if scale <= 1e-12:
        return np.zeros(len(spectrum))
    logged = np.log1p(100*spectrum/scale)
    flux = np.maximum(np.diff(logged, axis=0), 0).sum(axis=1)
    # Stationary tones can have tiny periodic FFT leakage. Do not normalize
    # that numerical modulation into a full-strength transient envelope.
    if float(np.max(flux, initial=0))/max(1e-12, float(np.mean(logged.sum(axis=1)))) < .01:
        return np.zeros(len(flux))
    envelope = np.maximum(flux-ndimage.median_filter(flux, size=51, mode="nearest"), 0)
    # Ignore the artificial window-start transition and a final truncated frame.
    envelope[:20] = 0
    envelope[-10:] = 0
    peak = float(np.max(envelope, initial=0))
    return envelope/peak if peak > 1e-12 else envelope


@dataclass(frozen=True)
class _Hypothesis:
    bpm: float
    periodicity: float
    score: float


@dataclass(frozen=True)
class _WindowEvidence:
    window: TempoWindow
    hypotheses: list[_Hypothesis]


def _window(samples: np.ndarray, rate: int, interval: Interval) -> _WindowEvidence:
    seconds = interval.end_seconds-interval.start_seconds
    rms = float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))
    common = dict(start_seconds=interval.start_seconds, end_seconds=interval.end_seconds)
    if seconds < 6 or rms < 1e-5:
        return _WindowEvidence(TempoWindow(**common, status="insufficient", bpm=None, periodicity=None, onset_count=0), [])
    envelope = _onset_envelope(samples, rate)
    peaks, _ = signal.find_peaks(envelope, height=.12, prominence=.10, distance=10)
    if len(peaks) < MIN_ONSETS:
        return _WindowEvidence(TempoWindow(**common, status="insufficient", bpm=None, periodicity=None, onset_count=len(peaks)), [])
    centered = envelope-np.mean(envelope)
    low, high = math.ceil(60*ANALYSIS_RATE/(HOP*BPM_MAX)), math.floor(60*ANALYSIS_RATE/(HOP*BPM_MIN))
    lags = np.arange(low, high+1)
    correlations = np.array([np.dot(centered[:-lag], centered[lag:]) /
        max(1e-12, np.sqrt(np.dot(centered[:-lag], centered[:-lag])*np.dot(centered[lag:], centered[lag:]))) for lag in lags])
    # Prefer a shorter repetition period when integer multiples have almost the
    # same evidence, retaining the explicit half/double ambiguity in the report.
    local, _ = signal.find_peaks(np.r_[0, correlations, 0], prominence=.03)
    indices = [int(i-1) for i in local if 0 < i <= len(correlations)]
    if not indices:
        return _WindowEvidence(TempoWindow(**common, status="weak", bpm=None, periodicity=0, onset_count=len(peaks)), [])
    iois = np.diff(peaks)
    # Repetition-pattern aliases such as 3:2 can outrank the underlying onset
    # grid when attacks are omitted or syncopated. Audio onset intervals supply
    # independent support for a candidate grid; this still cannot choose which
    # grid level is the musical beat, hence the explicit octave alternatives.
    grid_support = np.array([np.mean(np.abs(iois-np.maximum(1, np.round(iois/lag))*lag) <= 3) for lag in lags])
    combined = .65*np.maximum(correlations, 0)+.35*grid_support
    strongest = max(correlations[i] for i in indices)
    supported = [i for i in indices if correlations[i] >= MIN_PERIODICITY]
    strongest_combined = max((combined[i] for i in supported), default=0)
    plausible = [i for i in supported if combined[i] >= strongest_combined*.95]
    if not plausible:
        return _WindowEvidence(TempoWindow(**common, status="weak", bpm=None, periodicity=round(float(max(0, strongest)), 6), onset_count=len(peaks)), [])
    best = min(plausible, key=lambda i: lags[i])
    def hypothesis(index: int) -> _Hypothesis:
        lag = float(lags[index])
        if 0 < index < len(correlations)-1:
            left, center, right = correlations[index-1:index+2]
            denominator = left-2*center+right
            if denominator < -1e-12:
                lag += float(np.clip(.5*(left-right)/denominator, -.5, .5))
        bpm = round(float(np.clip(60*ANALYSIS_RATE/(HOP*lag), BPM_MIN, BPM_MAX)), 3)
        return _Hypothesis(bpm, round(float(np.clip(correlations[index], 0, 1)), 6), float(combined[index]))

    primary = hypothesis(best)
    hypotheses = [hypothesis(index) for index in sorted(supported, key=lambda i: combined[i], reverse=True)]
    return _WindowEvidence(TempoWindow(**common, status="periodic", bpm=primary.bpm,
        periodicity=primary.periodicity, onset_count=len(peaks)), hypotheses)


def _octave_related(value: float, reference: float) -> bool:
    return any(abs(value-reference*factor)/(reference*factor) < .04 for factor in (.125, .25, .5, 1, 2, 4, 8))


def _secondary(evidence: list[_WindowEvidence], primary_bpm: float, required: int) -> tuple[float, float, int] | None:
    """Find another measured stable pulse interpretation, never invent a ratio.

    This runs only after the primary local estimates passed stability checks.
    Shared low-scoring aliases therefore cannot make changing tempos look stable.
    """
    periodic = [item for item in evidence if item.window.status == "periodic"]
    seeds = [hypothesis for item in periodic for hypothesis in item.hypotheses
        if not _octave_related(hypothesis.bpm, primary_bpm)]
    groups = []
    for seed in seeds:
        matches = []
        for item in periodic:
            strongest = max(h.score for h in item.hypotheses)
            eligible = [h for h in item.hypotheses if abs(h.bpm-seed.bpm)/seed.bpm < .04 and h.score >= strongest*.5]
            if eligible:
                matches.append(max(eligible, key=lambda h: h.score))
        if len(matches) >= required:
            groups.append((float(np.mean([h.score for h in matches])), float(np.median([h.bpm for h in matches])),
                float(np.median([h.periodicity for h in matches])), len(matches)))
    if not groups:
        return None
    strongest = max(group[0] for group in groups)
    # Prefer the shorter alternative period among comparably supported aliases,
    # rather than picking a high-order subharmonic from tiny score differences.
    _, bpm, quality, support = max((group for group in groups if group[0] >= strongest*.95), key=lambda group: group[1])
    return round(bpm, 1), round(quality, 6), support


def estimate_audio_tempo(stream: BinaryIO, *, max_seconds: float = 120,
                         budget_seconds: float = ANALYSIS_BUDGET_SECONDS) -> TempoAnalysis:
    """Analyze actual native audio with explicit sampling and cooperative bounds.

    The caller bounds encoded bytes and serializes CPU requests. This function
    validates decoded metadata before allocation, reads at most sixty seconds,
    and checks an elapsed cooperative budget between bounded windows. It never
    changes source bytes, notes, musical-clock settings or playback.
    """
    deadline = time.monotonic()+budget_seconds
    info = inspect_audio(stream, max_seconds=max_seconds)
    intervals = analysis_intervals(info)
    warnings = ["Periodicity is an uncalibrated signal statistic, not a probability or measured song-tempo accuracy.",
        "The pulse can be interpreted at half or double tempo; meter and measure downbeat are not inferred.",
        "Only the disclosed audio intervals support these observations. No model, MIDI storage-clock value, or MIDI note event is used."]
    coverage: Literal["full", "sampled"] = "sampled" if info.duration_seconds > MAX_ANALYZED_SECONDS else "full"
    if coverage == "sampled":
        warnings.append("Up to three twenty-second intervals were sampled. Tempo changes or rhythms outside them may be missed.")
    evidence: list[_WindowEvidence] = []
    stream.seek(0)
    with sf.SoundFile(stream, closefd=False) as audio:
        for interval in intervals:
            start, end = round(interval.start_seconds*info.sample_rate), round(interval.end_seconds*info.sample_rate)
            count = max(1, math.ceil((end-start)/(MAX_WINDOW_SECONDS*info.sample_rate)))
            cuts = [start+round(i*(end-start)/count) for i in range(count+1)]
            for a, b in zip(cuts, cuts[1:]):
                _check_budget(deadline)
                try:
                    audio.seek(a)
                    samples = audio.read(b-a, dtype="float32", always_2d=True)
                except (RuntimeError, OSError) as exc:
                    raise TempoAudioError("invalid_samples", "The audio could not be decoded completely; no partial result was produced.") from exc
                if len(samples) != b-a or not np.isfinite(samples).all():
                    raise TempoAudioError("invalid_samples", "Audio samples are truncated or nonfinite; no tempo result was produced.")
                evidence.append(_window(samples, info.sample_rate, Interval(start_seconds=a/info.sample_rate, end_seconds=b/info.sample_rate)))
                _check_budget(deadline)
    windows = [item.window for item in evidence]
    periodic = [window for window in windows if window.status == "periodic"]
    result = dict(analyzed_intervals=intervals, coverage=coverage, windows=windows, warnings=warnings)
    if info.duration_seconds < MIN_SECONDS:
        return TempoAnalysis(**result, status="insufficient_evidence", reason_codes=["audio_too_short"], candidates=[])
    if len(periodic) < max(1, math.ceil(len(windows)*.66)):
        return TempoAnalysis(**result, status="insufficient_evidence", reason_codes=["weak_or_missing_periodicity"], candidates=[])
    values = [window.bpm for window in periodic]
    central = float(np.median(values))
    if any(abs(value-central)/central > MAX_WINDOW_DEVIATION for value in values):
        return TempoAnalysis(**result, status="unstable", reason_codes=["window_tempo_disagreement"], candidates=[])
    if len(periodic) != len(windows):
        warnings.append("Some analyzed windows lack periodic evidence; the candidate describes only the supporting windows.")
    if len(windows) == 1:
        warnings.append("Only one local window is available; cross-window tempo stability cannot be assessed.")
    quality = round(float(np.median([window.periodicity for window in periodic])), 6)
    bpm = round(central, 1)
    def family(value: float, quality: float, support: int, identifier: Literal["pulse-1", "pulse-2"],
               relation: Literal["primary", "alternative"]) -> list[TempoCandidate]:
        measured = TempoCandidate(bpm=value, relation=relation, family_id=identifier,
            periodicity=quality, supporting_windows=support, adoptable=True)
        variants = [TempoCandidate(bpm=round(value*factor, 3), relation=variant, family_id=identifier,
            periodicity=None, supporting_windows=0, adoptable=20 <= value*factor <= 300)
            for factor, variant in [(0.5, "half"), (2, "double")]]
        return [measured, *variants]

    candidates = family(bpm, quality, len(periodic), "pulse-1", "primary")
    reasons = ["periodic_audio_evidence", "half_double_ambiguity"]
    secondary = _secondary(evidence, bpm, max(1, math.ceil(len(windows)*.66)))
    if secondary is not None:
        other_bpm, other_quality, support = secondary
        candidates.extend(family(other_bpm, other_quality, support, "pulse-2", "alternative"))
        reasons.append("multiple_pulse_hypotheses")
    warnings.append("Triplets, syncopation and other subdivisions can create non-octave pulse interpretations. The displayed families are not exhaustive or guaranteed musical beats.")
    return TempoAnalysis(**result, status="candidates", reason_codes=reasons, candidates=candidates)
