"""Voron patch series (C3): Moonraker [power] devices behind the plug-service surface."""

from backend.app.services.moonraker_plug import MoonrakerPlugService


class _Plug:
    def __init__(self, device="printer", printer_id=1):
        self.name = "Voron socket"
        self.printer_id = printer_id
        self.moonraker_device = device


class _Service(MoonrakerPlugService):
    """Stub the two things that touch the outside world: the DB and Moonraker."""

    def __init__(self, responses, *, connection=("http://voron.test", "key")):
        self.responses = responses
        self.connection = connection
        self.paths: list[str] = []

    async def _connection(self, plug):  # noqa: ARG002
        return self.connection

    async def _request(self, plug, path):
        self.paths.append(path)
        return self.responses.get(path)


async def test_turn_on_reports_success_only_when_the_device_says_on():
    assert await _Service({"on": "on"}).turn_on(_Plug()) is True
    assert await _Service({"on": "error"}).turn_on(_Plug()) is False
    assert await _Service({"on": None}).turn_on(_Plug()) is False


async def test_status_shape_matches_the_other_plug_services():
    status = await _Service({"device": "off"}).get_status(_Plug())

    assert status == {"state": "OFF", "reachable": True, "device_name": "printer"}


async def test_unreachable_is_not_the_same_as_off():
    """A host that does not answer must not read as a plug that is switched off."""
    status = await _Service({"device": None}).get_status(_Plug())

    assert status["state"] is None
    assert status["reachable"] is False


async def test_a_device_moonraker_cannot_drive_has_no_actionable_state():
    status = await _Service({"device": "error"}).get_status(_Plug())

    assert status == {"state": None, "reachable": True, "device_name": "printer"}


async def test_toggle_reads_before_it_writes():
    service = _Service({"device": "on", "off": "off"})

    assert await service.toggle(_Plug()) is True
    assert service.paths == ["device", "off"]


async def test_energy_is_never_claimed():
    """Moonraker's power API answers on/off and nothing else."""
    assert await MoonrakerPlugService().get_energy(_Plug()) is None


async def test_a_half_configured_plug_does_not_call_moonraker():
    service = MoonrakerPlugService()

    assert await service._connection(_Plug(device=None)) is None
    assert await service._connection(_Plug(printer_id=None)) is None
