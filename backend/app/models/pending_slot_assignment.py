"""Pending spool-to-slot assignment (voron B8).

A row here says "assign this inventory spool to the next AMS slot that gets a
spool loaded on this printer". ``on_ams_change`` completes it the moment a
tray without an assignment reports filament, using the same assign path the
manual slot dialog uses, so the slot gets the spool's filament settings too.

Only one live (``pending``) row exists per spool; finished rows are kept for
the status endpoint and expire lazily (see the service module).
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.core.database import Base

STATUS_PENDING = "pending"
STATUS_COMPLETED = "completed"
STATUS_CANCELLED = "cancelled"
STATUS_TIMED_OUT = "timed_out"


class PendingSlotAssignment(Base):
    """A request to assign a spool to the next AMS slot that gets loaded."""

    __tablename__ = "pending_slot_assignment"

    id: Mapped[int] = mapped_column(primary_key=True)
    spool_id: Mapped[int] = mapped_column(ForeignKey("spool.id", ondelete="CASCADE"), index=True)
    # NULL = whichever printer loads a spool first.
    printer_id: Mapped[int | None] = mapped_column(ForeignKey("printers.id", ondelete="CASCADE"), index=True)
    # Where the request came from: "ui" today, "api" for scripts.
    source: Mapped[str] = mapped_column(String(20), default="ui")
    status: Mapped[str] = mapped_column(String(20), default=STATUS_PENDING, index=True)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=1800)

    # Filled when the request completes.
    assigned_printer_id: Mapped[int | None] = mapped_column(Integer)
    assigned_ams_id: Mapped[int | None] = mapped_column(Integer)
    assigned_tray_id: Mapped[int | None] = mapped_column(Integer)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    spool: Mapped["Spool"] = relationship()
    printer: Mapped["Printer | None"] = relationship()

    @property
    def printer_name(self) -> str | None:
        return self.printer.name if self.printer else None

    @property
    def expires_at(self) -> datetime | None:
        if self.created_at is None:
            return None
        created = self.created_at if self.created_at.tzinfo else self.created_at.replace(tzinfo=timezone.utc)
        return created + timedelta(seconds=self.timeout_seconds)

    @property
    def is_expired(self) -> bool:
        """True when a pending row has outlived its timeout (only pending rows expire)."""
        if self.status != STATUS_PENDING:
            return False
        expires = self.expires_at
        return expires is not None and datetime.now(timezone.utc) >= expires


from backend.app.models.printer import Printer  # noqa: E402, F401
from backend.app.models.spool import Spool  # noqa: E402, F401
