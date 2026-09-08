"""Give a Klipper print started outside Bambuddy a real archive (Voron patch series).

When a print is started from Mainsail, the print-start handler creates a
"fallback" archive with no file (a Bambu would be asked for its 3MF over
FTPS; Klipper has no FTPS). This pulls the G-code out of Moonraker instead,
stores it under the archive directory like a 3MF would be, and fills print
time, filament and colour from the slicer comments — so the archive card,
the usage tracker and the statistics see the same data a queued print has.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime
from pathlib import Path

from sqlalchemy import select

from backend.app.core.config import settings
from backend.app.core.database import async_session
from backend.app.models.archive import PrintArchive
from backend.app.services.gcode_metadata import parse_gcode_metadata
from backend.app.services.moonraker_client import MoonrakerClient

logger = logging.getLogger(__name__)

_SAFE = re.compile(r"[^A-Za-z0-9._ -]+")


async def attach_gcode_to_archive(printer_manager, printer_id: int, archive_id: int, filename: str) -> bool:
    """Download ``filename`` from the printer's Moonraker and attach it to ``archive_id``.

    Returns True when the archive now has a file. Safe to call for any
    printer: non-Klipper clients and missing files simply leave the archive
    as it was.
    """
    client = printer_manager.get_client(printer_id)
    if not isinstance(client, MoonrakerClient) or not filename:
        return False

    try:
        content = await asyncio.to_thread(client.download_file, filename)
    except Exception as exc:  # noqa: BLE001 - the fallback archive stays; nothing else to do
        logger.warning("Klipper archive: could not fetch %s from printer %s: %s", filename, printer_id, exc)
        return False

    base = _SAFE.sub("_", filename.rsplit("/", 1)[-1]).strip()
    if not base or base.strip(".") == "":
        # "." and ".." survive the character filter (dots are legal in a name)
        # and would make the join climb out of the archive directory.
        base = "print.gcode"
    stem = base[:-6] if base.lower().endswith(".gcode") else base
    archive_dir = settings.archive_dir / str(printer_id) / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{stem}"
    archive_dir.mkdir(parents=True, exist_ok=True)
    dest: Path = (
        archive_dir / base
    )  # SEC-PATH-OK: base is filename.rsplit("/", 1)[-1] with everything outside [A-Za-z0-9._ -] replaced and dot-only names rejected above — no separator and no ".." survives
    await asyncio.to_thread(dest.write_bytes, content)
    meta = await asyncio.to_thread(parse_gcode_metadata, dest)

    async with async_session() as db:
        archive = (await db.execute(select(PrintArchive).where(PrintArchive.id == archive_id))).scalar_one_or_none()
        if archive is None:
            dest.unlink(missing_ok=True)
            return False
        archive.file_path = str(dest.relative_to(settings.base_dir))
        archive.file_size = dest.stat().st_size
        archive.filename = base
        if meta.get("print_time_seconds") and not archive.print_time_seconds:
            archive.print_time_seconds = meta["print_time_seconds"]
        if meta.get("filament_used_grams") and not archive.filament_used_grams:
            archive.filament_used_grams = meta["filament_used_grams"]
        if meta.get("filament_type") and not archive.filament_type:
            archive.filament_type = meta["filament_type"]
        if meta.get("filament_color") and not archive.filament_color:
            archive.filament_color = meta["filament_color"]
        extra = dict(archive.extra_data or {})
        extra.pop("no_3mf_available", None)
        extra.pop("no_3mf_reason", None)
        extra["klipper_gcode_fetched"] = True
        if meta:
            extra["gcode_metadata"] = {k: v for k, v in meta.items() if k not in ("thumbnails",)}
        archive.extra_data = extra
        await db.commit()

    logger.info("Klipper archive: attached %s (%d bytes) to archive %s", base, len(content), archive_id)
    return True
