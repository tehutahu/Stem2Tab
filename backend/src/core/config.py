from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    file_bucket_path: Path = Field(default=Path("/data"))
    celery_broker_url: str = Field(default="redis://redis:6379/0")
    demucs_model: str = Field(default="htdemucs")
    api_port: int = Field(default=8000)
    log_level: str = Field(default="info")
    demucs_cache_subdir: Path | None = None

    @property
    def demucs_cache_dir(self) -> Path:
        """Directory to cache Demucs models."""
        if self.demucs_cache_subdir:
            return self.file_bucket_path / self.demucs_cache_subdir
        return self.file_bucket_path / "cache" / "demucs"


settings = Settings()

