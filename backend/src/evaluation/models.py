"""Canonical note-event models shared by benchmark backends and artifacts."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SourceValue = str | int | float | bool | None


class NoteEvent(BaseModel):
    """A single performed MIDI note expressed in absolute seconds."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    start: float = Field(ge=0.0)
    end: float
    midi: int = Field(ge=0, le=127)
    velocity: float = Field(default=1.0, gt=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_interval(self) -> NoteEvent:
        """Require a positive-duration interval."""
        if self.end <= self.start:
            raise ValueError("end must be greater than start")
        return self


class NoteEventSet(BaseModel):
    """A versioned, deterministically ordered collection of note events."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    source: dict[str, SourceValue] = Field(default_factory=dict)
    notes: tuple[NoteEvent, ...] = ()

    @field_validator("notes")
    @classmethod
    def sort_notes(cls, notes: tuple[NoteEvent, ...]) -> tuple[NoteEvent, ...]:
        """Normalize note ordering without removing or merging any events."""
        return tuple(sorted(notes, key=lambda note: (note.start, note.midi, note.end)))
