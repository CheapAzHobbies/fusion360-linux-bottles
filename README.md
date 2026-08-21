# Autodesk Fusion on Linux (Bottles + Wine)

Working notes for running Autodesk Fusion **2606.1.36** under Wine via Bottles on
Ubuntu 24.04 with an NVIDIA GPU.

This is not a from-scratch installer. Plenty of those exist already — see
[cryinkfly's installer](https://codeberg.org/cryinkfly/Autodesk-Fusion-360-on-Linux)
and [mxioi/fusion360-wine-linux](https://github.com/mxioi/fusion360-wine-linux).
What this documents is the set of changes that took a *broken* install to a
working one, and — just as usefully — the things that looked like fixes but
weren't.

## Status

| Works | Doesn't |
|---|---|
| Sign-in (OAuth callback) | **Data Panel renders black** |
| 3D viewport, grid, ViewCube | **Home tab renders blank** |
| Full ribbon, Browser tree, timeline | Notification banner renders as colour stripes |
| Sketching / modelling | Startup hangs intermittently (~1 in 3) |
| No debugger popups | |

Everything still broken is Chromium-backed UI. Fusion's own renderer is fine;
its embedded browser surfaces are not.

## Environment this was verified on

```
Ubuntu 24.04.4 LTS, kernel 7.0.0-29
Bottles 66.7 (flatpak), runner sys-wine-11.0 (wine-11.0)
Fusion 2606.1.36
NVIDIA RTX 3060 Ti, driver 580.173.02, X11
DXVK 2.7.1
```

## The five changes that mattered

### 1. Wine 11, not Wine 9

The single biggest one. The bottle was on `soda-9.0-1` (Wine 9.0 TkG, built April
2024) running a 2026 Fusion build. `AdskIdentityManager.exe` faulted on every
launch:

```
=>0 0x…  in combase (+0x1fcdf)
  1 0x…  in adskidentitymanager (+0x645df)
combase+0x1fcdf: mov 0x04(%rbx), %eax     rbx=0x7
```

Dereferencing offset 4 of a 7-byte "pointer" — a failed COM activation that was
never null-checked. Switching the runner to `sys-wine-11.0` fixed it outright.

```bash
flatpak run --command=bottles-cli com.usebottles.bottles \
  edit -b Fusion360 --runner sys-wine-11.0
flatpak run --command=/app/bin/wine \
  --env=WINEPREFIX="$BOTTLE" com.usebottles.bottles wineboot -u
```

> `sys-wine-11.0` is the Bottles flatpak's *bundled* Wine. A `flatpak update` can
> therefore move your Wine version. If Fusion breaks after one, suspect this first.

### 2. `QT_OPENGL=software` — the fix for the crash loop

Fusion's embedded Chromium was crash-looping ~150 times per three minutes:

```
ERROR:d3d_image_backing_factory.cc] Unable to create shared handle for DXGIResource 80004001
ERROR:shared_image_factory.cc]      CreateSharedImage: could not create backing.
ERROR:shared_context_state.cc]      Failed to make current since context is marked as lost
wine: Unhandled exception 0x80000003 … starting debugger...
```

`80004001` is `E_NOTIMPL`. Chromium asks D3D11 for a shared handle;
**neither DXVK nor Wine's own dxgi implements `CreateSharedHandle`.**

Setting Qt's GL implementation to software takes that path out of play:

| | before | after |
|---|---|---|
| DXGI shared-handle failures | 97 | **0** |
| `context is marked as lost` | 362 | **0** |
| crashes / 3 min | ~150 | ~24, all during startup, then quiet |

Set it as a bottle environment variable:

```
QT_OPENGL = software
```

### 3. `ChromiumGraphicsBackend = OpenGL`

An undocumented Fusion preference. Found by pulling strings out of
`NsBaseCore10.dll`:

```
ChromiumGraphicsBackend
GraphicsApiOtionAutomatic / …D3D11 / …Metal / …OpenGL     [sic — Autodesk's typo]
```

Valid values are `Automatic`, `d3d11`, `OpenGL`, `vulkan`. Setting `OpenGL`
dropped crashes from ~140 to ~23. Verify it took by grepping Fusion's own log for
`Chromium Graphics:`.

It lives in a **UTF-16** XML file, so edit it as UTF-16 or you'll corrupt it:

`drive_c/users/$USER/AppData/Roaming/Autodesk/Neutron Platform/Options/NMachineSpecificOptions.xml`

```xml
<CompatibilityGroup SchemaVersion="2" ToolTip="Compatibility options" UserName="Compatibility">
    <ChromiumGraphicsBackend UserName="ChromiumGraphicsBackend" Value="OpenGL"/>
</CompatibilityGroup>
```

> `d3d11` + `QT_OPENGL=software` together kill Fusion outright. Don't combine them.

### 4. The `adskidmgr://` sign-in callback

Autodesk's web sign-in ends by redirecting to `adskidmgr://…`. Something on the
host has to catch that scheme and hand it to the Identity Manager **inside the
same Wine prefix Fusion is running in**, or sign-in hangs forever with no error.

Two ways this silently breaks:

- The handler points at a different prefix than the one Fusion is running in.
  The browser login succeeds, the token lands somewhere else, Fusion waits forever.
- The handler runs a *different Wine build* than the bottle's:
  ```
  wine client error:0: version mismatch 930/787.
  ```
  Also silent — the token is simply dropped.

`scripts/adskidmgr-handler.sh` avoids both by reading the runner out of
`bottle.yml` at call time and auto-detecting the hashed `webdeploy/production/<hash>/`
directory, which Autodesk changes on every update. A working callback logs:

```
Found valid http route:/login
```

### 5. Silencing the Wine debugger popups

Every Chromium abort tripped Wine's `AeDebug` handler, which opened a `winedbg`
window that stole focus — dozens of times a minute.

```
AeDebug\Auto      = "1"     (both 32- and 64-bit views)
AeDebug\Debugger  = ""
HKCU\Software\Wine\WineDbg\ShowCrashDialog = dword:0
```

Note the semantics are the reverse of what you'd guess: `Auto="0"` means
*prompt before debugging* — that's what produces the "do you want to debug?"
dialog. `Auto="1"` is the silent setting.

## Virtual desktop

Enabled, sized to the **full desktop span** rather than a single monitor:

```
virtual_desktop     = true
virtual_desktop_res = 3840x1848      # your actual total span
```

Fusion's `EnumDisplayDevices` misbehaves on multi-monitor setups
(`Error getting display device string: (122) Insufficient buffer`), and startup
is more reliable with the virtual desktop on. Sizing it to the real span avoids
being trapped in a small box.

This does not fully fix the intermittent startup hang — see Known issues.

## Things that looked right and did nothing

Recorded so nobody burns an afternoon on them again.

| Tried | Result |
|---|---|
| `QTWEBENGINE_CHROMIUM_FLAGS=--disable-gpu …` | **No effect.** Reaches the process (`/proc/<pid>/environ` confirms) but Fusion builds Chromium's command line internally and never reads it. |
| `__EGL_VENDOR_LIBRARY_DIRS` → NVIDIA vendor JSON | No effect. libEGL still fell back to Mesa and reported `driver (null)`. |
| `QT_QUICK_BACKEND=software` | No change to crashes or to the black Data Panel. |
| Installing the WebView2 runtime | Harmless, never proven load-bearing. The crash it was meant to fix was actually the Wine version. |
| Disabling DXVK via the Bottles toggle | **Invalid test.** The toggle leaves DXVK's DLLs in `system32` with `native,builtin` overrides, so DXVK keeps running. To really disable it you must set `d3d11`/`dxgi`/`d3d9`/`d3d10core` to `builtin`. Doing so removed the keyed-mutex errors (138 → 0) but changed the crash count not at all — same `E_NOTIMPL` underneath. |
| `CUPS_SERVER` pointed at a dead port | 2 of 3 startups succeeded — no better than baseline, and it disables printing. |

## Known issues

**Data Panel and Home tab render black/blank.** The root cause is
`IDXGIResource1::CreateSharedHandle` returning `E_NOTIMPL` under Wine. Chromium
needs it for its shared-image compositing. This is a Wine feature gap, not a
setting. Keyed-mutex support landed in Wine 10.18, and there is
[ongoing work on shared resources via D3DKMT](https://github.com/doitsujin/dxvk/pull/5257),
so this may improve.

*Workaround for cloud models:* open Fusion Team in a browser, download the `.f3d`,
and use File → Open on the local file.

**Startup hangs roughly 1 launch in 3**, at `performInitialization1 START` in
`AppData/Local/Autodesk/Neutron Platform/logs/AppLogFile*.log`. All threads sleep;
nothing is spinning. A CUPS connection to `[::1]:631` is open when it happens and
there is an offline USB printer on this machine, but forcing CUPS lookups to fail
did not reliably fix it, so the link is unproven. Kill and relaunch.

## Files

- `scripts/adskidmgr-handler.sh` — `adskidmgr://` scheme handler; reads the runner from `bottle.yml`
- `scripts/fusion360` — launcher that starts the bottle's copy via `bottles-cli`
- `scripts/apply-wine-fixes.sh` — applies the registry changes from §5

## Useful log locations

```
Fusion's own log   drive_c/users/$USER/AppData/Local/Autodesk/Neutron Platform/logs/AppLogFile*.log
Chromium / Wine    stderr of the launcher
Sign-in callback   ~/.local/share/adskidmgr-handler.log
```

Fusion's own log is far more informative than Wine's stderr. Start there.
