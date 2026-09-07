"""Voron patch series: slicer-comment metadata from raw G-code."""

from backend.app.services.gcode_metadata import parse_gcode_metadata

ORCA_HEAD = """; HEADER_BLOCK_START
; BambuStudio 02.03.00.70
; model printing time: 13m 2s; total estimated time: 15m 10s
; total layer number: 42
; total filament length [mm] : 1234.50
; total filament weight [g] : 3.70
; filament_density: 1.04
; HEADER_BLOCK_END
; thumbnail begin 16x16 12
; iVBORw0KGgo=
; thumbnail end
G28
"""

ORCA_TAIL = """; CONFIG_BLOCK_START
; bed_temperature = 100
; filament_colour = #FF8700
; filament_type = ABS
; filament used [g] = 3.7
; filament used [mm] = 1234.5
; layer_height = 0.2
; nozzle_diameter = 0.4
; nozzle_temperature = 250
; printer_model = Voron 2.4 350
; printer_settings_id = "Voron 2.4 350 0.4 nozzle"
; CONFIG_BLOCK_END
"""

PRUSA_TAIL = """; filament used [mm] = 999.0
; filament used [g] = 2.5
; filament_type = PLA
; first_layer_bed_temperature = 60
; temperature = 215
; estimated printing time (normal mode) = 1h 2m 3s
; printer_model = MK4
"""


def test_orca_gcode_metadata(tmp_path):
    path = tmp_path / "carrier.gcode"
    path.write_text(ORCA_HEAD + "G1 X10\n" * 50 + ORCA_TAIL)

    meta = parse_gcode_metadata(path)

    assert meta["print_time_seconds"] == 15 * 60 + 10
    assert meta["total_layers"] == 42
    assert meta["filament_used_grams"] == 3.7
    assert meta["filament_used_mm"] == 1234.5
    assert meta["filament_type"] == "ABS"
    assert meta["filament_color"] == "#FF8700"
    assert meta["layer_height"] == 0.2
    assert meta["nozzle_diameter"] == 0.4
    assert meta["bed_temperature"] == 100
    assert meta["nozzle_temperature"] == 250
    assert meta["klipper_printer_profile"] == "Voron 2.4 350"
    assert "sliced_for_model" not in meta  # must not trip the Bambu model guard


def test_prusa_style_tail_only(tmp_path):
    path = tmp_path / "part.gcode"
    path.write_text("G28\n" + "G1 X1\n" * 20 + PRUSA_TAIL)

    meta = parse_gcode_metadata(path)

    assert meta["print_time_seconds"] == 3723
    assert meta["filament_type"] == "PLA"
    assert meta["bed_temperature"] == 60
    assert meta["nozzle_temperature"] == 215


def test_large_file_reads_head_and_tail(tmp_path):
    path = tmp_path / "big.gcode"
    with open(path, "w") as fh:
        fh.write(ORCA_HEAD)
        for _ in range(60_000):  # ~1 MB of moves between head and tail
            fh.write("G1 X12.345 Y67.890 E0.0123\n")
        fh.write(ORCA_TAIL)

    meta = parse_gcode_metadata(path)
    assert meta["print_time_seconds"] == 910
    assert meta["filament_type"] == "ABS"


def test_plain_gcode_without_comments(tmp_path):
    path = tmp_path / "bare.gcode"
    path.write_text("G28\nG1 X10\n")
    assert parse_gcode_metadata(path) == {}


def test_missing_file_is_empty(tmp_path):
    assert parse_gcode_metadata(tmp_path / "nope.gcode") == {}
