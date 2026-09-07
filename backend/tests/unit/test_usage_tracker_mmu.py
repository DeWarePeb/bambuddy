"""Voron patch series: per-tool filament booking through Happy Hare's tool-to-gate map."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.app.services import usage_tracker
from backend.app.services.gcode_metadata import parse_gcode_metadata
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


def _spool(spool_id=7):
    s = MagicMock()
    s.id = spool_id
    s.label_weight = 1000
    s.weight_used = 100.0
    s.cost_per_kg = 20.0
    s.material = "ABS"
    s.rgba = "FD8700FF"
    return s


def _pm(client):
    pm = MagicMock()
    pm.get_client.return_value = client
    return pm


def _mmu_client(num_gates=4, ttg=None):
    client = MoonrakerClient("http://v")
    client._mmu = True
    client._mmu_num_gates = num_gates
    client.last_ttg_map = ttg
    return client


def test_per_tool_filament_grams(tmp_path):
    path = tmp_path / "multi.gcode"
    path.write_text("; filament used [g] = 1.25, 0, 3.5\n; filament_type = PLA;PLA;PETG\nG28\n")
    meta = parse_gcode_metadata(path)
    assert meta["filament_used_grams"] == 4.75
    assert meta["filament_used_grams_per_tool"] == [1.25, 0.0, 3.5]
    assert meta["filament_type"] == "PLA"


@pytest.mark.asyncio
async def test_mmu_print_books_each_tool_to_its_gate_spool(monkeypatch):
    monkeypatch.setattr(usage_tracker, "_per_tool_grams_from_archive", lambda a: [10.0, 0.0, 4.0])  # noqa: ARG005
    client = _mmu_client(ttg=[3, 1, 0])  # tool0 -> gate3, tool2 -> gate0
    archive = MagicMock(filament_used_grams=14.0, file_path="archives/1/multi.gcode")
    spool_a, spool_b = _spool(7), _spool(8)
    db = _db(archive, MagicMock(spool_id=7), spool_a, MagicMock(spool_id=8), spool_b)
    handled: set = set()

    out = await _track_klipper_external_spool(
        1, 42, "completed", "multi.gcode", handled, _pm(client), db, tray_now_at_start=3
    )

    assert [(o["ams_id"], o["tray_id"], o["weight_used"], o["slot_id"]) for o in out] == [
        (0, 3, 10.0, 0),
        (0, 0, 4.0, 2),
    ]
    assert handled == {(0, 3), (0, 0)}
    assert spool_a.weight_used == 110.0 and spool_b.weight_used == 104.0


@pytest.mark.asyncio
async def test_single_tool_mmu_print_books_the_loaded_gate(monkeypatch):
    monkeypatch.setattr(usage_tracker, "_per_tool_grams_from_archive", lambda a: None)  # noqa: ARG005
    client = _mmu_client(num_gates=2)
    db = _db(MagicMock(filament_used_grams=6.0, file_path="archives/1/x.gcode"), MagicMock(spool_id=7), _spool())
    out = await _track_klipper_external_spool(
        1, 42, "completed", "x.gcode", set(), _pm(client), db, tray_now_at_start=1
    )
    assert out and (out[0]["ams_id"], out[0]["tray_id"], out[0]["weight_used"]) == (0, 1, 6.0)


def test_per_tool_grams_reads_gcode_archive(tmp_path, monkeypatch):
    from backend.app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "base_dir", tmp_path)
    gcode = tmp_path / "archives" / "1" / "multi.gcode"
    gcode.parent.mkdir(parents=True)
    gcode.write_text("; filament used [g] = 2, 3\nG28\n")
    assert usage_tracker._per_tool_grams_from_archive(MagicMock(file_path="archives/1/multi.gcode")) == [2.0, 3.0]
    assert usage_tracker._per_tool_grams_from_archive(MagicMock(file_path="archives/1/job.3mf")) is None
