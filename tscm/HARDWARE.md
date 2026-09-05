# Hardware: what to buy, and what it actually buys you

Two things to settle before any shopping list makes sense.

**Your laptop already covers three bands.** Bluetooth LE, Bluetooth Classic and
Wi-Fi need no adapter at all. That is roughly 80% of what turns up in a real
sweep, because most surveillance-capable consumer hardware is a Wi-Fi camera or
a BLE tracker. Buy nothing, run `sweep doctor`, and see what you get first.

**Your iPhone cannot host the rest of it, and no adapter changes that.** This is
a platform restriction, not a gap in the tool:

- iOS has no `libusb`, so an RTL-SDR or HackRF plugged into an iPhone's USB-C
  port is inert. (An unofficial `librtlsdr` port using `USBDriverKit` does run
  SDRs directly on **M-series iPads** without a jailbreak — iPhones use A-series
  chips and are not covered.)
- iOS gives apps no raw Bluetooth HCI access, and hides peripheral MAC addresses
  behind per-app rotating UUIDs. A BLE scanner app on iOS cannot see the
  addresses that identity fusion depends on.
- iOS has no Wi-Fi monitor mode and no raw 802.11 frames.

So the working architecture is **laptop or Raspberry Pi holds the radios, phone
is the screen**. That is what `sweep serve` is for:

```bash
sweep serve --sensors all --host 0.0.0.0        # on the laptop or Pi
# open the printed URL in Safari, then Share ▸ Add to Home Screen
```

The phone becomes a full-screen controller you can carry around the room while
the hardware stays on a desk — which is also better ergonomics for a sweep than
walking around holding a laptop.

---

## Laptop adapters

Prices are rough street prices as of 2026 and vary by region. The last column is
the one that matters: **supported today** means `sweep` drives it now, not that
it could in principle.

### Buy these first

| Hardware | ~Cost | Bands it unlocks | Supported today |
|---|---:|---|---|
| **RTL-SDR Blog V4** (R828D + RTL2832U, 1 PPM TCXO) | $40 | 500 kHz – 1.75 GHz. Feeds `rtl_433` for ~250 decoded sub-GHz protocols, and `rtl_power` for spectrum sweeps | ✅ `--sensors rtl433,spectrum` |
| **ESP32 dev board** (any WROOM-32/S3) | $8 | Host for both probes below | ✅ `firmware/` |
| **AD8317 or AD8318 log-detector module** + short whip | $12 | Broadband RF power, ~1 MHz – 10 GHz. Finds analogue video transmitters, encrypted links and anything with no decoder | ✅ `--sensors rf-power` |
| **TSOP38238** (38 kHz IR receiver) + **BPW34** photodiode + 1 MΩ resistor | $5 | Coded infrared *and* un-modulated IR flood — the night-vision-illuminator detection | ✅ `--sensors ir` |

That is about **$65** and it takes you from three bands to six. The AD8317 and
the BPW34 are the two parts that see things nothing else on the list can.

### Worth it if the budget stretches

| Hardware | ~Cost | Why | Supported today |
|---|---:|---|---|
| **HackRF One** (1 MHz – 6 GHz, half-duplex) | $150–320 | The RTL-SDR stops at 1.75 GHz, which leaves **2.4 GHz and 5.8 GHz analogue video** — the classic covert-camera bands — completely unswept. `hackrf_sweep` reaches them | ✅ `--sensors spectrum` |
| **USB-C ↔ USB-A hub with power passthrough** | $25 | An RTL-SDR draws real current and runs warm; a bus-powered hub keeps a thin laptop stable over a long sweep | n/a |
| **Thermal camera** (FLIR One, Topdon TC001, InfiRay P2 Pro) | $200–300 | A powered camera dissipates heat. Thermal finds devices behind plastic that emit no RF at all — including SD-card recorders with no radio, which are otherwise invisible to everything here | ❌ vendor app only |

### Supported by their own tools, not yet by `sweep`

Listing these honestly rather than implying integration that does not exist.
All are on the roadmap in `RESEARCH.md`.

| Hardware | ~Cost | What it adds | Status |
|---|---:|---|---|
| **TI CC1352P / CC26x2** (LAUNCHXL-CC26X2R1, or a Sonoff ZBDongle-P) | $20–40 | Runs **Sniffle**, the best open BLE 5 sniffer. Sees connection traffic, extended advertising and channel-hopping that host stacks hide | ❌ roadmap |
| **nRF52840 dongle** | $10–25 | Runs Nordic's **nRF Sniffer** into Wireshark. Different firmware and a different tool from Sniffle above — the two are often confused | ❌ roadmap |
| **Ubertooth One** | $120 | BLE sniffing plus a 2.4 GHz spectrum analyser mode (`ubertooth-specan`) | ❌ roadmap |
| **Alfa AWUS036ACM** (MT7612U) or **AWUS036AXML** (MT7921AU) | $35–60 | Wi-Fi **monitor mode**. `sweep`'s Wi-Fi sensor uses ordinary OS scan APIs, so it sees access points but not *clients*. Monitor mode sees the client devices too — and a hidden camera is a client | ❌ roadmap |
| **Airspy Mini / R2** | $100–200 | Much better dynamic range than an RTL-SDR. Needs `soapy_power` rather than `rtl_power` | ❌ roadmap |
| **Flipper Zero** | $170 | Excellent sub-GHz and IR receive libraries; its USB serial CLI could feed the probe protocol | ❌ roadmap |

### Wiring the probes

Both probes speak one newline protocol (`sweep/sensors/serialbridge.py`), so a
twenty-line sketch is enough. Reference firmware is in `firmware/`.

```
IR probe (ESP32)                      RF probe (ESP32)
  TSOP38238 OUT → GPIO 15               AD8317 VOUT → GPIO 34
  BPW34      → GPIO 34 (ADC),           AD8317 VCC  → 5V
              1 MΩ to GND,              antenna     → 2–5 cm whip
              cathode to 3V3                          (short on purpose:
                                                       you want near-field)
```

Fit **both** IR sensors. A TSOP-only probe demodulates at 38 kHz and is deaf to
a night-vision illuminator by design — it will never find a camera, which is the
main reason to have an IR probe at all.

```bash
sweep serve --sensors all --ir-port /dev/ttyUSB0 --rf-port /dev/ttyUSB1
```

### A Raspberry Pi is a better host than a laptop

A Pi 4 or 5 with the RTL-SDR and both probes attached can sit in a room running
`sweep serve --host 0.0.0.0` for days, while you check it from your phone. It
also solves the ergonomics problem: sweeping a room means moving the *probe*,
not the computer.

---

## iPhone

### What genuinely works

| Approach | Cost | What it gets you |
|---|---:|---|
| **`sweep serve` in Safari** | free | The whole tool. Add to Home Screen for a full-screen app with no browser chrome. This is the intended path |
| **Front camera as an IR viewer** | free | Front-facing cameras have a much weaker IR-cut filter than rear ones. Darken the room, open the selfie camera, and sweep it around: a night-vision illuminator shows as a faint purple-white glow. Genuinely effective, costs nothing, works on every iPhone |
| **iOS's own tracker alerts** | free | iOS surfaces "Unknown Accessory Detected" for Find My devices, and Apple's *Tracker Detect* app scans for them on demand. Worth leaving on — but note it covers Apple's network well, Google's partially, and **Samsung SmartTags not at all**, which is exactly the gap `sweep` fills |
| **USB-C thermal camera** (iPhone 15 and later) | $200–300 | **Topdon TC002C** (256×192, 40 mK, iOS USB-C only) or an **InfiRay P2 Pro** in its iOS variant — check the listing, some P2 Pro SKUs are Android-only. Runs in the vendor's app, not in `sweep`. Finds warm devices behind plastic, including ones with no radio at all |
| **RTL-SDR over the network** | $40 + a Pi | Run `rtl_tcp` on a Raspberry Pi and connect an iOS SDR client over Wi-Fi. The dongle is on the Pi, not the phone. Separate from `sweep`, same hardware |

### "But why can't the phone just do the detecting?"

Fair question, and it deserves a real answer rather than "iOS won't let you".
Here is what a **native iOS app** could and could not do, API by API. This is
about what Apple exposes to third-party code, not about the hardware — the
radios in an iPhone are perfectly capable.

| Capability | Native iOS app | Why |
|---|---|---|
| Scan BLE advertisements | ✅ **Yes** | CoreBluetooth gives third-party apps local name, advertised service UUIDs, service data, manufacturer data, TX power and RSSI. Most of `sweep`'s decoders would work on that. AirGuard's iOS app does exactly this. |
| Read BLE **MAC addresses** | ❌ No | You get a per-app `NSUUID` instead. It differs between apps and changes when the peripheral rotates its address. |
| Link a device across MAC rotation | ❌ No | Follows from the above. This is the core of `sweep`'s identity fusion — without it, one tracker following you for three hours is twelve unrelated strangers. |
| Scan continuously in the background | ⚠️ Crippled | Background scanning requires you to name specific service UUIDs up front and returns reduced advertisement data. A sweep that only runs while you stare at the screen is not a sweep. |
| Scan Wi-Fi | ❌ No | There is no public Wi-Fi scanning API. `NEHotspotHelper` needs a special Apple entitlement that is effectively unobtainable. This kills the most important band — Wi-Fi cameras. |
| Wi-Fi monitor mode / raw 802.11 | ❌ No | Not exposed at any privilege level. |
| Talk to a USB SDR | ❌ No | No `libusb` on iOS. An RTL-SDR or HackRF in the USB-C port is inert. The `USBDriverKit` port that made this work runs on **M-series iPads** only; iPhones are A-series. |
| Sub-GHz, infrared, broadband RF | ❌ No | No hardware, and no way to attach any. |
| Do any of this from **Safari** | ❌ No | WebKit has never shipped Web Bluetooth, and every browser on iOS is WebKit. The web UI cannot scan on iOS in any browser. |

Net: a native iOS app could cover perhaps **40% of one band out of six**, with
no stable identity and no background operation. That is a genuinely useful
tracker-detector — which is why AirGuard exists — but it is not this tool, and no
amount of work on my side changes any row in that table.

**Android is a different story**, and worth saying so plainly. Android exposes
BLE scanning *with real MAC addresses*, a Wi-Fi scanning API (throttled, and
needing location permission), and USB host access with `libusb` — RTL-SDR apps
run on Android today. An Android phone could be a genuine sensor node feeding
the same engine. It is not built, but nothing in the platform forbids it, and
that is the difference between the two.

### What does not work, whatever the shop says

- **RTL-SDR or HackRF straight into an iPhone's USB-C port.** No `libusb` on
  iOS. The `USBDriverKit` route exists but covers M-series iPads only.
- **"Hidden camera detector" apps that claim RF detection.** An iPhone exposes
  no broadband RF measurement to apps. These read Wi-Fi/BLE scan results at
  best, and are magnetometer theatre at worst.
- **Wi-Fi monitor mode or raw 802.11**, on any iPhone, with any adapter.
- **BLE MAC addresses.** iOS gives apps a per-app rotating UUID instead, so
  cross-session identity — the thing `sweep`'s fusion layer is built on — cannot
  be done from an iOS app at all.

### Lightning iPhones (14 and earlier)

Everything in the "genuinely works" table applies except USB-C thermal cameras;
Lightning thermal models exist (older FLIR One) but are being discontinued. The
web UI and the front-camera trick are unaffected.

---

## What to buy, in order

1. **Nothing.** Run `sweep doctor`, then `sweep serve`. Three bands, no spend.
2. **~$65** — RTL-SDR Blog V4, an ESP32, an AD8317 module, a TSOP38238 and a
   BPW34. Takes you to six bands and adds the two detections that find things
   nothing else does: broadband RF and IR flood.
3. **~$40** — a Raspberry Pi to host it all, so the sweep is portable and the
   phone is genuinely just a screen.
4. **~$150–320** — a HackRF, if analogue video at 2.4/5.8 GHz is in your threat
   model. It is the only item here that closes that band.
5. **~$200–300** — a thermal camera, if you are sweeping rooms rather than
   monitoring your own space. It is the only tool listed that finds a device
   with no radio at all.

## A caution on the shopping

Most products marketed as "hidden camera detectors" on marketplaces are one of
two things: a bare RF LED with no calibration, or a ring of red LEDs and a
viewing filter for spotting lens reflections. The second is genuinely useful and
costs almost nothing. The first is usually worse than the AD8317 module in the
list above, which is a real calibrated log detector for about the same money.

And the honest framing for all of it: more bands mean fewer blind spots, not
certainty. `sweep`'s coverage table exists to tell you which blind spots you
still have after spending the money.
