"""A model can report source corrections; only the local review UI applies them."""
from typing import Any
from pydantic import Field
from app.schemas.reading_note import StrictModel, Evidence


class CorrectionReport(StrictModel):
    source_ref: str = Field(min_length=1, max_length=12000)
    reason: str = Field(min_length=1, max_length=500)
    evidence: list[Evidence] = Field(min_length=1, max_length=5)
    replacement: dict[str, Any] | None = None


class CorrectionDecision(StrictModel):
    replacement: dict[str, Any] | None = None
    rebuild: bool = True
