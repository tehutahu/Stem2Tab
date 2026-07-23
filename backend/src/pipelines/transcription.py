"""Basic Pitch ONNX transcription pipeline."""

from __future__ import annotations

import os
from pathlib import Path

import structlog

logger = structlog.get_logger()


def transcribe_midi(input_wav: Path, output_dir: Path, *, job_id: str | None = None) -> Path:
    """Run Basic Pitch (ONNX) on a WAV file and return the generated MIDI path.

    The packaged ONNX model (`ICASSP_2022_MODEL_PATH`) is used; TensorFlow is not required.
    """
    if not input_wav.exists():
        raise FileNotFoundError(f"Input audio not found: {input_wav}")

    output_dir.mkdir(parents=True, exist_ok=True)
    midi_path = output_dir / f"{input_wav.stem}.mid"

    # Ensure ONNX backend is chosen even if other runtimes are present.
    os.environ.setdefault("BASIC_PITCH_MODEL_SERIALIZATION", "onnx")

    logger.info(
        "transcription_start",
        job_id=job_id,
        input_wav=str(input_wav),
        output_dir=str(output_dir),
    )

    try:
        from basic_pitch import inference

        _, midi_data, _ = inference.predict(
            audio_path=input_wav,
            model_or_model_path=inference.ICASSP_2022_MODEL_PATH,
        )
        midi_data.write(str(midi_path))
    except Exception as exc:  # pragma: no cover - defensive guard
        logger.exception("transcription_failed", job_id=job_id, error=str(exc))
        raise

    if not midi_path.exists():
        raise FileNotFoundError(f"MIDI output not generated at: {midi_path}")

    logger.info("transcription_complete", job_id=job_id, midi_path=str(midi_path))
    return midi_path

