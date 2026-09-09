"""Fork (issue #2): the WebSocket stream against a fake Moonraker.

The unit tests cover framing and merging as pure functions. This drives the real
thing — an aiohttp WebSocket server standing in for Moonraker — because the
parts that break in production are the ones no pure function has: does it
connect, does it send the subscribe, does it survive the server going away, does
it stop when told.

The server speaks the same JSON-RPC that Moonraker does: it answers the
subscribe with a full snapshot, then pushes partial updates.
"""

import asyncio
import json
import threading
import time

import pytest
from aiohttp import web

from backend.app.services.moonraker_stream import MoonrakerStatusStream


class FakeMoonraker:
    """A WebSocket server that behaves like Moonraker's, on a random port."""

    def __init__(self, snapshot: dict | None = None):
        self.snapshot = snapshot or {"print_stats": {"state": "printing"}, "extruder": {"temperature": 210.0}}
        self.subscribed: dict | None = None
        self.port: int | None = None
        self.api_keys: list[str | None] = []
        self._sockets: list[web.WebSocketResponse] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._runner: web.AppRunner | None = None

    # ------------------------------------------------------------- lifecycle

    def start(self) -> None:
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        assert self._ready.wait(10), "fake Moonraker did not start"

    def stop(self) -> None:
        # Close the sockets and tear the runner down inside the loop before
        # stopping it, or aiohttp is left with pending tasks and prints a wall
        # of "Task was destroyed" over the test output.
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._shutdown(), self._loop).result(5)
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread:
            self._thread.join(5)

    async def _shutdown(self) -> None:
        for ws in list(self._sockets):
            if not ws.closed:
                await ws.close()
        self._sockets.clear()
        if self._runner:
            await self._runner.cleanup()

    def _serve(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        app = web.Application()
        app.router.add_get("/websocket", self._handler)
        self._runner = web.AppRunner(app)
        self._loop.run_until_complete(self._runner.setup())
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        self._loop.run_until_complete(site.start())
        self.port = site._server.sockets[0].getsockname()[1]
        self._ready.set()
        self._loop.run_forever()

    # ---------------------------------------------------------------- server

    async def _handler(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.api_keys.append(request.headers.get("X-Api-Key"))
        self._sockets.append(ws)
        async for msg in ws:
            if msg.type is not web.WSMsgType.TEXT:
                continue
            body = json.loads(msg.data)
            if body.get("method") == "printer.objects.subscribe":
                self.subscribed = body["params"]["objects"]
                await ws.send_str(
                    json.dumps({"jsonrpc": "2.0", "id": body.get("id"), "result": {"status": self.snapshot, "eventtime": 1.0}})
                )
        return ws

    # ----------------------------------------------------------- test driving

    def push(self, update: dict) -> None:
        """Send a partial status update to every connected client."""
        frame = json.dumps({"jsonrpc": "2.0", "method": "notify_status_update", "params": [update, 2.0]})
        for ws in list(self._sockets):
            if not ws.closed:
                asyncio.run_coroutine_threadsafe(ws.send_str(frame), self._loop).result(5)

    def drop_connections(self) -> None:
        for ws in list(self._sockets):
            asyncio.run_coroutine_threadsafe(ws.close(), self._loop).result(5)
        self._sockets.clear()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


def wait_for(predicate, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


@pytest.fixture
def moonraker():
    server = FakeMoonraker()
    server.start()
    yield server
    server.stop()


@pytest.mark.integration
def test_subscribes_and_delivers_the_opening_snapshot(moonraker):
    received: list[dict] = []
    stream = MoonrakerStatusStream(moonraker.url, None, ["print_stats", "extruder"], received.append)
    stream.start()
    try:
        assert wait_for(lambda: received), "no snapshot arrived"
        assert received[0] == moonraker.snapshot
        assert moonraker.subscribed == {"print_stats": None, "extruder": None}
        assert wait_for(lambda: stream.healthy)
    finally:
        stream.stop()


@pytest.mark.integration
def test_delivers_a_pushed_update(moonraker):
    received: list[dict] = []
    stream = MoonrakerStatusStream(moonraker.url, None, ["extruder"], received.append)
    stream.start()
    try:
        assert wait_for(lambda: received)
        moonraker.push({"extruder": {"target": 0.0}})
        assert wait_for(lambda: len(received) >= 2), "pushed update never arrived"
        assert received[-1] == {"extruder": {"target": 0.0}}
    finally:
        stream.stop()


@pytest.mark.integration
def test_sends_the_api_key_on_the_handshake(moonraker):
    stream = MoonrakerStatusStream(moonraker.url, "secret-key", ["extruder"], lambda _: None)
    stream.start()
    try:
        assert wait_for(lambda: moonraker.api_keys)
        assert moonraker.api_keys[0] == "secret-key"
    finally:
        stream.stop()


@pytest.mark.integration
def test_reconnects_after_the_server_drops_the_connection(moonraker):
    """A Klipper host that reboots, or a tunnel that drops idle sockets. The
    poll covers the gap; the stream has to come back on its own."""
    received: list[dict] = []
    stream = MoonrakerStatusStream(moonraker.url, None, ["extruder"], received.append)
    stream.start()
    try:
        assert wait_for(lambda: received)
        moonraker.drop_connections()
        assert wait_for(lambda: len(received) >= 2, timeout=15), "never resubscribed"
    finally:
        stream.stop()


@pytest.mark.integration
def test_a_callback_that_raises_does_not_kill_the_stream(moonraker):
    """One bad frame must not end the session; the next one still arrives."""
    seen: list[dict] = []

    def explode(status):
        seen.append(status)
        raise RuntimeError("callback is broken")

    stream = MoonrakerStatusStream(moonraker.url, None, ["extruder"], explode)
    stream.start()
    try:
        assert wait_for(lambda: seen)
        moonraker.push({"extruder": {"target": 5.0}})
        assert wait_for(lambda: len(seen) >= 2), "stream died on the first bad callback"
    finally:
        stream.stop()


@pytest.mark.integration
def test_unreachable_server_is_not_healthy_and_does_not_raise():
    """The poll is still running, so this is a missed optimisation, not an error."""
    stream = MoonrakerStatusStream("http://127.0.0.1:1", None, ["extruder"], lambda _: None)
    stream.start()
    try:
        time.sleep(1.0)
        assert stream.healthy is False
    finally:
        stream.stop()


@pytest.mark.integration
def test_stop_ends_the_thread(moonraker):
    stream = MoonrakerStatusStream(moonraker.url, None, ["extruder"], lambda _: None)
    stream.start()
    assert wait_for(lambda: stream.healthy)
    stream.stop()
    assert stream.healthy is False
    assert wait_for(lambda: not any(t.name == "moonraker-ws" and t.is_alive() for t in threading.enumerate()))
