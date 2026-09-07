"""Queue dispatch to a Klipper printer (Voron patch series).

The scheduler's ``_start_print`` is written around Bambu's FTPS upload of a
sliced 3MF followed by an MQTT ``project_file`` command. Moonraker wants a
plain ``.gcode`` file in its ``gcodes`` root and a ``print/start`` call. This
module holds the two pieces the scheduler needs so its own diff stays a few
lines: the remote filename to use, and the upload itself (including
unpacking the plate G-code out of a sliced 3MF).

Sliced 3MF layout (Bambu Studio and OrcaSlicer alike)::

    Metadata/plate_1.gcode      # one per plate
    Metadata/plate_1.json
    Metadata/slice_info.config

A Voron slice from OrcaSlicer is normally exported as ``.gcode`` directly;
Bambuddy's library stores it as-is, and that case needs no unpacking.
"""

from __future__ import annotations

import logging
import re
import tempfile
import zipfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._ -]+")


def moonraker_remote_filename(filename: str, plate_id: int | None = None) -> str:
    """Name the file gets in Moonraker's ``gcodes`` root.

    ``foo.gcode.3mf`` / ``foo.3mf`` -> ``foo.gcode`` (plate 1) or
    ``foo_plate2.gcode`` (other plates). ``foo.gcode`` stays as it is. The
    ``.gcode`` suffix is what Klipper's file list and Bambuddy's expected-print
    matcher key on, so it is always present.
    """
    base = filename.rsplit("/", 1)[-1]
    for suffix in (".gcode.3mf", ".3mf", ".gcode"):
        if base.lower().endswith(suffix):
            base = base[: -len(suffix)]
            break
    base = _SAFE_NAME.sub("_", base).strip() or "print"
    if plate_id and plate_id > 1:
        base = f"{base}_plate{plate_id}"
    return f"{base}.gcode"


def extract_plate_gcode(threemf_path: Path, plate_id: int | None = None) -> Path:
    """Unpack ``Metadata/plate_<n>.gcode`` from a sliced 3MF into a temp file.

    Raises ``ValueError`` when the archive has no G-code for that plate — an
    unsliced project file, or a plate number the slicer never exported.
    """
    plate = plate_id or 1
    member = f"Metadata/plate_{plate}.gcode"
    with zipfile.ZipFile(threemf_path) as zf:
        names = set(zf.namelist())
        if member not in names:
            available = sorted(n for n in names if re.fullmatch(r"Metadata/plate_\d+\.gcode", n))
            if plate == 1 and len(available) == 1:
                member = available[0]
            else:
                raise ValueError(
                    f"{threemf_path.name} has no G-code for plate {plate}"
                    + (f" (found: {', '.join(available)})" if available else " (not sliced?)")
                )
        with tempfile.NamedTemporaryFile(prefix="bambuddy-klipper-", suffix=".gcode", delete=False) as tmp:
            with zf.open(member) as src:
                while True:
                    chunk = src.read(1024 * 1024)
                    if not chunk:
                        break
                    tmp.write(chunk)
            out = Path(tmp.name)
    return out


async def upload_to_moonraker(
    client: Any,
    file_path: Path,
    remote_filename: str,
    *,
    plate_id: int | None = None,
    progress_callback: Any = None,
    log_prefix: str = "",
) -> tuple[bool, str | None, Path | None]:
    """Upload ``file_path`` to the printer behind ``client`` as ``remote_filename``.

    Returns ``(uploaded, error_message, temp_path)``. ``temp_path`` is the
    unpacked G-code when the source was a 3MF; the caller deletes it once the
    dispatch is done (the scheduler already has that cleanup for injected
    files).
    """
    import asyncio

    if client is None:
        return False, "Printer is not connected", None

    temp_path: Path | None = None
    source = file_path
    if file_path.suffix.lower() == ".3mf":
        try:
            temp_path = await asyncio.to_thread(extract_plate_gcode, file_path, plate_id)
            source = temp_path
        except (ValueError, zipfile.BadZipFile, OSError) as exc:
            logger.error("%sCannot unpack G-code from %s: %s", log_prefix, file_path.name, exc)
            return False, f"Could not unpack G-code from {file_path.name}: {exc}", None

    try:
        await asyncio.to_thread(client.upload_file, source, remote_filename, progress_callback=progress_callback)
    except Exception as exc:  # noqa: BLE001 - surfaced as the queue item's error message
        logger.error("%sMoonraker upload of %s failed: %s", log_prefix, remote_filename, exc)
        return False, f"Upload to Moonraker failed: {exc}", temp_path

    logger.info("%sUploaded %s to Moonraker as %s", log_prefix, source.name, remote_filename)
    return True, None, temp_path
