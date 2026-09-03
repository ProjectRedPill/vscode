# Running sweep on Windows

Written for **Windows PowerShell 5.1** — the blue-icon `powershell.exe` that
ships with Windows and is what you get by default. Every command below is
PowerShell-safe.

> **`&&` does not work in Windows PowerShell 5.1.** It was only added in
> PowerShell 7. Chaining commands with `&&` gives you
> *"The token '&&' is not a valid statement separator in this version."*
> Put each command on its own line, or separate them with `;`.

---

## 1. Prerequisites

```powershell
python --version
git --version
```

You need Python **3.10 or newer**. If `python` opens the Microsoft Store, use
`py --version` instead and substitute `py -m pip` for `pip` throughout.

Anaconda is fine — if your prompt starts with `(base)` you are in the conda base
environment and `pip` will install there. That works, though a dedicated
environment is tidier:

```powershell
conda create -n sweep python=3.12 -y
conda activate sweep
```

**Use Windows Terminal if you have it.** The classic console window does not
handle the colour output or the QR code well. `sweep` now switches the console
into ANSI mode automatically, but Windows Terminal is still the better
experience. It is free in the Microsoft Store and is the default on Windows 11.

## 2. Get the code

You need to clone it first — this is the step people miss.

```powershell
cd $HOME
git clone --depth 1 https://github.com/ProjectRedPill/vscode.git
cd vscode\tscm
```

`--depth 1` matters: this lives inside a fork of VS Code, whose full history is
enormous. Shallow-cloning takes it from many minutes to about one.

If you want the branch before it is merged to `main`:

```powershell
git clone --depth 1 -b claude/spy-device-detection-563yo0 https://github.com/ProjectRedPill/vscode.git
cd vscode\tscm
```

## 3. Install

```powershell
pip install -e ".[all]"
```

Note the **double quotes** around `".[all]"`. PowerShell treats square brackets
as wildcard characters, and an unquoted `.[all]` can fail to match anything.

## 4. Check what your machine can do

```powershell
sweep doctor
sweep hwscan
```

`doctor` says which bands you can sense. `hwscan` says what your radios are
*capable* of and what `sweep` is not yet using.

If `sweep` is not recognised as a command, the Scripts directory is not on your
PATH. Use `python -m sweep` instead — identical in every way:

```powershell
python -m sweep doctor
```

## 5. Run it

```powershell
sweep serve --open
```

Opens `http://localhost:8787/` in your browser.

To reach it from your phone:

```powershell
sweep serve --host 0.0.0.0
```

**Windows Firewall will prompt on the first run.** You must click **Allow
access**, and tick **Private networks**. If you miss the prompt, your phone will
simply time out. To fix it afterwards, run this once from an **Administrator**
PowerShell:

```powershell
New-NetFirewallRule -DisplayName "sweep" -Direction Inbound -LocalPort 8787 -Protocol TCP -Action Allow -Profile Private
```

With probes attached, use COM ports rather than `/dev/tty…` paths. Find them
with:

```powershell
Get-CimInstance Win32_SerialPort | Select-Object DeviceID, Description
```

Then:

```powershell
sweep serve --host 0.0.0.0 --sensors all --ir-port COM3 --rf-port COM4
```

---

## What works on Windows, and what does not

| Band | Windows | Notes |
|---|---|---|
| **Bluetooth LE** | ✅ Full | Via `bleak` and WinRT. Full advertisement payloads, so every vendor decoder works. |
| **Wi-Fi** | ✅ Good | Via `netsh`. Access points only — same as every platform without monitor mode. |
| **Bluetooth Classic** | ⚠️ **Limited** | See below. This is the significant one. |
| **Sub-GHz ISM** | ✅ Full | Needs an RTL-SDR and `rtl_433.exe` on your PATH. |
| **Broadband RF** | ✅ Full | `rtl_power.exe` / `hackrf_sweep.exe`, or the ESP32 probe over a COM port. |
| **Infrared** | ✅ Full | ESP32 probe over a COM port. LIRC is Linux-only, but the probe is the better path anyway. |

### The Bluetooth Classic caveat

Windows exposes **no unprivileged API for an active BR/EDR inquiry**. On Linux,
`hcitool scan` actively asks discoverable devices to identify themselves. On
Windows, `sweep` can only enumerate what the Bluetooth stack already knows —
paired devices and ones seen recently.

Practically: **an unpaired wireless microphone or body-worn recorder sitting in
the room may not appear on Windows, where it would on Linux.** That is a real
detection gap and it is not something I can code around; it is a platform limit.

If Bluetooth Classic matters for your threat model, run the sensing side on a
Raspberry Pi or a Linux laptop and view it from Windows in the browser. That is
the same host/client split the tool is designed around.

### The terminal UI

`sweep scan` and `sweep find` draw fine on Windows but **keyboard shortcuts do
not work** — the interactive key handling uses `termios`, which is POSIX-only.
The footer tells you so at runtime.

Use the web UI instead. It is the better interface on every platform and it is
fully interactive on Windows:

```powershell
sweep serve --open
```

### Two more Windows-specific facts

- **Extended Advertising is not reachable on Windows.** Even if `hwscan` reports
  a Bluetooth 5 controller, the WinRT API does not expose extended advertising
  to third-party code the way BlueZ does. The capability is in your hardware and
  out of reach of the OS. Linux is the only route to it.
- **Monitor mode**: if `netsh wlan show drivers` reports
  *"Network monitor mode supported: Yes"*, treat that sceptically. Windows
  drivers report the capability far more often than they usefully expose it.

---

## Where your data lives

```
%LOCALAPPDATA%\sweep\sweep.db
```

Typically `C:\Users\<you>\AppData\Local\sweep\sweep.db`. Nothing leaves your
machine. Override with `--db` or the `SWEEP_HOME` environment variable.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `The token '&&' is not a valid statement separator` | PowerShell 5.1 has no `&&`. One command per line, or use `;`. |
| `Cannot find path 'C:\vscode\tscm'` | You have not cloned yet. See step 2. |
| `sweep : The term 'sweep' is not recognized` | Scripts dir not on PATH. Use `python -m sweep …`. |
| `pip install -e .[all]` matches nothing | Quote it: `pip install -e ".[all]"`. |
| Phone cannot reach the URL | Windows Firewall. Allow the app on **Private** networks, and confirm the phone is on the same Wi-Fi (not cellular). |
| Output is full of `←[38;5;46m` | Legacy console without ANSI. Use Windows Terminal, or add `--no-color`. |
| QR code looks like noise | Same cause. Windows Terminal renders it correctly; `--no-qr` prints just the URL. |
| `doctor` shows Bluetooth Classic unavailable | Confirm the Bluetooth radio is on; then read the caveat above — Windows cannot do an active inquiry regardless. |
| No BLE devices at all | `pip install bleak`, and check Bluetooth is enabled in Windows Settings. |
