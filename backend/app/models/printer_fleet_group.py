"""Operational printer grouping for the farm command center (voron B11).

A fleet group is a name and a set of printers — "Shop", "Prototyping", "Voron"
— used to bucket the fleet on the command center page. It is deliberately
separate from ``models/group.py``, which is upstream's *user permission* group
and has nothing to do with printers.

Grouping is presentation only: nothing schedules, dispatches or restricts
against a fleet group. A printer may belong to at most one group per group row
(``uq_printer_fleet_group_member``); a printer in no group at all falls back to
its ``location`` on the page, and then to "Ungrouped".

Ported from vmhomelab/printbuddy, rewritten for Bambuddy 1.2.5.x.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.core.database import Base


class PrinterFleetGroup(Base):
    """A named set of printers shown as one bucket on the command center."""

    __tablename__ = "printer_fleet_groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    color: Mapped[str | None] = mapped_column(String(20), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    members: Mapped[list["PrinterFleetGroupMember"]] = relationship(
        back_populates="group",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class PrinterFleetGroupMember(Base):
    """Membership link between a printer and a fleet group.

    ``ondelete="CASCADE"`` on both sides: deleting a printer drops its
    memberships, and deleting a group drops the whole set. Neither ever
    deletes a printer.
    """

    __tablename__ = "printer_fleet_group_members"
    __table_args__ = (UniqueConstraint("group_id", "printer_id", name="uq_printer_fleet_group_member"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("printer_fleet_groups.id", ondelete="CASCADE"), index=True)
    printer_id: Mapped[int] = mapped_column(ForeignKey("printers.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    group: Mapped[PrinterFleetGroup] = relationship(back_populates="members")
