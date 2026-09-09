"""Fork (A6): which Klipper object is the chamber is a per-printer setting.

Klipper has no chamber concept. An install names its own sensor, and the guess
Bambuddy falls back to only recognises one called "chamber" — so an enclosure
sensor called anything else went unnoticed and the card showed no chamber
temperature. Rather than asking people to rename a sensor in `printer.cfg` to
suit us, the object is configurable and the dialog offers what the printer
actually reports.
"""

import pytest

from backend.app.services import provider_options
from backend.app.services.moonraker_client import _pick_chamber_object, chamber_object_candidates

OBJECTS = [
    "extruder",
    "heater_bed",
    "toolhead",
    "temperature_sensor enclosure",
    "temperature_sensor mcu_temp",
    "temperature_fan chamber_fan",
    "heater_generic chamber_heater",
    "output_pin caselight",
]


# ------------------------------------------------------------------ candidates


def test_candidates_offer_every_object_that_could_report_a_temperature():
    assert chamber_object_candidates(OBJECTS) == [
        "heater_generic chamber_heater",
        "temperature_fan chamber_fan",
        "temperature_sensor enclosure",
        "temperature_sensor mcu_temp",
    ]


def test_candidates_leave_out_what_cannot_be_a_chamber():
    found = chamber_object_candidates(OBJECTS)
    assert "extruder" not in found
    assert "toolhead" not in found
    assert "output_pin caselight" not in found


def test_candidates_of_a_printer_with_none():
    assert chamber_object_candidates(["extruder", "toolhead"]) == []


# --------------------------------------------------------------------- picking


def test_configured_object_wins():
    """The whole point: a sensor named for the enclosure, not for us."""
    assert _pick_chamber_object(OBJECTS, "temperature_sensor enclosure") == "temperature_sensor enclosure"


def test_configured_object_wins_over_one_the_guess_would_have_found():
    objects = [*OBJECTS, "temperature_sensor chamber"]
    assert _pick_chamber_object(objects, "temperature_sensor enclosure") == "temperature_sensor enclosure"


def test_unconfigured_falls_back_to_the_old_guess():
    objects = ["extruder", "temperature_sensor chamber"]
    assert _pick_chamber_object(objects, None) == "temperature_sensor chamber"


def test_a_configured_object_the_printer_does_not_report_is_ignored():
    """Renamed or removed in printer.cfg. Polling a name Moonraker does not know
    would cost every poll an error, so fall back rather than insist."""
    objects = ["extruder", "temperature_sensor chamber"]
    assert _pick_chamber_object(objects, "temperature_sensor gone") == "temperature_sensor chamber"


def test_nothing_configured_and_nothing_to_guess():
    assert _pick_chamber_object(["extruder", "toolhead"], None) is None


@pytest.mark.parametrize("configured", ["", "   ", None])
def test_blank_configuration_is_no_configuration(configured):
    objects = ["temperature_sensor chamber"]
    assert _pick_chamber_object(objects, configured) == "temperature_sensor chamber"


# ------------------------------------------------------------ provider_options


def test_load_of_a_normal_blob():
    assert provider_options.load('{"chamber_object": "temperature_sensor x"}') == {"chamber_object": "temperature_sensor x"}


@pytest.mark.parametrize("raw", [None, "", "not json", "[1,2]", '"a string"', "null"])
def test_load_never_raises_on_junk(raw):
    """The column is free-form text. A printer with unreadable options must
    still load with defaults, not fail to construct."""
    assert provider_options.load(raw) == {}


def test_get_str_ignores_a_wrong_type():
    assert provider_options.get_str('{"chamber_object": 5}', "chamber_object") is None


def test_get_str_treats_blank_as_unset():
    assert provider_options.get_str('{"chamber_object": "  "}', "chamber_object") is None


def test_merge_adds_a_key():
    assert provider_options.merge(None, {"chamber_object": "a"}) == '{"chamber_object": "a"}'


def test_merge_keeps_the_other_keys():
    merged = provider_options.merge('{"other": "keep"}', {"chamber_object": "a"})
    assert provider_options.load(merged) == {"other": "keep", "chamber_object": "a"}


def test_merge_removes_rather_than_nulls():
    """Clearing a setting and never setting it are the same state coming back
    out; a stored null would be a third meaning nothing reads."""
    merged = provider_options.merge('{"chamber_object": "a", "other": "keep"}', {"chamber_object": None})
    assert provider_options.load(merged) == {"other": "keep"}


def test_merge_leaves_the_column_null_when_nothing_is_left():
    assert provider_options.merge('{"chamber_object": "a"}', {"chamber_object": None}) is None
