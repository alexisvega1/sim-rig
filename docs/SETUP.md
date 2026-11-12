# Setting this up on your own Mac

This is what worked on one machine: a MacBook Pro (M4, 48 GB), a MOZA R5 wheelbase with a KS Pro wheel,
MOZA pedals with the PHF haptic kit, CrossOver 26.3 and Assetto Corsa from Steam. It was worked out between
September and November 2026. Versions matter more than usual here, so they are given wherever one was the fix.
Nothing below needs Windows, Boot Camp or MOZA Pit House.

The order matters: get the game running, then force feedback, then the extras.

## 1. Assetto Corsa in CrossOver

1. Install [CrossOver](https://www.codeweavers.com/crossover), make a Windows 10 64-bit bottle, install Steam in it,
   then Assetto Corsa.
2. Set the bottle's graphics backend to **DXVK**. On this machine D3DMetal broke the game (a crash while the
   track loads). Once it works, do not switch backends.
3. Install [Content Manager](https://acstuff.club/app/) and [Custom Shaders Patch](https://acstuff.club/patch/)
   (0.2.11 here). Most mod tracks and every online traffic server need the patch.
4. **Always start the game from Content Manager's Drive button, in 64-bit.** Steam's Play button starts the
   32-bit game without the patch, which crashes on patched tracks.
5. Content Manager's own window draws black or flickers under DXVK. Fix: turn off hardware acceleration for
   Windows desktop apps in the bottle. It does not touch the game's rendering.

   | Registry key | Value | Data |
   |---|---|---|
   | `HKCU\Software\Microsoft\Avalon.Graphics` | `DisableHWAcceleration` (DWORD) | `1` |

   Registry values are set with CrossOver's own `wine`, for example:

   ```bash
   "/Applications/CrossOver.app/Contents/SharedSupport/CrossOver/bin/wine" --bottle "YourBottle" reg add "HKCU\Software\Microsoft\Avalon.Graphics" /v DisableHWAcceleration /t REG_DWORD /d 1 /f
   ```

Things that cost hours:

- Copy the whole bottle folder before changing anything in it.
- If two CrossOver versions are installed, only ever use one with a given bottle. Running the older one's
  `wine` re-stamped the bottle as the older version.
- After force-quitting the bottle, check for leftover `Steam.exe` processes (`ps -axo pid,ppid,pcpu,command | grep -i steam.exe`).
  Two of them were each burning two CPU cores with no window; the game then crashed joining a server with
  `TCPBuffer out of memory` in its log, while the crash dialog blamed the graphics settings.

## 2. Force feedback: Torqer

macOS has no force-feedback driver for MOZA bases. Torqer is a Mac app that fills the gap: a small `dinput8.dll`
it places in the bottle passes the game's force-feedback commands to the app, and the app drives the wheelbase
over USB.

- **Start order is the whole trick: Torqer first** (wait for it to show the base as connected), then Content Manager,
  then Drive. If Content Manager was opened before the wheel was plugged in, quit and reopen it.
- Health check: `lsof -nP -iUDP | grep -i torqer` should show it listening (UDP 9003 here). No socket, no force feedback.
- **If the wheel oscillates violently or goes dead:** quit the game, Torqer and anything else using the wheel.
  Switch the base off for 15 seconds and on again, let it calibrate with all software closed, then start Torqer,
  then the game. Restarting only one of them does not clear it. Never power-cycle the base while the game is running.
- Calm settings to start from in Content Manager (Settings, Assetto Corsa, Controls): gain 50%, minimum force 2%, filter 0%.

## 3. Wheel lights and screen

`rig/moza-dash.py` reads the game's telemetry (UDP 9996 on localhost, which crosses the CrossOver boundary) and
drives the wheel's rev lights and screen over the base's serial port. Per-car rev-light windows live in
`rig/config.json` and can be changed from the control board.

If the wheel's screen goes blank after several quick restarts, stop the script, wait 15 seconds, start it once.

## 4. Pedal haptics

See [`moza-phf/README.md`](../moza-phf/README.md). In short: the PHF Control Box goes on its own USB port, and
`moza-phf` buzzes the brake pedal for ABS and lock-up and the throttle for traction control, wheelspin, revs and shifts.

One trap: on USB the box also shows up as a game controller. The game then numbers the wheelbase differently and
steering and pedals go dead. Hide the box from the game, with the bottle shut down:

| Registry key | Value | Data |
|---|---|---|
| `HKCU\Software\Wine\DirectInput\Joysticks` | `Gudsen Pedal Haptic Feedback Module` | `disabled` |

If your saved controls then point at the wrong device, rebind them in Content Manager (here every `JOY=1` in
`cfg/controls.ini` had to become `JOY=0`).

## 5. Start everything with one click

`rig/start-rig.sh` (or the `MOZA Rig.command` launcher) finds the base and the box by USB id, starts the wheel
script, the haptics and the control board, and opens the board at http://127.0.0.1:8765. Working order for a session:

1. Torqer
2. `MOZA Rig`
3. Content Manager, then Drive

## 6. No Hesi

The No Hesi launcher installs normally, but its window is a web page drawn by Microsoft's WebView2, and under
CrossOver it shows a black window. Four things together make it draw:

1. **A fixed WebView2 runtime, version 126.** Take the `WebView2.Runtime.X64` package, version 126.0.2592.68, from
   NuGet (a `.nupkg` is a zip) and unpack its `WebView2` folder into the bottle, for example
   `C:\users\crossover\AppData\Local\NoHesiWebView2\126.0.2592.68`. Newer runtimes (133 and later) need a Windows
   compositing feature Wine does not have, and crash. Do not let it update.
2. **Point the launcher at that folder** with two environment variables:

   | Registry key | Value | Data |
   |---|---|---|
   | `HKCU\Environment` | `WEBVIEW2_BROWSER_EXECUTABLE_FOLDER` | the folder from step 1 |
   | `HKCU\Environment` | `WEBVIEW2_DEFAULT_BACKGROUND_COLOR` | `FF08060A` |

3. **Turn off its GPU drawing:**

   | Registry key | Value | Data |
   |---|---|---|
   | `HKLM\Software\Policies\Microsoft\Edge\WebView2\AdditionalBrowserArguments` | `NoHesiLauncher.exe` | `--disable-gpu --disable-gpu-compositing --disable-direct-composition` |

4. **Tell that one program it is running on Windows 7.** This is the step that finally made it paint: on
   Windows 10 the browser draws through a kind of child window that never appears on the Mac; on Windows 7 it
   falls back to plain drawing, which does.

   | Registry key | Value | Data |
   |---|---|---|
   | `HKCU\Software\Wine\AppDefaults\msedgewebview2.exe` | `Version` | `win7` |

If you applied the Content Manager fix from section 1 first, the launcher still works with these four in place.

**Force feedback on No Hesi is separate.** The launcher applies its own force-feedback settings (Settings, Controls,
Force feedback), so a wheel that is calm in Content Manager can still shake itself apart on a No Hesi server.
Values that are calm on a direct-drive base here:

| Setting | Value |
|---|---|
| Gain | 40% |
| Minimum force | 2% |
| Filter | about 20% |
| Road, kerb, ABS effects | 10% |
| Slip effect | 0% |
| Gyroscopic effect | on |
| Damper gain | 100% |
| Minimum damper | 30% |

The ones that stop the oscillation are minimum damper, gyroscopic effect, the filter and the lower gain. Changes
only apply after leaving the drive completely and pressing Start Playing again.

Graphics to start from with heavy traffic: MSAA 2x, anisotropic 8 to 16x, world detail high, shadows 2048, smoke medium.

**Updating the launcher:** its built-in updater fails under CrossOver ("failed publisher verification"). The
download it fetches is the launcher itself, a single `.exe`; replacing the installed `NoHesiLauncher.exe` with the
new one, with the launcher closed, updates it. The version 126 runtime carried on working with launcher 1.0.5.

**What does not work: scoring.** Joining servers, traffic, chat and driving all work. Points have never registered
on this setup: the score connection is refused by the server a moment after it opens, on every server and with
launcher 1.0.3 and 1.0.5. The patch version, the driver name and Content Manager's Steam setting were all ruled out.
The cause is not known.

**Modified cars are for offline use.** Editing a car's data changes its checksum, and online servers reject a
car that does not match theirs. The car builds in this repository are for hotlaps and practice.

## 7. The in-game gauge

Copy `assetto-corsa/apps/rig_power` into the game's `apps/lua` folder and enable "Boost and Power" from the apps bar
in a session. It needs Custom Shaders Patch.
