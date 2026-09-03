# Quickstart

> **On Windows?** Read **[WINDOWS.md](WINDOWS.md)** instead. PowerShell needs
> different syntax (no `&&`), the firewall needs a rule, and Bluetooth Classic
> has a real platform limitation worth knowing about before you rely on it.

## 1. Install

```bash
cd tscm
pip install -e '.[all]'
```

`.[all]` pulls in `bleak` (much better Bluetooth) and `pyserial` (for the
probes). The core works without either — `pip install -e .` is enough if you
want zero dependencies.

## 2. See what your machine can detect

```bash
sweep doctor
```

Run this first, every time, on any new machine. It prints every band, whether
you can sense it, what you are blind to without it, and the exact command or
purchase that would fix it. It ends with the single best next step.

Nothing else in this tool means much until you have read that output — "no
findings" from a sweep that could only hear Bluetooth is not the same claim as
"no findings" from one that could hear everything.

Then, once, run:

```bash
sweep hwscan
```

`doctor` tells you which radios work. `hwscan` inspects what those radios are
*capable* of and flags what sweep is not yet using — typically Bluetooth 5
Extended Advertising, LE Coded PHY, and Wi-Fi monitor mode. Those are the
upgrades that cost nothing.

## 3. Launch it

### Just on this machine

```bash
sweep serve --open
```

Opens `http://localhost:8787/` in your browser.

### On your phone (the useful way)

```bash
sweep serve --host 0.0.0.0
```

It prints your LAN URL **and a QR code**. Point your phone's camera at the
terminal, tap the notification, done — no typing the access token by hand.

```
  http://192.168.1.42:8787/?t=3K4KK6kYAlWVndD3B7u7FA

  Scan this with your phone's camera:

    ▄▄▄▄▄▄▄  ▄ ▄▄▄  ▄▄▄▄▄▄▄
    █ ▄▄▄ █ ▀█▄ ▄▀▀ █ ▄▄▄ █
    █ ███ █ █▄ ▀█▄█ █ ███ █
    ...
```

On iPhone, once it loads: **Share ▸ Add to Home Screen**. You get a full-screen
app with no browser bars, and it reconnects by itself when the phone wakes.

> `--host 0.0.0.0` puts your device inventory on the network over plain HTTP.
> The token in the URL is the only thing protecting it. Use a network you trust.

### With every sensor, including add-on hardware

```bash
sweep serve --host 0.0.0.0 --sensors all \
    --ir-port /dev/ttyUSB0 \
    --rf-port /dev/ttyUSB1
```

### Prefer the terminal?

```bash
sweep scan                                # live dashboard
sweep find "AirTag"                       # ranging view for one device
sweep sweep --duration 300 --out room.md  # timed sweep, writes a report
```

## 4. Use it

1. **Mark your locations.** Press `m` (or tap **Mark location**) every time you
   physically move. This is not optional decoration — the follow-detection rules
   ask *"has this been present across several locations?"*, and without location
   marks they cannot fire at all. A tracker in your bag and a tracker on your
   neighbour's shelf look identical from one spot.

2. **Tag your own devices.** Open a device, tap **Mine**. It stays tracked but
   stops shouting, so real alerts are not buried under your own headphones.

3. **Check the Coverage tab.** It shows what you are sensing, what you are blind
   to, and what each upgrade would unlock. It also tells you which machine is
   doing the sensing — the one you are using, or a remote one you are viewing.

4. **Use the finder.** Tap a device, then **Find it**. Walk a few metres, stand
   still for about ten seconds, read the arrow. Trust the trend, not any single
   reading.

## Where the detecting happens

**On your PC, the detecting happens on your PC.** Run `sweep serve` on your
laptop, open it in that laptop's browser, and the devices you see are the ones
physically around that laptop. There is no server anywhere else and nothing
leaves the machine. The UI says so directly when the browser is on the same
machine as the radios.

"Host" only becomes a separate thing when you *choose* to split it:

```
    sweep runs here                       you look at it here
    ───────────────                       ──────────────────
    your laptop                    →      the same laptop's browser
                                          (the normal case — you are the sensor)

    a laptop or Raspberry Pi       →      your iPhone over Wi-Fi
    in the room being swept               (the phone is a remote screen)
```

The second row is useful for ergonomics — sweeping a room means moving a probe
around, which is easier when the computer can stay on a desk — and it is the
*only* option on iOS, because an iPhone cannot run the sensors at all.

That is a platform restriction, not a shortcut: iOS gives apps no `libusb` (an
SDR in the USB-C port is inert), no Wi-Fi scanning API, no monitor mode, and
rotating per-app UUIDs instead of BLE MAC addresses. A native iOS app could
cover about 40% of one band out of six, with no stable device identity.
[HARDWARE.md](HARDWARE.md) has the full API-by-API breakdown, including why
Android is genuinely different.

See [HARDWARE.md](HARDWARE.md) for what to plug into the host.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `sweep doctor` says nothing is usable | No Bluetooth or Wi-Fi tooling. Linux: `apt install bluez network-manager`. Then `pip install bleak`. |
| Phone cannot reach the URL | You are on loopback. Re-run with `--host 0.0.0.0`, and check both devices are on the same network (phone Wi-Fi off / cellular on is the usual cause). |
| `cannot bind 0.0.0.0:8787` | Something else has the port. Add `--port 9000`. |
| The QR code will not scan | Widen the terminal, or increase the font size. `--no-qr` prints just the URL. |
| Everything reads `<1 m` | Expected. Distance is a path-loss estimate, routinely wrong by 2× indoors. Use the finder's warmer/colder, not the metres. |
| BLE devices have no names or vendors | You are on the `bluetoothctl` fallback. `pip install bleak` for full advertisement payloads. |
| Nothing found and you expected something | Read the Coverage tab. A quiet sweep on two bands is not a quiet room. |
| You are on Windows | See [WINDOWS.md](WINDOWS.md) — PowerShell syntax, firewall, and the Bluetooth Classic caveat. |
