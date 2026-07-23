"""Demucs-based stem separation helper."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import structlog

logger = structlog.get_logger()

# Demucs standard 4 stems
DEFAULT_STEMS = ("vocals", "drums", "bass", "other")


def separate_stems(
    input_audio: Path,
    output_dir: Path,
    *,
    model_name: str,
    cache_dir: Path,
    job_id: str | None = None,
) -> dict[str, Path]:
    """Run Demucs via CLI and return generated stem paths."""
    if not input_audio.exists():
        raise FileNotFoundError(f"Input audio not found: {input_audio}")

    output_dir.mkdir(parents=True, exist_ok=True)
    tmp_root = output_dir / "_demucs"
    shutil.rmtree(tmp_root, ignore_errors=True)
    tmp_root.mkdir(parents=True, exist_ok=True)

    env = {
        **os.environ,
        "DEMUCS_CACHEDIR": str(cache_dir),
        "TORCH_HOME": str(cache_dir),
    }

    cmd = ["demucs", "-n", model_name, "--out", str(tmp_root), str(input_audio)]
    logger.info("demucs_start", job_id=job_id, cmd=" ".join(cmd), cache_dir=str(cache_dir))

    try:
        subprocess.run(cmd, check=True, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except FileNotFoundError as exc:
        logger.exception("demucs_not_found", job_id=job_id, error=str(exc))
        raise RuntimeError("Demucs CLI not found. Ensure demucs is installed in the environment.") from exc
    except subprocess.CalledProcessError as exc:
        logger.exception("demucs_failed", job_id=job_id, returncode=exc.returncode, stderr=exc.stderr.decode("utf-8", "ignore"))
        raise RuntimeError(f"Demucs separation failed: {exc}") from exc

    separated_dir = tmp_root / model_name / input_audio.stem
    stems: dict[str, Path] = {}
    for stem in DEFAULT_STEMS:
        candidate = separated_dir / f"{stem}.wav"
        if candidate.exists():
            dest = output_dir / f"{stem}.wav"
            shutil.move(str(candidate), dest)
            stems[stem] = dest

    shutil.rmtree(tmp_root, ignore_errors=True)

    if not stems:
        raise RuntimeError(f"No stems produced by Demucs at {separated_dir}")

    logger.info("demucs_complete", job_id=job_id, stems=list(stems.keys()))
    return stems


