# Testing Printhok on your Klipper printer

This is for someone who saw the Discord post and is deciding whether to spend an hour on it. It
tells you what to do, in what order, what is likely to break, and how to get back off it.

Printhok is a fork of [Bambuddy](https://github.com/maziggy/bambuddy) that drives a Klipper printer
over Moonraker as a normal printer, next to Bambu machines. Read
[`../../README.md`](../../README.md) first if you have not — especially **What has actually been
run**, which is the honest scope.

---

## Before you start

**It does not touch your Klipper config.** Printhok talks to Moonraker over its HTTP API and
WebSocket, the same interface Mainsail and Fluidd use. It uploads G-code, starts and stops prints,
sets temperatures, and reads status. It does not write `printer.cfg`, does not install a Klipper
plugin, and does not need anything on the printer host.

**Do not point it at a printer that is mid-print for the first run.** Not because it is known to
break one — because you are testing unfamiliar software against a machine with a hot nozzle, and the
first thing you want to learn is whether the card reads correctly, which an idle printer answers just
as well.

**Run it somewhere disposable.** A container, a VM, a spare Pi. It keeps its state in one SQLite
file, so "disposable" is genuinely disposable.

---

## Install

Full instructions are in the [README](../../README.md#install). The short version:

```bash
sudo mkdir -p /opt/bambuddy
sudo git clone https://github.com/DeWarePeb/printhok.git /opt/bambuddy
sudo bash /opt/bambuddy/install/install.sh --path /opt/bambuddy
```

Then open `http://<that-host>:8000`.

The clone-first step is not optional: upstream's installer hardcodes a clone from `maziggy/bambuddy`,
so pointing it at this fork does not work. Cloning first makes the installer adopt the tree it finds.

---

## The path through it, in order

Each step is worth reporting on its own. If step 2 fails there is no point doing step 5, and
"step 2 failed like *this*" is the most useful message you can send.

**1. Add the printer.** Settings → Printers → Add, toggle **Klipper**, give the Moonraker URL. Use
whatever URL your browser uses for Mainsail or Fluidd — `http://voron.local` is fine, so is
`http://192.168.1.40:7125`. If Moonraker has authorization on, paste an API key.

> Expected: it probes Moonraker, fills in the name, and registers your configured webcam as a
> camera. A synthetic serial `KLIPPER-<host>` appears — that is normal, Bambuddy keys everything on
> a serial and Klipper has none.

**2. Read the card.** State, nozzle, bed, progress, layer, time left, the webcam.

> The most likely thing to be wrong for you and right for me. Temperature sensors especially: mine
> are Cartographer and a couple of MCU sensors, yours are not.

**3. Chamber temperature.** Edit the printer → chamber sensor. It offers every temperature object
your printer reports; pick your enclosure sensor.

> Known: if the dropdown is empty of anything sensible, you have no chamber sensor and that is
> correct behaviour, not a bug. Worth reporting if a sensor you *do* have is missing from the list.

**4. Start a file already on the printer.** File manager → pick something in your `gcodes` → start.

> Lowest-risk print test: nothing is uploaded, it just calls `print/start` on a file you already
> trust.

**5. Queue a file from Printhok.** Drop an Orca-sliced `.gcode` or `.gcode.3mf` in the library, queue
it on the Klipper printer, start it.

> Expected: unpack, upload to Moonraker's `gcodes`, start, card goes RUNNING, and when it finishes
> there is an archive row with filament and time. This is the path that matters most.

**6. Let it finish, then check the archive.** Filament grams, duration, thumbnail, cost.

**7. Start a print from Mainsail instead**, and check Printhok files it too.

**8. If you have a Happy Hare MMU** — this is the ask. Do the gates show up as AMS trays? Does
assigning a spool to a gate write the gate map? Does a multi-material job map tools to gates
correctly? This is the least-tested surface in the fork and the one where I would most expect to be
wrong.

---

## Known rough edges

- **Empty panels on a Klipper card.** Calibration, drying and some AMS panels are Bambu-only and
  currently render empty rather than hiding. Cosmetic, known, not worth a report.
- **The other twelve locales.** Klipper-specific strings exist in English and Dutch; the other twelve
  fall back to English mid-page.
- **The iOS Notify provider** has never been tested against the real app.
- **Chamber-temperature reporting on a Klipper printer** is newer than the rest — see the register.
- **DNS rebinding** is not guarded on the Moonraker URL. Only relevant if you would let someone
  untrusted type a printer address into your instance.

The full per-feature register, including what each commit does and what is open, is
[`features.md`](features.md).

---

## Getting back off it

Nothing here is one-way.

- **Just stop it:** `sudo systemctl stop bambuddy && sudo systemctl disable bambuddy`.
- **Remove it:** stop the service, then delete `/opt/bambuddy` and the service user. It is a normal
  systemd install in one directory.
- **Switch to upstream Bambuddy instead:** the fork's migrations are additive only — eight columns
  across four existing tables and three tables of its own. Nothing is dropped, nothing changes type,
  and every added column is nullable or has a default, so upstream's code ignores the lot. Point
  upstream's installer at a fresh directory, copy the SQLite file over, and your Bambu printers,
  archive and spools come with you. Klipper rows become printers upstream cannot drive — delete
  them. Take a copy of the database first anyway; it costs nothing.
- **Your printer is unchanged either way.** Nothing was installed on it.

---

## Reporting

[Issues on the fork](https://github.com/DeWarePeb/printhok/issues). That is the tracker — there is
no Printhok Discord, and there will not be one until enough people are running this to need it. If
you found this through a thread somewhere, replying there is fine too, but a thread scrolls and an
issue does not.

Include:

- the commit: `git -C /opt/bambuddy rev-parse --short HEAD`
- printer, and Klipper front end (Mainsail / Fluidd / neither)
- the Moonraker URL form you entered, and whether authorization is on
- `journalctl -u bambuddy -f` around the moment it went wrong
- a screenshot, if it is something you can see

**Please do not send fork bugs to [upstream](https://github.com/maziggy/bambuddy/issues) or the
Bambuddy Discord.** maziggy did not write this and should not have to triage it. If you can
reproduce the same problem on stock Bambuddy with a Bambu printer, then it is upstream's and they
would want to know.

No support is promised — this is maintained for one workshop. But a good report on the Klipper path
will get read.
