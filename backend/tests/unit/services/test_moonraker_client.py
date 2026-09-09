"""Voron patch series: MoonrakerClient state mapping, dispatch helpers, schema identity."""

import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from backend.app.schemas.printer import PrinterCreate, klipper_identity_from_url
from backend.app.services.moonraker_client import (
    MAX_DOWNLOAD_BYTES,
    MoonrakerClient,
    MoonrakerDownloadTooLarge,
    _declared_length,
    map_moonraker_state,
)
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


def test_pick_light_object_prefers_chamber_light_over_mmu_leds():
    from backend.app.services.moonraker_client import _pick_light_object

    voron = [
        "neopixel _unit0_gate0_leds",
        "neopixel _unit0_gate1_leds",
        "neopixel _unit0_gate0_box",
        "neopixel jw_leds",
        "neopixel chamber_lights",
        "output_pin caselight",
        "fan",
    ]
    assert _pick_light_object(voron) == "neopixel chamber_lights"
    assert _pick_light_object(["output_pin caselight", "neopixel sb_leds"]) == "output_pin caselight"
    assert _pick_light_object(["neopixel _unit0_gate0_leds", "neopixel jw_leds"]) is None


def test_external_slot_is_reported_as_virtual_tray_254():
    client = _FakeMoonraker([_status(state="printing"), _status(state="standby", filename="")])
    client.request_status_update()
    vt = client.state.raw_data["vt_tray"]
    assert vt and vt[0]["id"] == 254 and vt[0]["tray_type"] == ""
    assert client.state.tray_now == 254  # printing -> external slot is "in use"
    client.request_status_update()
    assert client.state.tray_now == 255  # idle -> nothing loaded into the hotend

    assert client.ams_set_filament_setting(255, 0, "GFB00", "ABS", "ABS", "#FD8700", 240, 270) is True
    tray = client.state.raw_data["vt_tray"][0]
    assert tray["tray_type"] == "ABS" and tray["tray_color"] == "FD8700" and tray["state"] == 11
    assert client.ams_set_filament_setting(0, 1, "", "PLA", "", "", 0, 0) is False  # no AMS on Klipper
    assert client.reset_ams_slot(255, 0) is True
    assert client.state.raw_data["vt_tray"][0]["tray_type"] == ""


# ------------------------------------------------------------------ downloads


class _FakeStream:
    """Stands in for the context manager ``httpx.stream`` returns."""

    def __init__(self, chunks, content_length=None, status_error=None):
        self._chunks = chunks
        self.headers = {} if content_length is None else {"content-length": str(content_length)}
        self._status_error = status_error

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        if self._status_error:
            raise self._status_error

    def iter_bytes(self):
        yield from self._chunks


@pytest.mark.parametrize(
    ("header", "expected"),
    [(None, None), ("12", 12), ("0", 0), ("not-a-number", None), ("", None)],
)
def test_declared_length(header, expected):
    """A malformed Content-Length must read as "unknown", not raise: the body
    still has to be counted either way."""
    assert _declared_length(header) == expected


def test_download_file_returns_a_small_file():
    client = MoonrakerClient("http://printer:7125")
    with patch("backend.app.services.moonraker_client.httpx.stream") as stream:
        stream.return_value = _FakeStream([b"G28", b"\nG1"], content_length=6)
        assert client.download_file("a.gcode") == b"G28\nG1"


def test_download_file_refuses_a_declared_size_over_the_cap():
    """Refused before a byte of the body is read."""
    client = MoonrakerClient("http://printer:7125")
    with patch("backend.app.services.moonraker_client.httpx.stream") as stream:
        stream.return_value = _FakeStream([b"x"], content_length=MAX_DOWNLOAD_BYTES + 1)
        with pytest.raises(MoonrakerDownloadTooLarge):
            client.download_file("huge.gcode")


def test_download_file_counts_the_body_when_no_length_is_declared():
    """Content-Length is the server's claim, and a chunked response has none —
    so the arriving bytes are counted too."""
    client = MoonrakerClient("http://printer:7125")
    with patch("backend.app.services.moonraker_client.httpx.stream") as stream:
        stream.return_value = _FakeStream([b"abc", b"def"], content_length=None)
        with pytest.raises(MoonrakerDownloadTooLarge):
            client.download_file("chunked.gcode", max_bytes=4)


def test_download_file_accepts_a_body_exactly_at_the_cap():
    """The limit is inclusive; an off-by-one here would refuse a legal file."""
    client = MoonrakerClient("http://printer:7125")
    with patch("backend.app.services.moonraker_client.httpx.stream") as stream:
        stream.return_value = _FakeStream([b"abcd"], content_length=4)
        assert client.download_file("edge.gcode", max_bytes=4) == b"abcd"


def test_download_file_lets_an_http_error_through():
    """A 404 must stay a 404 — the caller distinguishes "no file" from "too big"."""
    client = MoonrakerClient("http://printer:7125")
    error = httpx.HTTPStatusError("404", request=MagicMock(), response=MagicMock())
    with patch("backend.app.services.moonraker_client.httpx.stream") as stream:
        stream.return_value = _FakeStream([], status_error=error)
        with pytest.raises(httpx.HTTPStatusError):
            client.download_file("missing.gcode")


# ------------------------------------------------------ transport switching


class _StubStatusStream:
    """Only the bit effective_poll_interval looks at."""

    def __init__(self, healthy):
        self.healthy = healthy


def test_poll_interval_backs_off_while_the_stream_is_healthy():
    """The poll never stops, it gets out of the way — so a wedged WebSocket
    costs latency for one interval, not a card that stops updating."""
    client = MoonrakerClient("http://printer:7125", poll_interval=2.0, slow_poll_interval=30.0)
    client._stream = _StubStatusStream(healthy=True)
    assert client.effective_poll_interval() == 30.0


def test_poll_interval_snaps_back_when_the_stream_goes_quiet():
    client = MoonrakerClient("http://printer:7125", poll_interval=2.0, slow_poll_interval=30.0)
    client._stream = _StubStatusStream(healthy=False)
    assert client.effective_poll_interval() == 2.0


def test_poll_interval_without_a_stream_is_the_plain_one():
    client = MoonrakerClient("http://printer:7125", poll_interval=2.0, slow_poll_interval=30.0)
    assert client.effective_poll_interval() == 2.0


def test_transport_poll_never_opens_a_stream():
    """For a Moonraker behind something that will not pass an upgrade request."""
    client = MoonrakerClient("http://printer:7125", transport="poll")
    client._objects = ["print_stats"]
    client._start_stream()
    assert client._stream is None


def test_transport_auto_opens_one():
    client = MoonrakerClient("http://printer:7125", transport="auto")
    client._objects = ["print_stats"]
    with patch("backend.app.services.moonraker_client.MoonrakerStatusStream") as stream_cls:
        client._start_stream()
    stream_cls.assert_called_once()
    stream_cls.return_value.start.assert_called_once()


def test_pushed_updates_are_merged_before_they_are_applied():
    """A partial frame must not reach _apply_status on its own — it would look
    like a printer that had just lost every field it did not mention."""
    client = MoonrakerClient("http://printer:7125")
    client._status_cache = {"extruder": {"temperature": 210.0, "target": 210.0}}
    with patch.object(client, "_apply_status") as apply_status:
        client._on_pushed_status({"extruder": {"target": 0.0}})
    apply_status.assert_called_once()
    assert apply_status.call_args.args[0]["extruder"] == {"temperature": 210.0, "target": 0.0}


def test_pushed_updates_are_coalesced():
    """Moonraker pushes whenever the toolhead moves. Every frame is merged, but
    the fan-out behind it is not run several times a second."""
    client = MoonrakerClient("http://printer:7125")
    with patch.object(client, "_apply_status") as apply_status:
        client._on_pushed_status({"toolhead": {"position": [0, 0, 0, 0]}})
        client._on_pushed_status({"toolhead": {"position": [1, 0, 0, 0]}})
        client._on_pushed_status({"toolhead": {"position": [2, 0, 0, 0]}})
    assert apply_status.call_count == 1
    # Dropped from the work, never from the picture.
    assert client._status_cache["toolhead"]["position"] == [2, 0, 0, 0]


def test_a_printer_that_was_off_at_connect_still_gets_a_stream():
    """connect() gives up before _start_stream when the printer is unreachable,
    and the poll is what brings the connection back — so the poll has to open
    the stream too, or a printer powered on after Bambuddy never gets one."""
    client = MoonrakerClient("http://printer:7125", transport="auto")
    # Stop after a single pass: the loop checks the event, works, then waits.
    with (
        patch.object(client, "request_status_update", return_value=True),
        patch.object(client, "_discover_objects") as discover,
        patch.object(client, "_start_stream") as start_stream,
        patch.object(client._stop, "wait", side_effect=lambda _: client._stop.set()),
    ):
        client._poll_loop()
    discover.assert_called_once()
    start_stream.assert_called_once()


def test_the_poll_does_not_reopen_a_stream_that_already_exists():
    client = MoonrakerClient("http://printer:7125", transport="auto")
    client._stream = _StubStatusStream(healthy=True)
    with (
        patch.object(client, "request_status_update", return_value=True),
        patch.object(client, "_discover_objects") as discover,
        patch.object(client._stop, "wait", side_effect=lambda _: client._stop.set()),
    ):
        client._poll_loop()
    discover.assert_not_called()


def test_the_poll_never_opens_a_stream_when_transport_is_poll():
    client = MoonrakerClient("http://printer:7125", transport="poll")
    with (
        patch.object(client, "request_status_update", return_value=True),
        patch.object(client, "_discover_objects") as discover,
        patch.object(client._stop, "wait", side_effect=lambda _: client._stop.set()),
    ):
        client._poll_loop()
    discover.assert_not_called()
    assert client._stream is None
