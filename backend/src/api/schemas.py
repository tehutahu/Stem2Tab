from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class JobStatus(str, Enum):
    """Represents the lifecycle state of a job."""

    PENDING = "PENDING"
    STARTED = "STARTED"
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    RETRY = "RETRY"
    REVOKED = "REVOKED"


class JobCreateResponse(BaseModel):
    """Response returned when a job is created."""

    job_id: str = Field(..., description="Unique job identifier")


class JobStatusResponse(BaseModel):
    """Status payload returned for a job."""

    job_id: str = Field(..., description="Unique job identifier")
    status: JobStatus = Field(..., description="Current job status")
    progress: int = Field(..., ge=0, le=100, description="Progress percentage")
    created_at: datetime = Field(..., description="Job creation time (UTC)")
    updated_at: datetime = Field(..., description="Last status update time (UTC)")
    files: list[str] = Field(default_factory=list, description="Available artifact names")
    error: str | None = Field(default=None, description="Error message if failed")

