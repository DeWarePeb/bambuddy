"""Pushed status from Moonraker's WebSocket (fork, issue #2).

`MoonrakerClient` polls `printer/objects/query` every two seconds. That is a
card up to two seconds stale and one request per printer per tick, forever.
Moonraker will push instead, over the JSON-RPC WebSocket at `/websocket`.

**This does not replace the poll, on purpose.** Moonraker behind a reverse proxy
that does not forward WebSocket upgrades is a common setup, and so is a Klipper
host reachable only over a tunnel that drops idle connections. A card that stops
updating because of either is a worse failure than a two-second delay. So the
stream is an accelerator: when it is healthy the client backs its poll interval
off, and when it stops the poll comes straight back. Nothing is only knowable
through the WebSocket.

Two consequences worth knowing:

* Updates are **partial**. Moonraker sends the fields that changed, of the
  objects that changed, so a merge one level deep is needed to keep a full
  picture. The subscribe call answers with the complete current status, which is
  what seeds it.
* Updates are **frequent** — `toolhead` alone moves constantly during a print.
  The client coalesces before doing anything with them; this module just
  delivers what arrives.

Built on aiohttp, which is already a declared dependency. The `websockets`
package is present too, but only because `uvicorn[standard]` happens to pull it,
and a transport that disappears when uvicorn changes its extras is not one to
build on.
"""

import asyncio
import json
import logging
import threading
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse, urlunparse

import aiohttp

logger = logging.getLogger(__name__)

# How long without a message before the stream counts as unhealthy and the poll
# takes back over. Moonraker is chatty during a print and quiet when idle, so
# this is generous: it is a "the connection is wedged" detector, not a heartbeat.
STALE_AFTER_SECONDS = 60.0

_RECONNECT_DELAYS = (1.0, 2.0, 5.0, 10.0, 30.0)


def websocket_url(base_url: str) -> str:
    """`http://host:7125` -> `ws://host:7125/websocket`, https -> wss."""
    parsed = urlparse(base_url if "://" in base_url else f"http://{base_url}")
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return urlunparse((scheme, parsed.netloc, "/websocket", "", "", ""))


def subscribe_request(objects: list[str], request_id: int = 1) -> str:
    """The JSON-RPC call that asks for pushed updates on *objects*.

    A null value per object means "every field of it", which is what the poll
    asks for too — the two transports must not disagree about what they watch,
    or the card would change shape depending on which one is live.
    """
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "method": "printer.objects.subscribe",
            "params": {"objects": dict.fromkeys(objects)},
            "id": request_id,
        }
    )


def parse_message(raw: str) -> tuple[str, dict[str, Any]] | None:
    """Classify one frame.

    Returns `("status", {...})` for a status payload — either a pushed
    `notify_status_update` or the full snapshot that answers the subscribe — or
    `("klippy", {"event": name})` for a Klipper lifecycle notification, or None
    for anything else, including malformed frames. Never raises: a printer is
    allowed to say something this does not understand.
    """
    try:
        message = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(message, dict):
        return None

    method = message.get("method")
    if method == "notify_status_update":
        params = message.get("params")
        if isinstance(params, list) and params and isinstance(params[0], dict):
            return ("status", params[0])
        return None
    if isinstance(method, str) and method.startswith("notify_klippy_"):
        return ("klippy", {"event": method[len("notify_klippy_") :]})

    # The answer to our subscribe: {"result": {"status": {...}, "eventtime": n}}
    result = message.get("result")
    if isinstance(result, dict) and isinstance(result.get("status"), dict):
        return ("status", result["status"])
    return None


def merge_status(cached: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    """Fold a partial update into the cached full status.

    One level deep: Moonraker names the objects that changed and, within each,
    only the fields that changed. Replacing the object wholesale would drop every
    field it did not mention — the temperature would vanish the moment only the
    target moved.

    Returns a new dict rather than mutating, so a caller holding the previous one
    for comparison still has it.
    """
    merged = dict(cached)
    for name, fields in update.items():
        if isinstance(fields, dict) and isinstance(merged.get(name), dict):
            merged[name] = {**merged[name], **fields}
        else:
            merged[name] = fields
    return merged


class MoonrakerStatusStream:
    """Runs a WebSocket in its own thread and hands status updates to a callback.

    Start it after the object list is known; stop it with the client. Every
    failure is a log line and a reconnect, never an exception into the caller:
    the poll is still running, so a stream that cannot connect is a missed
    optimisation rather than a broken printer.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str | None,
        objects: list[str],
        on_status: Callable[[dict[str, Any]], None],
        on_klippy_event: Callable[[str], None] | None = None,
    ) -> None:
        self._url = websocket_url(base_url)
        self._headers = {"X-Api-Key": api_key} if api_key else {}
        self._objects = list(objects)
        self._on_status = on_status
        self._on_klippy_event = on_klippy_event
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._last_message = 0.0
        self._connected = False
        # The loop and task the thread is running, so stop() can reach into it.
        # Setting the event is not enough on its own: the thread spends its life
        # awaiting the next frame, and an idle printer sends none, so a flag
        # nobody looks at leaves the thread alive forever.
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task | None = None

    # ------------------------------------------------------------- lifecycle

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="moonraker-ws", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        loop, task = self._loop, self._task
        if loop is not None and task is not None and not task.done():
            # Cancelling from this thread is not safe; hand it to the loop.
            loop.call_soon_threadsafe(task.cancel)
        thread = self._thread
        if thread and thread.is_alive() and timeout > 0:
            thread.join(timeout)
        self._thread = None
        self._connected = False

    @property
    def healthy(self) -> bool:
        """Connected and heard from recently enough to be trusted."""
        if not self._connected:
            return False
        return (time.monotonic() - self._last_message) < STALE_AFTER_SECONDS

    # ---------------------------------------------------------------- internals

    def _run(self) -> None:
        try:
            asyncio.run(self._reconnect_forever())
        except asyncio.CancelledError:
            pass  # stop() asked for this
        except Exception:  # noqa: BLE001 - the thread must not take the process with it
            logger.exception("Moonraker stream thread stopped")
        finally:
            self._connected = False
            self._loop = None
            self._task = None

    async def _reconnect_forever(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._task = asyncio.current_task()
        attempt = 0
        while not self._stop.is_set():
            try:
                await self._session()
                attempt = 0  # a clean session resets the backoff
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - every failure is a retry
                logger.debug("Moonraker stream %s: %s", self._url, exc)
            finally:
                self._connected = False
            if self._stop.is_set():
                return
            delay = _RECONNECT_DELAYS[min(attempt, len(_RECONNECT_DELAYS) - 1)]
            attempt += 1
            await asyncio.sleep(delay)

    async def _session(self) -> None:
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=10, sock_read=None)
        async with (
            aiohttp.ClientSession(timeout=timeout) as session,
            session.ws_connect(self._url, headers=self._headers, heartbeat=30) as ws,
        ):
            await ws.send_str(subscribe_request(self._objects))
            self._connected = True
            self._last_message = time.monotonic()
            logger.info("Moonraker stream connected: %s", self._url)
            async for frame in ws:
                if self._stop.is_set():
                    return
                if frame.type is not aiohttp.WSMsgType.TEXT:
                    continue
                self._last_message = time.monotonic()
                self._dispatch(frame.data)

    def _dispatch(self, raw: str) -> None:
        parsed = parse_message(raw)
        if parsed is None:
            return
        kind, payload = parsed
        try:
            if kind == "status":
                self._on_status(payload)
            elif kind == "klippy" and self._on_klippy_event:
                self._on_klippy_event(str(payload.get("event") or ""))
        except Exception:  # noqa: BLE001 - a bad callback must not kill the stream
            logger.exception("Moonraker stream callback failed")
