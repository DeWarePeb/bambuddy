"""Schemas for pending spool-to-slot assignments (voron B8)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.app.schemas.spool import SpoolResponse

DEFAULT_TIMEOUT_SECONDS = 1800
MIN_TIMEOUT_SECONDS = 60
MAX_TIMEOUT_SECONDS = 86400


class PendingSlotAssignmentCreate(BaseModel):
    """Body for POST /inventory/assignments/pending."""

    spool_id: int = Field(gt=0)
    # None = whichever printer loads a spool first.
    printer_id: int | None = Field(default=None, gt=0)
    timeout_seconds: int = Field(default=DEFAULT_TIMEOUT_SECONDS, ge=MIN_TIMEOUT_SECONDS, le=MAX_TIMEOUT_SECONDS)
    source: Literal["ui", "api"] = "ui"


class PendingSlotAssignmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    spool_id: int
    printer_id: int | None = None
    printer_name: str | None = None
    source: str
    status: str
    timeout_seconds: int
    created_at: datetime | None = None
    expires_at: datetime | None = None
    completed_at: datetime | None = None
    assigned_printer_id: int | None = None
    assigned_ams_id: int | None = None
    assigned_tray_id: int | None = None
    spool: SpoolResponse | None = None
