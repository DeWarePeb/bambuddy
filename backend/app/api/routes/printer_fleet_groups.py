"""Printer fleet groups for the farm command center (voron B11).

CRUD over a name plus a set of printer ids. Reading needs ``printers:read``,
writing ``printers:update`` — a fleet group is printer metadata, so it borrows
the printer permissions rather than inventing its own.

Ported from vmhomelab/printbuddy, rewritten for Bambuddy 1.2.5.x. Two
deliberate differences: a duplicate name answers 400 instead of letting the
unique constraint surface as a 500, and both write paths reject unknown printer
ids before touching the table.
"""

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.core.auth import RequirePermissionIfAuthEnabled
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.models.printer import Printer
from backend.app.models.printer_fleet_group import PrinterFleetGroup, PrinterFleetGroupMember
from backend.app.schemas.printer_fleet_group import (
    PrinterFleetGroupCreate,
    PrinterFleetGroupResponse,
    PrinterFleetGroupUpdate,
)

router = APIRouter(prefix="/printer-fleet-groups", tags=["printer-fleet-groups"])


def _to_response(group: PrinterFleetGroup) -> PrinterFleetGroupResponse:
    return PrinterFleetGroupResponse(
        id=group.id,
        name=group.name,
        color=group.color,
        sort_order=group.sort_order,
        printer_ids=[member.printer_id for member in sorted(group.members, key=lambda item: item.printer_id)],
        created_at=group.created_at,
        updated_at=group.updated_at,
    )


async def _validate_printer_ids(db: AsyncSession, printer_ids: list[int]) -> list[int]:
    """Drop duplicates, keep order, and 400 on any id that is not a printer."""
    unique_ids = list(dict.fromkeys(printer_ids))
    if not unique_ids:
        return []
    result = await db.execute(select(Printer.id).where(Printer.id.in_(unique_ids)))
    found = set(result.scalars().all())
    missing = [printer_id for printer_id in unique_ids if printer_id not in found]
    if missing:
        raise HTTPException(400, f"Unknown printer id(s): {', '.join(str(item) for item in missing)}")
    return unique_ids


async def _ensure_name_free(db: AsyncSession, name: str, exclude_id: int | None = None) -> None:
    """Reject a duplicate name here so the caller gets 400, not a 500 from the constraint."""
    query = select(PrinterFleetGroup.id).where(func.lower(PrinterFleetGroup.name) == name.lower())
    if exclude_id is not None:
        query = query.where(PrinterFleetGroup.id != exclude_id)
    if (await db.execute(query)).scalar_one_or_none() is not None:
        raise HTTPException(400, f"A printer fleet group named '{name}' already exists")


async def _load_group(db: AsyncSession, group_id: int) -> PrinterFleetGroup:
    result = await db.execute(
        select(PrinterFleetGroup)
        .options(selectinload(PrinterFleetGroup.members))
        .where(PrinterFleetGroup.id == group_id)
    )
    group = result.scalar_one_or_none()
    if group is None:
        raise HTTPException(404, "Printer fleet group not found")
    return group


@router.get("/", response_model=list[PrinterFleetGroupResponse])
async def list_printer_fleet_groups(
    _=RequirePermissionIfAuthEnabled(Permission.PRINTERS_READ),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(PrinterFleetGroup)
        .options(selectinload(PrinterFleetGroup.members))
        .order_by(PrinterFleetGroup.sort_order, PrinterFleetGroup.name)
    )
    return [_to_response(group) for group in result.scalars().all()]


@router.post("/", response_model=PrinterFleetGroupResponse)
async def create_printer_fleet_group(
    data: PrinterFleetGroupCreate,
    _=RequirePermissionIfAuthEnabled(Permission.PRINTERS_UPDATE),
    db: AsyncSession = Depends(get_db),
):
    name = data.name.strip()
    if not name:
        raise HTTPException(400, "Group name is required")
    await _ensure_name_free(db, name)
    printer_ids = await _validate_printer_ids(db, data.printer_ids)
    group = PrinterFleetGroup(name=name, color=data.color, sort_order=data.sort_order)
    group.members = [PrinterFleetGroupMember(printer_id=printer_id) for printer_id in printer_ids]
    db.add(group)
    await db.commit()
    return _to_response(await _load_group(db, group.id))


@router.put("/{group_id}", response_model=PrinterFleetGroupResponse)
async def update_printer_fleet_group(
    group_id: int,
    data: PrinterFleetGroupUpdate,
    _=RequirePermissionIfAuthEnabled(Permission.PRINTERS_UPDATE),
    db: AsyncSession = Depends(get_db),
):
    group = await _load_group(db, group_id)
    name = data.name.strip()
    if not name:
        raise HTTPException(400, "Group name is required")
    await _ensure_name_free(db, name, exclude_id=group.id)
    printer_ids = await _validate_printer_ids(db, data.printer_ids)
    group.name = name
    group.color = data.color
    group.sort_order = data.sort_order
    await db.execute(delete(PrinterFleetGroupMember).where(PrinterFleetGroupMember.group_id == group.id))
    group.members = [PrinterFleetGroupMember(group_id=group.id, printer_id=printer_id) for printer_id in printer_ids]
    await db.commit()
    return _to_response(await _load_group(db, group.id))


@router.delete("/{group_id}", status_code=204)
async def delete_printer_fleet_group(
    group_id: int,
    _=RequirePermissionIfAuthEnabled(Permission.PRINTERS_UPDATE),
    db: AsyncSession = Depends(get_db),
):
    group = await _load_group(db, group_id)
    await db.delete(group)
    await db.commit()
    return Response(status_code=204)
