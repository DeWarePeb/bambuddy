"""Voron patch series (C4): timelapses from moonraker-timelapse."""

import httpx

from backend.app.services import klipper_timelapse


class _Printer:
    def __init__(self, provider="klipper", api_url="http://voron.test", auth_token="key"):
        self.name = "Voron"
        self.provider = provider
        self.api_url = api_url
        self.auth_token = auth_token


def _transport(handler):
    """Patch the module's AsyncClient so it routes through a mock transport."""
    original = klipper_timelapse.httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs.pop("timeout", None)
        return original(transport=httpx.MockTransport(handler), **kwargs)

    return factory


def test_is_klipper_only_for_klipper_printers():
    assert klipper_timelapse.is_klipper(_Printer()) is True
    assert klipper_timelapse.is_klipper(_Printer(provider="bambu")) is False


async def test_listing_keeps_videos_and_their_moonraker_paths(monkeypatch):
    def handler(request):
        assert request.url.path == "/server/files/list"
        assert request.headers["X-Api-Key"] == "key"
        return httpx.Response(
            200,
            json={
                "result": [
                    {"path": "timelapse_2026-09-08_1.mp4", "size": 4211, "modified": 1757000000.0},
                    {"path": "sub/older.mp4", "size": 900, "modified": 1756000000.0},
                    {"path": "frames/frame0001.jpg", "size": 12, "modified": 1757000001.0},
                ]
            },
        )

    monkeypatch.setattr(klipper_timelapse.httpx, "AsyncClient", _transport(handler))
    videos = await klipper_timelapse.list_videos(_Printer())

    assert [v["name"] for v in videos] == ["timelapse_2026-09-08_1.mp4", "older.mp4"]
    # The path stays relative to the timelapse root — that is what every
    # Moonraker file endpoint wants back.
    assert videos[1]["path"] == "sub/older.mp4"
    assert videos[0]["size"] == 4211


async def test_a_printer_without_the_plugin_lists_nothing(monkeypatch):
    """No timelapse root is a 404, and that reads the same as "no videos yet"."""

    def handler(request):  # noqa: ARG001
        return httpx.Response(404, json={"error": "root not found"})

    monkeypatch.setattr(klipper_timelapse.httpx, "AsyncClient", _transport(handler))

    assert await klipper_timelapse.list_videos(_Printer()) == []


async def test_a_short_download_is_refused(monkeypatch):
    def handler(request):  # noqa: ARG001
        return httpx.Response(200, content=b"12345")

    monkeypatch.setattr(klipper_timelapse.httpx, "AsyncClient", _transport(handler))

    assert await klipper_timelapse.download(_Printer(), "v.mp4", expected_size=5) == b"12345"
    # The delete that follows an attach is gated on this check, so a truncated
    # transfer must not pass.
    assert await klipper_timelapse.download(_Printer(), "v.mp4", expected_size=9) is None


async def test_settled_waits_for_ffmpeg_to_stop_writing(monkeypatch):
    async def growing(printer):  # noqa: ARG001
        return [{"path": "v.mp4", "name": "v.mp4", "size": 5000}]

    monkeypatch.setattr(klipper_timelapse, "list_videos", growing)

    assert await klipper_timelapse.settled(_Printer(), "v.mp4", 5000, wait=0) is True
    assert await klipper_timelapse.settled(_Printer(), "v.mp4", 4000, wait=0) is False


async def test_settled_accepts_a_file_that_vanished(monkeypatch):
    async def empty(printer):  # noqa: ARG001
        return []

    monkeypatch.setattr(klipper_timelapse, "list_videos", empty)

    assert await klipper_timelapse.settled(_Printer(), "v.mp4", 5000, wait=0) is True


async def test_nothing_is_attempted_without_a_moonraker_url():
    printer = _Printer(api_url=None)

    assert await klipper_timelapse.list_videos(printer) == []
    assert await klipper_timelapse.download(printer, "v.mp4") is None
    assert await klipper_timelapse.delete(printer, "v.mp4") is False
