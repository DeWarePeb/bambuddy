<p align="center">
  <img src="frontend/public/img/printhok_logo_dark.svg" alt="Printhok" width="280">
</p>

<h1 align="center">Printhok</h1>

<p align="center">
  <strong>Your printers. No cloud. Bambu <em>and</em> Klipper.</strong><br>
  A fork of <a href="https://github.com/maziggy/bambuddy">Bambuddy</a> that puts a Voron on the same
  dashboard as your P1S — same queue, same archive, same spool inventory.
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-AGPL--3.0-blue?style=flat-square" alt="AGPL-3.0"></a>
  <img src="https://img.shields.io/badge/upstream-maziggy%2Fbambuddy-6b7280?style=flat-square" alt="Upstream">
  <img src="https://img.shields.io/badge/base-main%409b2c49d8-6b7280?style=flat-square" alt="Base commit">
  <img src="https://img.shields.io/badge/support-none%20promised-lightgrey?style=flat-square" alt="Support">
</p>

---

## What this is

Printhok is [maziggy/bambuddy](https://github.com/maziggy/bambuddy) plus a patch series. Not a
rewrite, not a competitor, not a hard fork that drifted — a stack of commits that gets **rebased**
onto upstream, never merged, so upstream's work keeps arriving.

- **Upstream base:** `main` at `9b2c49d8` (just after tag `v1.2.5.5`)
- **The fork:** 35 commits — Klipper/Moonraker as a first-class printer, eleven features backported
  from [vmhomelab/printbuddy](https://github.com/vmhomelab/printbuddy), and the branding
- **Unchanged:** the backend, the systemd service name (`bambuddy`), the install path
  (`/opt/bambuddy`), the database, and the whole HTTP API. Anything built against Bambuddy — Home
  Assistant, scripts, a browser bookmark — keeps working
- **The name** is cosmetic and lives in one JSON file. See [Renaming it](#renaming-it)

AGPL-3.0, same as upstream. The modification notice required by section 5(a) is
[`NOTICE-modifications.md`](NOTICE-modifications.md).

> **"Printhok"** is Dutch for *print shed* — the room the printers actually live in.

---

## Why Printhok instead of Bambuddy?

One reason, and it is the whole reason: **Bambuddy speaks MQTT to Bambu Lab printers. Printhok also
speaks Moonraker to Klipper printers.** Most of the rest follows from that.

| | Bambuddy `main` | Printhok |
|---|---|---|
| Bambu Lab printers | yes | yes — untouched code path |
| Klipper / Moonraker printers | no | status, queue, dispatch, camera, control |
| Multi-material on Klipper | no | Happy Hare MMU shown as AMS units |
| Raw `.gcode` in the library | no, 3MF only | yes, with slicer metadata parsed out |
| Filament tracking without an AMS | no | external spool slot, grams booked per print |
| Prints started from Mainsail | not archived | archived, G-code fetched back from Moonraker |
| Finding Klipper hosts on the LAN | no | subnet scan on port 7125 |
| "Almost done" notification | no | at 97 %, with a camera snapshot |
| One notification provider, some printers | one or all | any subset |
| Low-stock + maintenance alerts in one place | no | one banner, `GET /alerts/summary` |
| TV / kiosk wall | partial (Cam Wall) | `/tv`, token-authenticated, no login on the screen |
| Open Filament Database lookup | no | in the Add Spool form, off by default |
| Spool queued for the next loaded slot | no | pending slot assignments |
| Notify (iOS) Live Activities | no | countdown tile on the lock screen |

**If every printer you own is a Bambu Lab, use upstream.** You get releases four times a month, a
Windows installer, Docker images, a wiki and a Discord. Printhok gets you none of that and adds a
rebase you have to run yourself.

**If you have a Klipper machine alongside the Bambus**, this is the fork that stops you from running
two dashboards.

---

## What the fork adds, in detail

### Klipper and Moonraker as a first-class printer

`MoonrakerClient` is deliberately **duck-typed against `BambuMQTTClient`** — same `state:
PrinterState`, same methods. That is the design decision the whole fork rests on: the printer
manager, the status routes, the WebSocket, the scheduler, the Home Assistant sensors and the archive
never learn that Klipper exists. The diff inside upstream's own files is roughly 60 lines; the rest
is new files, which is why the rebase stays cheap.

- Add a printer, toggle **Bambu / Klipper**, give a Moonraker URL and an optional API key
- A poll thread on `/printer/objects/query` maps Klipper states onto `PrinterState`
- Queue dispatch unpacks `Metadata/plate_N.gcode` from a sliced 3MF and uploads it to Moonraker
- The webcam configured in Moonraker is registered automatically as an external camera, so the
  printer card and Home Assistant's Generic Camera both work
- Chamber light picks the right LED by name and skips MMU and toolhead strips
- No FTPS archive sweep, no MQTT diagnostics, and no calibration or drying panels on a Klipper card

### Happy Hare MMU as AMS units

Gates are reported as AMS trays (unit = `gate // 4`, tray = `gate % 4`), the loaded gate follows
`tray_now`, and bypass stays slot 254. Assigning a spool writes `MMU_GATE_MAP`; load and unload are
`MMU_SELECT` + `MMU_LOAD` / `MMU_UNLOAD`; a queue dispatch carrying an AMS mapping sends
`MMU_TTG_MAP`, and filament is booked per tool using the grams each extruder reports in the slicer
comments. An MMU unit shows one slot per gate rather than a fixed four.

### The rest of the Klipper work

- **Raw `.gcode` in the library** once a Klipper printer exists — print time, filament, layers and
  temperatures read from the slicer comments, under the same keys the 3MF parser uses. With no
  Klipper printer configured, upstream's rejection is unchanged
- **File manager over Moonraker** — list, download, delete, and *start a file already on the printer*
- **Archive for prints started outside Printhok** — the G-code is fetched from Moonraker instead of
  filing a row with nothing in it
- **External spool slot** for any printer without an AMS, with grams booked from the archive

### Backported from Printbuddy

Eleven features that exist in [vmhomelab/printbuddy](https://github.com/vmhomelab/printbuddy) but not
upstream, reimplemented against this codebase rather than merged — a trial merge put more than a
hundred source files in conflict:

| | Feature |
|---|---|
| B1 | "Print almost done" notification at 97 %, with a camera snapshot |
| B2 | A notification provider can target a chosen subset of printers |
| B3 | Multi-word inventory search — `abs black` matches type *and* colour |
| B4 | Project progress: parts done, failed and queued under the parts bar |
| B5 | One alert banner for maintenance due and spools running low |
| B6 | Open Filament Database lookup in the Add Spool form (setting, default off) |
| B7 | Subnet scan finds Klipper hosts via Moonraker on port 7125 |
| B8 | Queue a spool for the next slot that gets loaded, on any printer |
| B9 | Notify (iOS) provider with a Live Activity countdown tile |
| B10 | `/tv` kiosk wall with its own token scope — no login on the screen |

`/tv` deserves a note. A TV tile names the file on the bed and the spool feeding the hotend, so it was
not folded into the existing `camwall` scope — a camwall token is issued on the promise that the wall
never names the part. It gets a fourth long-lived scope, `tv`, and its own feed (`GET /tv/printers`,
one request per wall refresh) returning exactly the fields a tile draws. Serial numbers, IPs and
access codes stay on the server, and `tray_now` is resolved server-side so a wall token sees the
loaded spool and nothing else. Mint one on the Camera API Tokens page.

### Branding

The display name and logo come from `static/img/brand.json`, read at runtime by the sidebar, the login
and setup screens, the stream overlay and the tab title. Edit it on the server, reload the page, done
— no rebuild. The backend, the service, the paths and the API all still say Bambuddy, and so do the
translated strings; renaming those would be rebase surface for no gain.

---

## Everything upstream does, you keep

The fork removes nothing. Print archive with thumbnails and cost, live monitoring and control, the
print queue and scheduler, AMS handling and RFID, the file library, MakerWorld integration, projects
and plates, notifications, spool inventory, SpoolBuddy, sensors, finance and invoicing, the virtual
printer and proxy mode, integrated slicing and slicer pipelines, maintenance reminders, optional
authentication, and fourteen languages.

Upstream's own pages are the honest reference for all of it:
**[maziggy/bambuddy README](https://github.com/maziggy/bambuddy#-features)** ·
**[wiki.bambuddy.cool](http://wiki.bambuddy.cool)** · **[demo.bambuddy.cool](https://demo.bambuddy.cool)**

---

## Install

> Upstream's `install.sh` hardcodes a clone from `maziggy/bambuddy`, so it cannot fetch this fork on
> its own. Clone first, then let the installer take over the existing tree. Branch `main` and branch
> `voron` in this repository point at the same commit, which is what makes the installer's default
> branch work.

### Native (Linux, the tested path)

```bash
sudo mkdir -p /opt/bambuddy
sudo git clone https://github.com/DeWarePeb/bambuddy.git /opt/bambuddy
sudo bash /opt/bambuddy/install/install.sh --path /opt/bambuddy
```

The installer finds the existing `.git`, fetches `origin` (this fork), and continues as it normally
does: service user, virtualenv, frontend build, systemd unit, port 8000.

**Requirements:** Python 3.10+ (3.11/3.12 recommended), Node for the frontend build, and at least one
of — a Bambu Lab printer with Developer Mode enabled, or a Klipper printer reachable at
`http://<host>:7125`. On a 2 GB machine the frontend build needs a heap cap or Node runs out of
memory:

```bash
NODE_OPTIONS=--max-old-space-size=1400 npm run build
```

### Docker

There is **no published Printhok image**. `docker-compose.yml` still points at
`ghcr.io/maziggy/bambuddy:latest`, which is upstream, not this fork. Build from source:

```bash
git clone https://github.com/DeWarePeb/bambuddy.git printhok
cd printhok
docker compose up -d --build
```

### Windows installer

Upstream only. The signed `.exe` on maziggy's releases page installs Bambuddy, not Printhok.

---

## Update

`install/update.sh` is branch- and remote-agnostic, so upstream's updater works unmodified — it
follows whichever `origin` and branch the tree is on, which here is this fork:

```bash
sudo /opt/bambuddy/install/update.sh
```

It stops the service, snapshots the database through the backup API, resets to `origin/<branch>`,
installs Python dependencies, rebuilds the frontend, restarts, and rolls back if any step fails.

Take a backup before a big jump anyway: **Settings → Backup → Create Backup**. See
[`UPDATING.md`](UPDATING.md) for the Docker and no-`.git` cases.

---

## Staying current with upstream

This is the maintenance promise, and the only thing that keeps a fork like this alive: **rebase on
every upstream release, never merge.** Printbuddy lost the ability to follow upstream by skipping
three releases in a row; this repository exists in order not to repeat that.

```bash
git fetch upstream --tags
git rebase --onto <new-upstream-ref> 9b2c49d8 voron
git branch -f main voron
```

Expect conflicts in `PrintersPage.tsx`, `print_scheduler.py` and `routes/printers.py`, and a
guaranteed one in this README. Budget one to two hours per upstream release.

The full runbook — conflict hotspots, what to do when upstream ships a feature the fork already has,
the test suite, deploy and rollback — is
**[`docs/printhok/upstream-sync.md`](docs/printhok/upstream-sync.md)**.

Every added feature, why it exists and which files carry it:
**[`docs/printhok/features.md`](docs/printhok/features.md)**.

---

## Renaming it

`frontend/public/img/brand.json`, built to `static/img/brand.json`:

```json
{
  "name": "Printhok",
  "logoDark": "/img/printhok_logo_dark.svg",
  "logoLight": "/img/printhok_logo_light.svg"
}
```

Change the name, drop your own two SVGs next to it, reload. It is served from `/img/` specifically so
it bypasses the SPA catch-all route.

---

## Supported printers

| Series | Models |
|--------|--------|
| X1 | X1, X1 Carbon, X1E |
| X2 | X2D |
| H2 | H2D, H2D Pro, H2C, H2S |
| P1 | P1P, P1S |
| P2 | P2S |
| A1 | A1, A1 Mini |
| A2 | A2L |
| **Klipper** | **any printer running Moonraker; Happy Hare MMU supported. Developed against a Voron 2.4** |

---

## Support, issues, contributing

**No support is promised.** This fork is maintained for one workshop, and published because AGPL-3.0
requires the source and because the Klipper work may be useful to someone else.

- **Bug in Bambuddy itself?** → [upstream issues](https://github.com/maziggy/bambuddy/issues) and the
  [Discord](https://discord.gg/aFS3ZfScHM). Please do not send them fork bugs
- **Bug in the Klipper path, the ported features, or the branding?** → issues here. Include the commit
  you are on: `git -C /opt/bambuddy rev-parse --short HEAD`
- **A feature that belongs upstream?** Send it upstream. Anything here that could live in Bambuddy is
  better off there — a smaller patch series is a fork that survives

Pull requests are welcome for the fork-only surface. Keep the rule: one commit per subject, rebased,
never a merge from upstream. See [`CONTRIBUTING.md`](CONTRIBUTING.md).

---

## License and credits

AGPL-3.0 — see [`LICENSE`](LICENSE). Upstream copyright and history are kept intact; the required
modification notice is [`NOTICE-modifications.md`](NOTICE-modifications.md).

- **[maziggy/bambuddy](https://github.com/maziggy/bambuddy)** — all of it. Printhok is a patch series
  on someone else's very good application. If you use this fork, go
  [sponsor maziggy](https://github.com/sponsors/maziggy)
- **[vmhomelab/printbuddy](https://github.com/vmhomelab/printbuddy)** — source of the eleven
  backported features, same AGPL-3.0 license
- **[SpoolEase](https://github.com/yanshay/SpoolEase)** by yanshay — upstream's inspiration for
  NFC-based spool tracking
- Bambu Lab, the Klipper and Moonraker projects, and Happy Hare
