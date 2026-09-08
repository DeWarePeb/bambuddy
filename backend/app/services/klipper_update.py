"""Klipper, Moonraker and web-UI versions from update_manager (Voron patch series, C6).

The Firmware page checks every printer's version against Bambu Lab's download
page. For a Klipper printer that question has no answer — its model is a Voron,
not an X1C — so the row simply said nothing, while the machine's Klipper could
be a year behind its Moonraker.

Moonraker's `machine/update/status` already knows: it is what Mainsail's own
update panel draws. This reads it and reports the same shape the Bambu check
reports, so the badge and the list on the Firmware page need no new concepts —
plus a per-component breakdown, because "Klipper is behind" and "the OS has 14
packages waiting" are different jobs and a single version string cannot say
both.

Read-only, deliberately. Moonraker can install these updates over its own API
and this does not offer to: a `machine/update/klipper` restarts Klipper, and
doing that from a print-management dashboard is a decision the person standing
next to the printer should make in Mainsail.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_TIMEOUT = 10.0

# Moonraker reports these as version_info entries. "system" is the OS package
# count rather than a version, so it is described rather than compared.
_SYSTEM = "system"


def _component(name: str, info: dict[str, Any]) -> dict[str, Any] | None:
    """One update_manager entry as {name, current, latest, update_available}."""
    if name == _SYSTEM:
        count = info.get("package_count")
        if not isinstance(count, int) or count <= 0:
            return {"name": name, "current": None, "latest": None, "update_available": False}
        return {"name": name, "current": None, "latest": f"{count}", "update_available": True}

    current = str(info.get("version") or "") or None
    latest = str(info.get("remote_version") or "") or None
    if current is None and latest is None:
        return None
    return {
        "name": name,
        "current": current,
        "latest": latest,
        # Moonraker's own rule: a component is behind when the two differ and
        # both are known. It reports "?" for a repo it could not reach, and a
        # comparison against that would raise a false alarm on every poll.
        "update_available": bool(current and latest and current != latest and "?" not in (current, latest)),
    }


def summarize(status: dict[str, Any]) -> dict[str, Any]:
    """Turn `machine/update/status` into the Firmware page's vocabulary.

    ``current_version`` and ``latest_version`` are Klipper's, because that is
    the firmware in the sense the page means; everything else rides along in
    ``components``.
    """
    version_info = status.get("version_info") or {}
    components: list[dict[str, Any]] = []
    for name, info in version_info.items():
        if not isinstance(info, dict):
            continue
        entry = _component(str(name), info)
        if entry is not None:
            components.append(entry)
    components.sort(key=lambda c: (c["name"] != "klipper", c["name"] != "moonraker", c["name"]))

    klipper = next((c for c in components if c["name"] == "klipper"), None)
    return {
        "current_version": klipper["current"] if klipper else None,
        "latest_version": klipper["latest"] if klipper else None,
        "update_available": any(c["update_available"] for c in components),
        "components": components,
    }


async def check_updates(printer) -> dict[str, Any] | None:
    """Ask a Klipper printer's Moonraker what is behind. None when it cannot say.

    ``refresh=false``: Moonraker refreshes its own view on a timer, and forcing
    one on every page load would hit GitHub's rate limit for every printer.
    """
    base_url = (getattr(printer, "api_url", None) or "").rstrip("/")
    if not base_url:
        return None
    headers = {"X-Api-Key": printer.auth_token} if getattr(printer, "auth_token", None) else {}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.get(f"{base_url}/machine/update/status?refresh=false", headers=headers)
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:  # noqa: BLE001 - host down, or update_manager not configured
        logger.debug("Moonraker update status unavailable for %s: %s", getattr(printer, "name", "?"), exc)
        return None
    result = payload.get("result", payload)
    return summarize(result) if isinstance(result, dict) else None
