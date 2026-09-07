"""Voron patch series: Happy Hare MMU reported as AMS units, gate map and tool-to-gate dispatch."""

from backend.app.services.moonraker_client import MoonrakerClient


def _base_status():
    return {
        "print_stats": {"state": "standby", "filename": "", "print_duration": 0.0, "info": {}},
        "virtual_sdcard": {"progress": 0.0},
        "display_status": {"progress": 0.0},
        "extruder": {"temperature": 25.0, "target": 0.0},
        "heater_bed": {"temperature": 25.0, "target": 0.0},
        "toolhead": {"homed_axes": ""},
        "fan": {"speed": 0.0},
    }


def _mmu_status(
    gate=0,
    filament="Loaded",
    statuses=(2, 1, 0),
    materials=("PETG", "ABS", ""),
    colors=("1a1a1a", "FD8700", ""),
):
    s = _base_status()
    s["mmu"] = {
        "enabled": True,
        "num_gates": len(statuses),
        "gate": gate,
        "tool": gate,
        "next_tool": -1,
        "filament": filament,
        "action": "Idle",
        "print_state": "ready",
        "is_locked": False,
        "is_paused": False,
        "reason_for_pause": "",
        "has_bypass": True,
    }
    s["mmu_machine"] = {"happy_hare_version": "4.0.0", "num_gates": len(statuses)}
    s["save_variables"] = {
        "variables": {
            "mmu_state_gate_status": list(statuses),
            "mmu_state_gate_material": list(materials),
            "mmu_state_gate_color": list(colors),
            "mmu_state_gate_filament_name": ["Polymaker", "", ""],
            "mmu_state_gate_temperature": [230, 250, 200],
            "mmu_state_gate_spool_id": [-1, 7, -1],
        }
    }
    return s


class _Fake(MoonrakerClient):
    def __init__(self, statuses):
        super().__init__("http://voron.test", serial_number="KLIPPER-VORON")
        self._statuses = list(statuses)
        self._mmu = True

    def _query(self, objects):  # noqa: ARG002
        return self._statuses.pop(0)

    def _get(self, path, timeout=None):  # noqa: ARG002
        return {}


def test_gates_become_ams_trays():
    client = _Fake([_mmu_status()])
    client.request_status_update()

    ams = client.state.raw_data["ams"]
    assert len(ams) == 1 and ams[0]["id"] == 0 and ams[0]["sw_ver"] == "4.0.0"
    trays = ams[0]["tray"]
    assert [t["id"] for t in trays] == [0, 1, 2]
    assert trays[0]["tray_type"] == "PETG" and trays[0]["tray_color"] == "1A1A1AFF" and trays[0]["state"] == 11
    assert trays[0]["tray_sub_brands"] == "Polymaker" and trays[0]["nozzle_temp_max"] == 230
    assert trays[1]["tray_type"] == "ABS" and trays[1]["state"] == 10 and trays[1]["spoolman_id"] == 7
    assert trays[2]["state"] == 9 and trays[2]["exists"] is False
    assert client.state.tray_now == 0  # gate 0 is in the hotend
    assert client.state.raw_data["mmu"]["num_gates"] == 3
    assert client.state.raw_data["vt_tray"][0]["id"] == 254  # bypass slot stays
    assert "save_variables" not in client.state.raw_data["moonraker"]


def test_six_gates_span_two_units_and_unloaded_state():
    st = _mmu_status(gate=-1, filament="Unloaded", statuses=(1,) * 6, materials=("PLA",) * 6, colors=("ff0000",) * 6)
    client = _Fake([st])
    client.request_status_update()
    ams = client.state.raw_data["ams"]
    assert [len(u["tray"]) for u in ams] == [4, 2]
    assert client._mmu_gate(1, 1) == 5 and client._mmu_gate(1, 2) is None and client._mmu_gate(255, 0) is None
    assert client.state.tray_now == 255


def test_mmu_commands():
    client = _Fake([_mmu_status()])
    client.request_status_update()
    sent: list[str] = []
    client.send_gcode = lambda g: (sent.append(g), True)[1]  # type: ignore[method-assign]

    assert client.ams_load_filament(1) is True
    assert client.ams_unload_filament() is True
    assert client.ams_load_filament(254) is True
    assert client.ams_load_filament(9) is False
    assert client.ams_set_filament_setting(0, 1, "GFB00", "ABS", "Bambu ABS", "#FD8700", 240, 270) is True
    assert client.reset_ams_slot(0, 2) is True
    assert client.ams_control("resume") is True

    assert sent[0] == "MMU_SELECT GATE=1\nMMU_LOAD"
    assert sent[1] == "MMU_UNLOAD"
    assert sent[2] == "MMU_SELECT_BYPASS"
    assert sent[3] == 'MMU_GATE_MAP GATE=1 MATERIAL="ABS" COLOR="fd8700" NAME="Bambu ABS" TEMP=270 AVAILABLE=1'
    assert sent[4] == 'MMU_GATE_MAP GATE=2 MATERIAL="" COLOR="" NAME="" AVAILABLE=0'
    assert sent[5] == "MMU_UNLOCK"
    # the external slot still works next to the MMU
    assert client.ams_set_filament_setting(255, 0, "", "PLA", "", "", 0, 0) is True


def test_start_print_pushes_tool_to_gate_map():
    client = _Fake([_mmu_status()])
    client.request_status_update()
    sent: list[str] = []
    client.send_gcode = lambda g: (sent.append(g), True)[1]  # type: ignore[method-assign]
    client._post = lambda path, payload=None, timeout=None: sent.append(path) or {}  # type: ignore[method-assign]

    assert client.start_print("job.gcode", 1, ams_mapping=[2, 0]) is True
    assert sent[0] == "MMU_TTG_MAP MAP=2,0"
    assert sent[1].startswith("printer/print/start?filename=job.gcode")
    assert client.last_ttg_map == [2, 0]

    # an unmapped / external slot falls back to the tool's own gate
    assert client.start_print("job.gcode", 1, ams_mapping=[254, 1]) is True
    assert sent[2] == "MMU_TTG_MAP MAP=0,1"


def test_disabled_mmu_reports_no_units():
    st = _mmu_status()
    st["mmu"]["enabled"] = False
    client = _Fake([st])
    client.request_status_update()
    assert client.state.raw_data["ams"] == []
    assert client.state.raw_data["mmu"] == {"enabled": False}
