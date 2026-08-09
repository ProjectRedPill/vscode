# sweep

Passive multi-band device discovery and counter-surveillance sweep.

Listens to Bluetooth LE, Bluetooth Classic, Wi-Fi, sub-GHz ISM, broadband RF and
infrared; fuses what it hears into device identities that survive MAC rotation
and span radios; and tells you as much as each device is willing to reveal about
itself. Then it helps you physically find the ones you care about.

**Receive-only.** It never connects, pairs, transmits, deauthenticates or jams.
Everything it reports comes from what devices already broadcast in the clear.

**[QUICKSTART.md](QUICKSTART.md)** — install, launch, and get it onto your phone.
[HARDWARE.md](HARDWARE.md) — what to plug in, and what an iPhone can and cannot
do. [RESEARCH.md](RESEARCH.md) — the survey of the ten projects this draws from.

---

## Install

The core has **no dependencies** — it runs on the Python standard library, on
purpose, because a tool you may need on an unfamiliar machine should not require
a package index.

```bash
cd tscm
pip install -e .              # core only
pip install -e '.[all]'       # + bleak (better BLE) + pyserial (probes)
```

Then check what your hardware can actually see:

```bash
sweep doctor
```

This is the first thing to run and the most important output in the tool. It
tells you which bands you can sense, which you cannot, and exactly what to
install or plug in to close each gap — *before* you draw conclusions from a
sweep.

## Use

```bash
sweep serve                               # web UI (recommended) — Chrome, Safari, phone
sweep scan                                # terminal dashboard
sweep scan --sensors all                  # include SDR / IR / RF probes
sweep find "AirTag"                       # walk-around ranging on one device
sweep sweep --duration 300 --out room.md  # timed sweep, writes a full report
sweep decode 0201061eff4c001219...        # decode a captured advert, no hardware
```

## The web UI

```bash
sweep serve --open                        # this machine only
sweep serve --sensors all --host 0.0.0.0  # reachable from your phone
```

Binding to the network prints your LAN URL **and a QR code** you can point a
phone camera at, so the access token never has to be typed by hand. The QR
encoder is built in — no dependency, and it works with no internet.

Three views: a device list sorted by risk, a detail panel with every decoded
fact, and a full-screen **finder** — one large number that rises as you get
closer, with warmer/colder and a signal trace.

Built to be used from a phone while the radios stay on a laptop or a Raspberry
Pi, because that is the only arrangement iOS allows (see
[HARDWARE.md](HARDWARE.md)). On iPhone, open the URL in Safari and use
**Share ▸ Add to Home Screen** for a full-screen app.

- No framework, no build step, no CDN, no web fonts — three static files, so it
  works with no internet at all.
- Live updates over Server-Sent Events; reconnects by itself when a phone wakes.
- Dark and light, following the system theme with a manual override.
- Knows whether the browser is on the sensing machine or looking at it from
  somewhere else, and says which. On the host it tells you these are the devices
  around *you*; from a phone it says plainly that the phone is a screen, and
  lists the things iOS *can* usefully do.
- A **Coverage** tab showing every band: what it detects, what you are blind to
  without it, and the exact command or purchase that would switch it on.
- Verified in Chromium at desktop and iPhone viewports by the tests in
  `tests/test_web_ui.py`.

**`--host 0.0.0.0` exposes your sweep to the network.** It mints an access token
and prints it in the URL, but traffic is plain HTTP — use it on a network you
trust. The default binding is loopback only.

### Keys in the terminal view

| Key | Action |
|---|---|
| `↑` `↓` / `j` `k` | select a device |
| `enter` / `d` | every decoded fact about it |
| `f` | **find it** — the ranging view |
| `m` | **mark a new location** (see below — this is the important one) |
| `b` | freeze the current device set as the baseline |
| `y` / `n` / `s` | mark as mine / known / suspect |
| `a` | show all devices including your own |
| `q` | quit |

### Marking locations is what makes it work

A tracker on your neighbour's shelf and a tracker in your bag look identical
from one spot. They stop looking identical the moment you move: theirs drops
out, yours does not.

So when you change location, press `m`. Follow-detection rules ask *"has this
been present across several locations?"* rather than *"is this nearby?"*, and
without epochs they cannot fire at all. This is AirGuard's insight, generalised
to every band.

```
sweep scan          →  press m when you leave the café
                    →  press m again at the office
                    →  anything present in all three locations is flagged
```

### Teaching it your own devices

```bash
sweep trust AA:BB:CC:DD:EE:FF mine --label "my phone"
sweep trust 11:22:33:44:55:66 known --label "neighbour's TV"
sweep trusted                       # list what you have recorded
```

Devices you mark as `mine` or `known` are still tracked and still generate
findings for the record — the findings are just stripped of urgency, so they
stop drowning out the ones that matter.

---

## What it detects

### Bluetooth LE

Every advertisement is decoded, not just listed:

- **Apple Continuity** — Find My separation state and battery, AirPods model
  with per-bud and case charge, Nearby Info (screen on/off, Wi-Fi state,
  activity level, call in progress), Handoff, tethering, AirDrop.
- **Google Fast Pair** — exact model IDs; Find My Device network separation.
- **Microsoft CDP** — device form factor (Windows laptop, Xbox, Surface Hub…),
  which is one of the few places a device states what it is in plain text.
- **Samsung SmartThings Find** — SmartTag separation state. Worth its own
  mention: Samsung tags are *not* surfaced by iOS or stock Android tracker
  alerts, so they are the ones most likely to be used and least likely to be
  caught by a phone.
- **Tile / Chipolo** — including the fact that Tile IDs are static and therefore
  trivially followable, which cuts both ways.
- **iBeacon, Eddystone, AltBeacon** — including TLM telemetry: battery, and
  uptime, which tells you how long a beacon has been installed.
- **GATT services, GAP appearance, Class of Device** — including the *Capturing*
  service bit, which is a device declaring itself an audio or video source.
- **Address type** — public, static random, resolvable-private, non-resolvable,
  or locally-administered, with the honest caveat that this cannot be determined
  from the address alone; the Bluetooth stack's own answer is used when
  available.

### Bluetooth Classic

Where the wireless microphones, body cameras, dashcams and car kits live —
frequently BR/EDR-only and completely invisible to a BLE-only scanner. Class of
Device, service profiles, LMP version and chipset vendor (behind an opt-in,
since that one opens a connection).

### Wi-Fi

Access points with BSSID, SSID, channel, security, hidden-SSID and open-network
flags. Most hidden cameras are Wi-Fi cameras, and the cheap ones fall back to
hosting their own SSID when they cannot reach a network — so this band catches
the single most common covert-camera failure mode.

### Sub-GHz ISM (315 / 433 / 868 / 915 MHz)

Via `rtl_433` and an RTL-SDR dongle: ~250 protocols including door and window
contacts, PIR motion sensors, TPMS (which move with a vehicle, and are therefore
useful for spotting a car that keeps reappearing), key fobs, and the cheap FSK
bugs that live on 433 MHz.

### Broadband RF

Two sensors for the devices that have no digital identity at all:

- **Spectrum sweep** (`rtl_power` / `hackrf_sweep`) — reports carriers that rise
  above a rolling noise floor, with the band plan named: 1.2, 2.4 and 5.8 GHz
  analogue video are the classic wireless-camera technologies and carry no MAC,
  no name and nothing to scan for.
- **Power probe** (AD8317 log detector, see `firmware/rf_probe/`) — no tuning, no
  demodulation, responds to anything from ~1 MHz to ~10 GHz. Cannot identify a
  device, but cannot be defeated by encryption or by a protocol nobody has
  decoded either. Sweep it over surfaces and watch the level peak.

### Infrared

- **Coded IR** — remote-control protocols via LIRC or the probe firmware.
- **IR flood** — steady un-modulated 850/940 nm emission. This is a night-vision
  illuminator lighting a dark room, invisible to the eye, and one of the most
  reliable hidden-camera indicators there is. A 38 kHz demodulating receiver
  (what LIRC and every Flipper-class device uses) is **deaf to this by design**,
  so `firmware/ir_probe/` carries a bare photodiode as well.

---

## Optional hardware

Everything below is optional; `sweep` degrades to fewer bands rather than
failing, and always says which.

| Band | Hardware | Software |
|---|---|---|
| BLE / BT Classic | any built-in adapter | `pip install bleak`, or bluez |
| Wi-Fi | any built-in adapter | NetworkManager (`nmcli`) or `iw` |
| Sub-GHz ISM | RTL-SDR dongle (~£25) | `rtl_433` |
| Spectrum | RTL-SDR (24 MHz–1.7 GHz) or HackRF (1 MHz–6 GHz) | `rtl_power` / `hackrf_sweep` |
| Broadband RF | ESP32 + AD8317 module (~£15) | [`firmware/rf_probe/`](firmware/rf_probe/) |
| Infrared | ESP32 + TSOP38238 **and** BPW34 photodiode (~£5) | [`firmware/ir_probe/`](firmware/ir_probe/) |

[HARDWARE.md](HARDWARE.md) has the full buying guide: what each adapter actually
unlocks, what `sweep` drives today versus what is roadmap, and what an iPhone
can and cannot do (short version: it cannot host any of this — no `libusb`, no
raw HCI, no monitor mode — so it is the screen, not the sensor).

Both probes speak one newline protocol over serial, so anything that can print a
line can feed them — including a file, which is how you replay a capture:

```bash
sweep sweep --sensors ir --ir-fifo captured.log --duration 60
```

---

## Reading the results honestly

This matters more than any feature, so it is worth being blunt about it.

**A clean report is not an all-clear.** A powered-off camera, a wired device, an
SD-card recorder with no radio, a device that wakes for four seconds an hour, or
anything using a protocol none of your active sensors decode will all produce
exactly the same output as an empty room. Every report opens with a coverage
table naming what was *not* sensed, for this reason.

**Signature matches say "looks like", not "is".** Names and MAC addresses can be
changed by anyone who cares to. A match is a reason to look; an absence of
matches is not a reason to relax.

**Distances are order-of-magnitude.** They come from a log-distance path-loss
model. Indoors, expect a factor of two or worse — walls, bodies and metal all
attenuate. Use them for ordering devices, not for measuring.

**Severity is calibrated deliberately.** A separated tracker is *medium*: it is
also what any tag whose owner stepped away looks like. Only one that follows you
across several marked locations is *critical*. Rules that cry wolf get ignored,
and an ignored tool detects nothing.

---

## Scope and lawful use

Built for sweeping your own spaces and your own devices, or spaces you have
permission to sweep — hotel rooms, short-term rentals, your home, your car,
your office.

It is passive by construction: it decodes what devices already broadcast to
anyone in range, and it has no ability to transmit, connect, pair, deauthenticate
or jam. That is a design decision, not an oversight — those capabilities would
make the tool worse at its job and illegal in more places.

Using it to track a person, to inventory someone else's private space, or to
locate a device you do not have a right to locate is misuse. In many
jurisdictions it is also a crime. Radio monitoring, interception and
counter-surveillance law varies enormously by country; know yours.

---

## Development

```bash
pip install -e '.[dev]'
python -m pytest tests/ -q
```

129 tests, no radio hardware required. The decoding layer (`intel/`) is pure
functions over bytes and is fully covered; `tests/test_engine.py` drives the
real engine end to end through a scripted fake sensor; `tests/test_web.py`
exercises the HTTP and SSE surface over real TCP; `tests/test_web_ui.py` drives
the UI in Chromium at phone and desktop viewports and is skipped automatically
when Playwright is not installed.

### Adding a band

1. Subclass `Sensor` in `sensors/`, implement `available()` and `run()`.
2. Add one line to `REGISTRY` in `sensors/__init__.py`.

Nothing else changes. Sensors emit `Observation`s and never touch device state;
fusion, classification, rules, storage and UI all pick it up automatically.

### Layout

```
sweep/
  core/      models · fusion (identity) · rssi (Kalman + ranging) · store · engine
  sensors/   ble · btclassic · wifi · sdr · ir · rfpower · serialbridge
  intel/     oui · sig (SIG assigned numbers) · ble (advert decode) ·
             parsers/ (apple, google, microsoft, samsung, tile, beacons) ·
             signatures · classify
  threat/    rules (location epochs, follow detection, camera/IR/RF rules)
  ui/        tui (list · detail · find) · render · report
  web/       server (HTTP + SSE) · static/ (index.html · app.css · app.js)
  core/capability.py   one catalogue of bands, blind spots and upgrades,
                       shared by `doctor`, the report and the Coverage tab
  core/qr.py           dependency-free QR encoder for the phone join URL
firmware/    ir_probe · rf_probe   (reference Arduino/ESP32 sketches)
```

MIT licensed.
