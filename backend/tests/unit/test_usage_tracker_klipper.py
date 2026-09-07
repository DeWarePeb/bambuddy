"""Voron patch series: filament booking for Klipper printers (external slot, archive grams)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.app.services.moonraker_client import MoonrakerClient
from backend.app.services.usage_tracker import _track_klipper_external_spool


def _db(*rows):
    db = AsyncMock()
    results = []
    for row in rows:
        r = MagicMock()
        r.scalar_one_or_none.return_value = row
        results.append(r)
    db.execute = AsyncMock(side_effect=results)
    db.add = MagicMock()
    return db


def _spool(weight_used=100.0, cost_per_kg=20.0):
    s = MagicMock()
    s.id = 7
    s.label_weight = 1000
    s.weight_used = weight_used
    s.cost_per_kg = cost_per_kg
    s.material = "ABS"
    s.rgba = "FD8700FF"
    return s


def _pm(client):
    pm = MagicMock()
    pm.get_client.return_value = client
    return pm


@pytest.mark.asyncio
async def test_completed_print_books_full_archive_grams_to_external_spool():
    archive = MagicMock(filament_used_grams=12.5)
    assignment = MagicMock(spool_id=7)
    spool = _spool()
    db = _db(archive, assignment, spool)
    handled: set = set()

    out = await _track_klipper_external_spool(
        1,
        42,
        "completed",
        "carrier.gcode",
        handled,
        _pm(MoonrakerClient("http://v")),
        db,
        tray_now_at_start=254,
        last_progress=100.0,
        default_filament_cost=0.0,
    )

    assert out and out[0]["weight_used"] == 12.5 and out[0]["ams_id"] == 255 and out[0]["tray_id"] == 0
    assert out[0]["cost"] == 0.25
    assert spool.weight_used == 112.5
    assert (255, 0) in handled
    db.add.assert_called_once()


@pytest.mark.asyncio
async def test_failed_print_is_scaled_by_progress():
    db = _db(MagicMock(filament_used_grams=20.0), MagicMock(spool_id=7), _spool())
    out = await _track_klipper_external_spool(
        1,
        42,
        "failed",
        "x.gcode",
        set(),
        _pm(MoonrakerClient("http://v")),
        db,
        tray_now_at_start=254,
        last_progress=25.0,
    )
    assert out[0]["weight_used"] == 5.0


@pytest.mark.asyncio
async def test_skips_bambu_clients_and_unassigned_slots():
    # Not a Moonraker client -> nothing, and no DB access
    db = _db()
    assert await _track_klipper_external_spool(1, 42, "completed", "x", set(), _pm(MagicMock()), db) == []
    db.execute.assert_not_called()

    # Moonraker client but nobody assigned a spool to the external slot
    db2 = _db(MagicMock(filament_used_grams=9.0), None)
    assert (
        await _track_klipper_external_spool(1, 42, "completed", "x", set(), _pm(MoonrakerClient("http://v")), db2) == []
    )
    db2.add.assert_not_called()


@pytest.mark.asyncio
async def test_archive_without_estimate_books_nothing():
    db = _db(MagicMock(filament_used_grams=None))
    assert (
        await _track_klipper_external_spool(1, 42, "completed", "x", set(), _pm(MoonrakerClient("http://v")), db) == []
    )
