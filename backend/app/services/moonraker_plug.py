"""Moonraker power devices as smart plugs (Voron patch series, C3).

Klipper users rarely bolt a Tasmota onto the wall socket. They wire a relay,
a Shelly or a TP-Link into Moonraker's ``[power]`` section and switch the
printer from Mainsail's own power button. Bambuddy's plug automation — turn on
at print start, off after cooldown, off when drying finishes, the schedules —
never saw those, so a Klipper printer either got a second plug configured twice
or no automation at all.

A plug of type ``moonraker`` carries no address of its own. It points at a
printer, and the printer already holds the Moonraker URL and API key, so
changing either in one place keeps working. The device is named by
``moonraker_device``, which is Moonraker's own device name from
``machine/device_power/devices``.

Energy is not reported: Moonraker's power API answers ``on``/``off`` and
nothing else, whatever the relay behind it can measure. ``get_energy``
returning None is the same answer a Tasmota without a power meter gives, so
every consumer already handles it.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

import httpx

if TYPE_CHECKING:
    from backend.app.models.smart_plug import SmartPlug

logger = logging.getLogger(__name__)

_TIMEOUT = 5.0


async def list_devices(base_url: str, auth_token: str | None = None) -> list[dict[str, Any]]:
    """The printer's configured ``[power]`` devices, for the plug dialog's picker."""
    url = f"{base_url.rstrip('/')}/machine/device_power/devices"
    headers = {"X-Api-Key": auth_token} if auth_token else {}
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        payload = response.json()
    devices = (payload.get("result") or payload).get("devices") or []
    return [
        {"device": str(d.get("device")), "status": d.get("status"), "type": d.get("type")}
        for d in devices
        if isinstance(d, dict) and d.get("device")
    ]


class MoonrakerPlugService:
    """The plug-service surface (turn_on / turn_off / toggle / get_status / get_energy).

    Resolving the printer per call rather than caching it: a plug's printer can
    be repointed, and its Moonraker URL edited, while the process runs.
    """

    async def _connection(self, plug: "SmartPlug") -> tuple[str, str | None] | None:
        from sqlalchemy import select

        from backend.app.core.database import async_session
        from backend.app.models.printer import Printer

        if not plug.printer_id or not plug.moonraker_device:
            return None
        async with async_session() as db:
            printer = (
                await db.execute(select(Printer).where(Printer.id == plug.printer_id))
            ).scalar_one_or_none()
        if printer is None or not printer.api_url:
            return None
        return printer.api_url.rstrip("/"), printer.auth_token

    async def _request(self, plug: "SmartPlug", path: str) -> str | None:
        """Call one device_power endpoint; returns the device's reported state."""
        connection = await self._connection(plug)
        if connection is None:
            logger.warning("Moonraker plug '%s' has no printer or device configured", plug.name)
            return None
        base_url, token = connection
        device = quote(str(plug.moonraker_device), safe="")
        headers = {"X-Api-Key": token} if token else {}
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                response = await client.get(f"{base_url}/machine/device_power/{path}?device={device}", headers=headers)
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:  # noqa: BLE001 - printer host down, or device renamed in printer.cfg
            logger.warning("Moonraker plug '%s' (%s): %s", plug.name, plug.moonraker_device, exc)
            return None
        result = payload.get("result") if isinstance(payload, dict) else None
        if not isinstance(result, dict):
            return None
        # Both endpoints answer {"<device>": "on"}; device_power/device echoes
        # the name it was asked about, so read by key and fall back to the only
        # value present rather than assuming the echo matches byte for byte.
        state = result.get(str(plug.moonraker_device))
        if state is None and len(result) == 1:
            state = next(iter(result.values()))
        return str(state).lower() if state is not None else None

    async def turn_on(self, plug: "SmartPlug") -> bool:
        return await self._request(plug, "on") == "on"

    async def turn_off(self, plug: "SmartPlug") -> bool:
        return await self._request(plug, "off") == "off"

    async def toggle(self, plug: "SmartPlug") -> bool:
        state = await self.get_status(plug)
        if state.get("state") == "ON":
            return await self.turn_off(plug)
        return await self.turn_on(plug)

    async def get_status(self, plug: "SmartPlug") -> dict:
        """``{state, reachable, device_name}`` — the contract the other plug services use."""
        state = await self._request(plug, "device")
        if state is None:
            return {"state": None, "reachable": False, "device_name": plug.moonraker_device}
        if state not in ("on", "off"):
            # "error" and "init" are Moonraker's own words for a device it cannot
            # drive. Reachable, but its state is not a state we can act on.
            return {"state": None, "reachable": True, "device_name": plug.moonraker_device}
        return {"state": state.upper(), "reachable": True, "device_name": plug.moonraker_device}

    async def get_energy(self, plug: "SmartPlug") -> dict | None:  # noqa: ARG002
        """Moonraker's power API reports no energy — see the module docstring."""
        return None


moonraker_plug_service = MoonrakerPlugService()
