"""Timelapses from moonraker-timelapse (Voron patch series, C4).

A Bambu print that recorded a timelapse ends with the video attached to its
archive, and — since #1397 — with the finish photo pulled out of the last
frame. A Klipper print ended with neither, because the client reported
``timelapse_was_active: False`` unconditionally and the whole scan is gated on
that flag. Plenty of Klipper users run moonraker-timelapse; their videos were
simply left on the printer.

Nothing about the scan itself needs to change. Upstream's flow — snapshot the
video directory at print start, diff it at completion, download, attach,
delete from the printer — is a clock-free design that works just as well
against Moonraker's ``timelapse`` file root as against a Bambu's SD card. This
module is only the four I/O calls that flow makes, spoken to Moonraker instead
of FTPS.

Paths: entries carry the file's path *relative to the timelapse root*, which
is what every Moonraker file endpoint wants. They never reach a filesystem —
the video is written under the archive directory by ``attach_timelapse``,
which does its own safe-join on the basename.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)

_TIMEOUT = 10.0
_DOWNLOAD_TIMEOUT = 300.0
# Same extensions the Bambu scan accepts, plus nothing: moonraker-timelapse
# writes MP4, and a stray file of another kind is not this print's video.
VIDEO_EXTENSIONS = (".mp4", ".avi", ".mov")


def _headers(printer) -> dict[str, str]:
    token = getattr(printer, "auth_token", None)
    return {"X-Api-Key": token} if token else {}


def _base_url(printer) -> str | None:
    url = getattr(printer, "api_url", None)
    return url.rstrip("/") if url else None


def is_klipper(printer) -> bool:
    return getattr(printer, "provider", "bambu") == "klipper"


async def list_videos(printer) -> list[dict[str, Any]]:
    """Videos in Moonraker's ``timelapse`` root, shaped like the FTP listing.

    An empty list is also what a printer without moonraker-timelapse gives:
    the root does not exist and Moonraker answers 404. That is the same answer
    as "no videos yet", and the caller treats both the same way.
    """
    base_url = _base_url(printer)
    if not base_url:
        return []
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.get(f"{base_url}/server/files/list?root=timelapse", headers=_headers(printer))
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:  # noqa: BLE001 - no timelapse root, or the host is down
        logger.debug("[TIMELAPSE] Moonraker listing failed for %s: %s", getattr(printer, "name", "?"), exc)
        return []

    files = payload.get("result", payload)
    if isinstance(files, dict):
        files = files.get("files") or []
    videos: list[dict[str, Any]] = []
    for entry in files if isinstance(files, list) else []:
        if not isinstance(entry, dict):
            continue
        path = str(entry.get("path") or entry.get("filename") or "")
        if not path.lower().endswith(VIDEO_EXTENSIONS):
            continue
        videos.append(
            {
                "name": path.rsplit("/", 1)[-1],
                "path": path,
                "is_directory": False,
                "size": int(entry.get("size") or 0),
                "modified": entry.get("modified"),
            }
        )
    return videos


async def download(printer, remote_path: str, expected_size: int | None = None) -> bytes | None:
    """Fetch one video. Returns None on any short or failed transfer.

    The length check is what makes the delete afterwards safe: a truncated
    download must leave the printer's copy alone so the next round can retry.
    """
    base_url = _base_url(printer)
    if not base_url:
        return None
    try:
        async with httpx.AsyncClient(timeout=_DOWNLOAD_TIMEOUT) as client:
            response = await client.get(
                f"{base_url}/server/files/timelapse/{quote(remote_path.lstrip('/'), safe='/')}",
                headers=_headers(printer),
            )
            response.raise_for_status()
            content = response.content
    except Exception as exc:  # noqa: BLE001
        logger.warning("[TIMELAPSE] Moonraker download of %s failed: %s", remote_path, exc)
        return None
    if expected_size is not None and len(content) != expected_size:
        logger.warning(
            "[TIMELAPSE] Moonraker served %s bytes of %s, expected %s — treating as a short read",
            len(content),
            remote_path,
            expected_size,
        )
        return None
    return content


async def settled(printer, remote_path: str, size: int, *, wait: float = 2.0) -> bool:
    """True once the file has stopped growing.

    moonraker-timelapse renders with ffmpeg after the print ends, and the file
    is listed while it is still being written. A video downloaded mid-render
    matches its own (short) listing and would pass the length check, so the
    original must not be deleted until the size has held still.
    """
    await asyncio.sleep(wait)
    for entry in await list_videos(printer):
        if entry["path"] == remote_path:
            return int(entry["size"]) == size
    # Gone from the listing between the download and now: nothing left to
    # protect, and the bytes in hand are all there will ever be.
    return True


async def delete(printer, remote_path: str) -> bool:
    """Remove the video from the printer, once it is safely attached."""
    base_url = _base_url(printer)
    if not base_url:
        return False
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.delete(
                f"{base_url}/server/files/timelapse/{quote(remote_path.lstrip('/'), safe='/')}",
                headers=_headers(printer),
            )
            response.raise_for_status()
    except Exception as exc:  # noqa: BLE001 - the file stays; the next diff filters it by name
        logger.warning("[TIMELAPSE] Could not delete %s from Moonraker: %s", remote_path, exc)
        return False
    return True
