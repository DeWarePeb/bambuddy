"""Voron patch series (B7): the subnet scan also reports Klipper hosts (Moonraker on 7125)."""

from unittest.mock import AsyncMock, patch

import pytest

from backend.app.services import discovery as discovery_module
from backend.app.services.discovery import SubnetScanner, klipper_serial_for_host


def test_klipper_serial_matches_the_printer_schema_rule():
    # schemas/printer.py derives the same serial from the Moonraker URL host,
    # so a discovered host is recognised as "already added".
    assert klipper_serial_for_host("192.168.2.177") == "KLIPPER-192-168-2-177"
    assert klipper_serial_for_host("voron.local") == "KLIPPER-VORON-LOCAL"


class TestProbeHostKlipper:
    @pytest.mark.asyncio
    async def test_moonraker_host_is_reported_as_klipper_printer(self):
        scanner = SubnetScanner()

        async def _check_port(ip, port, timeout):
            return port == SubnetScanner.MOONRAKER_PORT

        with (
            patch.object(scanner, "_check_port", side_effect=_check_port),
            patch.object(
                discovery_module,
                "fetch_moonraker_printer_info",
                AsyncMock(return_value={"hostname": "voron", "software_version": "v0.12.0-1"}),
            ),
        ):
            await scanner._probe_host("192.168.2.177", 0.5)

        found = scanner.discovered_printers
        assert len(found) == 1
        printer = found[0]
        assert printer.provider == "klipper"
        assert printer.serial == "KLIPPER-192-168-2-177"
        assert printer.name == "voron"
        assert printer.ip_address == "192.168.2.177"
        assert printer.api_url == "http://192.168.2.177:7125"
        assert printer.to_dict()["provider"] == "klipper"

    @pytest.mark.asyncio
    async def test_port_open_but_not_moonraker_is_ignored(self):
        scanner = SubnetScanner()

        async def _check_port(ip, port, timeout):
            return port == SubnetScanner.MOONRAKER_PORT

        with (
            patch.object(scanner, "_check_port", side_effect=_check_port),
            patch.object(discovery_module, "fetch_moonraker_printer_info", AsyncMock(return_value=None)),
        ):
            await scanner._probe_host("192.168.2.50", 0.5)

        assert scanner.discovered_printers == []

    @pytest.mark.asyncio
    async def test_closed_ports_probe_nothing(self):
        scanner = SubnetScanner()
        fetch = AsyncMock()
        with (
            patch.object(scanner, "_check_port", AsyncMock(return_value=False)),
            patch.object(discovery_module, "fetch_moonraker_printer_info", fetch),
        ):
            await scanner._probe_host("192.168.2.51", 0.5)

        fetch.assert_not_awaited()
        assert scanner.discovered_printers == []

    @pytest.mark.asyncio
    async def test_bambu_path_is_unchanged(self):
        scanner = SubnetScanner()
        fetch = AsyncMock()
        with (
            patch.object(scanner, "_check_port", AsyncMock(return_value=True)),
            patch.object(scanner, "_get_printer_info_ssdp", AsyncMock(return_value=("01P00A123456789", "P2S", "N7"))),
            patch.object(discovery_module, "fetch_moonraker_printer_info", fetch),
        ):
            await scanner._probe_host("192.168.2.3", 0.5)

        fetch.assert_not_awaited()
        [printer] = scanner.discovered_printers
        assert printer.provider == "bambu"
        assert printer.api_url is None
        assert printer.serial == "01P00A123456789"


class TestFetchMoonrakerPrinterInfo:
    @pytest.mark.asyncio
    async def test_requires_moonraker_shape(self):
        class _Resp:
            def __init__(self, payload):
                self._payload = payload

            def raise_for_status(self):
                return None

            def json(self):
                return self._payload

        class _Client:
            def __init__(self, payload):
                self._payload = payload

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def get(self, url):
                return _Resp(self._payload)

        import httpx

        good = {"result": {"hostname": "voron", "software_version": "v0.12", "state": "ready"}}
        with patch.object(httpx, "AsyncClient", lambda **kw: _Client(good)):
            info = await discovery_module.fetch_moonraker_printer_info("192.168.2.177", 7125, 0.5)
        assert info == good["result"]

        with patch.object(httpx, "AsyncClient", lambda **kw: _Client({"result": {"something": "else"}})):
            assert await discovery_module.fetch_moonraker_printer_info("192.168.2.177", 7125, 0.5) is None
