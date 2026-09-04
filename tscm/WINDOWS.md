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

## 2. Deploy it into a folder you choose

The layout below keeps everything sweep needs inside one directory, so it can
be moved or deleted in one piece:

```
<InstallDir>\
    src\        the git clone
    .venv\      an isolated Python environment
    sweep.cmd   launcher
```

The virtual environment is deliberate. Installing into a conda `base`
environment works, but sweep then breaks the moment that environment is rebuilt
or you activate a different one. A dedicated `.venv` owns everything it needs.

### Manual (recommended — one command at a time, easy to check)

Substitute your own path for `$Install` if you want it elsewhere.

```powershell
$Install = "$HOME\.cursor\sweep"
New-Item -ItemType Directory -Force -Path $Install
cd $Install
```

Clone. `--depth 1` matters — this lives inside a fork of VS Code whose full
history is enormous, and shallow cloning takes it from many minutes to about one:

```powershell
git clone --depth 1 -b claude/spy-device-detection-563yo0 https://github.com/ProjectRedPill/vscode.git src
```

Once the branch is merged, use `-b main` instead.

Create the environment and install:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".\src\tscm[all]"
```

> **Quote the path.** PowerShell treats `[` and `]` as wildcard characters, so
> an unquoted `.\src\tscm[all]` can silently match nothing.

Make a launcher so you do not have to type the venv path every time:

```powershell
'@echo off' | Set-Content sweep.cmd -Encoding ASCII
'"%~dp0.venv\Scripts\sweep.exe" %*' | Add-Content sweep.cmd -Encoding ASCII
```

Check it:

```powershell
.\sweep.cmd doctor
```

### Scripted (for re-deploying and updating)

The script lives *inside* the repo, so it cannot do the very first clone for
you — do the manual steps above once, then this handles every rebuild:

```powershell
.\src\tscm\scripts\deploy-windows.ps1 -InstallDir "$HOME\.cursor\sweep" -Branch claude/spy-device-detection-563yo0
```

It updates the clone, rebuilds the environment, reinstalls, rewrites the
launcher and verifies. Safe to re-run at any time.

If PowerShell refuses to run it — *"running scripts is disabled on this
system"* — allow local scripts for that one session only:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

> That script was written on Linux and **has not been run on a real Windows
> machine**. If it misbehaves, the manual steps above do exactly the same thing
> and are easier to debug a line at a time.

### Updating later

```powershell
cd "$HOME\.cursor\sweep\src"
git pull
```

The install is editable (`-e`), so a `git pull` is the whole update — no
reinstall needed.

### A note on `.cursor`

`C:\Users\Rick\.cursor` is **Cursor's own configuration directory** — it holds
extensions, `mcp.json` and rules. Cursor can rewrite or reset it during updates
or a reinstall, which would take the clone and your sweep database with it.

Using a `sweep` subfolder (as above) avoids colliding with anything Cursor owns,
which is the main risk. But if you would rather it were somewhere Cursor never
touches, any other path works identically — `$HOME\tools\sweep`, say. Nothing in
sweep cares where it lives.

Worth knowing regardless: your sweep database does **not** live in the install
folder. It is at `%LOCALAPPDATA%\sweep\sweep.db`, so it survives deleting and
re-deploying the folder. That is the file to back up if you care about sweep
history.

## 3. Check what your machine can do

From the install folder:

```powershell
cd "$HOME\.cursor\sweep"
.\sweep.cmd doctor
.\sweep.cmd hwscan
```

`doctor` says which bands you can sense. `hwscan` says what your radios are
*capable* of and what `sweep` is not yet using.

To drop the `.\sweep.cmd` prefix, add the folder to PATH for this session:

```powershell
$env:Path += ";$HOME\.cursor\sweep"
sweep doctor
```

To make that permanent (takes effect in new terminals):

```powershell
[Environment]::SetEnvironmentVariable("Path", $env:Path + ";$HOME\.cursor\sweep", "User")
```

## 4. Run it

```powershell
.\sweep.cmd serve --open
```

Opens `http://localhost:8787/` in your browser.

To reach it from your phone:

```powershell
.\sweep.cmd serve --host 0.0.0.0
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
.\sweep.cmd serve --host 0.0.0.0 --sensors all --ir-port COM3 --rf-port COM4
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
| `sweep : The term 'sweep' is not recognized` | You are outside the install folder, or it is not on PATH. Use `.\sweep.cmd …` from the folder, or add it to PATH (step 3). |
| `pip install -e .\src\tscm[all]` matches nothing | PowerShell treats `[ ]` as wildcards. Quote it: `".\src\tscm[all]"`. |
| Phone cannot reach the URL | Windows Firewall. Allow the app on **Private** networks, and confirm the phone is on the same Wi-Fi (not cellular). |
| Output is full of `←[38;5;46m` | Legacy console without ANSI. Use Windows Terminal, or add `--no-color`. |
| QR code looks like noise | Same cause. Windows Terminal renders it correctly; `--no-qr` prints just the URL. |
| `doctor` shows Bluetooth Classic unavailable | Confirm the Bluetooth radio is on; then read the caveat above — Windows cannot do an active inquiry regardless. |
| No BLE devices at all | `pip install bleak`, and check Bluetooth is enabled in Windows Settings. |
| `pip` times out mid-install | A slow index, not your setup. Re-run the install command — pip resumes from cache. If only the `--upgrade pip` step failed, ignore it and carry on. |
| Install fails on `[all]` | Fall back to the core, which needs nothing: `.\.venv\Scripts\python.exe -m pip install -e ".\src\tscm"`. You lose better BLE and probe support, not the tool. |
