# Printhok feature register

Every change this fork makes to [maziggy/bambuddy](https://github.com/maziggy/bambuddy), what it is
for, which files carry it, and what is still open. This is the checklist for a rebase: after moving
the series onto a new upstream base, everything below must still be true.

Base: upstream `main` at `9b2c49d8`. Commits are given as they exist on `voron` today; a rebase
rewrites the SHAs but not the order or the subjects.

**Legend:** ✅ done and in use · ⚠️ done with a known gap · ⬜ deliberately not done

**Parts:** A — Klipper and Moonraker, built here · B — reimplemented from Printbuddy · C — Klipper
parity, built here · D — deliberately not ported · E — from the other forks.

---

## Part A — Klipper and Moonraker (fork-original)

### A0 · A Klipper printer is just a printer

| | |
|---|---|
| **Status** | ✅ |
| **Commits** | `e1b216d9` model · `3f87c069` client · `40308741` manager · `54de892a` routes + scheduler · `1f2b48f2` frontend · `eff07678` tests · `0af92dca` no FTPS sweep · `830717a1` chamber light |
| **New files** | `backend/app/services/moonraker_client.py`, `backend/app/services/moonraker_dispatch.py`, `backend/tests/unit/services/test_moonraker_client.py` |
| **Touched upstream** | `models/printer.py`, `schemas/printer.py`, `core/database.py`, `services/printer_manager.py`, `services/print_scheduler.py`, `api/routes/printers.py`, `main.py`, `frontend/src/api/client.ts`, `frontend/src/pages/PrintersPage.tsx` |

**Why it is built this way.** `MoonrakerClient` exposes the same `state: PrinterState` and the same
methods as upstream's `BambuMQTTClient`. Because of that duck-typing, the printer manager, the status
routes, the WebSocket, the scheduler, the Home Assistant sensors and the archive need no provider
branch at all — the diff inside upstream's files is about 60 lines. Printbuddy solved the same problem
with a provider-factory package, which touches far more upstream code and is the reason its merges
became unmanageable.

**What it does.** New `Printer` columns `provider` (default `bambu`), `api_url`, `auth_token`,
`provider_options`, with an ALTER migration for SQLite and Postgres. Klipper rows get the synthetic
serial `KLIPPER-<host>` and an IP derived from the Moonraker URL. A poll thread on
`/printer/objects/query` maps Klipper state onto `PrinterState`. Create and test probe Moonraker and
register its configured webcam as an external camera. Queue dispatch uploads to Moonraker instead of
FTPS, and the two FTPS cleanups are skipped. `main.py` skips the FTPS archive sweep for Klipper
printers. The chamber light is selected by name so MMU and toolhead LED strips are not driven.

**Guard against upstream drift:** if upstream ever adds its own `provider` column or a non-Bambu
transport, this is the commit to reconcile first — see `upstream-sync.md`.

### A1 · External spool slot for printers without an AMS

| | |
|---|---|
| **Status** | ✅ |
| **Commit** | `6ca70bbc` |

A printer with no AMS gets a single external slot you assign a spool to, and filament is booked from
the grams the archive records. Upstream only knows Bambu's external spool (tray 254/255), so without
this a Klipper print produces no filament usage and no cost anywhere in the archive or reports.

### A2 · Klipper printer card

| | |
|---|---|
| **Status** | ✅ |
| **Commits** | `e6dd9e3d`, `92345de7` |

A "Klipper" badge, no MQTT diagnostics panel, and no AMS, calibration or drying blocks — those simply
never render, because a Klipper printer reports no AMS. Jog, home, extrude and temperature controls
were already in upstream's card and work as-is over Moonraker G-code, so no separate control panel was
needed. Bambu-only folders (`/cache`, `/model`, `/timelapse`) return an empty list in the file manager
rather than a warning.

### A3 / A4 · File manager over Moonraker, start a file in place

| | |
|---|---|
| **Status** | ✅ |
| **Commits** | `518b07f1`, `54234c7f` |

List, download and delete files on the printer, and start one that is already there — for G-code that
was uploaded through Mainsail without going back through the library.

### A5 · Smart plug energy per print

| | |
|---|---|
| **Status** | ✅ configuration only, no code |

Link the printer to its smart plug under Settings → Smart plugs and fill in the energy-total sensor;
kWh then flows through upstream's existing plumbing. Nothing in the fork changes.

Done for all three printers as of 2026-09-09: Stekker Henk, Eddy and Kees each carry a
`ha_energy_total_entity`, so `archive.energy_cost` is real on every machine. That is what makes E1's
price worth anything, and it is why the "estimate energy without a plug" idea from another fork was
dropped rather than ported — there is no printer here without one.

### A6 · Chamber temperature

| | |
|---|---|
| **Status** | ✅ |
| **Commit** | `d3216632` |
| **New file** | `backend/app/services/provider_options.py` |

Klipper has no chamber concept: an install names its own sensor in `printer.cfg`. The original guess
only recognised an object starting `temperature_sensor chamber`, so an enclosure sensor called
anything else — most of them — went unnoticed and the card showed nothing where a Bambu shows a
temperature. The answer used to be "rename your sensor", which is fine for the person who wrote it
and no use to anyone else.

The object is now configured per printer, offered in the edit dialog as a list of what that printer
actually reports: `GET /printers/{id}/klipper/chamber-candidates` asks Moonraker live, so a sensor
added since Bambuddy started appears without a restart. Empty means Automatic — the old guess,
unchanged — and the help line names what it found, so the default is legible rather than mysterious.
A disconnected printer falls back to a free-text box instead of an empty dropdown.

A configured object the printer does not report is ignored in favour of the guess: Moonraker answers
a query for an unknown object with an error, so insisting would cost every poll rather than just the
chamber reading, and a sensor renamed in `printer.cfg` would take the whole card down with it.

Stored in `printers.provider_options` — the nullable JSON column A0 added and nothing had read since.
`provider_options.py` is the only thing that knows the storage is JSON; routes read and write typed
fields. Forgiving on the way in, because the column is free-form text a hand-edited database could
leave in any shape and a printer that fails to load is worse than one with default options. Clearing
a key removes it rather than storing null, so "cleared" and "never set" cannot come to mean different
things.

### A7 · Fourteen locales

| | |
|---|---|
| **Status** | ✅ |
| **Commits** | `e6dd9e3d`, `d63f8954`, `b5463da0` |

All Klipper keys exist in all fourteen locales. B10's `printers.tv.*` and B11's `farm.*` followed in
`d63f8954`; B6's, B8's and B9's blocks — the last that shipped `en`/`nl` only — in `b5463da0`. Every
key this fork adds now exists in every locale, and `npm run check:i18n` proves it on each run.

### A8 · Archive a print started outside Printhok

| | |
|---|---|
| **Status** | ✅ |
| **Commits** | `e6dd9e3d`, hardening in `cdb8351e` |
| **New file** | `backend/app/services/klipper_archive.py` |

Start a print from Mainsail and the archive row still gets its G-code, fetched back from Moonraker,
instead of an empty fallback. `cdb8351e` fixed a real path bug found by the SSRF/path backstop test:
the filename filter allowed dots through, so a printer reporting a file literally named `..` resolved
one directory above the archive. Dot-only names now fall back to `print.gcode`.

### A9 · Happy Hare MMU as AMS units

| | |
|---|---|
| **Status** | ✅ |
| **Commits** | `17cb2a29`, `2a16e30c` |

`mmu`, `mmu_machine` and `save_variables` are polled. Gates map onto AMS trays (unit = `gate // 4`,
tray = `gate % 4`, so the global tray id is the gate number), the loaded gate follows `tray_now`, and
bypass stays slot 254. Assigning a spool writes `MMU_GATE_MAP`. Load and unload are `MMU_SELECT` +
`MMU_LOAD` / `MMU_UNLOAD`. A queue dispatch carrying an AMS mapping sends `MMU_TTG_MAP`, and filament
is booked per tool through that map using the per-extruder grams from the slicer comments. `2a16e30c`
makes an MMU unit render one slot per gate instead of a fixed four.

### A10 · Raw `.gcode` in the library

| | |
|---|---|
| **Status** | ✅ |
| **Commits** | `2287722c`, `2d9064c7` |
| **New file** | `backend/app/services/gcode_metadata.py` |

Upstream accepts 3MF only. Once an active Klipper printer exists, a bare `.gcode` file is accepted and
its slicer comments are parsed for print time, filament, layers and temperatures under the same keys
the 3MF parser produces, so the library card, the queue and the cost calculation all behave normally.
With no Klipper printer configured the upstream rejection is unchanged, which keeps upstream's tests
green.

### A11 · Branding

| | |
|---|---|
| **Status** | ✅ |
| **Commits** | `ebc0d486`, `e241ee12` |
| **New file** | `frontend/src/brand.ts` |

Display name and logo from `/brand.json`, used by the sidebar, login, setup, stream overlay and tab
title. `e241ee12` moved it under `/img/` so it bypasses the SPA catch-all route. Changing the name on a
running install is a file edit and a page reload — no rebuild. The backend, service name, install
paths, API and translated strings deliberately keep saying Bambuddy: renaming those is pure rebase
surface.

### A12 · Pushed status over Moonraker's WebSocket

| | |
|---|---|
| **Status** | ✅ |
| **Commits** | `a7bbd33c`, `6448b477` lazy start |
| **New files** | `backend/app/services/moonraker_stream.py`, `backend/tests/unit/services/test_moonraker_stream.py`, `backend/tests/integration/test_moonraker_stream_live.py` |

A0 polled `printer/objects/query` every two seconds: a card up to two seconds stale, and one request
per Klipper printer per tick, forever. Moonraker pushes over the JSON-RPC socket at `/websocket`.

**The poll stays, deliberately.** Moonraker behind a reverse proxy that will not forward an upgrade
is common, and so is a host reached through a tunnel that drops idle connections; a card that stops
updating because of either is worse than a two-second delay. The stream is an accelerator — while it
is healthy the poll interval backs off from 2s to 30s, and the moment it goes quiet it snaps back.
Nothing is knowable only through the socket.

Both transports reach the card through `_apply_status`, split out of the poll for exactly that
reason: two paths that processed status differently would be two things to keep in step, and the
difference would surface on someone else's printer, not here.

Pushed updates are **partial** — the fields that changed of the objects that changed — so they merge
into a cached full picture seeded by the snapshot that answers the subscribe. Replacing an object
wholesale would drop its temperature the moment only the target moved. They are also **frequent**,
since toolhead position never stops changing, so applying is coalesced to four times a second while
the merge runs on every frame: dropped from the work, never from the picture.

Per-printer `transport` setting beside the chamber object: Automatic, or polling only.

Built on aiohttp, already a declared dependency. `websockets` is installed as well, but only because
`uvicorn[standard]` pulls it, and a transport that disappears when uvicorn changes its extras is not
one to build on.

**Verified against the real Voron on 2026-09-09.** It connects at `ws://192.168.2.177/websocket` —
port 80, so through Mainsail's own nginx rather than straight to Moonraker on 7125, which is the
setup most people actually have and the one the fallback exists for. Temperatures then change on
every three-second sample while the poll is backed off to thirty, which only the socket can be doing.

The fake Moonraker in the integration tests earned its place first: it drives connect, subscribe,
snapshot, pushed update, API key on the handshake, reconnect after the server hangs up, and a
callback that raises — and it caught the bug this kind of change exists to have. `stop()` set a flag
the thread never looked at, because the thread spends its life awaiting a frame that an idle printer
never sends, so every disconnect leaked a thread.

`6448b477` fixed a second one, found by deploying onto a printer that was switched off: the stream
started at the end of `connect()`, inside the try that reachability decides, and nothing opened it
afterwards because the poll is what recovers that connection. A printer powered on after the server —
the normal order in a workshop — would have looked fine and never used the socket at all. The poll
now opens it the first time the printer answers.

### A13 · A Klipper printer is not drawn as a Bambu

| | |
|---|---|
| **Status** | ✅ |
| **New file** | `frontend/public/img/printers/klipper.svg` |

Every image in `public/img/printers/` is a product render of a specific Bambu machine, and
`getPrinterImage` fell back to `default.png` — the X1 — for any model string it did not recognise.
A Klipper model is free text and matches none of them, so every Voron, RatRig and Ender in this fork
was drawn as a Bambu Lab X1, badge and all.

`getPrinterImage` now takes the provider, and anything that is not `bambu` gets neutral line art:
the frame every CoreXY shares, in `currentColor`, carrying no brand. Not a photo of a Voron, because
Klipper machines are all different and a picture of one particular printer would be just as wrong as
a picture of an X1.

The provider is checked before the model, so a machine whose name happens to contain a Bambu model —
"Voron A1", "X1 clone" — cannot be steered back to a render by a substring match. Bambu printers are
untouched.

---

## Part B — Ported from Printbuddy

Reimplemented against this codebase, not merged. A trial merge of Printbuddy onto upstream put more
than a hundred source files in conflict; the nine files both projects touch heavily
(`PrintersPage.tsx`, `print_scheduler.py`, `routes/printers.py`, `core/database.py` and friends) are
exactly the ones a merge cannot resolve cleanly.

### B1 · "Print almost done" at 97 %, with a snapshot ✅ `d581c6ba`

A fourth notification event beside start, finish and failure, carrying a camera snapshot — so you walk
to the printer as it finishes rather than after.
Touches `services/notification_service.py`, `models/notification_template.py`, `main.py`,
`components/AddNotificationModal.tsx`, `NotificationProviderCard.tsx`, `en`/`nl` locales.

### B2 · A provider can target several printers ✅ `f5ae369d` + `ee8a6e9e`

Upstream allows one printer or all. This adds a `printer_ids` JSON column with a migration, schema and
UI. `ee8a6e9e` is the follow-up that put `printer_ids` on `NotificationProvider` /
`NotificationProviderCreate` instead of on `FailureAnalysis` and `updateArchive`, where the first pass
had wrongly placed it — five `tsc` errors that only appeared once the whole series was built together.

### B3 · Multi-word inventory search ✅ `4a0b8b8b`

`abs black` matches across fields instead of demanding one substring.
`frontend/src/utils/inventorySearch.ts`.

### B4 · Project progress counts ✅ `a16f8293`

Parts done, failed and queued on one line under the parts bar of `ProjectsPage.tsx`. A deliberately
small local version of Printbuddy's Farm Command Center, which is built for twenty-plus printers.

### B5 · One alert banner ✅ `5549e216`

`GET /alerts/summary` (new `api/routes/alerts.py`) plus an amber banner in `Layout.tsx`, combining
spools running low and maintenance that is due.

### B6 · Open Filament Database lookup ✅ `0a5211bf`

Search OFD from the Add Spool form and fill the fields from the result. New
`services/open_filament_database.py` and `api/routes/open_filament_database.py`, plus
`components/spool-form/OpenFilamentDatabaseSearch.tsx`. Off by default — it is an external API call —
under Settings → Filament controls. Documented separately in
[`docs/open-filament-database.md`](../open-filament-database.md).

### B7 · Moonraker subnet scan ✅ `fff5c138`

"Add printer" discovery also probes port 7125, so a second Klipper machine is found the same way a
Bambu is. `services/discovery.py`.

### B8 · Pending spool-to-slot assignment ✅ `fdca5c3b`, `81334b7e`

"This spool goes into the next slot that gets loaded." New table `pending_slot_assignments` with a
30-minute timeout, `services/pending_slot_assignment.py`, and controls in the inventory list and the
slot dialog. The default target is **any printer** — that is the point of the feature — and you can
still pick one; opened from a slot dialog, that printer is preselected.

### B9 · Notify (iOS) Live Activity provider ⚠️ `39537544`

A provider that pushes a Live Activity countdown tile to the iOS app *Notify*. Ported line by line
from Printbuddy: same endpoints, query strings and field names (`title`, `body`, `progress`, `status`,
`symbol`, `tint`). Two deliberate differences — `groupType` is `bambuddy` rather than `printbuddy` (a
free grouping string), and `iconUrl` is omitted because Printbuddy points at its own icon on GitHub
and there is no public URL for the Printhok logo, so pushes show the app's default icon.
**Gap:** never tested against the paid app. Whether Notify accepts the payloads is unverified.

### B10 · `/tv` kiosk wall ✅ `9adb27eb` + `b3312677`

A wall of tiles for a screen in the workshop, with a refresh interval.

`b3312677` added the part that makes it usable on a TV: a token mode, so the screen does not have to
stay logged in. It is a **fourth long-lived scope, `tv`**, not a widening of `camwall` — a camwall
token is issued on the promise that the wall never names the part, and a TV tile names the file on the
bed and the spool feeding the hotend. Its own feed `GET /tv/printers` returns one response per refresh
containing exactly the fields a tile draws; serials, IPs and access codes stay server-side, and
`tray_now` is resolved on the server so a wall token sees the loaded spool and no other tray. The page
keeps a single render path — both modes collapse into `TvTileData` via `tileFromStatus` /
`tileFromFeed` — so the kiosk wall and the logged-in wall cannot drift apart. Tokens are minted on the
existing Camera API Tokens page, which hands back a ready `/tv?token=…` URL (`&cams=0` hides the camera
images, `&refresh=10` sets the poll cadence).
Translated in all fourteen locales since `d63f8954`; it shipped `en`/`nl` only.

### B11 · Farm command center at `/farm` ✅

A single screen for the whole operation: fleet tiles bucketed by group, fleet
utilization, parts completed today, active projects, a roll-up of everything that
wants a hand, and the shortcuts out to inventory and maintenance.

**Backend (new):** `models/printer_fleet_group.py`, `schemas/printer_fleet_group.py`,
`api/routes/printer_fleet_groups.py`, `tests/integration/test_printer_fleet_groups_api.py`.
A fleet group is a name plus a set of printers — deliberately *not* upstream's
`models/group.py`, which is a user permission group. Grouping is presentation only:
nothing schedules or restricts against it. Reads need `printers:read`, writes
`printers:update`. Two deliberate differences from Printbuddy: a duplicate name
answers 400 rather than letting the unique constraint surface as a 500, and unknown
printer ids are rejected before the table is touched.

**Frontend (new):** `pages/FarmCommandCenterPage.tsx`, `utils/farmFleet.ts` and tests
for both. Every calculation lives in the util so it is testable without rendering.

`classifyFleetState` mirrors `classifyPrinterStatus` in `PrintersPage.tsx` instead of
importing it: that function is module-private there, and exporting it would mean
editing the file upstream changes most, on every release, forever. The buckets have to
stay in step — FAILED with no attached HMS code is terminal like FINISH, not an alert,
and a disconnected printer is offline whatever its last state said.
`PrintersPageBucketing.test.ts` mirrors the same rules for the same reason.

**Touched upstream:** `main.py` and `core/database.py` (router, model registry, idempotent
migration), `models/__init__.py`, `api/client.ts`, `App.tsx` (route), `Layout.tsx`
(sidebar entry), `en`/`nl` locales.

Ported from Printbuddy's `FarmCommandCenterPage`, with three changes: it is translated
rather than hardcoded English, "TV mode" points at this fork's `/tv` rather than
Printbuddy's `/farm-monitor`, and the alert builder returns i18n keys instead of
sentences. Printbuddy's *Dispatch Suggestions* panel is not included — it only ever
restated queued and failed counts that B4 already puts on the Projects page.

Translated in all fourteen locales in `d63f8954`, which also corrected the five count-bearing
`farm` keys from a bare base key to the `_one`/`_other` pairs the rest of the codebase uses.

---

## Part C — Klipper parity (fork-original)

Six places where a Klipper printer was still a second-class citizen next to a Bambu on the same
dashboard. Each one is Moonraker already exposing something this fork ignored. Nothing here is ported
from Printbuddy — it does not have any of it either.

### C1 · Import past jobs from Moonraker's history ✅ `7cd26066`

`POST /printers/{id}/klipper/import-history` walks `server/history/list` and writes the archive rows
Bambuddy never saw, so a machine that has been printing for a year does not arrive with an empty
history next to a Bambu showing months. Identity is `subtask_id = "moonraker:<job_id>"` — an existing
indexed column that already answers "is this the same print", and one no Klipper client sets
otherwise, so a re-run is a set difference rather than a scan of every archive's JSON.

Two deliberate limits. Rows carry no file, marked the way upstream marks a print whose 3MF could not
be fetched: pulling hundreds of multi-megabyte G-codes to fill a history view is not a trade anyone
asked for. And grams are booked only where the slicer wrote a weight — Moonraker reports millimetres,
converting those needs a diameter and a density this code cannot know, and a guess would land in the
cost column of every report. The millimetres go to `extra_data`.

New file `services/klipper_history.py`. Button in the printer's edit dialog: a setup-time action, run
once per printer.

### C2 · Klipper's own shutdown and error message ✅ `3b7136d9`

A Klipper shutdown makes `printer/objects/query` answer 503, so the card said "Offline" — the word a
printer switched off at the wall gets — for a machine standing there with "MCU 'mcu' shutdown: Lost
communication with MCU" on its screen. The Klipper badge hides the connection diagnostic too, so
there was nothing left to click.

Moonraker answers `printer/info` precisely when it cannot answer a status query. The client asks on
every failed poll, keeps the answer in `raw_data["klippy"]`, and clears it when Klipper replies
normally. A repeat failure only re-broadcasts when the reason changed, so an offline printer does not
push a websocket frame every two seconds. Surfaced as `klippy_state` / `klippy_message` on
`PrinterStatus`, on the connection pill, and as a `printer_faults` group in B5's banner — faults
only, because a printer switched off overnight is not an alert.

Also closed a gap B5 left: its banner block existed in `en` and `nl` only. All fourteen now have it.

### C3 · Moonraker `[power]` devices as a plug type ✅ `e7a79413`

Klipper users wire a relay into Moonraker's `[power]` section rather than bolting a Tasmota on the
wall, and none of the plug automation could see those — a Klipper printer had its socket configured
twice or had no automation at all.

A plug of type `moonraker` carries no address: it points at a printer, which already holds the URL
and API key. The device is picked from `machine/device_power/devices`, not typed, because Moonraker
matches names exactly and a typo would fail silently at print start. `get_energy` returns None, which
is honest — that API answers on/off and nothing else — and every energy consumer already handles a
plug without a meter. Unreachable stays distinct from off: an auto-off that believed a silent host
was already off would leave a printer powered all night.

New file `services/moonraker_plug.py`, one new nullable column `smart_plugs.moonraker_device`, and
three lines at each of the two service-dispatch points.

### C4 · Timelapses from moonraker-timelapse ✅ `57a8c6cc`

`timelapse_was_active` was hardcoded False and upstream's whole timelapse flow is gated on it, so a
Klipper print got neither the video nor the finish photo taken from its last frame.

The flow itself needed nothing: snapshot the directory at print start, diff at completion, download,
attach, delete, is transport-agnostic. This adds the four I/O calls spoken to Moonraker's `timelapse`
file root — four branches in `main.py`, no restructuring — and asks whether the plugin is installed
(`server/info` components) and switched on (`machine/timelapse/settings`, read per print, because
that toggle gets flipped between prints).

Both safety gates carry over: a short download returns None so the printer keeps its copy, and
`settled` re-lists before the delete, because ffmpeg renders after the print ends and a file listed
mid-render serves its own short length.

New file `services/klipper_timelapse.py`.

### C5 · `exclude_object` drives the skip-objects UI ✅ `08ca81c3`

Cancelling one failed part is a Klipper feature every recent slicer emits, and Bambuddy already had
the whole UI for it — modal, list, pick-on-camera overlay — built for Bambu's `skip_objects` and
unreachable from Klipper.

The route and the modal are already provider-agnostic, so this fills `printable_objects` and
`skipped_objects` from Klipper's `exclude_object` and implements `skip_objects` on the client.
Klipper names objects rather than numbering them, so the id is the index in the list Klipper reports
and the client keeps the mapping — stable for the print, being the `EXCLUDE_OBJECT_DEFINE` lines the
slicer wrote. Polygons become the bounding box the camera-pick maps clicks through, and only polygons
do: a centre alone gives a box with no area.

The objects route's two fallbacks reach for the print's 3MF over FTPS, which a Klipper printer
neither has nor serves; both are skipped for Klipper, whose list is refreshed on every poll.

### C6 · Klipper and Moonraker versions from `update_manager` ✅ `32159ee7`

The Firmware page checks each printer against Bambu Lab's download page, where a Voron has no entry,
so the row stayed blank for a machine whose Klipper might be a year behind its Moonraker.

`machine/update/status` already knows. Klipper's version becomes the badge; every component rides
along in a new `components` list, because "Klipper is behind" and "the OS has 14 packages waiting"
are different jobs. `refresh=false` — Moonraker refreshes on its own timer and forcing one per page
load would spend a GitHub rate limit per printer. A component Moonraker could not check reports `?`,
which is not treated as a difference.

Read-only on purpose: updating Klipper restarts it, and that belongs to whoever is standing next to
the printer, in Mainsail. So the Klipper badge is not a button, and both firmware-upload routes
refuse a Klipper printer rather than starting an FTPS transfer that cannot land.

New file `services/klipper_update.py`.

---

## Part E — from the other forks

Upstream has 393 forks; eight diverge meaningfully. Surveyed on 2026-09-09 —
`OneDrive\Claude\docs\bambuddy-forks-survey.md` has the full landscape and what was
rejected. Like Part B these are reimplementations: every one of those forks is
hundreds of commits behind upstream, so a merge is not on the table.

### E1 · Suggested selling price ✅ `97f2fa59`

Upstream's `finance` module is a chargeback system — cost centres, budgets, per-user
wallets, a print charged against a balance. It answers "who owes what for the machine
time", which is a makerspace's question. Nothing upstream answers a shop's.

Four settings (labour per print hour, markup, minimum price, and a switch) turn the
`cost` and `energy_cost` already on every archive row into a suggested price beside the
existing cost line, the margin in the tooltip, and a marker when the minimum rather than
the markup set the number. Off by default.

The arithmetic is a pure frontend util (`utils/printPrice.ts`) so it follows a settings
change without a round trip and is testable without rendering. The one part that cannot
live there is the reference figure: `GET /archives/price-reference`
(`services/print_price.py`) returns the **median** unit cost of recent completed prints,
because the list the browser holds is paginated and filtered, and a median taken from it
would describe whichever page was open. Median rather than mean — one exotic filament or
one nine-hour failure drags an average somewhere unhelpful — and zero-cost rows are
excluded, since filament that was never priced is not a print that was free.

A markup below 1 prices under cost and the margin reads negative rather than being
clamped; that is the number doing its job. Negative settings are treated as unset, so a
stray minus cannot put a nonsense price on every card.

From [Aito3D/fenrir](https://github.com/Aito3D/fenrir), the one fork built for a print
shop rather than a farm. Its margin-curve charts and formula popovers are not here.

### E2 · Movement refused while a print is loaded ✅ `69804332`

The printer card already hides jog and home while a job runs — advice, not enforcement.
`bed-jog`, `xy-jog`, `extruder-jog` and `home-axes` accepted the call anyway, so a stale
tab, a second browser, an API client, or a print starting from the queue while the panel
is open could put a relative move in front of the slicer's own G-code.

All four now answer 409 on `printer_manager.is_print_active` — deliberately the predicate
upstream already uses to refuse a start, not a new one, so the API and the button cannot
disagree. Temperature, fan, light and pause/resume/stop stay available. Klipper is covered
without a provider branch, because the predicate reads the shared `PrinterState`.

The guard sits after argument validation (a typo still answers 400) and before the client
lookup (nothing reaches the machine on the refused path). Fifteen existing tests patched
`printer_manager` with a bare MagicMock, whose auto-created `is_print_active` is truthy;
they now say the printer is idle, which is what they always meant.

From [khaosdoctor/bambuddy](https://github.com/khaosdoctor/bambuddy).

### E3 · Queue job name ✅ `418bfbbe`

An optional label on a queue item, offered in the print modal's schedule block. When set,
the dispatcher derives the **uploaded filename** from it, so the machine's own screen
names the order rather than the model. Empty means today's behaviour byte for byte.

Deliberately not the source fork's approach. khaosdoctor sends a separate `subtask_name`
on the MQTT command and leaves the file alone, which reads tidier until you follow
`subtask_name` through this codebase: it is how the cover image and the 3MF are resolved
over FTP, what the archive is named after, and what the running-print matcher compares
against. A label there splits a print's identity from its file in fourteen places.
Renaming the upload keeps one name for one print, and the archive carries the order number
too.

The label is free text, so `safe_path_component` runs before it is a path — separators and
the Windows-reserved set become dashes, `..` cannot climb, and the byte budget stops short
of the filesystem cap so the suffix fits. Each deriver keeps its own rules on top: the
Bambu path replaces spaces because the firmware parses `ftp://{filename}` as a URL,
Moonraker keeps them because it takes the name over an HTTP multipart upload, and the plate
suffix survives so two plates of one job cannot overwrite each other. Not offered on the
bulk update, where one label across many items would have every upload overwrite the last.

### E4 · Bounded Klipper downloads, and the A0 read ✅ `f2e744c6`

`MoonrakerClient.download_file` and `klipper_timelapse.download` both took
`response.content` — a whole file, in memory, at a size the printer decides. Both now
stream and count against a 512 MiB cap, checking `Content-Length` first where it exists
and the arriving bytes regardless. Over the cap raises, which every caller already treats
as "leave the archive as it was".

Found by reading [Timpan4/layercove](https://github.com/Timpan4/layercove), which solved
Klipper support independently. Three further differences are recorded rather than changed
— redirects (no fix needed: httpx does not follow them by default), a WebSocket transport
instead of our two-second poll, and DNS-rebinding protection on `api_url`. Written up in
[`moonraker-vs-layercove.md`](moonraker-vs-layercove.md).

---

## Part D — deliberately not ported

Other printer brands (Prusa Link/Connect, Elegoo SDCP, Creality CFS, Snapmaker U1) — every extra
provider doubles the conflict surface, and the fork's whole design is one non-Bambu transport. Panda
Breath. Printbuddy's staging, batch review, production plans and alert groups — all aimed at
twenty-plus printers. Its Dispatch Suggestions panel, which only restated counts B4 already shows.
The Docker self-update sidecar, which upstream already covers with in-app updates.

The Farm Command Center itself *was* skipped on these grounds and later ported anyway, on request —
see B11. Three of its seven panels already existed in smaller form as B4, B5 and B10.

Also not ported because **upstream already has it**: print progress in the browser tab, camera view
mode on the card, quantity when creating a spool, empty-spool weight from the catalogue, go2rtc
snapshots, the A2L model, in-app update, and Cam Wall.

Things upstream has that Printbuddy does not, and that this fork therefore keeps: the sensors tab,
SpoolBuddy, finance and invoicing, Dutch, print progress in the tab title, plate restore for the final
photo, automatic archive and file cleanup, the trash bin, and fourteen languages against nine.

---

### A14 · The Moonraker URL goes through the SSRF guard

| | |
|---|---|
| **Status** | ✅ |
| **New function** | `normalize_moonraker_url` in `backend/app/schemas/printer.py` |
| **Touched** | `schemas/printer.py`, `api/routes/printers.py`, `tests/unit/test_outbound_url_ssrf_guards.py` |

`api_url` is admin-entered and then fetched, and nothing checked where it pointed. It was recorded in
`KNOWN_UNGUARDED_NEEDS_SCHEME_AWARE_GUARD` next to upstream's external camera URLs on the assumption
that it shared their problem. It does not: those must also accept `rtsp://`, which the LAN-service
guard rejects, whereas Moonraker is only ever http(s). So the existing guard fits `api_url` as-is and
the two camera entries stay where they are.

One helper now does the normalizing and the checking together, and all three paths call it —
`PrinterCreate`, `PrinterUpdate` and the `POST /printers/test` probe. That last one is the reason the
work was worth doing beyond bookkeeping: it hands the response back to the caller, so an unguarded
probe was an SSRF that reflected what it read. The `PrinterUpdate` path mattered too — the PATCH
handler used to prefix the scheme itself, *after* validation, so a target refused on create could be
edited in afterwards.

A scheme-less value ("192.168.2.177") is prefixed rather than rejected: it is what the add-printer
form asks for and what the stored rows hold. That is the opposite of the call made for the scheme-less
*settings* URLs, and deliberately so — those stay inert because every consumer hands them to httpx,
which refuses a URL with no scheme, while this one is prefixed and then genuinely fetched.

**Still open, by policy:** DNS rebinding. The guard does not resolve hostnames, so `http://evil.test`
resolving to 169.254.169.254 at request time still passes — the same TOCTOU hole every LAN-tier
consumer has, documented in `_url_safety.py`. Closing it means resolving at request time and pinning
the address, which is a change to the shared guard and not to this fork's patch series.

## Open items

Each one has an issue on the fork, so this table is the summary and the issue is the detail.

| | | |
|---|---|---|
| [#1](https://github.com/DeWarePeb/printhok/issues/1) | A14 | Mostly closed. `api_url` is guarded on all three paths (A14 above), and the two entries moved to `GUARDED_BODY_URLS`. What remains is DNS rebinding, which is the shared guard's documented TOCTOU and not specific to this fork. Upstream's `external_camera_url` / `external_camera_snapshot_url` stay in `KNOWN_UNGUARDED_NEEDS_SCHEME_AWARE_GUARD` — they really do need the scheme-aware variant, because they also dial `rtsp://`. |
| [#3](https://github.com/DeWarePeb/printhok/issues/3) | B9 | Notify payloads unverified against the real iOS app. Needs the paid app; no test can answer it. |
| [#4](https://github.com/DeWarePeb/printhok/issues/4) | i18n | Only the fork's own counted keys have proper Slavic plurals. `8cc1ad1b`, C1 and C2 gave twenty `ru`/`uk` keys their `_few` and `_many` forms; upstream's still use the two-form convention, so roughly thirteen keys per Slavic locale resolve through fallback. Pre-existing and not the fork's to fix — but `b5463da0` taught the gate the difference, so fixing it no longer trips anything. |

Both suites are green as of `302a49db`, verified on LXC 109 in `/opt/bambuddy-b`: backend
`pytest -n 4` at 11970 passed / 1 skipped, and `npm run test:run` at 262 test files followed by
`check:i18n`.

**Typecheck with `npx tsc -b`, or just `npm run build`.** `npx tsc --noEmit` compiles nothing here —
the root `tsconfig.json` is a references file with no `files` or `include` of its own, so it exits 0
without looking at a line. Twenty-six real errors passed three such "clean" checks during Part E and
surfaced only at deploy, where `build` is `tsc -b && vite build`: the short-circuit meant no bundle,
and because `static/` is tracked, the reset had already put upstream's committed bundle back. See
`302a49db`.
