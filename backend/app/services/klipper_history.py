"""Import a Klipper printer's past jobs from Moonraker's history (Voron patch series).

Moonraker keeps every job it ever ran in ``server/history/list`` — filename,
start and end time, duration, status and the slicer metadata. Bambuddy learns
about a print only while it is watching, so adding an existing Klipper machine
gives an empty archive next to a Bambu that shows months of history. This walks
Moonraker's list once and writes the missing rows.

Identity is ``subtask_id = "moonraker:<job_id>"``. That column already exists for
exactly this purpose — "is this the same print I saw before" — it is indexed,
and no Klipper client ever sets it otherwise, so a re-import is a cheap set
difference rather than a scan of every archive's JSON.

Imported rows carry no file: Moonraker still has the G-code for most of them,
but pulling hundreds of multi-megabyte files to fill a history view is not a
trade anyone asked for. They are marked the way upstream marks a print whose
3MF could not be fetched (``no_3mf_available``), which the archive UI already
understands.

Filament is booked in grams only when the slicer wrote a weight. Moonraker
always reports millimetres of filament, and converting those needs a diameter
and a density this code cannot know — a guess would flow straight into the cost
column of every report. The millimetres are kept in ``extra_data`` instead.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from backend.app.models.archive import PrintArchive
from backend.app.services.moonraker_client import MoonrakerClient

logger = logging.getLogger(__name__)

# Moonraker job status -> the archive's own vocabulary. "in_progress" is
# deliberately absent: that job is either running right now (the live path owns
# it) or was interrupted by a crash Moonraker never got to record.
_STATUS_MAP = {
    "completed": "completed",
    "cancelled": "aborted",
    "interrupted": "aborted",
    "error": "failed",
    "klippy_shutdown": "failed",
    "klippy_disconnect": "failed",
    "server_exit": "failed",
}

# One request per page. Moonraker answers the whole list in one go if asked, but
# a printer with thousands of jobs would then build a single huge response.
_PAGE_SIZE = 100

SUBTASK_PREFIX = "moonraker:"


def _subtask_id(job_id: Any) -> str:
    return f"{SUBTASK_PREFIX}{job_id}"


def _at(value: Any) -> datetime | None:
    """Moonraker timestamps are Unix seconds; the archive stores naive UTC."""
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    if seconds <= 0:
        return None
    return datetime.fromtimestamp(seconds, tz=timezone.utc).replace(tzinfo=None)


def _int(value: Any) -> int | None:
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def fetch_history(client: MoonrakerClient, limit: int) -> list[dict[str, Any]]:
    """Page through ``server/history/list``, newest first, up to ``limit`` jobs."""
    jobs: list[dict[str, Any]] = []
    start = 0
    while len(jobs) < limit:
        page = min(_PAGE_SIZE, limit - len(jobs))
        result = client._get(f"server/history/list?limit={page}&start={start}&order=desc")
        batch = result.get("jobs") or []
        if not isinstance(batch, list) or not batch:
            break
        jobs.extend(job for job in batch if isinstance(job, dict))
        if len(batch) < page:
            break
        start += page
    return jobs[:limit]


def archive_from_job(printer_id: int, job: dict[str, Any]) -> PrintArchive | None:
    """Turn one Moonraker history entry into an archive row, or None to skip it."""
    job_id = job.get("job_id")
    status = _STATUS_MAP.get(str(job.get("status") or "").lower())
    if job_id is None or status is None:
        return None

    filename = str(job.get("filename") or "").strip()
    base = filename.rsplit("/", 1)[-1] or "print.gcode"
    metadata = job.get("metadata") if isinstance(job.get("metadata"), dict) else {}

    started_at = _at(job.get("start_time"))
    completed_at = _at(job.get("end_time"))
    # print_duration excludes pauses and heat-up; total_duration is wall clock.
    # The archive's print_time_seconds is compared against slicer estimates
    # elsewhere, so the printing time is the honest one.
    duration = _int(job.get("print_duration")) or _int(job.get("total_duration"))

    extra: dict[str, Any] = {
        "no_3mf_available": True,
        "no_3mf_reason": "imported_from_moonraker",
        "imported_from_moonraker": True,
        "moonraker_job_id": str(job_id),
        "moonraker_filename": filename,
        "moonraker_status": job.get("status"),
    }
    filament_mm = _float(job.get("filament_used"))
    if filament_mm:
        extra["filament_used_mm"] = round(filament_mm, 2)
    total_duration = _int(job.get("total_duration"))
    if total_duration:
        extra["total_duration_seconds"] = total_duration

    return PrintArchive(
        printer_id=printer_id,
        filename=base,
        file_path="",  # No file: see the module docstring.
        file_size=0,
        print_name=base[:-6] if base.lower().endswith(".gcode") else base,
        print_time_seconds=duration,
        filament_used_grams=_float(metadata.get("filament_weight_total")),
        filament_type=(str(metadata.get("filament_type") or "").split(";")[0].strip() or None),
        total_layers=_int(metadata.get("layer_count")),
        layer_height=_float(metadata.get("layer_height")),
        nozzle_diameter=_float(metadata.get("nozzle_diameter")),
        bed_temperature=_int(metadata.get("first_layer_bed_temp")),
        nozzle_temperature=_int(metadata.get("first_layer_extr_temp")),
        status=status,
        started_at=started_at,
        completed_at=completed_at,
        subtask_id=_subtask_id(job_id),
        extra_data=extra,
    )


async def import_history(db, printer_id: int, client: MoonrakerClient, limit: int = 500) -> dict[str, int]:
    """Import up to ``limit`` of the printer's most recent Moonraker jobs.

    Returns counts of what happened. Safe to run repeatedly: jobs already
    imported are recognised by their ``subtask_id`` and left alone, so a second
    run only picks up what has been printed since.
    """
    jobs = await asyncio.to_thread(fetch_history, client, limit)
    if not jobs:
        return {"found": 0, "imported": 0, "skipped": 0}

    candidates: dict[str, PrintArchive] = {}
    skipped = 0
    for job in jobs:
        archive = archive_from_job(printer_id, job)
        if archive is None:
            skipped += 1
            continue
        # A job id repeated inside one response would otherwise insert twice.
        candidates.setdefault(str(archive.subtask_id), archive)

    if candidates:
        existing = set(
            (
                await db.execute(
                    select(PrintArchive.subtask_id).where(
                        PrintArchive.printer_id == printer_id,
                        PrintArchive.subtask_id.in_(list(candidates)),
                    )
                )
            )
            .scalars()
            .all()
        )
    else:
        existing = set()

    imported = 0
    for subtask_id, archive in candidates.items():
        if subtask_id in existing:
            skipped += 1
            continue
        db.add(archive)
        imported += 1

    if imported:
        await db.commit()

    logger.info(
        "Klipper history: printer %s — %s job(s) read, %s imported, %s skipped",
        printer_id,
        len(jobs),
        imported,
        skipped,
    )
    return {"found": len(jobs), "imported": imported, "skipped": skipped}
