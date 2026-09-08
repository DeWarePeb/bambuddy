"""Voron patch series (B5): everything that needs a hand, in one place.

Maintenance that is due and spools that are running low each have their own
page, so a full walk-through of Maintenance and Inventory was needed to know
whether anything wanted attention today. This endpoint gathers both into one
summary that the layout shows as a banner. Read-only; it reuses the same
calculations as the two pages, so the numbers always agree.

C2 added a third kind: a printer whose firmware is reporting a fault. That one
has no page of its own to walk through — the card says "offline", the same as
a printer someone switched off at the wall.
"""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.routes.maintenance import _get_printer_maintenance_internal, ensure_default_types
from backend.app.api.routes.settings import get_setting
from backend.app.core.auth import RequirePermissionIfAuthEnabled
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.models.printer import Printer
from backend.app.models.spool import Spool
from backend.app.models.user import User
from backend.app.services.printer_manager import printer_manager

router = APIRouter(prefix="/alerts", tags=["alerts"])

DEFAULT_LOW_STOCK_THRESHOLD_PCT = 20.0


async def _low_stock_threshold(db: AsyncSession) -> float:
    """The global inventory threshold (%), as the Inventory page reads it."""
    raw = await get_setting(db, "low_stock_threshold")
    try:
        return float(raw) if raw is not None else DEFAULT_LOW_STOCK_THRESHOLD_PCT
    except (TypeError, ValueError):
        return DEFAULT_LOW_STOCK_THRESHOLD_PCT


def spool_is_low(spool: Spool, default_threshold_pct: float) -> tuple[bool, float, float]:
    """Same rule as the Inventory page: remaining% below the spool's own
    threshold, or the global one when the spool has none. Returns
    (is_low, remaining_g, remaining_pct)."""
    label = float(spool.label_weight or 0)
    remaining_g = max(0.0, label - float(spool.weight_used or 0))
    remaining_pct = (remaining_g / label * 100.0) if label > 0 else 0.0
    threshold = spool.low_stock_threshold_pct if spool.low_stock_threshold_pct is not None else default_threshold_pct
    return remaining_pct < float(threshold), remaining_g, remaining_pct


@router.get("/summary")
async def get_alerts_summary(
    db: AsyncSession = Depends(get_db),
    _m: User | None = RequirePermissionIfAuthEnabled(Permission.MAINTENANCE_READ),
    _i: User | None = RequirePermissionIfAuthEnabled(Permission.INVENTORY_READ),
):
    """Maintenance due (or nearly), spools below their threshold, printers reporting a fault."""
    await ensure_default_types(db)

    maintenance_due: list[dict] = []
    maintenance_warning: list[dict] = []
    printers = (await db.execute(select(Printer).where(Printer.is_active.is_(True)))).scalars().all()
    for printer in printers:
        overview = await _get_printer_maintenance_internal(printer.id, db, commit=False)
        for item in overview.maintenance_items:
            if not (item.is_due or item.is_warning):
                continue
            entry = {
                "item_id": item.id,
                "printer_id": printer.id,
                "printer_name": printer.name,
                "name": item.maintenance_type_name,
                "hours_until_due": item.hours_until_due,
                "days_until_due": item.days_until_due,
            }
            (maintenance_due if item.is_due else maintenance_warning).append(entry)

    # Voron patch series (C2): a Klipper printer sitting in shutdown is the most
    # urgent thing this banner can carry, and it is invisible everywhere else —
    # the card says "offline" like a printer that is merely switched off.
    # Only faults, never "unreachable": a powered-down printer is not an alert.
    printer_faults: list[dict] = []
    for printer in printers:
        state = printer_manager.get_status(printer.id)
        if state is None or state.connected:
            continue
        klippy = (state.raw_data or {}).get("klippy")
        if not isinstance(klippy, dict) or klippy.get("state") not in ("shutdown", "error"):
            continue
        printer_faults.append(
            {
                "printer_id": printer.id,
                "printer_name": printer.name,
                "state": klippy.get("state"),
                "message": klippy.get("message"),
            }
        )

    threshold = await _low_stock_threshold(db)
    low_stock: list[dict] = []
    spools = (await db.execute(select(Spool).where(Spool.archived_at.is_(None)).order_by(Spool.id))).scalars().all()
    for spool in spools:
        is_low, remaining_g, remaining_pct = spool_is_low(spool, threshold)
        if not is_low:
            continue
        low_stock.append(
            {
                "spool_id": spool.id,
                "material": spool.material,
                "brand": spool.brand,
                "color_name": spool.color_name,
                "remaining_g": round(remaining_g),
                "remaining_pct": round(remaining_pct, 1),
            }
        )
    low_stock.sort(key=lambda s: s["remaining_pct"])

    return {
        "maintenance_due": maintenance_due,
        "printer_faults": printer_faults,
        "maintenance_warning": maintenance_warning,
        "low_stock": low_stock,
        "low_stock_threshold_pct": threshold,
        "total": len(maintenance_due) + len(low_stock) + len(printer_faults),
    }
