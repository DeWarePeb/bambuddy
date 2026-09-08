"""Voron patch series (C2): a Klipper printer says why it went quiet.

A Klipper shutdown makes ``printer/objects/query`` answer 503, which every
layer above reads as "offline" — the same word a printer switched off at the
wall gets. The reason is one request away on ``printer/info``.
"""

from backend.app.services.moonraker_client import MoonrakerClient

_SHUTDOWN = "MCU 'mcu' shutdown: Lost communication with MCU 'mcu'"


class _Klippy(MoonrakerClient):
    """Moonraker is up; Klipper is not. Queries fail, printer/info answers."""

    def __init__(self, info, *, info_raises=False, **kwargs):
        super().__init__("http://voron.test", **kwargs)
        self.info = info
        self.info_raises = info_raises
        self.info_calls = 0

    def _query(self, objects):  # noqa: ARG002
        raise RuntimeError("Klippy is not ready (HTTP 503)")

    def _get(self, path, timeout=None):  # noqa: ARG002
        if path == "printer/info":
            self.info_calls += 1
            if self.info_raises:
                raise RuntimeError("connection refused")
            return dict(self.info)
        raise AssertionError(f"unexpected GET {path}")


def test_shutdown_reason_reaches_the_state():
    client = _Klippy({"state": "shutdown", "state_message": _SHUTDOWN})

    client._mark_unreachable("HTTP 503")

    assert client.state.connected is False
    assert client.state.raw_data["klippy"] == {"state": "shutdown", "message": _SHUTDOWN}


def test_unreachable_moonraker_claims_nothing():
    """No answer from printer/info means no diagnosis, not a wrong one."""
    client = _Klippy({}, info_raises=True)

    client._mark_unreachable("connection refused")

    assert client.state.raw_data["klippy"] == {}


def test_state_change_fires_once_per_distinct_reason():
    seen: list[dict] = []
    client = _Klippy(
        {"state": "shutdown", "state_message": _SHUTDOWN},
        on_state_change=lambda state: seen.append(dict(state.raw_data.get("klippy") or {})),
    )
    client.state.connected = True

    client._mark_unreachable("HTTP 503")  # transition: connected -> offline
    client._mark_unreachable("HTTP 503")  # same reason, no new broadcast
    client.info = {"state": "error", "state_message": "Option 'x' is not valid"}
    client._mark_unreachable("HTTP 503")  # reason changed, broadcast again

    assert [s.get("state") for s in seen] == ["shutdown", "error"]


def test_a_good_poll_clears_the_fault():
    class _Recovered(_Klippy):
        """Same printer, but its next status query succeeds."""

        def _query(self, objects):  # noqa: ARG002
            return {"print_stats": {"state": "standby"}}

    client = _Recovered({"state": "shutdown", "state_message": _SHUTDOWN})
    client._mark_unreachable("HTTP 503")
    assert client.state.raw_data["klippy"]["state"] == "shutdown"

    client.request_status_update()

    assert client.state.connected is True
    assert client.state.raw_data["klippy"] == {}


def test_startup_is_reported_too():
    client = _Klippy({"state": "startup", "state_message": "Klipper is loading"})

    client._mark_unreachable("HTTP 503")

    assert client.state.raw_data["klippy"]["state"] == "startup"
    assert client.info_calls == 1
