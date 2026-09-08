"""Klipper printers over Moonraker, shaped like ``BambuMQTTClient``.

Voron patch series. Every consumer in Bambuddy — printer_manager, the status
routes, the scheduler, HA sensors, the websocket broadcaster — reads a
``PrinterState`` off ``client.state`` and calls a handful of methods on the
client. This class keeps that exact surface so none of them need a provider
branch: a Klipper printer is just a client whose ``state`` is filled from
Moonraker's HTTP API instead of MQTT reports.

What maps cleanly: connection, print state, progress, layers, remaining
time, nozzle/bed/chamber temperatures, part fan, pause/resume/stop, G-code,
temperature and fan targets, homing/jogging, chamber light (as a Klipper
``SET_PIN`` / ``LED`` macro), file upload/delete/list and print start.

What does not exist on Klipper and is answered with ``False`` (or an empty
value) instead of raising: AMS, K-profiles, calibration runs, drying,
HMS actions, xcam options, timelapse, the Bambu virtual printer, raw MQTT.
``__getattr__`` catches the long tail of Bambu-only methods so a route that
was never taught about Klipper fails soft (returns False, the UI shows
"unsupported") instead of a 500.

Polling: Moonraker has no push channel we want to hold open from a thread,
so a daemon thread polls ``/printer/objects/query`` every ``poll_interval``
seconds and fires the same callbacks the MQTT client fires, from a
non-loop thread — printer_manager already bridges those with
``_schedule_async``.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

import httpx

from backend.app.services.bambu_mqtt import MQTTLogEntry, PrinterState

logger = logging.getLogger(__name__)

# Moonraker print_stats.state -> Bambu gcode_state vocabulary the rest of the
# app is written against (see print_scheduler._ACTIVE_PRINT_STATES).
_STATE_MAP = {
    "printing": "RUNNING",
    "paused": "PAUSE",
    "complete": "FINISH",
    "cancelled": "FAILED",
    "error": "FAILED",
    "standby": "IDLE",
    "ready": "IDLE",
}

# Objects polled every cycle. Chamber sensors and fans are discovered once at
# connect from /printer/objects/list and appended.
_BASE_OBJECTS = ["print_stats", "virtual_sdcard", "display_status", "extruder", "heater_bed", "toolhead", "fan"]

_CHAMBER_OBJECT_PREFIXES = ("temperature_sensor chamber", "temperature_fan chamber", "heater_generic chamber")


def map_moonraker_state(raw_state: Any) -> str:
    return _STATE_MAP.get(str(raw_state or "").lower(), "unknown")


_LIGHT_KINDS = ("output_pin ", "led ", "neopixel ", "dotstar ", "pca9533 ", "pca9632 ")
# Names Klipper users give the chamber light, best match first.
_LIGHT_NAME_HINTS = ("chamber_light", "chamber_lights", "caselight", "case_light", "chamber", "light", "lights", "lamp")


def _pick_light_object(objects: list[Any]) -> str | None:
    """Choose the object that most plausibly is the chamber light.

    Klipper has no "chamber light" concept; people name an output_pin or a
    neopixel strip for it. Prefer names that say chamber/caselight/light,
    and skip Happy Hare / ERCF internals (``_unit0_gate0_leds`` and friends),
    which are also neopixels and otherwise win by being listed first.
    """
    candidates: list[tuple[int, str]] = []
    for candidate in objects:
        full = str(candidate)
        lower = full.lower()
        if not lower.startswith(_LIGHT_KINDS):
            continue
        name = lower.split(" ", 1)[1] if " " in lower else ""
        if name.startswith("_") or any(
            tag in name for tag in ("gate", "mmu", "ercf", "unit", "status", "logo", "nozzle", "sb_")
        ):
            continue
        rank = next((i for i, hint in enumerate(_LIGHT_NAME_HINTS) if hint in name), None)
        if rank is not None:
            candidates.append((rank, full))
    if not candidates:
        return None
    return min(candidates)[1]


_EMPTY_EXTERNAL_TRAY: dict[str, Any] = {
    "id": 254,
    "tray_type": "",
    "tray_sub_brands": "",
    "tray_color": "",
    "tray_info_idx": "",
    "tray_id_name": "",
    "remain": -1,
    "tag_uid": "",
    "tray_uuid": "",
    "nozzle_temp_min": None,
    "nozzle_temp_max": None,
    "state": 10,
}


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class MoonrakerClient:
    """Moonraker-backed printer client with the ``BambuMQTTClient`` surface."""

    STALE_AFTER_SECONDS = 30.0

    def __init__(
        self,
        base_url: str,
        auth_token: str | None = None,
        *,
        serial_number: str = "",
        model: str | None = None,
        on_state_change: Callable[[PrinterState], None] | None = None,
        on_print_start: Callable[[dict], None] | None = None,
        on_print_complete: Callable[[dict], None] | None = None,
        on_layer_change: Callable[[int], None] | None = None,
        on_print_progress: Callable[[int], None] | None = None,
        on_bed_temp_update: Callable[[float], None] | None = None,
        on_print_running_observed: Callable[[dict], None] | None = None,
        poll_interval: float = 2.0,
        timeout: float = 5.0,
        **_ignored_bambu_callbacks: Any,
    ) -> None:
        if not base_url:
            raise ValueError("Moonraker base URL is required for Klipper printers")
        if "://" not in base_url:
            base_url = f"http://{base_url}"
        self.base_url = base_url.rstrip("/")
        self.auth_token = auth_token or None
        self.serial_number = serial_number
        self.model = model
        self.ip_address = urlparse(self.base_url).hostname or ""
        self.access_code = "-"
        self.poll_interval = poll_interval
        self.timeout = timeout

        self.on_state_change = on_state_change
        self.on_print_start = on_print_start
        self.on_print_complete = on_print_complete
        self.on_layer_change = on_layer_change
        self.on_print_progress = on_print_progress
        self.on_bed_temp_update = on_bed_temp_update
        self.on_print_running_observed = on_print_running_observed

        self.state = PrinterState()
        self.last_connect_error: str | None = None
        self._last_message_time: float = 0.0
        self._drying_targets: dict[int, dict] = {}  # read by printer_manager.get_drying_targets
        self._logs: list[MQTTLogEntry] = []
        self._logging_enabled = False

        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._objects: list[str] = list(_BASE_OBJECTS)
        self._chamber_object: str | None = None
        self._light_object: str | None = None
        self._has_sample = False
        self._last_state: str | None = None
        self._last_percent: int = -1
        self._last_layer: int = -1
        self._last_bed_temp: float | None = None
        self._metadata_for: str | None = None
        self._metadata: dict[str, Any] = {}
        self._print_started_at: float | None = None
        self._last_progress = 0.0
        self._last_layer_num = 0
        # The one "external" slot (virtual tray 254). Filled by
        # ams_set_filament_setting when a spool is assigned in the inventory.
        self._external_tray: dict[str, Any] = dict(_EMPTY_EXTERNAL_TRAY)
        # Happy Hare MMU (discovered at connect). Gates are reported as AMS
        # units of four trays so global tray id == gate number.
        # Why the printer is not answering, when it is not answering: Klipper's
        # own state and message from ``printer/info``. Empty while all is well.
        self._klippy: dict[str, Any] = {}
        # moonraker-timelapse present in Moonraker's component list (C4).
        # Whether it is switched *on* is asked per print, not cached.
        self._timelapse_component = False
        # C5: Klipper's exclude_object, if the printer has it. Object ids are
        # indices into _object_names, which skip_objects translates back.
        self._exclude_object = False
        self._object_names: list[str] = []
        self._mmu = False
        self._mmu_num_gates = 0
        self._ams_units: list[dict[str, Any]] = []
        self._mmu_info: dict[str, Any] = {}
        self.last_ttg_map: list[int] | None = None  # tool -> gate map sent at the last dispatch

    # ------------------------------------------------------------------ HTTP

    def _headers(self) -> dict[str, str]:
        return {"X-Api-Key": self.auth_token} if self.auth_token else {}

    def _get(self, path: str, timeout: float | None = None) -> dict[str, Any]:
        response = httpx.get(
            f"{self.base_url}/{path.lstrip('/')}", headers=self._headers(), timeout=timeout or self.timeout
        )
        response.raise_for_status()
        data = response.json()
        return data.get("result", data) if isinstance(data, dict) else {}

    def _post(self, path: str, payload: dict[str, Any] | None = None, timeout: float | None = None) -> dict[str, Any]:
        response = httpx.post(
            f"{self.base_url}/{path.lstrip('/')}",
            json=payload or {},
            headers=self._headers(),
            timeout=timeout or self.timeout,
        )
        response.raise_for_status()
        data = response.json()
        return data.get("result", data) if isinstance(data, dict) else {}

    def _query(self, objects: list[str]) -> dict[str, Any]:
        query = "&".join(quote(name, safe="") for name in objects)
        result = self._get(f"printer/objects/query?{query}")
        status = result.get("status") if isinstance(result, dict) else None
        return status if isinstance(status, dict) else {}

    def _safe_call(self, description: str, fn: Callable[[], Any]) -> bool:
        try:
            fn()
            self._record_log("sent", description)
            return True
        except Exception as exc:  # noqa: BLE001 - any transport error means "command not delivered"
            logger.warning("[%s] Moonraker %s failed: %s", self.serial_number, description, exc)
            self._record_log("error", f"{description}: {exc}")
            return False

    # ---------------------------------------------------------- lifecycle

    def connect(self, loop: Any = None) -> None:  # noqa: ARG002 - signature parity with BambuMQTTClient
        """Probe Moonraker once, discover optional objects, start the poll thread."""
        self._stop.clear()
        try:
            info = self._get("server/info")
            self.state.connected = True
            self.state.firmware_version = str(info.get("moonraker_version") or "") or None
            # C4: does this Moonraker have moonraker-timelapse installed at all?
            self._timelapse_component = "timelapse" in (info.get("components") or [])
            self.last_connect_error = None
            self._discover_objects()
            self.request_status_update()
        except Exception as exc:  # noqa: BLE001
            self.last_connect_error = str(exc)
            self.state.connected = False
            # Usually this is a printer that is up but whose Klipper is in
            # shutdown — connect on a machine with an error on its screen should
            # say what the error is, not wait for the first poll to fail too.
            self._refresh_klippy()
            logger.warning("[%s] Moonraker connect failed: %s", self.serial_number, exc)
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(
                target=self._poll_loop, name=f"moonraker-{self.serial_number or self.ip_address}", daemon=True
            )
            self._thread.start()

    def disconnect(self, timeout: float = 0) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive() and timeout > 0:
            thread.join(timeout)
        self._thread = None
        self.state.connected = False

    def _discover_objects(self) -> None:
        try:
            objects = self._get("printer/objects/list").get("objects") or []
        except Exception:  # noqa: BLE001 - optional; polling works without it
            return
        chamber = next((o for o in objects if str(o).startswith(_CHAMBER_OBJECT_PREFIXES)), None)
        polled = list(_BASE_OBJECTS)
        # Happy Hare MMU: live state on the `mmu` object, the gate map (material,
        # colour, name, availability) in Klipper's save_variables.
        self._mmu = "mmu" in objects
        if self._mmu:
            polled.extend(["mmu", "mmu_machine", "save_variables"])
        # C5: cancel-object support, present whenever [exclude_object] is enabled.
        self._exclude_object = "exclude_object" in objects
        if self._exclude_object:
            polled.append("exclude_object")
        if chamber:
            self._chamber_object = chamber
            polled.append(chamber)
        light = _pick_light_object(objects)
        if light:
            self._light_object = light
            polled.append(light)
        self._objects = polled

    def _poll_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.request_status_update()
            except Exception as exc:  # noqa: BLE001 - keep polling through transient errors
                self._mark_unreachable(str(exc))
            self._stop.wait(self.poll_interval)

    def _mark_unreachable(self, reason: str) -> None:
        was_connected = self.state.connected
        self.state.connected = False
        self.last_connect_error = reason
        previous_klippy = self._klippy
        self._refresh_klippy()
        if was_connected:
            logger.warning("[%s] Moonraker unreachable: %s", self.serial_number, reason)
            if self._klippy.get("state"):
                logger.warning("[%s] Klipper reports %s: %s", self.serial_number, *self._klippy_pair())
        # A repeat failure is only worth broadcasting when the reason changed:
        # Klipper can take a few rounds to settle on "shutdown", and a card that
        # already says "offline" would otherwise never pick the message up.
        if (was_connected or self._klippy != previous_klippy) and self.on_state_change:
            self.on_state_change(self.state)

    def _klippy_pair(self) -> tuple[str, str]:
        return str(self._klippy.get("state") or "unknown"), str(self._klippy.get("message") or "no message")

    def _refresh_klippy(self) -> None:
        """Ask Moonraker *why* the printer is not answering, and remember it.

        A failed object query is not evidence of an unreachable printer. The
        common case is the opposite: Moonraker is up and answering, and says
        Klipper is in shutdown or error — at which point ``objects/query``
        returns 503 and everything above this reads it as "offline". The reason
        ("MCU 'mcu' shutdown: Lost communication with MCU") is one request away
        on ``printer/info``, which Moonraker answers precisely when Klipper
        cannot. Without this the card shows a bare "Offline" for a printer
        standing right there with an error on its screen.
        """
        klippy: dict[str, Any] = {}
        try:
            info = self._get("printer/info", timeout=self.timeout)
            state = str(info.get("state") or "").lower() or None
            if state:
                klippy = {"state": state, "message": str(info.get("state_message") or "").strip() or None}
        except Exception:  # noqa: BLE001 - Moonraker itself is down; we know nothing more
            klippy = {}
        self._klippy = klippy
        raw = dict(self.state.raw_data or {})
        raw["provider"] = "klipper"
        raw["klippy"] = klippy
        self.state.raw_data = raw

    # BambuMQTTClient parity — printer_manager calls these on every status read.
    @property
    def is_stale(self) -> bool:
        return self._last_message_time > 0 and (time.monotonic() - self._last_message_time) > self.STALE_AFTER_SECONDS

    def check_staleness(self) -> bool:
        if self.state.connected and self.is_stale:
            self._mark_unreachable("no successful poll for %.0fs" % (time.monotonic() - self._last_message_time))
        return self.state.connected

    def mark_power_off(self) -> bool:
        if not self.state.connected:
            return False
        self.state.connected = False
        self.state.state = "unknown"
        return True

    def force_reconnect_stale_session(self, reason: str) -> None:
        logger.info("[%s] reconnect requested (%s); next poll re-probes", self.serial_number, reason)

    # ------------------------------------------------------------- status

    def request_status_update(self) -> bool:
        """Poll Moonraker once and update ``state``; fires the same callbacks as MQTT reports."""
        with self._lock:
            status = self._query(self._objects)
            previous_state = self._last_state if self._has_sample else None
            was_connected = self.state.connected

            print_stats = status.get("print_stats") or {}
            virtual_sdcard = status.get("virtual_sdcard") or {}
            display_status = status.get("display_status") or {}
            extruder = status.get("extruder") or {}
            heater_bed = status.get("heater_bed") or {}
            toolhead = status.get("toolhead") or {}

            self.state.connected = True
            self.last_connect_error = None
            self._klippy = {}  # Klipper answered; whatever it was complaining about is over.
            self._last_message_time = time.monotonic()
            self.state.state = map_moonraker_state(print_stats.get("state"))

            filename = str(print_stats.get("filename") or "") or None
            if filename:
                base = filename.rsplit("/", 1)[-1]
                self.state.gcode_file = base
                self.state.current_print = base
                self.state.subtask_name = base
                self._load_metadata(filename)
            elif self.state.state == "IDLE":
                self.state.gcode_file = None
                self.state.current_print = None
                self.state.subtask_name = None

            info = print_stats.get("info") or {}
            layer = info.get("current_layer")
            total = info.get("total_layer") or self._metadata.get("layer_count")
            self.state.layer_num = int(layer) if isinstance(layer, (int, float)) else 0
            self.state.total_layers = int(total) if isinstance(total, (int, float)) else 0

            progress = _f(virtual_sdcard.get("progress"), _f(display_status.get("progress")))
            duration = _f(print_stats.get("print_duration"))
            if self.state.state == "FINISH":
                self.state.progress = 100.0
                self.state.remaining_time = 0
            elif self.state.state in ("IDLE", "FAILED", "unknown"):
                self.state.progress = 0.0
                self.state.remaining_time = 0
            else:
                self.state.progress = round(max(0.0, min(progress, 1.0)) * 100, 1)
                self.state.remaining_time = self._remaining_minutes(duration, self.state.progress)

            temps: dict[str, Any] = {
                "nozzle": _f(extruder.get("temperature")),
                "nozzle_target": _f(extruder.get("target")),
                "bed": _f(heater_bed.get("temperature")),
                "bed_target": _f(heater_bed.get("target")),
            }
            if self._chamber_object and isinstance(status.get(self._chamber_object), dict):
                chamber = status[self._chamber_object]
                temps["chamber"] = _f(chamber.get("temperature"))
                temps["chamber_target"] = _f(chamber.get("target"))
            self.state.temperatures = temps

            fan = status.get("fan") or {}
            self.state.cooling_fan_speed = int(round(_f(fan.get("speed")) * 100)) if fan else None
            if self._light_object and isinstance(status.get(self._light_object), dict):
                light = status[self._light_object]
                value = light.get("value")
                if value is None and isinstance(light.get("color_data"), list) and light["color_data"]:
                    value = max(light["color_data"][0] or [0])
                self.state.chamber_light = _f(value) > 0

            # A Klipper printer feeds from one spool. Report it the way a Bambu
            # reports its external spool holder (virtual tray 254 = AMS 255 /
            # tray 0): the card shows an "External" slot with the assign-spool
            # dialog, and the usage tracker books filament to whatever spool is
            # assigned there. tray_now points at it while printing so the
            # tracker's tray_now_at_start lands on the same key.
            self.state.tray_now = 254 if self.state.state in ("RUNNING", "PAUSE") else 255
            if self._mmu:
                self._apply_mmu(status)
            if self._exclude_object:
                self._apply_exclude_object(status)
            self.state.raw_data = {
                "provider": "klipper",
                "klippy": {},
                "moonraker": {k: v for k, v in status.items() if k != "save_variables"},
                "homed_axes": toolhead.get("homed_axes"),
                "print_duration": duration,
                "filament_used": _f(print_stats.get("filament_used")),
                "estimated_time": self._metadata.get("estimated_time"),
                "message": print_stats.get("message") or display_status.get("message"),
                "ams": [dict(u, tray=[dict(t) for t in u["tray"]]) for u in self._ams_units],
                "vt_tray": [dict(self._external_tray)],
                "mmu": dict(self._mmu_info),
            }

            self._emit(previous_state, was_connected)
            self._last_state = self.state.state
            self._has_sample = True
        return True

    # -------------------------------------------------------- skip objects

    def _apply_exclude_object(self, status: dict[str, Any]) -> None:
        """Klipper's ``exclude_object`` in the shape the skip-objects UI reads (C5).

        Upstream's modal, its camera-pick overlay and its route are entirely
        provider-agnostic — they read ``printable_objects`` and
        ``skipped_objects`` off the state and post a list of integer ids back.
        Klipper names its objects instead of numbering them, so the id is the
        object's index in the list Klipper reports, and ``_object_names`` keeps
        the mapping for ``skip_objects`` to translate back. That list is stable
        for the duration of a print: it comes from the ``EXCLUDE_OBJECT_DEFINE``
        lines the slicer wrote into the file being printed.
        """
        exclude = status.get("exclude_object")
        if not isinstance(exclude, dict):
            return
        objects = exclude.get("objects")
        if not isinstance(objects, list):
            return

        names: list[str] = []
        printable: dict[int, dict[str, Any]] = {}
        xs: list[float] = []
        ys: list[float] = []
        for index, obj in enumerate(objects):
            if not isinstance(obj, dict) or not obj.get("name"):
                continue
            name = str(obj["name"])
            center = obj.get("center") if isinstance(obj.get("center"), (list, tuple)) else None
            x = _f(center[0]) if center and len(center) > 0 else None
            y = _f(center[1]) if center and len(center) > 1 else None
            names.append(name)
            printable[index] = {"name": name, "x": x, "y": y}
            for point in obj.get("polygon") or []:
                if isinstance(point, (list, tuple)) and len(point) >= 2:
                    xs.append(_f(point[0]))
                    ys.append(_f(point[1]))

        self._object_names = names
        self.state.printable_objects = printable
        self.state.printable_objects_count = len(printable)
        excluded = {str(n) for n in (exclude.get("excluded_objects") or [])}
        self.state.skipped_objects = [i for i, name in enumerate(names) if name in excluded]
        # The pick-on-camera overlay maps a click onto the bed through this box.
        # Only from polygons: an object's centre alone gives a box with no area.
        if xs and ys:
            self.state.printable_objects_bbox_all = [min(xs), min(ys), max(xs), max(ys)]

    def skip_objects(self, object_ids: list[int]) -> bool:
        """Exclude objects from the running print (C5).

        Klipper cancels by name and has no batch form, so this sends one
        ``EXCLUDE_OBJECT`` per id. A partial failure is reported as failure but
        leaves the objects that did go through excluded — the next poll reads
        the truth back off ``exclude_object`` either way, so the card never
        shows a skip that did not happen.
        """
        if not self._object_names:
            return False
        ok = True
        for object_id in object_ids:
            if not 0 <= int(object_id) < len(self._object_names):
                ok = False
                continue
            name = self._object_names[int(object_id)]
            if not self.send_gcode(f'EXCLUDE_OBJECT NAME="{name}"'):
                ok = False
        return ok

    # ------------------------------------------------------ Happy Hare MMU

    def _apply_mmu(self, status: dict[str, Any]) -> None:
        """Turn Happy Hare's state into AMS units the card and the tracker understand.

        Gate ``g`` becomes AMS unit ``g // 4``, tray ``g % 4`` — so Bambuddy's
        global tray id equals the gate number and ``ams_mapping`` from the
        queue can be sent to Happy Hare as a tool-to-gate map unchanged.
        """
        mmu = status.get("mmu") or {}
        variables = (status.get("save_variables") or {}).get("variables") or {}
        machine = status.get("mmu_machine") or {}
        if not isinstance(mmu, dict) or not mmu.get("enabled"):
            self._ams_units = []
            self._mmu_info = {"enabled": False}
            return

        num_gates = int(mmu.get("num_gates") or machine.get("num_gates") or 0)
        self._mmu_num_gates = num_gates
        colors = variables.get("mmu_state_gate_color") or []
        materials = variables.get("mmu_state_gate_material") or []
        names = variables.get("mmu_state_gate_filament_name") or []
        statuses = variables.get("mmu_state_gate_status") or []
        temps = variables.get("mmu_state_gate_temperature") or []
        spool_ids = variables.get("mmu_state_gate_spool_id") or []

        def _at(seq: list, i: int, default: Any = None) -> Any:
            return seq[i] if i < len(seq) else default

        gate = int(mmu.get("gate", -1) if mmu.get("gate") is not None else -1)
        loaded = str(mmu.get("filament") or "").lower() == "loaded"
        version = str(machine.get("happy_hare_version") or "")

        units: list[dict[str, Any]] = []
        for unit_index in range((num_gates + 3) // 4):
            trays: list[dict[str, Any]] = []
            for slot in range(4):
                g = unit_index * 4 + slot
                if g >= num_gates:
                    break
                gate_status = int(_at(statuses, g, -1) or 0)
                color = str(_at(colors, g, "") or "").lstrip("#").upper()
                if len(color) == 6:
                    color += "FF"
                temp = _at(temps, g)
                trays.append(
                    {
                        "id": slot,
                        "tray_type": str(_at(materials, g, "") or ""),
                        "tray_sub_brands": str(_at(names, g, "") or ""),
                        "tray_color": color,
                        "tray_info_idx": "",
                        "tray_id_name": "",
                        "remain": -1,
                        "tag_uid": "",
                        "tray_uuid": "",
                        "nozzle_temp_min": int(temp) if isinstance(temp, (int, float)) and temp > 0 else None,
                        "nozzle_temp_max": int(temp) if isinstance(temp, (int, float)) and temp > 0 else None,
                        "state": 11 if (loaded and g == gate) else (10 if gate_status > 0 else 9),
                        "exists": gate_status > 0,
                        "spoolman_id": _at(spool_ids, g) if (_at(spool_ids, g) or -1) >= 0 else None,
                    }
                )
            units.append(
                {
                    "id": unit_index,
                    "humidity": None,
                    "temp": None,
                    "is_ams_ht": False,
                    "tray": trays,
                    "serial_number": f"MMU-{self.serial_number}-{unit_index}",
                    "sw_ver": version,
                    "dry_time": 0,
                    "dry_status": 0,
                    "dry_sub_status": 0,
                }
            )
        self._ams_units = units
        self._mmu_info = {
            "enabled": True,
            "version": version,
            "num_gates": num_gates,
            "gate": gate,
            "tool": mmu.get("tool"),
            "next_tool": mmu.get("next_tool"),
            "filament": mmu.get("filament"),
            "action": mmu.get("action"),
            "print_state": mmu.get("print_state"),
            "is_locked": bool(mmu.get("is_locked")),
            "is_paused": bool(mmu.get("is_paused")),
            "reason_for_pause": mmu.get("reason_for_pause") or "",
            "has_bypass": bool(mmu.get("has_bypass")),
            "ttg_map": list(self.last_ttg_map) if self.last_ttg_map else None,
        }
        # A loaded gate is the tray in the hotend; the bypass keeps tray 254.
        if loaded and 0 <= gate < num_gates:
            self.state.tray_now = gate
        elif loaded and gate < 0 and mmu.get("has_bypass"):
            self.state.tray_now = 254
        else:
            self.state.tray_now = 255
        next_tool = mmu.get("next_tool")
        self.state.tray_tar = int(next_tool) if isinstance(next_tool, int) and next_tool >= 0 else 255

    def _mmu_gate(self, ams_id: int, tray_id: int) -> int | None:
        """AMS unit/tray -> Happy Hare gate, or None when that slot is not an MMU gate."""
        if not self._mmu or int(ams_id) == 255:
            return None
        gate = int(ams_id) * 4 + int(tray_id)
        return gate if 0 <= gate < self._mmu_num_gates else None

    def ams_load_filament(self, tray_id: int, extruder_id: int | None = None) -> bool:  # noqa: ARG002
        """Select a gate and load it to the nozzle (``tray_id`` is the global tray id = gate)."""
        if not self._mmu:
            return False
        if int(tray_id) >= 254:
            return self.send_gcode("MMU_SELECT_BYPASS")
        if not 0 <= int(tray_id) < self._mmu_num_gates:
            return False
        return self.send_gcode(f"MMU_SELECT GATE={int(tray_id)}\nMMU_LOAD")

    def ams_unload_filament(self, tray_id: int | None = None) -> bool:  # noqa: ARG002
        if not self._mmu:
            return False
        return self.send_gcode("MMU_UNLOAD")

    def ams_control(self, action: str) -> bool:
        if not self._mmu:
            return False
        command = {"resume": "MMU_UNLOCK", "pause": "MMU_PAUSE", "reset": "MMU_RECOVER", "done": "MMU_UNLOCK"}.get(
            str(action).lower()
        )
        return self.send_gcode(command) if command else False

    def _timelapse_recording(self) -> bool:
        """Whether moonraker-timelapse is installed *and* switched on (C4).

        Gates upstream's whole timelapse flow, which is why it is asked here
        rather than assumed: a Klipper printer with the component installed but
        the toggle off produces no video, and a scan for one would poll a
        printer for two minutes on every single print for nothing.

        Read at the lifecycle transition rather than cached at connect —
        moonraker-timelapse's own UI can flip `enabled` between prints, which
        is exactly how people use it.
        """
        if not self._timelapse_component:
            return False
        try:
            settings = self._get("machine/timelapse/settings")
        except Exception:  # noqa: BLE001 - component gone or host busy; claim nothing
            return False
        return bool(settings.get("enabled"))

    def _load_metadata(self, filename: str) -> None:
        if filename == self._metadata_for:
            return
        self._metadata_for = filename
        self._metadata = {}
        try:
            meta = self._get(f"server/files/metadata?filename={quote(filename, safe='')}")
            self._metadata = {
                "estimated_time": _f(meta.get("estimated_time")) or None,
                "layer_count": meta.get("layer_count"),
                "filament_total": meta.get("filament_total"),
                "thumbnails": meta.get("thumbnails") or [],
            }
        except Exception:  # noqa: BLE001 - metadata is a nicety
            pass

    def _remaining_minutes(self, duration: float, progress_percent: float) -> int:
        estimated = self._metadata.get("estimated_time")
        if estimated:
            return int(max(float(estimated) - duration, 0.0) // 60)
        if progress_percent <= 0 or duration <= 0:
            return 0
        total = duration / min(progress_percent / 100.0, 1.0)
        return int(max(total - duration, 0.0) // 60)

    def _lifecycle_payload(self) -> dict[str, Any]:
        return {
            "filename": self.state.gcode_file,
            "subtask_name": self.state.subtask_name,
            "remaining_time": self.state.remaining_time * 60 if self.state.remaining_time > 0 else None,
            "raw_data": self.state.raw_data,
            "ams_mapping": None,
            "timelapse_was_active": self._timelapse_recording(),
            "hms_errors": [],
            "last_progress": self._last_progress,
            "last_layer_num": self._last_layer_num,
        }

    def _emit(self, previous_state: str | None, was_connected: bool) -> None:
        active_now = self.state.state in ("RUNNING", "PAUSE")
        active_before = previous_state in ("RUNNING", "PAUSE")

        if previous_state is None:
            # First sample after (re)connect. A print already running is the
            # restart-recovery case: main.py wants on_print_running_observed,
            # never a synthetic start.
            if active_now and self.on_print_running_observed:
                self.on_print_running_observed(self._lifecycle_payload())
                self._print_started_at = time.monotonic()
        elif not active_before and active_now:
            self._print_started_at = time.monotonic()
            self._last_progress = 0.0
            self._last_layer_num = 0
            if self.on_print_start:
                logger.info("[%s] PRINT START detected - file: %s", self.serial_number, self.state.gcode_file)
                self.on_print_start(self._lifecycle_payload())
        elif active_before and not active_now:
            payload = self._lifecycle_payload()
            if self.state.state == "FINISH":
                payload["status"] = "completed"
            elif self.state.state == "FAILED":
                payload["status"] = "failed"
            else:
                payload["status"] = "aborted"
            if self._print_started_at is not None:
                payload["actual_time_seconds"] = max(1, int(time.monotonic() - self._print_started_at))
                self._print_started_at = None
            if self.on_print_complete:
                logger.info(
                    "[%s] PRINT %s - file: %s", self.serial_number, payload["status"].upper(), payload["filename"]
                )
                self.on_print_complete(payload)

        if active_now:
            self._last_progress = self.state.progress
            self._last_layer_num = self.state.layer_num
            percent = int(self.state.progress)
            if percent != self._last_percent and self.on_print_progress:
                self.on_print_progress(percent)
            self._last_percent = percent
            if self.state.layer_num != self._last_layer and self.on_layer_change:
                self.on_layer_change(self.state.layer_num)
            self._last_layer = self.state.layer_num
        else:
            self._last_percent = -1
            self._last_layer = -1

        bed = self.state.temperatures.get("bed")
        if isinstance(bed, (int, float)) and bed != self._last_bed_temp:
            self._last_bed_temp = float(bed)
            if self.on_bed_temp_update:
                self.on_bed_temp_update(float(bed))

        if self.on_state_change:
            self.on_state_change(self.state)

    # ------------------------------------------------------------ commands

    def start_print(
        self,
        filename: str,
        plate_id: int = 1,  # noqa: ARG002
        ams_mapping: list[int] | None = None,
        **_bambu_options: Any,
    ) -> bool:
        """Start ``filename`` (relative to Moonraker's gcodes root). Upload first with ``upload_file``.

        With a Happy Hare MMU, ``ams_mapping`` (slicer filament slot -> global
        tray id, i.e. gate) is pushed as the tool-to-gate map first, so a plate
        sliced with T0/T1 can print from whichever gates hold the right spools.
        """
        target = filename.lstrip("/")
        if self._mmu and ams_mapping:
            self.last_ttg_map = None
            gates: list[int] = []
            for slot, tray in enumerate(ams_mapping):
                tray = int(tray) if isinstance(tray, (int, float)) else -1
                gates.append(tray if 0 <= tray < self._mmu_num_gates else slot)
            # Always sent, also for the identity map: the previous print may
            # have left a different map behind.
            if not self.send_gcode("MMU_TTG_MAP MAP=" + ",".join(str(g) for g in gates)):
                return False
            self.last_ttg_map = gates
        return self._safe_call(
            f"print start {target}",
            lambda: self._post(f"printer/print/start?filename={quote(target, safe='/')}", timeout=30.0),
        )

    def stop_print(self) -> bool:
        return self._safe_call("print cancel", lambda: self._post("printer/print/cancel", timeout=30.0))

    def pause_print(self) -> bool:
        return self._safe_call("print pause", lambda: self._post("printer/print/pause", timeout=30.0))

    def resume_print(self) -> bool:
        return self._safe_call("print resume", lambda: self._post("printer/print/resume", timeout=30.0))

    def send_gcode(self, gcode: str) -> bool:
        return self._safe_call(
            f"gcode {gcode!r}",
            lambda: self._post(f"printer/gcode/script?script={quote(gcode, safe='')}", timeout=60.0),
        )

    def set_bed_temperature(self, target: int) -> bool:
        return self.send_gcode(f"M140 S{int(target)}")

    def set_nozzle_temperature(self, target: int, nozzle: int = 0) -> bool:
        return self.send_gcode(f"M104 S{int(target)} T{int(nozzle)}")

    def set_chamber_temperature(self, target: int) -> bool:
        if not self._chamber_object or not self._chamber_object.startswith("heater_generic"):
            return False
        heater = self._chamber_object.split(" ", 1)[1]
        return self.send_gcode(f"SET_HEATER_TEMPERATURE HEATER={heater} TARGET={int(target)}")

    def set_fan_speed(self, fan: int, speed: int) -> bool:
        # Bambu fan ids: 1 = part cooling; the rest have no Klipper counterpart.
        if int(fan) != 1:
            return False
        return self.set_part_fan(speed)

    def set_part_fan(self, speed: int) -> bool:
        pwm = max(0, min(255, int(round(int(speed) * 255 / 100))))
        return self.send_gcode(f"M106 S{pwm}")

    def set_chamber_light(self, on: bool) -> bool:
        if not self._light_object:
            return False
        kind, _, name = self._light_object.partition(" ")
        if kind == "output_pin":
            return self.send_gcode(f"SET_PIN PIN={name} VALUE={1 if on else 0}")
        level = 1 if on else 0
        return self.send_gcode(f"SET_LED LED={name} RED={level} GREEN={level} BLUE={level} WHITE={level}")

    def home_axes(self, axes: str = "XYZ") -> bool:
        axes = "".join(a for a in axes.upper() if a in "XYZ")
        return self.send_gcode("G28" if axes in ("", "XYZ") else f"G28 {' '.join(axes)}")

    def move_axis(self, axis: str, distance: float, speed: int = 3000) -> bool:
        axis = axis.upper()
        if axis not in ("X", "Y", "Z", "E"):
            return False
        return self.send_gcode(f"G91\nG1 {axis}{float(distance)} F{int(speed)}\nG90")

    def disable_motors(self) -> bool:
        return self.send_gcode("M84")

    def enable_motors(self) -> bool:
        return False

    def set_print_speed(self, mode: int) -> bool:
        # Bambu levels: 1 silent, 2 standard, 3 sport, 4 ludicrous -> feedrate %.
        factor = {1: 50, 2: 100, 3: 125, 4: 166}.get(int(mode))
        if factor is None:
            return False
        self.state.speed_level = int(mode)
        return self.send_gcode(f"M220 S{factor}")

    # ---------------------------------------------------- external spool slot

    def ams_set_filament_setting(
        self,
        ams_id: int,
        tray_id: int,
        tray_info_idx: str,
        tray_type: str,
        tray_sub_brands: str,
        tray_color: str,
        nozzle_temp_min: int,
        nozzle_temp_max: int,
        setting_id: str = "",  # noqa: ARG002
    ) -> bool:
        """Record what is loaded on the single external slot (AMS 255 / tray 0).

        Bambu firmware stores this on the printer; Klipper has nowhere to put
        it, so it lives on the client and is reported back in ``vt_tray`` so the
        card and the usage tracker see the same slot a Bambu would show.
        """
        gate = self._mmu_gate(ams_id, tray_id)
        if gate is not None:
            # Happy Hare keeps the gate map itself; MMU_GATE_MAP persists it.
            temp = None
            for candidate in (nozzle_temp_max, nozzle_temp_min):
                if isinstance(candidate, (int, float)) and candidate > 0:
                    temp = int(candidate)
                    break
            parts = [f"MMU_GATE_MAP GATE={gate}"]
            parts.append(f'MATERIAL="{(tray_type or "").strip()}"')
            parts.append(f'COLOR="{(tray_color or "").lstrip("#")[:6].lower()}"')
            parts.append(f'NAME="{(tray_sub_brands or "").strip()}"')
            if temp:
                parts.append(f"TEMP={temp}")
            parts.append("AVAILABLE=1")
            return self.send_gcode(" ".join(parts))
        if int(ams_id) != 255 or int(tray_id) != 0:
            return False
        self._external_tray.update(
            {
                "tray_info_idx": tray_info_idx or "",
                "tray_type": tray_type or "",
                "tray_sub_brands": tray_sub_brands or "",
                "tray_color": (tray_color or "").lstrip("#").upper(),
                "nozzle_temp_min": nozzle_temp_min,
                "nozzle_temp_max": nozzle_temp_max,
                "state": 11 if tray_type else 10,
            }
        )
        self.state.raw_data["vt_tray"] = [dict(self._external_tray)]
        if self.on_state_change:
            self.on_state_change(self.state)
        return True

    def reset_ams_slot(self, ams_id: int, tray_id: int) -> bool:
        gate = self._mmu_gate(ams_id, tray_id)
        if gate is not None:
            return self.send_gcode(f'MMU_GATE_MAP GATE={gate} MATERIAL="" COLOR="" NAME="" AVAILABLE=0')
        if int(ams_id) != 255 or int(tray_id) != 0:
            return False
        self._external_tray = dict(_EMPTY_EXTERNAL_TRAY)
        self.state.raw_data["vt_tray"] = [dict(self._external_tray)]
        if self.on_state_change:
            self.on_state_change(self.state)
        return True

    def send_command(self, command: dict) -> None:  # noqa: ARG002 - raw MQTT has no Moonraker equivalent
        logger.debug("[%s] raw MQTT command ignored on a Klipper printer", self.serial_number)

    def publish_raw(self, topic: str, payload: Any, qos: int = 1) -> bool:  # noqa: ARG002
        return False

    def register_raw_message_handler(self, handler: Any) -> None:  # noqa: ARG002
        return None

    def unregister_raw_message_handler(self, handler: Any) -> None:  # noqa: ARG002
        return None

    # --------------------------------------------------------------- files

    def upload_file(self, local_path: Path, remote_name: str, *, progress_callback: Any = None) -> bool:  # noqa: ARG002
        """Upload ``local_path`` into Moonraker's gcodes root as ``remote_name``."""
        name = remote_name.lstrip("/")
        directory, _, basename = name.rpartition("/")
        with open(local_path, "rb") as fh:
            response = httpx.post(
                f"{self.base_url}/server/files/upload",
                data={"root": "gcodes", "path": directory},
                files={"file": (basename, fh, "application/octet-stream")},
                headers=self._headers(),
                timeout=max(self.timeout, 300.0),
            )
        response.raise_for_status()
        self._record_log("sent", f"upload {name}")
        return True

    def delete_file(self, remote_name: str) -> bool:
        name = remote_name.lstrip("/")
        return self._safe_call(
            f"delete {name}",
            lambda: httpx.delete(
                f"{self.base_url}/server/files/gcodes/{quote(name, safe='/')}",
                headers=self._headers(),
                timeout=self.timeout,
            ).raise_for_status(),
        )

    def list_files(self, path: str = "/") -> list[dict[str, Any]]:
        """Directory listing of Moonraker's ``gcodes`` root, same shape as the Bambu FTP listing."""
        sub = path.strip("/")
        target = f"gcodes/{sub}" if sub else "gcodes"
        result = self._get(f"server/files/directory?path={quote(target, safe='/')}&extended=false")
        out: list[dict[str, Any]] = []
        for d in result.get("dirs") or []:
            name = str(d.get("dirname") or "")
            if not name or name.startswith("."):
                continue
            out.append({"name": name, "is_directory": True, "size": 0, "modified": d.get("modified")})
        for f in result.get("files") or []:
            name = str(f.get("filename") or "")
            if not name:
                continue
            out.append(
                {
                    "name": name,
                    "is_directory": False,
                    "size": int(f.get("size") or 0),
                    "modified": f.get("modified"),
                }
            )
        return out

    def download_file(self, remote_name: str) -> bytes:
        name = remote_name.lstrip("/")
        response = httpx.get(
            f"{self.base_url}/server/files/gcodes/{quote(name, safe='/')}",
            headers=self._headers(),
            timeout=max(self.timeout, 300.0),
        )
        response.raise_for_status()
        return response.content

    def get_storage_info(self) -> dict[str, Any]:
        """``{used_bytes, free_bytes}`` for the gcodes root, from Moonraker's disk usage."""
        try:
            result = self._get("server/files/directory?path=gcodes&extended=false")
            usage = result.get("disk_usage") or {}
            return {"used_bytes": usage.get("used"), "free_bytes": usage.get("free")}
        except Exception:  # noqa: BLE001
            return {"used_bytes": None, "free_bytes": None}

    # ------------------------------------------------------------- logging

    def _record_log(self, direction: str, text: str) -> None:
        if not self._logging_enabled:
            return
        self._logs.append(
            MQTTLogEntry(
                timestamp=datetime.now(timezone.utc).isoformat(),
                topic="moonraker",
                direction="out" if direction == "sent" else "in",
                payload={"result": direction, "text": text},
            )
        )
        del self._logs[:-500]

    def enable_logging(self, enabled: bool = True) -> None:
        self._logging_enabled = enabled

    def get_logs(self) -> list:
        return list(self._logs)

    def clear_logs(self) -> None:
        self._logs.clear()

    @property
    def logging_enabled(self) -> bool:
        return self._logging_enabled

    # ------------------------------------------------- Bambu-only fallbacks

    def __getattr__(self, name: str):
        """Bambu-only methods (AMS, K-profiles, calibration, drying, xcam...) fail soft.

        Only reached for attributes not defined above. Private names raise as
        usual so genuine bugs stay visible.
        """
        if name.startswith("_"):
            raise AttributeError(name)

        def _unsupported(*args: Any, **kwargs: Any) -> bool:  # noqa: ARG001
            logger.debug("[%s] %s() is not available on a Klipper printer", self.serial_number, name)
            return False

        return _unsupported


def probe_moonraker(base_url: str, auth_token: str | None = None, timeout: float = 5.0) -> dict[str, Any]:
    """One-shot connection test used by the add-printer dialog.

    Returns the same shape as ``printer_manager.test_connection``: ``success``
    plus a ``message``, and on success the Klipper hostname, Moonraker
    version, and the webcams Moonraker knows about so the caller can
    prefill the external camera.
    """
    client = MoonrakerClient(base_url, auth_token, timeout=timeout)
    try:
        info = client._get("server/info")
        printer_info = client._get("printer/info")
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        message = (
            "Moonraker rejected the request (401): an API key is required"
            if code == 401
            else f"Moonraker answered HTTP {code}"
        )
        return {"success": False, "message": message}
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "message": f"Could not reach Moonraker at {client.base_url}: {exc}"}

    webcams: list[dict[str, Any]] = []
    try:
        for cam in client._get("server/webcams/list").get("webcams") or []:
            if not cam.get("enabled", True):
                continue
            stream = str(cam.get("stream_url") or "")
            snapshot = str(cam.get("snapshot_url") or "")
            origin = f"{urlparse(client.base_url).scheme}://{urlparse(client.base_url).hostname}"
            webcams.append(
                {
                    "name": cam.get("name"),
                    "stream_url": stream if "://" in stream else origin + "/" + stream.lstrip("/"),
                    "snapshot_url": snapshot if "://" in snapshot else origin + "/" + snapshot.lstrip("/"),
                }
            )
    except Exception:  # noqa: BLE001 - webcams are optional
        pass

    return {
        "success": True,
        "message": "Connected to Moonraker",
        "hostname": printer_info.get("hostname"),
        "klippy_state": info.get("klippy_state"),
        "moonraker_version": info.get("moonraker_version"),
        "klipper_version": printer_info.get("software_version"),
        "webcams": webcams,
    }
