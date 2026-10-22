"""CPU pYIN evaluation adapters with preserved frames and separate note assembly."""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from src.evaluation.io import read_midi, write_note_events_csv, write_note_events_json
from src.evaluation.models import NoteEvent, NoteEventSet
from src.evaluation.note_assembly import AssemblyConfig, assemble_notes

if TYPE_CHECKING:
    from src.evaluation.adapters import AdapterConfig, TranscriptionResult

_SR = 22050
_HOP = 256
_FRAME = 4096
_ENERGY_FLOOR = 1e-4


def _extract_frames(audio: np.ndarray, sr: int) -> tuple[np.ndarray, ...]:
    import librosa

    f0, voiced, probability = librosa.pyin(
        audio, fmin=27.5, fmax=392., sr=sr, frame_length=_FRAME, hop_length=_HOP,
        n_thresholds=100, beta_parameters=(2, 18), boltzmann_parameter=2.,
        resolution=.1, max_transition_rate=35.92, switch_prob=.01,
        no_trough_prob=.01, fill_na=np.nan, center=True, pad_mode="constant",
    )
    return f0, voiced, probability, np.arange(len(f0)) * _HOP / sr


def _activity_and_onsets(
    audio: np.ndarray, sr: int, times: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    import librosa

    window = 2 * round(.005 * sr) + 1
    rms = librosa.feature.rms(y=audio, frame_length=window, hop_length=_HOP,
                              center=True, pad_mode="constant")[0]
    energy = np.interp(times, np.arange(len(rms)) * _HOP / sr, rms)
    # A global peak-relative gate can erase quiet notes following a louder note.
    # Keep only the fixed near-silence floor; voicing is still required separately.
    active = energy > _ENERGY_FLOOR
    strength = librosa.onset.onset_strength(y=audio, sr=sr, hop_length=_HOP,
                                          n_fft=2048, center=True)
    onsets = librosa.onset.onset_detect(
        onset_envelope=strength, sr=sr, hop_length=_HOP, units="time",
        backtrack=False, normalize=True, pre_max=2, post_max=1,
        pre_avg=8, post_avg=9, wait=2, delta=.07,
    )
    return active, onsets, energy


def _raw_events(f0: np.ndarray, voiced: np.ndarray, times: np.ndarray, duration: float) -> NoteEventSet:
    notes = []
    pitch, start = None, 0.
    for frequency, flag, time in zip(f0, voiced, times):
        value = None
        if flag and np.isfinite(frequency) and frequency > 0:
            candidate = int(np.rint(12 * (np.log2(frequency) - np.log2(440.)) + 69))
            if 0 <= candidate <= 127:
                value = candidate
        if value != pitch:
            end = min(float(time), duration)
            if pitch is not None and end > start:
                notes.append(NoteEvent(start=start, end=end, midi=pitch, velocity=80 / 127))
            pitch, start = value, float(time)
    if pitch is not None and duration > start:
        notes.append(NoteEvent(start=start, end=duration, midi=pitch, velocity=80 / 127))
    return NoteEventSet(notes=tuple(notes))


def _write_precise_midi(events: NoteEventSet, path: Path) -> None:
    import pretty_midi

    midi = pretty_midi.PrettyMIDI(initial_tempo=120., resolution=_SR)
    bass = pretty_midi.Instrument(program=33, name="pYIN evaluation bass")
    bass.notes = [pretty_midi.Note(80, n.midi, n.start, n.end) for n in events.notes]
    midi.instruments.append(bass)
    midi.write(str(path))


class PyinTranscriber:
    """Evaluate pYIN raw runs or independently assembled notes; never use a reference."""

    def __init__(self, *, assembled: bool = True) -> None:
        """Select assembled pYIN notes or the unprocessed frame-run baseline."""
        self.assembled = assembled
        self.name = "pyin" if assembled else "pyin_raw"

    def run(self, audio_path: Path, output_dir: Path, *, config: AdapterConfig) -> TranscriptionResult:
        """Save raw frames/notes alongside optional activity/onset-aware assembly."""
        from importlib import metadata
        import librosa
        from src.evaluation.adapters import TranscriptionResult

        del config
        if not audio_path.is_file():
            raise FileNotFoundError(f"Input audio not found: {audio_path}")
        audio, _ = librosa.load(audio_path, sr=_SR, mono=True)
        if not len(audio):
            raise ValueError("pYIN requires nonempty audio")
        duration = len(audio) / _SR
        f0, voiced, probability, times = _extract_frames(audio, _SR)
        active, onsets, energy = _activity_and_onsets(audio, _SR, times)
        output_dir.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(output_dir / "pyin_frames.npz", f0=f0, voiced_flag=voiced,
                            voiced_probability=probability, time=times, active=active,
                            energy=energy, onset_times=onsets)
        raw = _raw_events(f0, voiced, times, duration)
        raw_path = output_dir / "raw.mid"
        _write_precise_midi(raw, raw_path)
        write_note_events_json(raw, output_dir / "raw_events.json")
        write_note_events_csv(raw, output_dir / "raw_events.csv")
        settings = AssemblyConfig()
        selected = raw_path
        if self.assembled:
            events = assemble_notes(f0, voiced, times, duration, active=active,
                                    onset_times=onsets, config=settings)
            selected = output_dir / "assembled.mid"
            _write_precise_midi(events, selected)
        parameters = {
            "sample_rate": _SR, "fmin": 27.5, "fmax": 392., "frame_length": _FRAME,
            "hop_length": _HOP, "center": True, "pad_mode": "constant",
            "n_thresholds": 100, "beta_parameters": [2, 18], "boltzmann_parameter": 2.,
            "resolution": .1, "max_transition_rate": 35.92, "switch_prob": .01,
            "no_trough_prob": .01, "fill_na": "NaN", "energy_floor": _ENERGY_FLOOR,
            "activity_policy": "absolute_rms_floor", "energy_window_samples": 2 * round(.005 * _SR) + 1,
            "onset_n_fft": 2048, "onset_delta": .07,
            "onset_peak_pick": {"pre_max": 2, "post_max": 1, "pre_avg": 8, "post_avg": 9, "wait": 2},
            "assembly": settings.model_dump(),
        }
        return TranscriptionResult(
            raw_midi_path=raw_path,
            events=read_midi(selected, source={"kind": "estimate", "transcriber": self.name}),
            metadata={"backend": self.name, "package_version": metadata.version("librosa"),
                      "processing": "assembled" if self.assembled else "raw_frame_runs",
                      "parameters": json.dumps(parameters, sort_keys=True),
                      "raw_midi_path": str(raw_path), "selected_midi_path": str(selected),
                      "raw_frames_path": str(output_dir / "pyin_frames.npz"),
                      "midi_resolution": _SR, "uses_reference": False},
        )
