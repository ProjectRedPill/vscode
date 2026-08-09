# Prior art, comparison, and the merge plan

The Instagram post that started this describes a 30-second hack: ask Claude for
a Bluetooth RSSI "warmer/colder" readout, walk around the office, find the
phone. That trick is genuinely good and it is the seed of this project — but it
solves about 5% of the actual problem. A phone you are looking for is
cooperating with you: it is powered on, advertising, and you know its name. A
device you are looking for that does *not* want to be found is a different
question entirely, and answering it takes more than one band.

This document is the survey behind that decision: what already exists, what each
project is genuinely best at, and which specific ideas were taken into `sweep`.

**Method and caveats.** Star counts and metadata were pulled from the GitHub API
on 2026-08-09 and are a popularity signal, not a quality one. The Hugging Face
section is from web search rather than direct inspection — `huggingface.co` is
blocked by the network egress policy in the environment this was researched in,
so treat those entries as leads to verify rather than confirmed findings.

---

## The ten most relevant projects

Ranked by relevance to *this* goal — identifying and locating nearby devices,
hostile or your own, with maximum detail — not by general merit.

| # | Project | Stars | Lang | License | Bands covered | What it is genuinely best at | Where it falls short here |
|---|---------|------:|------|---------|---------------|------------------------------|---------------------------|
| 1 | [bettercap/bettercap](https://github.com/bettercap/bettercap) | 19.8k | Go | GPL-3.0 | Wi-Fi, BLE, HID/2.4 GHz, CAN, IP | The broadest single recon surface in open source. Its BLE and 802.11 modules are mature, scriptable, and give real per-device detail. | Offence-first: half the modules transmit (deauth, MITM, rogue AP). No fusion across bands — each module keeps its own device list. No sub-GHz, no IR. |
| 2 | [kismetwireless/kismet](https://github.com/kismetwireless/kismet) | 2.2k | C++ | GPL-2.0 | Wi-Fi, BT/BLE, RTL-SDR, Zigbee, ADS-B | The best *architecture* in the field. Capture sources are separate processes speaking a defined protocol, so a new radio is a new binary, not a fork. Excellent long-running datastore and alerting. | Heavy to deploy and to learn. Built for fixed-site wireless IDS, not for a person walking around a room. No IR. No ranging UI. |
| 3 | [merbanan/rtl_433](https://github.com/merbanan/rtl_433) | 7.7k | C | GPL-2.0 | 315/433/868/915 MHz ISM | ~250 hand-written protocol decoders. If a door sensor, PIR, TPMS, key fob or weather station is transmitting, this identifies it *and* decodes its payload. Irreplaceable — nothing else comes close. | Sub-GHz only. Knows nothing about BLE, Wi-Fi or IR, and has no concept of a device identity that spans bands. |
| 4 | [seemoo-lab/AirGuard](https://github.com/seemoo-lab/AirGuard) | 2.4k | Kotlin | GPL-3.0 | BLE | The single best *idea* in this whole list: a tracker near you is not a threat, a tracker that **follows** you is. Correlates sightings across locations over time before it alerts. Academic work from TU Darmstadt's SEEMOO lab. | Android app, not reusable code. BLE only, and only consumer finding networks. No ranging beyond Apple's own sound-play. |
| 5 | [justcallmekoko/ESP32Marauder](https://github.com/justcallmekoko/ESP32Marauder) | 11.9k | C++ | MIT | Wi-Fi, BT/BLE (ESP32) | Proves a $10 board can do serious passive discovery, and ships a genuinely usable handheld UI. Includes explicitly defensive modes (detector, not just deauther). | Firmware for one board, not a library. Constrained by ESP32 RAM. No sub-GHz or IR without add-on hardware. |
| 6 | [RamboRogers/rfhunter](https://github.com/RamboRogers/rfhunter) | 1.2k | C++ | MIT | 1 MHz – 10 GHz, power only | The right answer to "what about devices with no digital identity". An AD8317 log detector responds to encrypted links, analogue video transmitters and protocols nobody has decoded. Cheap and effective. | Zero identification — it is a field-strength meter. Standalone device with an OLED; no logging, no correlation, no host integration. |
| 7 | [hbldh/bleak](https://github.com/hbldh/bleak) | 2.5k | Python | MIT | BLE | One asyncio API over BlueZ, CoreBluetooth and WinRT. The only realistic way to write cross-platform BLE in Python. | A transport library, deliberately. Hands you raw manufacturer bytes and stops — no decoding, no vendor knowledge, no identity. |
| 8 | [flipperdevices/flipperzero-firmware](https://github.com/flipperdevices/flipperzero-firmware) | 16.5k | C | GPL-3.0 | Sub-GHz, IR, NFC, RFID, BLE | The most complete *consumer* IR and sub-GHz protocol libraries anywhere, plus proof that multi-band handheld UX can be pleasant. | Bound to its own hardware. Receive paths are built for capture-and-replay, not continuous monitoring or alerting. |
| 9 | [1technophile/OpenMQTTGateway](https://github.com/1technophile/OpenMQTTGateway) | 4.1k | C++ | GPL-3.0 | BLE, 433 MHz, IR, LoRa | The one project here that already treats BLE + sub-GHz + IR as one pipeline, with a large library of BLE vendor payload decoders (Xiaomi, TPMS, scales, sensors). | Built for home automation: everything is oriented at publishing state to MQTT, not at investigating unknown devices. No threat model. |
| 10 | [agittins/bermuda](https://github.com/agittins/bermuda) | 1.9k | Python | MIT | BLE | Serious, practical BLE distance work — filtering, path-loss modelling and multi-receiver trilateration that actually holds up indoors. | Home Assistant integration, tightly coupled. Needs fixed receivers, so it cannot help a single person walking with one laptop. |

**Also examined, not in the top ten:** [urh](https://github.com/jopohl/urh)
(12.6k, now archived — the best protocol reverse-engineering GUI, but an
analyst's tool, not a monitor); [bluing](https://github.com/fO-000/bluing)
(1.0k, deep BR/EDR intelligence gathering, Linux-only and partly active);
[bluehood](https://github.com/dannymcc/bluehood) (1.1k, close in spirit — passive
neighbourhood BLE monitoring on a Pi — but BLE-only);
[ESP32-DIV](https://github.com/cifertech/ESP32-DIV) (3.7k) and
[HaleHound-CYD](https://github.com/JesseCHale/HaleHound-CYD) (1.5k), multi-protocol
ESP32 toolkits that add IR and nRF24 to the Marauder formula;
[SDR++](https://github.com/AlexandreRouma/SDRPlusPlus) (6.2k) and
[SigDigger](https://github.com/BatchDrake/SigDigger) (2.9k) for spectrum work;
[awesome-bluetooth-security](https://github.com/engn33r/awesome-bluetooth-security)
as a reference index.

### Hugging Face

There is no ready-made "spy device detector" model, and the honest finding is
that machine learning is not the bottleneck here — protocol decoding and
cross-band identity resolution are, and both are deterministic problems. What
does exist is useful for one specific future job, RF fingerprinting of
transmitters whose payload tells you nothing:

| Resource | Type | Relevance |
|---|---|---|
| DeepSig **RadioML 2018.01A** | Dataset | 24 modulation classes across SNRs. The standard benchmark for "what kind of signal is this?" — the natural next step past `rtl_433`'s hand-written decoders. |
| **ManySig-ZF** | Dataset | RF fingerprinting with zero-forcing equalisation. Aimed at identifying *individual* transmitters from hardware imperfections. |
| **CommRad RF** | Dataset | 2,700+ signals from 27 radios in an indoor multipath environment — closer to real sweep conditions than anechoic captures. |
| **rtl-ml** ([GitHub](https://github.com/TrevTron/rtl-ml), dataset on HF) | Model + pipeline | RTL-SDR capture → classify (FM, NOAA, APRS, FRS/GMRS, ISM, pagers) on a Raspberry Pi. The most directly transplantable of these. |

**Decision: not in v1.** A classifier that needs a GPU and a labelled corpus is
the wrong dependency for a tool that must run on an unfamiliar laptop with no
network. The `SpectrumSensor` interface is shaped so a classifier can be added
behind it later without changing anything upstream.

---

## The merge plan

### The gap nothing on that list fills

Every project above is excellent within one band and blind outside it. That
matters more than it sounds, because **the blind spot is where the device is.**
Someone hiding a camera does not care which of your tools is best in class; they
care that a BLE scanner cannot see an analogue 5.8 GHz video transmitter, that
`rtl_433` cannot see infrared, and that no amount of Wi-Fi scanning finds a
device that only wakes for four seconds an hour.

Three specific gaps, in order of how much they cost you:

1. **No cross-band identity.** Run bettercap and rtl_433 side by side and you
   get two device lists that never reference each other. A phone's Wi-Fi BSSID
   and its BLE MAC are unrelated numbers, so the same object appears twice and
   neither entry knows about the other.
2. **No re-identification across MAC rotation.** Modern BLE devices rotate their
   address every ~15 minutes. To a stateless scanner, one tracker following you
   for three hours looks like twelve unrelated strangers — which destroys
   exactly the signal AirGuard proved is the important one.
3. **Scanning is not finding.** A table of dBm values does not help you locate a
   thing in a room. The Instagram post got this right and almost every serious
   tool gets it wrong.

### Architecture

The unifying idea is a strict one-way flow, borrowed from Kismet and made
smaller:

```
  sensors/ ──emit──▶  Observation  ──▶  core/fusion  ──▶  Device  ──▶  threat/rules  ──▶  Finding
   BLE                (immutable        (identity          (mutable      (judgement,
   BT Classic          fact: one         resolution)        belief)       advisory only)
   Wi-Fi               sensor, one
   sub-GHz (rtl_433)   moment, one                    ┌──▶ core/store   (local SQLite, never leaves the box)
   RF power probe      emitter)                       ├──▶ ui/tui       (list · detail · FIND)
   IR probe                                           └──▶ ui/report    (markdown + JSON, states its own blind spots)
```

Sensors never touch device state. Fusion owns the device table and is
single-threaded, so there are no locks. Rules are pure functions that produce
advice and cannot delete or mute anything. Adding a band means writing one class
and adding one registry line.

### What was taken from where

| Source | Idea taken | Where it lives | What changed |
|---|---|---|---|
| **AirGuard** | A tracker near you is noise; a tracker that follows you across locations is the signal. | `threat/rules.py` — the **location epoch**. The operator presses `m` when they move; follow rules ask "present in ≥3 epochs?" | Generalised from Apple Find My to *every* band. A rotating-MAC device, a sub-GHz emitter or an unknown RF carrier that follows you is flagged by the same mechanism. |
| **Kismet** | Capture sources as independent, hot-pluggable units; long-running datastore. | `sensors/base.py` + `core/store.py` | Simplified from separate processes to asyncio tasks — one machine, one operator. Every sensor reports *why* it is unavailable, which becomes the coverage table in the report. |
| **rtl_433** | 250 protocol decoders and their JSON output. | `sensors/sdr.py` → `Rtl433Sensor` | Not reimplemented — invoked as a subprocess and its JSON mapped onto `Observation`. Reimplementing that decoder corpus would be foolish; wrapping it takes 80 lines. |
| **RFHunter** | A log-amp power probe finds what no decoder can. | `sensors/rfpower.py` + `firmware/rf_probe/` | Demoted from a standalone gadget to a sensor. The board just streams dBm; thresholds, baselines and alerting moved into software where they can change without a soldering iron. |
| **bermuda** | Kalman-filtered RSSI and honest path-loss modelling. | `core/rssi.py` | Two filters, not one. An archival filter (q=0.12) for the device table, and a much livelier one (q=1.0, ~33% gain per sample) for the finder — a filter tuned for a stationary sensor lags several metres behind someone walking. |
| **bleak** | Cross-platform BLE without three platform backends. | `sensors/ble.py` | Used as the primary backend, with a dependency-free `bluetoothctl` fallback so the tool still works on a machine you cannot `pip install` on. |
| **OpenMQTTGateway** | BLE + sub-GHz + IR as one pipeline; vendor payload decoding. | `intel/parsers/*` | The decoders were written fresh against the public specs and reoriented: OMG asks "what is this sensor reading?", `sweep` asks "what does this payload reveal about the device and its owner?" |
| **Flipper / ESP32-DIV** | IR belongs in a multi-band tool. | `sensors/ir.py` + `firmware/ir_probe/` | Added the detection neither does: **un-modulated IR flood**. A 38 kHz demodulating receiver is deaf to a night-vision illuminator by design, so the probe carries a bare photodiode as well. This is one of the most reliable hidden-camera tells and it needs the right sensor. |
| **bettercap / ESP32Marauder** | Breadth of passive discovery. | `sensors/*` | Every transmitting capability dropped. `sweep` is strictly receive-only — no deauth, no injection, no pairing, no connections. A counter-surveillance tool that announces itself is self-defeating, and the offensive modules are what make those tools legally awkward to run in a hotel room. |
| **Everything, by omission** | — | `ui/report.py` | No surveyed tool tells you what it *could not see*. Every report here opens with a coverage table and an explicit blind-spot list, because "three findings" reads as "the room is clean" unless you say otherwise. |

### What is new here

Four things that no surveyed project does, in rough order of importance:

1. **Cross-band and cross-rotation identity resolution** (`core/fusion.py`).
   Links rotated addresses via whatever survives rotation — a Tile's static ID,
   a Find My public key, Microsoft's slower-rotating CDP account hash, or a
   stable name combined with an exact advertisement fingerprint. Links across
   radios via the fact that consumer SoCs hand out consecutive MACs to their
   Wi-Fi and Bluetooth interfaces. Every link is scored and stored **with its
   reason**, and the UI shows the reason rather than asserting identity.
2. **The finder view** (`ui/tui.py` + `core/rssi.py`). The Instagram trick, done
   properly: a short recent window against a rolling baseline, oversized digits
   readable at arm's length, and a dB delta converted into a distance *ratio*
   ("0.34× the distance") rather than a fake absolute metre reading. Works on
   any band — you can range in on an unknown 433 MHz carrier or an infrared
   flood exactly as you would on a BLE tag.
3. **Deep advertisement decoding as a first-class product** (`intel/`). Apple
   Continuity (Find My separation state and battery, AirPods model and per-bud
   charge, Nearby Info device state), Microsoft CDP form factor, Google Fast
   Pair exact model IDs, Samsung SmartTag separation, Eddystone telemetry
   including uptime, full Class-of-Device decoding including the *Capturing*
   service bit. Plus the field most scanners get wrong: **address type**, with
   an honest admission that it cannot be determined from the address alone and a
   pass-through of the Bluetooth stack's own answer when available.
4. **Calibrated honesty.** Signature matches say "looks like a camera", not "is
   a camera". A separated tracker is *medium* severity; only one that follows
   you across locations is critical. Distance estimates carry their model's
   error. `sweep doctor` tells you what you cannot see before you start, and the
   report repeats it at the end.

### Deliberately not merged

- **Anything that transmits.** Deauth, jamming, MITM, rogue APs, BLE spam.
- **Active probing.** `hcitool info` opens a connection to the target and is
  detectable, so it sits behind an explicit `deep_probe` opt-in and is off by
  default.
- **Wi-Fi monitor mode and client capture.** Genuinely finds more — it sees
  clients, not just APs — but needs root and a compatible chipset. Documented as
  an optional path rather than made a requirement.
- **Cloud anything.** No telemetry, no uploads, no online lookups. The device
  inventory of a place you live is precisely the data that should never leave
  the machine, and a tool that needs the network is useless in the room where
  you most want it.

### Roadmap

- **Multi-receiver trilateration.** Two or three cheap ESP32 nodes reporting
  into the same fusion engine turn "warmer/colder" into an actual position. The
  `Observation` model already carries what this needs.
- **GPS-driven epochs.** Automatic location marking instead of pressing `m`,
  which makes the follow rules work while driving — the case where vehicle
  trackers matter most.
- **Spectrum classification.** The Hugging Face datasets above, behind the
  existing `SpectrumSensor` interface, to name carriers `rtl_433` cannot decode.
- **BLE sniffer support.** An nRF52 sniffer sees connection traffic and
  extended advertising that host stacks hide, and would close the gap on devices
  that only advertise briefly.
