# A0 read against LayerCove's Moonraker transport

[Timpan4/layercove](https://github.com/Timpan4/layercove) is another Bambuddy fork that added
Klipper support, independently and differently: 164 commits ahead of upstream, a provider registry
with a capability-driven UI, and a Moonraker transport split across `moonraker_http.py`,
`moonraker_websocket.py` and an ASGI upload-limit middleware. This fork solved the same problem with
a duck-typed `MoonrakerClient` and about sixty lines of diff inside upstream's files (see A0).

Read on 2026-09-09 against `backend/app/services/moonraker_client.py`. Four things they do that we
do not; one of them was worth acting on. Read, not run — this is a comparison of source, not a
benchmark.

## 1 · Redirects — no change needed

They block redirects on the Moonraker WebSocket explicitly. The equivalent worry on our side is a
redirect carrying the `X-Api-Key` header to another host.

All six of our call sites use module-level `httpx` functions, and **httpx does not follow redirects
by default** — unlike `requests`, where this would have been a real leak. Nothing overrides it. A 3xx
comes back as a response, fails to parse as JSON, and is handled like any other bad reply.

Worth recording precisely because it looks like a gap and is not: the protection is a library
default, so a future switch to `requests`, or an `httpx.Client(follow_redirects=True)`, would open it
silently.

## 2 · Download size — fixed

Neither `MoonrakerClient.download_file` nor `klipper_timelapse.download` bounded what it read.
Both took `response.content`: the whole file, in memory, before anything wrote it out. The size is
the printer's to decide. A multi-day print's G-code runs to hundreds of megabytes, a 4K timelapse
likewise, and this app has already been OOM-killed once on its container.

LayerCove bounds the equivalent path at the other end — an ASGI middleware that rejects an oversized
multipart body with 413 before it is spooled. Ours is an outbound fetch, so the shape differs, but
the discipline is the one we were missing.

Both now stream and count, capped at `MAX_DOWNLOAD_BYTES` (512 MiB). `Content-Length` is checked
first where present, so an unreasonable file is refused before its body is read, and the arriving
bytes are counted regardless — the header is the server's claim, and a chunked response carries
none. The timelapse path refuses even earlier when the directory listing already declared the size.

Over the cap raises, which every caller already treats as "leave the archive as it was": the same
outcome as any other failed fetch, and better than taking the process down with it.

## 3 · WebSocket instead of polling — recorded, not done

They subscribe to Moonraker's WebSocket for live status. We poll `/printer/objects/query` on a
thread every two seconds, which is a status update up to two seconds late and a request per printer
per tick, forever.

The WebSocket is the better transport. It is also a rewrite of the one part of A0 that is load-
bearing and well covered by tests, for a benefit measured in seconds of latency on a three-printer
shop. Their own commit list shows the cost honestly: eleven follow-up commits titled "harden",
"complete the lifecycle", "isolate the callbacks", "reset reconnect state safely", "clear stale job
state". Not now, and not without a reason better than tidiness.

## 4 · SSRF and DNS rebinding on the printer URL — still open, still recorded

They resolve the Moonraker host up front and pin the connection to the approved peer addresses,
refuse unix sockets, and control TLS verification per printer. That closes DNS rebinding on an
admin-entered URL.

Ours is unguarded, and was already recorded as such: `test_outbound_url_ssrf_guards.py` lists
`("PrinterCreate", "api_url")` and `("PrinterUpdate", "api_url")` under
`KNOWN_UNGUARDED_NEEDS_SCHEME_AWARE_GUARD`, in the same class as upstream's own camera URLs. The
comment there still describes the work: closing it needs a scheme-aware variant of the LAN-service
guard, not a one-line delegation.

Unchanged by this read, but now with a worked example of what the fix looks like if it is ever
picked up.
