"""Voron patch series: attaching Moonraker G-code to a fallback archive."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.app.services import klipper_archive
from backend.app.services.moonraker_client import MoonrakerClient

GCODE = b"; total estimated time: 1h 0m 0s\n; total layer number: 10\nG28\n; filament used [g] = 4.2\n; filament_type = ABS\n"


def _archive():
    a = MagicMock()
    a.id = 5
    a.print_time_seconds = None
    a.filament_used_grams = None
    a.filament_type = None
    a.filament_color = None
    a.extra_data = {"no_3mf_available": True, "no_3mf_reason": "klipper"}
    return a


def _session_factory(archive):
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = archive
    db.execute = AsyncMock(return_value=result)

    @asynccontextmanager
    async def factory():
        yield db

    return factory, db


@pytest.mark.asyncio
async def test_attach_downloads_stores_and_fills_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr(klipper_archive.settings, "base_dir", tmp_path)
    monkeypatch.setattr(klipper_archive.settings, "archive_dir", tmp_path / "archives")
    archive = _archive()
    factory, db = _session_factory(archive)
    monkeypatch.setattr(klipper_archive, "async_session", factory)

    client = MoonrakerClient("http://voron.test")
    client.download_file = lambda name: GCODE  # type: ignore[method-assign]
    pm = MagicMock()
    pm.get_client.return_value = client

    assert await klipper_archive.attach_gcode_to_archive(pm, 1, 5, "Voron_Test_Cube.gcode") is True

    assert archive.file_path.startswith("archives/1/")
    assert archive.file_path.endswith("Voron_Test_Cube.gcode")
    assert (tmp_path / archive.file_path).read_bytes() == GCODE
    assert archive.file_size == len(GCODE)
    assert archive.print_time_seconds == 3600
    assert archive.filament_used_grams == 4.2
    assert archive.filament_type == "ABS"
    assert "no_3mf_available" not in archive.extra_data
    assert archive.extra_data["klipper_gcode_fetched"] is True
    db.commit.assert_awaited()


@pytest.mark.asyncio
async def test_attach_is_a_noop_for_bambu_clients_and_failed_downloads(tmp_path, monkeypatch):
    pm = MagicMock()
    pm.get_client.return_value = MagicMock()  # not a MoonrakerClient
    assert await klipper_archive.attach_gcode_to_archive(pm, 1, 5, "x.gcode") is False

    client = MoonrakerClient("http://voron.test")

    def boom(name):
        raise RuntimeError("404")

    client.download_file = boom  # type: ignore[method-assign]
    pm.get_client.return_value = client
    assert await klipper_archive.attach_gcode_to_archive(pm, 1, 5, "x.gcode") is False
