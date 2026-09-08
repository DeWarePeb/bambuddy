"""Read-only TV / kiosk feed for token-authenticated wall displays (voron B10).

The ``/tv`` page inside the SPA runs on the ordinary printers API behind a JWT.
A screen taped to the workshop wall has no login, so — exactly like the Cam Wall
— it authenticates with a long-lived token carried in the URL.

It gets its own ``tv`` scope rather than reusing ``camwall`` because the two
feeds are trusted with different things. A Cam Wall tile is video plus a state
chip and deliberately never names the part; a TV tile is the other way round:
the file on the bed, progress, layer, ETA and the spool feeding the hotend, with
video as a thumbnail. Folding that into ``camwall`` would silently widen every
wall token already handed out.

What is *not* served is the same list the Cam Wall withholds: ``serial_number``
and ``ip_address`` never leave this endpoint, so a URL on a wall cannot be read
off the screen into printer credentials.

One call for the whole wall rather than the SPA's printers + N status requests:
a kiosk polls on a fixed interval with no WebSocket to invalidate anything, and
N+1 requests every few seconds is a poor trade for a screen nobody touches.
"""

import logging

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import RequireTvTokenIfAuthEnabled
from backend.app.core.database import get_db
from backend.app.models.printer import Printer
from backend.app.services.printer_manager import printer_manager

_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tv", tags=["tv"])

# Mirrors resolveActiveTray() in frontend/src/utils/tvMode.ts: tray_now is a
# global tray id — ams*4+slot for a regular AMS, the unit id itself (128+) for
# an AMS-HT, 254 for the external spool, 255 for "nothing loaded".
_EXTERNAL_TRAY_BASE = 254
_NOTHING_LOADED = 255


def _tray_fields(tray: dict) -> dict | None:
    """The four fields a TV tile draws for the loaded spool, or None when the
    slot holds no filament (a loaded-but-empty slot is not a spool)."""
    if not tray.get("tray_type"):
        return None
    return {
        "tray_type": tray.get("tray_type"),
        "tray_sub_brands": tray.get("tray_sub_brands"),
        "tray_color": tray.get("tray_color"),
        "remain": tray.get("remain"),
    }


def _active_tray(state) -> dict | None:
    """The tray feeding the hotend right now, resolved server-side.

    The signed-in page resolves this in the browser from the full AMS payload;
    a token holder gets only the answer, not the other slots.
    """
    tray_now = getattr(state, "tray_now", None)
    if tray_now is None or tray_now < 0 or tray_now == _NOTHING_LOADED:
        return None
    raw = getattr(state, "raw_data", None) or {}

    if tray_now >= _EXTERNAL_TRAY_BASE:
        vt_trays = [t for t in (raw.get("vt_tray") or []) if isinstance(t, dict)]
        if not vt_trays:
            return None
        index = tray_now - _EXTERNAL_TRAY_BASE
        return _tray_fields(vt_trays[index] if index < len(vt_trays) else vt_trays[0])

    for unit in raw.get("ams") or []:
        if not isinstance(unit, dict):
            continue
        try:
            unit_id = int(unit.get("id", 0))
        except (TypeError, ValueError):
            continue
        for tray in unit.get("tray") or []:
            if not isinstance(tray, dict):
                continue
            try:
                slot_id = int(tray.get("id", 0))
            except (TypeError, ValueError):
                continue
            # AMS-HT units carry a single tray and use their own id as the
            # global id; a regular AMS numbers its four slots within the unit.
            global_id = unit_id if unit_id >= 128 else unit_id * 4 + slot_id
            if global_id == tray_now:
                return _tray_fields(tray)
    return None


@router.get("/printers")
async def list_tv_printers(
    _: None = RequireTvTokenIfAuthEnabled,
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """Every active printer plus the fields one TV tile draws.

    Ordered by name so tile positions stay put across polls — a wall that
    reshuffles itself is unusable to watch. Inactive printers are left out
    here rather than client-side, matching what the signed-in page shows.
    """
    result = await db.execute(select(Printer).where(Printer.is_active.is_(True)).order_by(Printer.name))
    printers = list(result.scalars().all())

    payload: list[dict] = []
    for printer in printers:
        state = printer_manager.get_status(printer.id)
        entry: dict = {
            "id": printer.id,
            "name": printer.name,
            "model": printer.model,
            "location": printer.location,
            "provider": printer.provider,
            "external_camera_enabled": printer.external_camera_enabled,
            "camera_rotation": printer.camera_rotation or 0,
            # Mirrors get_printer_status(): no state object at all means the
            # printer was never connected this run; a state object still has
            # to be asked whether its link is currently up.
            "connected": bool(state and state.connected),
            "state": None,
            "current_print": None,
            "subtask_name": None,
            "gcode_file": None,
            "progress": None,
            "remaining_time": None,
            "layer_num": None,
            "total_layers": None,
            # Codes only, like the Cam Wall feed — enough for the tile to paint
            # its error chip, and the same shape the signed-in page classifies.
            "hms_errors": [],
            "tray": None,
        }
        if state is not None:
            entry.update(
                {
                    "state": state.state,
                    "current_print": state.current_print,
                    "subtask_name": state.subtask_name,
                    "gcode_file": state.gcode_file,
                    "progress": state.progress,
                    "remaining_time": state.remaining_time,
                    "layer_num": state.layer_num,
                    "total_layers": state.total_layers,
                    "hms_errors": [
                        {
                            "code": e.code,
                            "attr": e.attr,
                            "module": e.module,
                            "severity": e.severity,
                            "actions": e.actions or [],
                        }
                        for e in (state.hms_errors or [])
                    ],
                    "tray": _active_tray(state),
                }
            )
        payload.append(entry)

    return payload
