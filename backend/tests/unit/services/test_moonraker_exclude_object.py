"""Voron patch series (C5): Klipper's exclude_object behind the skip-objects UI."""

from backend.app.services.moonraker_client import MoonrakerClient

_EXCLUDE = {
    "objects": [
        {"name": "BENCHY_ID_0", "center": [100.0, 100.0], "polygon": [[90, 90], [110, 90], [110, 110], [90, 110]]},
        {"name": "BENCHY_ID_1", "center": [160.0, 100.0], "polygon": [[150, 90], [170, 90], [170, 110], [150, 110]]},
        {"name": "BENCHY_ID_2", "center": [220.0, 100.0], "polygon": [[210, 90], [230, 90], [230, 110], [210, 110]]},
    ],
    "excluded_objects": ["BENCHY_ID_1"],
    "current_object": "BENCHY_ID_0",
}


class _Klipper(MoonrakerClient):
    def __init__(self, exclude=None):
        super().__init__("http://voron.test")
        self._exclude_object = True
        self.sent: list[str] = []
        self.gcode_ok = True
        self.exclude = _EXCLUDE if exclude is None else exclude

    def send_gcode(self, gcode):
        self.sent.append(gcode)
        return self.gcode_ok


def _applied(client=None):
    client = client or _Klipper()
    client._apply_exclude_object({"exclude_object": client.exclude})
    return client


def test_objects_become_the_ids_the_skip_ui_posts_back():
    client = _applied()

    assert client.state.printable_objects_count == 3
    assert client.state.printable_objects[0] == {"name": "BENCHY_ID_0", "x": 100.0, "y": 100.0}
    assert client.state.printable_objects[2]["name"] == "BENCHY_ID_2"


def test_already_excluded_objects_come_back_as_skipped():
    assert _applied().state.skipped_objects == [1]


def test_bbox_spans_every_polygon_for_the_camera_pick():
    assert _applied().state.printable_objects_bbox_all == [90.0, 90.0, 230.0, 110.0]


def test_skip_sends_one_exclude_per_object_by_name():
    client = _applied()

    assert client.skip_objects([0, 2]) is True
    assert client.sent == ['EXCLUDE_OBJECT NAME="BENCHY_ID_0"', 'EXCLUDE_OBJECT NAME="BENCHY_ID_2"']


def test_an_unknown_id_is_refused_without_guessing():
    client = _applied()

    assert client.skip_objects([9]) is False
    assert client.sent == []


def test_a_refused_gcode_is_reported_as_failure():
    client = _applied()
    client.gcode_ok = False

    assert client.skip_objects([0]) is False


def test_skip_before_any_objects_are_known_does_nothing():
    client = _Klipper()

    assert client.skip_objects([0]) is False
    assert client.sent == []


def test_an_object_list_without_polygons_leaves_the_bbox_alone():
    """A centre point alone would make a box with no area — worse than none."""
    client = _Klipper({"objects": [{"name": "A", "center": [10, 10]}], "excluded_objects": []})
    _applied(client)

    assert getattr(client.state, "printable_objects_bbox_all", None) is None
    assert client.state.printable_objects_count == 1
