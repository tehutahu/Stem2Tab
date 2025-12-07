import os
from pathlib import Path

import structlog
from demucs.pretrained import get_model

logger = structlog.get_logger()


def ensure_model(model_name: str, cache_dir: Path) -> Path:
    """
    Ensure Demucs model is available locally.

    Downloads the model on first use into the provided cache directory.
    """
    resolved_cache = cache_dir.expanduser().resolve()
    resolved_cache.mkdir(parents=True, exist_ok=True)

    # Demucs respects DEMUCS_CACHEDIR for download location.
    os.environ["DEMUCS_CACHEDIR"] = str(resolved_cache)
    logger.info("demucs_model_check", model=model_name, cache_dir=str(resolved_cache))

    get_model(name=model_name)
    return resolved_cache

