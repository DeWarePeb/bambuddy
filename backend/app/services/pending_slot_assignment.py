"""Pending spool-to-slot assignments (voron B8).

Lifecycle of "assign this spool to the next slot that gets loaded":

* ``create_pending_assignment`` — mark a spool; an older pending mark on the
  same spool is replaced.
* ``try_complete_pending_assignment`` — called from ``on_ams_change`` for a
  tray that just reported filament and has no assignment yet. Picks the
  oldest matching request and runs the normal ``assign_spool`` route handler
  so the slot gets the spool's filament settings exactly like a manual assign.
* ``cancel_pending_assignment`` / ``list_assignments`` — UI plumbing.

Timeouts are lazy: any read path first flips over-age pending rows to
``timed_out``. No background tasks, nothing to restore after a restart.

Ported from Printbuddy's assign-on-next-slot API (vmhomelab/printbuddy#15)
and rewritten against Bambuddy's RFID auto-assign path.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.core.websocket import ws_manager
from backend.app.models.pending_slot_assignment import (
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_PENDING,
    STATUS_TIMED_OUT,
    PendingSlotAssignment,
)
from backend.app.models.printer import Printer
from backend.app.models.spool import Spool
from backend.app.services.spool_tag_matcher import get_spool_by_tag, is_valid_tag, link_tag_to_inventory_spool

logger = logging.getLogger(__name__)

WS_EVENT = "pending_slot_assignment_changed"


class PendingAssignmentError(ValueError):
    """Raised for caller mistakes; ``status_code`` maps to the HTTP reply."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _load_options():
    return (
        selectinload(PendingSlotAssignment.spool).selectinload(Spool.k_profiles),
        selectinload(PendingSlotAssignment.printer),
    )


async def _broadcast(event: str, assignment: PendingSlotAssignment, printer_name: str | None = None) -> None:
    """Tell the UI a request changed. ``printer_name`` overrides the requested printer's name
    (on completion it is the printer that actually took the spool — the request may say "any")."""
    try:
        await ws_manager.broadcast(
            {
                "type": WS_EVENT,
                "event": event,
                "assignment_id": assignment.id,
                "spool_id": assignment.spool_id,
                "printer_id": assignment.printer_id,
                "printer_name": printer_name or assignment.printer_name,
                "status": assignment.status,
                "assigned_printer_id": assignment.assigned_printer_id,
                "assigned_ams_id": assignment.assigned_ams_id,
                "assigned_tray_id": assignment.assigned_tray_id,
            }
        )
    except Exception:
        logger.debug("pending slot assignment broadcast failed", exc_info=True)


async def expire_stale(db: AsyncSession) -> list[PendingSlotAssignment]:
    """Flip pending rows past their timeout to ``timed_out``. Returns what changed."""
    result = await db.execute(
        select(PendingSlotAssignment).options(*_load_options()).where(PendingSlotAssignment.status == STATUS_PENDING)
    )
    expired = [row for row in result.scalars().all() if row.is_expired]
    for row in expired:
        row.status = STATUS_TIMED_OUT
        row.completed_at = datetime.now(timezone.utc)
    if expired:
        await db.commit()
        for row in expired:
            logger.info("Pending slot assignment %d for spool %d timed out", row.id, row.spool_id)
            await _broadcast("timed_out", row)
    return expired


async def list_assignments(
    db: AsyncSession,
    *,
    spool_id: int | None = None,
    printer_id: int | None = None,
    include_finished: bool = False,
) -> list[PendingSlotAssignment]:
    await expire_stale(db)
    query = select(PendingSlotAssignment).options(*_load_options()).order_by(PendingSlotAssignment.created_at.asc())
    if not include_finished:
        query = query.where(PendingSlotAssignment.status == STATUS_PENDING)
    if spool_id is not None:
        query = query.where(PendingSlotAssignment.spool_id == spool_id)
    if printer_id is not None:
        query = query.where(PendingSlotAssignment.printer_id == printer_id)
    result = await db.execute(query)
    return list(result.scalars().all())


async def get_assignment(db: AsyncSession, assignment_id: int) -> PendingSlotAssignment | None:
    await expire_stale(db)
    result = await db.execute(
        select(PendingSlotAssignment).options(*_load_options()).where(PendingSlotAssignment.id == assignment_id)
    )
    return result.scalar_one_or_none()


async def create_pending_assignment(
    db: AsyncSession,
    *,
    spool_id: int,
    printer_id: int | None,
    timeout_seconds: int,
    source: str = "ui",
) -> PendingSlotAssignment:
    """Mark ``spool_id`` for the next loaded slot. Replaces an earlier pending mark on the same spool."""
    spool = (await db.execute(select(Spool).where(Spool.id == spool_id))).scalar_one_or_none()
    if spool is None:
        raise PendingAssignmentError("Spool not found", 404)
    if spool.archived_at is not None:
        raise PendingAssignmentError("Cannot assign an archived spool", 400)
    if printer_id is not None:
        printer = (await db.execute(select(Printer).where(Printer.id == printer_id))).scalar_one_or_none()
        if printer is None:
            raise PendingAssignmentError("Printer not found", 404)

    previous = await db.execute(
        select(PendingSlotAssignment).where(
            PendingSlotAssignment.spool_id == spool_id,
            PendingSlotAssignment.status == STATUS_PENDING,
        )
    )
    for old in previous.scalars().all():
        old.status = STATUS_CANCELLED
        old.completed_at = datetime.now(timezone.utc)

    assignment = PendingSlotAssignment(
        spool_id=spool_id,
        printer_id=printer_id,
        source=source,
        status=STATUS_PENDING,
        timeout_seconds=timeout_seconds,
    )
    db.add(assignment)
    await db.commit()
    # Reload with spool + printer so the response and the broadcast carry names.
    assignment = await get_assignment(db, assignment.id) or assignment
    logger.info(
        "Pending slot assignment %d: spool %d waits for the next loaded slot on printer %s (timeout %ds)",
        assignment.id,
        spool_id,
        printer_id if printer_id is not None else "any",
        timeout_seconds,
    )
    await _broadcast("created", assignment)
    return assignment


async def cancel_pending_assignment(db: AsyncSession, assignment_id: int) -> PendingSlotAssignment | None:
    """Cancel a pending request. Returns None when there is no pending row with that id."""
    result = await db.execute(
        select(PendingSlotAssignment)
        .options(*_load_options())
        .where(PendingSlotAssignment.id == assignment_id, PendingSlotAssignment.status == STATUS_PENDING)
    )
    assignment = result.scalar_one_or_none()
    if assignment is None:
        return None
    assignment.status = STATUS_CANCELLED
    assignment.completed_at = datetime.now(timezone.utc)
    await db.commit()
    logger.info("Pending slot assignment %d cancelled", assignment_id)
    await _broadcast("cancelled", assignment)
    return assignment


def _spool_identifiers(spool: Spool) -> tuple[str, str]:
    """The spool's RFID identity, with the firmware's all-zero sentinels treated as absent."""
    tag_uid = spool.tag_uid or ""
    tray_uuid = spool.tray_uuid or ""
    if not is_valid_tag(tag_uid, tray_uuid):
        return "", ""
    return tag_uid, tray_uuid


def _same_identity(spool_tag: str, spool_uuid: str, slot_tag: str, slot_uuid: str) -> bool:
    """True when the spool's tag/UUID matches what the tray reports (case-insensitive)."""
    if spool_uuid and slot_uuid:
        return spool_uuid.upper() == slot_uuid.upper()
    if spool_tag and slot_tag:
        return spool_tag.upper() == slot_tag.upper()
    return False


async def _pick_candidate(
    db: AsyncSession, candidates: list[PendingSlotAssignment], tray: dict
) -> tuple[PendingSlotAssignment | None, bool]:
    """Choose the request this tray completes. Second value: link the tray's tag to the spool.

    Rules, oldest request first:
    * tray without a readable tag (generic spool): first candidate wins.
    * tray with a tag and a spool that has one: they must be the same spool.
    * tray with a tag and a spool without one: the request wins unless the tag
      already belongs to another inventory spool — then the RFID auto-assign
      path owns this tray and the request keeps waiting.
    """
    slot_tag = tray.get("tag_uid") or ""
    slot_uuid = tray.get("tray_uuid") or ""
    slot_has_tag = is_valid_tag(slot_tag, slot_uuid)

    tag_owner: Spool | None = None
    if slot_has_tag:
        tag_owner = await get_spool_by_tag(db, slot_tag, slot_uuid)

    for candidate in candidates:
        spool = candidate.spool
        if spool is None or spool.archived_at is not None:
            continue
        if not slot_has_tag:
            return candidate, False
        spool_tag, spool_uuid = _spool_identifiers(spool)
        if spool_tag or spool_uuid:
            if _same_identity(spool_tag, spool_uuid, slot_tag, slot_uuid):
                return candidate, False
            continue
        if tag_owner is not None and tag_owner.id != spool.id:
            continue
        return candidate, tag_owner is None
    return None, False


async def try_complete_pending_assignment(
    db: AsyncSession,
    *,
    printer_id: int,
    ams_id: int,
    tray_id: int,
    tray: dict,
) -> PendingSlotAssignment | None:
    """Complete the oldest matching request for a tray that just reported filament.

    Returns the completed row, or None when nothing was waiting for this tray.
    The caller must already have established that the slot has no assignment.
    """
    await expire_stale(db)
    result = await db.execute(
        select(PendingSlotAssignment)
        .options(*_load_options())
        .where(
            PendingSlotAssignment.status == STATUS_PENDING,
            (PendingSlotAssignment.printer_id == printer_id) | (PendingSlotAssignment.printer_id.is_(None)),
        )
        .order_by(PendingSlotAssignment.created_at.asc(), PendingSlotAssignment.id.asc())
    )
    candidates = list(result.scalars().all())
    if not candidates:
        return None

    assignment, link_tag = await _pick_candidate(db, candidates, tray)
    if assignment is None:
        logger.info(
            "Printer %d AMS%d-T%d loaded but no pending request matches (tag=%s uuid=%s)",
            printer_id,
            ams_id,
            tray_id,
            tray.get("tag_uid") or "",
            tray.get("tray_uuid") or "",
        )
        return None

    spool = assignment.spool
    if link_tag:
        await link_tag_to_inventory_spool(db, spool, tray)

    # Same handler the slot dialog calls: upsert + MQTT filament settings + broadcast.
    from backend.app.api.routes.inventory import assign_spool
    from backend.app.schemas.spool import SpoolAssignmentCreate

    try:
        await assign_spool(
            data=SpoolAssignmentCreate(spool_id=spool.id, printer_id=printer_id, ams_id=ams_id, tray_id=tray_id),
            db=db,
            current_user=None,
        )
    except Exception:
        logger.exception(
            "Pending slot assignment %d: assigning spool %d to printer %d AMS%d-T%d failed",
            assignment.id,
            spool.id,
            printer_id,
            ams_id,
            tray_id,
        )
        return None

    assignment.status = STATUS_COMPLETED
    assignment.assigned_printer_id = printer_id
    assignment.assigned_ams_id = ams_id
    assignment.assigned_tray_id = tray_id
    assignment.completed_at = datetime.now(timezone.utc)
    await db.commit()
    logger.info(
        "Pending slot assignment %d completed: spool %d -> printer %d AMS%d-T%d",
        assignment.id,
        spool.id,
        printer_id,
        ams_id,
        tray_id,
    )
    taker = (await db.execute(select(Printer.name).where(Printer.id == printer_id))).scalar_one_or_none()
    await _broadcast("completed", assignment, printer_name=taker)
    return assignment
