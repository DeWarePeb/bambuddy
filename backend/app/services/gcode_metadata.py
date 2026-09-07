"""Metadata from a raw G-code file's slicer comments (Voron patch series).

Bambuddy's library reads print time, filament and layer data from a sliced
3MF (``ThreeMFParser``). A Klipper printer is fed plain ``.gcode``, whose
metadata lives in ``;`` comments instead: OrcaSlicer / Bambu Studio put a
summary near the top and the full settings block at the very end;
PrusaSlicer / SuperSlicer put everything at the end. This parser reads the
head and tail of the file and returns the same keys the 3MF parser produces
so the library card, the queue estimate and the archive stats do not care
which slicer wrote the file.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_HEAD_BYTES = 256 * 1024
_TAIL_BYTES = 256 * 1024

# "1d 2h 13m 2s", "13m 2s", "2h 0m 5s"
_DURATION = re.compile(r"(?:(\d+)d\s*)?(?:(\d+)h\s*)?(?:(\d+)m\s*)?(?:(\d+)s)?")

_TIME_LINES = (
    re.compile(r";\s*total estimated time:\s*([0-9dhms ]+)", re.I),  # Orca / Bambu Studio
    re.compile(r";\s*estimated printing time \(normal mode\)\s*=\s*([0-9dhms ]+)", re.I),  # Prusa family
    re.compile(r";\s*model printing time:\s*([0-9dhms ]+)", re.I),
)


def _duration_seconds(text: str) -> int | None:
    m = _DURATION.fullmatch(text.strip())
    if not m or not any(m.groups()):
        return None
    d, h, mi, s = (int(g) if g else 0 for g in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + s


def _first_float(patterns: list[re.Pattern[str]], text: str) -> float | None:
    for pat in patterns:
        m = pat.search(text)
        if m:
            try:
                return float(m.group(1).split(",")[0].split(";")[0].strip())
            except ValueError:
                continue
    return None


def _first_str(patterns: list[re.Pattern[str]], text: str) -> str | None:
    for pat in patterns:
        m = pat.search(text)
        if m:
            value = m.group(1).strip()
            if value:
                return value
    return None


def parse_gcode_metadata(path: Path) -> dict[str, Any]:
    """Return library-style metadata for ``path`` (empty dict when nothing is found)."""
    try:
        size = path.stat().st_size
        with open(path, "rb") as fh:
            head = fh.read(_HEAD_BYTES)
            if size > _HEAD_BYTES + _TAIL_BYTES:
                fh.seek(size - _TAIL_BYTES)
                tail = fh.read(_TAIL_BYTES)
            else:
                tail = b""
    except OSError:
        return {}
    text = (head + b"\n" + tail).decode("utf-8", errors="replace")

    meta: dict[str, Any] = {}

    for pat in _TIME_LINES:
        m = pat.search(text)
        if m:
            seconds = _duration_seconds(m.group(1))
            if seconds:
                meta["print_time_seconds"] = seconds
                break

    # Orca / Bambu Studio list one value per extruder ("= 1.2, 0, 3.4");
    # keep the per-tool list for MMU booking and the sum for the card.
    m = re.search(r";\s*filament used \[g\]\s*=\s*([0-9.,; ]+)", text, re.I)
    if m:
        per_tool: list[float] = []
        for part in re.split(r"[,;]", m.group(1)):
            try:
                per_tool.append(round(float(part.strip()), 3))
            except ValueError:
                continue
        if per_tool:
            meta["filament_used_grams"] = round(sum(per_tool), 2)
            if len(per_tool) > 1:
                meta["filament_used_grams_per_tool"] = per_tool
    mm = _first_float([re.compile(r";\s*filament used \[mm\]\s*=\s*([0-9.,; ]+)", re.I)], text)
    if mm is not None:
        meta["filament_used_mm"] = round(mm, 1)

    layers = _first_float(
        [
            re.compile(r";\s*total layer number:\s*(\d+)", re.I),
            re.compile(r";\s*total layers count:\s*(\d+)", re.I),
            re.compile(r";\s*LAYER_COUNT:\s*(\d+)", re.I),
        ],
        text,
    )
    if layers:
        meta["total_layers"] = int(layers)

    layer_height = _first_float([re.compile(r";\s*layer_height\s*=\s*([0-9.]+)", re.I)], text)
    if layer_height:
        meta["layer_height"] = layer_height
    nozzle = _first_float([re.compile(r";\s*nozzle_diameter\s*=\s*([0-9.]+)", re.I)], text)
    if nozzle:
        meta["nozzle_diameter"] = nozzle

    filament_type = _first_str([re.compile(r";\s*filament_type\s*=\s*([A-Za-z0-9+\- ]+)", re.I)], text)
    if filament_type:
        meta["filament_type"] = filament_type.split(";")[0].strip()
    color = _first_str([re.compile(r";\s*filament_colou?r\s*=\s*(#[0-9A-Fa-f]{6})", re.I)], text)
    if color:
        meta["filament_color"] = color

    bed = _first_float(
        [
            re.compile(r";\s*(?:hot_plate|textured_plate|cool_plate|eng_plate)_temp\s*=\s*(\d+)", re.I),
            re.compile(r";\s*bed_temperature\s*=\s*(\d+)", re.I),
            re.compile(r";\s*first_layer_bed_temperature\s*=\s*(\d+)", re.I),
        ],
        text,
    )
    if bed:
        meta["bed_temperature"] = int(bed)
    nozzle_temp = _first_float(
        [
            re.compile(r";\s*nozzle_temperature\s*=\s*(\d+)", re.I),
            re.compile(r";\s*temperature\s*=\s*(\d+)", re.I),
        ],
        text,
    )
    if nozzle_temp:
        meta["nozzle_temperature"] = int(nozzle_temp)

    printer_model = _first_str(
        [
            re.compile(r";\s*printer_model\s*=\s*([^\n]+)", re.I),
            re.compile(r";\s*printer_settings_id\s*=\s*([^\n]+)", re.I),
        ],
        text,
    )
    if printer_model:
        # Informational only. Deliberately not ``sliced_for_model``: that key
        # drives the queue's model-match guard, which knows Bambu names.
        meta["klipper_printer_profile"] = printer_model.strip('" ')

    slicer = _first_str(
        [
            re.compile(r";\s*generated by\s+([^\n]+)", re.I),
            re.compile(r";\s*(OrcaSlicer [^\n]+|BambuStudio [^\n]+|PrusaSlicer [^\n]+|SuperSlicer [^\n]+)", re.I),
        ],
        text,
    )
    if slicer:
        meta["slicer"] = slicer.strip()

    return meta
