"""Voron patch series (C6): update_manager on the Firmware page."""

from backend.app.services.klipper_update import summarize

_STATUS = {
    "version_info": {
        "system": {"package_count": 14, "package_list": ["libc6", "curl"]},
        "moonraker": {"version": "v0.9.3-52", "remote_version": "v0.9.3-52"},
        "klipper": {"version": "v0.12.0-244", "remote_version": "v0.12.0-310"},
        "mainsail": {"version": "v2.13.2", "remote_version": "v2.14.0"},
    }
}


def test_klipper_is_the_version_the_firmware_page_shows():
    summary = summarize(_STATUS)

    assert summary["current_version"] == "v0.12.0-244"
    assert summary["latest_version"] == "v0.12.0-310"
    assert summary["update_available"] is True


def test_every_component_is_listed_klipper_and_moonraker_first():
    names = [c["name"] for c in summarize(_STATUS)["components"]]

    assert names[:2] == ["klipper", "moonraker"]
    assert set(names) == {"klipper", "moonraker", "mainsail", "system"}


def test_a_component_on_the_latest_version_is_not_flagged():
    components = {c["name"]: c for c in summarize(_STATUS)["components"]}

    assert components["moonraker"]["update_available"] is False
    assert components["mainsail"]["update_available"] is True


def test_system_reports_a_package_count_not_a_version():
    system = next(c for c in summarize(_STATUS)["components"] if c["name"] == "system")

    assert system["current"] is None
    assert system["latest"] == "14"
    assert system["update_available"] is True


def test_no_os_packages_waiting_is_not_an_update():
    summary = summarize({"version_info": {"system": {"package_count": 0}}})
    system = summary["components"][0]

    assert system["update_available"] is False
    assert summary["update_available"] is False


def test_a_repo_moonraker_could_not_reach_raises_no_alarm():
    """Moonraker writes "?" for a component it failed to check."""
    summary = summarize({"version_info": {"klipper": {"version": "v0.12.0-244", "remote_version": "?"}}})

    assert summary["update_available"] is False
    assert summary["current_version"] == "v0.12.0-244"


def test_a_printer_with_nothing_to_report():
    summary = summarize({})

    assert summary == {
        "current_version": None,
        "latest_version": None,
        "update_available": False,
        "components": [],
    }
