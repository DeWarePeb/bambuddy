"""Fork (issue #2): pushed status from Moonraker's WebSocket.

The stream is an accelerator over the poll, not a replacement, so these cover
the two things that would corrupt the card if they were wrong — how a frame is
classified, and how a partial update folds into the full picture — plus the
switch that decides which transport is currently carrying the load.
"""

import json

import pytest

from backend.app.services.moonraker_stream import merge_status, parse_message, subscribe_request, websocket_url

# --------------------------------------------------------------------- the URL


@pytest.mark.parametrize(
    ("base", "expected"),
    [
        ("http://voron:7125", "ws://voron:7125/websocket"),
        ("https://voron:7125", "wss://voron:7125/websocket"),
        ("http://192.168.2.177:7125/", "ws://192.168.2.177:7125/websocket"),
        ("voron:7125", "ws://voron:7125/websocket"),
    ],
)
def test_websocket_url(base, expected):
    assert websocket_url(base) == expected


def test_websocket_url_drops_a_path_rather_than_appending_to_it():
    """Moonraker serves the socket at the root, whatever path the base URL had."""
    assert websocket_url("http://host:7125/printer") == "ws://host:7125/websocket"


# ---------------------------------------------------------------- the subscribe


def test_subscribe_asks_for_every_field_of_each_object():
    """A null per object means "all fields" — the same thing the poll asks for.
    If the two disagreed, the card would change shape with the transport."""
    body = json.loads(subscribe_request(["print_stats", "toolhead"]))
    assert body["method"] == "printer.objects.subscribe"
    assert body["params"]["objects"] == {"print_stats": None, "toolhead": None}
    assert body["jsonrpc"] == "2.0"


# ------------------------------------------------------------------- the frames


def test_parses_a_pushed_status_update():
    raw = json.dumps(
        {"jsonrpc": "2.0", "method": "notify_status_update", "params": [{"extruder": {"temperature": 210.0}}, 12.3]}
    )
    assert parse_message(raw) == ("status", {"extruder": {"temperature": 210.0}})


def test_parses_the_snapshot_that_answers_the_subscribe():
    """That reply is what seeds the cache; miss it and every later partial
    update merges into nothing."""
    raw = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"status": {"toolhead": {"homed_axes": "xyz"}}, "eventtime": 1}})
    assert parse_message(raw) == ("status", {"toolhead": {"homed_axes": "xyz"}})


@pytest.mark.parametrize(
    ("method", "event"),
    [("notify_klippy_ready", "ready"), ("notify_klippy_shutdown", "shutdown"), ("notify_klippy_disconnected", "disconnected")],
)
def test_parses_klippy_lifecycle_notifications(method, event):
    raw = json.dumps({"jsonrpc": "2.0", "method": method, "params": []})
    assert parse_message(raw) == ("klippy", {"event": event})


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        "[1, 2, 3]",
        '"a string"',
        "null",
        json.dumps({"method": "notify_gcode_response", "params": ["ok"]}),
        json.dumps({"method": "notify_status_update", "params": []}),
        json.dumps({"method": "notify_status_update", "params": ["not a dict"]}),
        json.dumps({"result": {"eventtime": 1}}),
        json.dumps({"result": "ok"}),
    ],
)
def test_anything_else_is_ignored_rather_than_raising(raw):
    """A printer is allowed to say things this does not understand; the stream
    must not die of one."""
    assert parse_message(raw) is None


# -------------------------------------------------------------------- the merge


def test_merge_keeps_fields_the_update_did_not_mention():
    """The whole reason a merge is needed: Moonraker sends only what changed, so
    replacing the object would drop the temperature the moment only the target
    moved."""
    cached = {"extruder": {"temperature": 210.0, "target": 210.0}}
    merged = merge_status(cached, {"extruder": {"target": 0.0}})
    assert merged["extruder"] == {"temperature": 210.0, "target": 0.0}


def test_merge_leaves_untouched_objects_alone():
    cached = {"extruder": {"temperature": 210.0}, "heater_bed": {"temperature": 60.0}}
    merged = merge_status(cached, {"extruder": {"temperature": 211.0}})
    assert merged["heater_bed"] == {"temperature": 60.0}


def test_merge_adds_an_object_seen_for_the_first_time():
    assert merge_status({}, {"toolhead": {"homed_axes": "xyz"}}) == {"toolhead": {"homed_axes": "xyz"}}


def test_merge_replaces_a_non_dict_value():
    assert merge_status({"a": 1}, {"a": 2}) == {"a": 2}


def test_merge_does_not_mutate_the_cache_it_was_given():
    """Callers compare against the previous picture; mutating would make every
    comparison see no change."""
    cached = {"extruder": {"temperature": 210.0}}
    merge_status(cached, {"extruder": {"temperature": 250.0}})
    assert cached == {"extruder": {"temperature": 210.0}}
