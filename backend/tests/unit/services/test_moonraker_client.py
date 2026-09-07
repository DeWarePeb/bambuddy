"""Voron patch series: MoonrakerClient state mapping, dispatch helpers, schema identity."""

import zipfile
from pathlib import Path

import pytest

from backend.app.schemas.printer import PrinterCreate, klipper_identity_from_url
from backend.app.services.moonraker_client import MoonrakerClient, map_moonraker_state
from backend.app.services.moonraker_dispatch import extract_plate_gcode, moonraker_remote_filename

# --------------------------------------------------------------------- state


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("printing", "RUNNING"),
        ("paused", "PAUSE"),
        ("complete", "FINISH"),
        ("cancelled", "FAILED"),
        ("error", "FAILED"),
        ("standby", "IDLE"),
        ("ready", "IDLE"),
        (None, "unknown"),
        ("something-new", "unknown"),
    ],
)
def test_map_moonraker_state(raw, expected):
    assert map_moonraker_state(raw) == expected


def _status(state="printing", progress=0.42, filename="gcodes/keychain.gcode", layer=12, total=80, duration=600.0):
    return {
        "print_stats": {
            "state": state,
            "filename": filename,
            "print_duration": duration,
            "filament_used": 1234.5,
            "info": {"current_layer": layer, "total_layer": total},
        },
        "virtual_sdcard": {"progress": progress},
        "display_status": {"progress": progress},
        "extruder": {"temperature": 214.9, "target": 215.0},
        "heater_bed": {"temperature": 60.1, "target": 60.0},
        "toolhead": {"homed_axes": "xyz"},
        "fan": {"speed": 0.5},
    }


class _FakeMoonraker(MoonrakerClient):
    """Feed canned object queries instead of HTTP."""

    def __init__(self, statuses, **kwargs):
        super().__init__("http://voron.test", **kwargs)
        self._statuses = list(statuses)
        self.metadata = {"estimated_time": 3600.0, "layer_count": 80}

    def _query(self, objects):  # noqa: ARG002
        return self._statuses.pop(0)

    def _get(self, path, timeout=None):  # noqa: ARG002
        if path.startswith("server/files/metadata"):
            return dict(self.metadata)
        raise AssertionError(f"unexpected GET {path}")


def test_status_poll_fills_printer_state():
    client = _FakeMoonraker([_status()], serial_number="KLIPPER-VORON")
    client.request_status_update()

    s = client.state
    assert s.connected is True
    assert s.state == "RUNNING"
    assert s.gcode_file == "keychain.gcode"
    assert s.subtask_name == "keychain.gcode"
    assert s.progress == 42.0
    assert s.layer_num == 12 and s.total_layers == 80
    # estimated 3600 s, 600 s elapsed -> 50 min left
    assert s.remaining_time == 50
    assert s.temperatures["nozzle"] == 214.9
    assert s.temperatures["bed_target"] == 60.0
    assert s.cooling_fan_speed == 50
    assert s.raw_data["provider"] == "klipper"
    assert s.raw_data["ams"] == []


def test_print_start_and_complete_callbacks_fire_on_transitions():
    events: list[tuple[str, object]] = []
    client = _FakeMoonraker(
        [
            _status(state="standby", progress=0.0, filename=""),
            _status(state="printing", progress=0.1),
            _status(state="printing", progress=0.5, layer=40),
            _status(state="complete", progress=1.0, layer=80),
        ],
        serial_number="KLIPPER-VORON",
        on_print_start=lambda d: events.append(("start", d)),
        on_print_complete=lambda d: events.append(("complete", d)),
        on_print_progress=lambda p: events.append(("progress", p)),
        on_layer_change=lambda n: events.append(("layer", n)),
    )
    for _ in range(4):
        client.request_status_update()

    kinds = [k for k, _ in events]
    assert kinds.count("start") == 1
    assert kinds.count("complete") == 1
    start = next(d for k, d in events if k == "start")
    assert start["filename"] == "keychain.gcode"
    assert start["subtask_name"] == "keychain.gcode"
    complete = next(d for k, d in events if k == "complete")
    assert complete["status"] == "completed"
    assert complete["hms_errors"] == []
    assert ("progress", 50) in events
    assert ("layer", 40) in events
    assert client.state.progress == 100.0


def test_first_sample_while_printing_reports_running_observed_not_start():
    events: list[str] = []
    client = _FakeMoonraker(
        [_status(state="printing")],
        on_print_start=lambda d: events.append("start"),  # noqa: ARG005
        on_print_running_observed=lambda d: events.append("observed"),  # noqa: ARG005
    )
    client.request_status_update()
    assert events == ["observed"]


def test_cancelled_print_reports_failed():
    events: list[dict] = []
    client = _FakeMoonraker(
        [_status(state="printing"), _status(state="cancelled", progress=0.3)],
        on_print_complete=events.append,
    )
    client.request_status_update()
    client.request_status_update()
    assert events and events[0]["status"] == "failed"


def test_bambu_only_methods_fail_soft():
    client = MoonrakerClient("http://voron.test")
    assert client.ams_load_filament(1) is False
    assert client.set_kprofile("x") is False
    assert client.start_calibration() is False
    assert client.send_drying_command(0, 50, 60) is False
    with pytest.raises(AttributeError):
        _ = client._does_not_exist  # private names still raise


def test_gcode_helpers_build_expected_commands():
    sent: list[str] = []
    client = MoonrakerClient("http://voron.test")
    client.send_gcode = lambda g: (sent.append(g), True)[1]  # type: ignore[method-assign]

    client.set_part_fan(100)
    client.set_bed_temperature(60)
    client.set_nozzle_temperature(215)
    client.home_axes("XY")
    client.move_axis("Z", 10, 600)
    client.set_print_speed(3)

    assert sent[0] == "M106 S255"
    assert sent[1] == "M140 S60"
    assert sent[2] == "M104 S215 T0"
    assert sent[3] == "G28 X Y"
    assert sent[4] == "G91\nG1 Z10.0 F600\nG90"
    assert sent[5] == "M220 S125"
    assert client.set_fan_speed(2, 50) is False  # aux fan has no Klipper equivalent


# ------------------------------------------------------------------ dispatch


@pytest.mark.parametrize(
    ("filename", "plate", "expected"),
    [
        ("keychain.gcode.3mf", 1, "keychain.gcode"),
        ("keychain.3mf", None, "keychain.gcode"),
        ("keychain.gcode", 1, "keychain.gcode"),
        ("Multi Plate.gcode.3mf", 2, "Multi Plate_plate2.gcode"),
        ("weird/name#1?.3mf", 1, "name_1_.gcode"),
    ],
)
def test_moonraker_remote_filename(filename, plate, expected):
    assert moonraker_remote_filename(filename, plate) == expected


def _make_3mf(tmp_path: Path, plates: dict[int, bytes]) -> Path:
    path = tmp_path / "job.gcode.3mf"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        for plate_id, gcode in plates.items():
            zf.writestr(f"Metadata/plate_{plate_id}.gcode", gcode)
    return path


def test_extract_plate_gcode_picks_requested_plate(tmp_path):
    threemf = _make_3mf(tmp_path, {1: b"; plate one\nG28\n", 2: b"; plate two\nG28\n"})
    out = extract_plate_gcode(threemf, 2)
    try:
        assert out.read_bytes() == b"; plate two\nG28\n"
    finally:
        out.unlink(missing_ok=True)


def test_extract_plate_gcode_falls_back_to_single_plate(tmp_path):
    threemf = _make_3mf(tmp_path, {3: b"; only plate\n"})
    out = extract_plate_gcode(threemf, 1)
    try:
        assert out.read_bytes() == b"; only plate\n"
    finally:
        out.unlink(missing_ok=True)


def test_extract_plate_gcode_rejects_unsliced_project(tmp_path):
    path = tmp_path / "project.3mf"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("3D/3dmodel.model", "<model/>")
    with pytest.raises(ValueError, match="not sliced"):
        extract_plate_gcode(path, 1)


# -------------------------------------------------------------------- schema


def test_klipper_identity_from_url():
    assert klipper_identity_from_url("http://192.168.2.177") == ("KLIPPER-192-168-2-177", "192.168.2.177")
    assert klipper_identity_from_url("voron.local:7125") == ("KLIPPER-VORON-LOCAL", "voron.local")


def test_printer_create_derives_klipper_identity():
    p = PrinterCreate(name="Voron", provider="klipper", api_url="192.168.2.177")
    assert p.api_url == "http://192.168.2.177"
    assert p.serial_number == "KLIPPER-192-168-2-177"
    assert p.ip_address == "192.168.2.177"
    assert p.access_code == "-"


def test_printer_create_bambu_still_requires_access_code():
    with pytest.raises(ValueError):
        PrinterCreate(name="P2S", serial_number="01P00A000000000", ip_address="192.168.2.4")


def test_printer_create_klipper_requires_api_url():
    with pytest.raises(ValueError):
        PrinterCreate(name="Voron", provider="klipper")

