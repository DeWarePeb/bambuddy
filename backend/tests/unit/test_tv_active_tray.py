"""The TV feed resolves ``tray_now`` server-side (voron B10).

The signed-in page walks the full AMS payload in the browser
(``resolveActiveTray`` in frontend/src/utils/tvMode.ts); a wall token gets only
the answer. These tests pin the two implementations to the same rules — global
tray id is ``ams*4+slot``, an AMS-HT unit uses its own id, 254 is the external
spool and 255 is "nothing loaded" — so the two modes cannot drift apart.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.app.api.routes.tv import _active_tray

pytestmark = [pytest.mark.unit]


def _state(tray_now, *, ams=None, vt_tray=None):
    return SimpleNamespace(
        tray_now=tray_now,
        raw_data={"ams": ams or [], "vt_tray": vt_tray or []},
    )


def _tray(tray_id, tray_type="PLA", **extra):
    return {
        "id": tray_id,
        "tray_type": tray_type,
        "tray_sub_brands": extra.get("sub_brands", "PLA Basic"),
        "tray_color": extra.get("color", "FF6A13FF"),
        "remain": extra.get("remain", 60),
    }


class TestActiveTray:
    def test_regular_ams_slot(self):
        state = _state(5, ams=[{"id": 1, "tray": [_tray(0), _tray(1, sub_brands="PETG HF")]}])
        assert _active_tray(state)["tray_sub_brands"] == "PETG HF"

    def test_second_unit_does_not_shadow_the_first(self):
        """1*4+0 = 4 and 0*4+0 = 0 — the unit id has to be part of the maths."""
        state = _state(
            4,
            ams=[
                {"id": 0, "tray": [_tray(0, sub_brands="unit zero")]},
                {"id": 1, "tray": [_tray(0, sub_brands="unit one")]},
            ],
        )
        assert _active_tray(state)["tray_sub_brands"] == "unit one"

    def test_ams_ht_uses_its_own_id(self):
        state = _state(129, ams=[{"id": 129, "tray": [_tray(0, sub_brands="ABS in the HT")]}])
        assert _active_tray(state)["tray_sub_brands"] == "ABS in the HT"

    def test_external_spool(self):
        state = _state(254, vt_tray=[_tray(254, sub_brands="external PLA")])
        assert _active_tray(state)["tray_sub_brands"] == "external PLA"

    def test_nothing_loaded(self):
        assert _active_tray(_state(255, ams=[{"id": 0, "tray": [_tray(0)]}])) is None

    def test_klipper_printer_without_ams_reports_no_tray(self):
        """No AMS and no tray_now — the tile then shows no spool block at all
        rather than an empty one.
        """
        assert _active_tray(_state(None)) is None

    def test_loaded_but_empty_slot_is_not_a_spool(self):
        state = _state(0, ams=[{"id": 0, "tray": [_tray(0, tray_type=None)]}])
        assert _active_tray(state) is None

    def test_unknown_tray_now_resolves_to_nothing(self):
        state = _state(9, ams=[{"id": 0, "tray": [_tray(0)]}])
        assert _active_tray(state) is None

    def test_only_the_four_display_fields_come_back(self):
        state = _state(0, ams=[{"id": 0, "tray": [dict(_tray(0), tag_uid="DEADBEEF", tray_uuid="x" * 32)]}])
        assert set(_active_tray(state)) == {"tray_type", "tray_sub_brands", "tray_color", "remain"}

    def test_malformed_payload_does_not_raise(self):
        """raw_data comes off the wire; a garbled unit must not 500 the wall."""
        state = SimpleNamespace(
            tray_now=0,
            raw_data={"ams": ["not a dict", {"id": "x", "tray": None}, {"id": 0, "tray": ["nope"]}]},
        )
        assert _active_tray(state) is None
