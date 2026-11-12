# moza-phf — MOZA Pedal Haptic Feedback without Pit House (macOS + Linux)

The PHF kit's Control Box is MOZA's "S12 pedal vibration module". Its wire
command is public: giantorth/moza-simhub-plugin decoded it from a Pit House
capture (`docs/protocol/devices/pedal-haptics.md`). `moza_phf/s12.py` implements
that command, so **no scanning, fuzzing or Windows capture is needed any more.**

Status, honestly:

| piece | state |
|---|---|
| S12 frame format (set / stop / query, both envelopes, 0x7e stuffing) | matches the plugin's documented byte vectors, which come from real Pit House traffic |
| streaming driver (`buzz`, `run`) | drives the real box: brake and throttle motors confirmed by feel on 2026-11-11 |
| Assetto Corsa UDP client (port 9996) | pedals / rpm / gear / lap clock / track position / car coordinates checked against the live game; the ABS, TC and wheel-speed fields behind the effects are from Kunos' doc and only judged by feel |
| control board (`board`), settings file, per-car rev lights | running on this rig; a test buzz and a strength change from the board reach the driver without a restart |
| lap recorder + analysis (Track map tab) | tested on simulated laps; packet fields checked against the game; see "Control board" below for what real laps have shown |
| DiRT Rally 2 / BeamNG telemetry | written, unvalidated |
| capture tools (`diff`, `dump`, `sniff`), `identify` | tested on synthetic data; no longer needed for the PHF |
| `scan` / `fuzz` | obsolete — they hunted for a command that is now known. Don't run them. |

## Your setup (M4 Mac, Assetto Corsa in CrossOver)

No Wine in the hardware path. macOS exposes MOZA serial ports natively as
`/dev/cu.usbmodem*`, and AC's telemetry is plain UDP on localhost, which crosses
the CrossOver boundary:

    AC (CrossOver) --UDP 9996--> moza-phf run ac --serial--> PHF box --> motor

### Wiring: box on its own USB cable (recommended)

- Pedals stay plugged straight into the wheelbase, exactly as they work today.
- PHF module -> box **BRAKE** port, 12 V adapter -> box **POWER**.
- Box **USB -> the Mac**. Nothing in the box's WHEELBASE or PEDAL ports.

The box then shows up as its own serial port (USB id 346e:002f), separate from
the base's port that `moza-dash.py` holds for the wheel screen and LEDs, so the
two never fight. Untested assumption: that the box drives the motor with no
pedals wired into it. The protocol addresses motor ports, not pedals, so it should.

```bash
cd ~/dev/moza-phf
.venv/bin/moza-phf probe      # asks the box for its state; changes nothing
.venv/bin/moza-phf buzz       # brake motor, 50 Hz at 50 % for 1.5 s, then stop
.venv/bin/moza-phf run ac     # with a session loaded in AC: ABS and lock-up on the brake
```

`run` options: `--pedals brake,throttle` for the pedals that have a motor,
`--dry-run` to print the effects instead of sending them. Strengths, frequencies
and on/off per effect come from the control board's settings file, re-read live.
The motor stops by itself about half a second after the tool stops sending, and
goes quiet while the game is paused.

## Control board

    ~/Desktop/MOZA Rig.command   ->   http://127.0.0.1:8765

`~/dev/moza-rig/start-rig.sh` starts three things: the wheel dashboard bridge
(`~/dev/moza-rig/moza-dash.py`), the haptics (`moza-phf run ac`) and the board
(`moza-phf board`). Logs are in `~/dev/moza-rig/logs/`.

| tab | what it does | where the data lives |
|---|---|---|
| Pedal haptics | overall strength; per effect on/off, strength, frequency, Test | `~/dev/moza-rig/config.json` (`rigcfg.py`) |
| Rev lights | default window (start / full / flash, % of the car's limiter) and per-car overrides | same file, `leds` |
| Lap times | progression, sectors, best lap per car, from Content Manager's saved sessions | the bottle's `content-manager/Data/Progress/Sessions/*.json` |
| Track map | speed-coloured map, lap-against-lap traces, corner table, 25-slice losses, suggestions | `~/dev/moza-rig/laps/<track>/<car>/*.json`, written by the board's recorder (`laprec.py`) |

The board only edits the settings file and drops test requests in
`~/dev/moza-rig/state/`; the haptics loop (`rig.py`) and the dash pick them up.
It listens on 127.0.0.1 only and refuses writes from other web pages.

The recorder keeps a lap only when it saw it from line to line, so the lap out
of the pits is dropped. Laps are stored on 2000 equal steps of track position,
which is what lets `lapsan.py` compare two laps point for point (time delta,
braking points, minimum and exit speeds, the "ideal lap" from 25 slices).
`moza-phf board --no-record --port 8766` runs a second, look-only copy.

### Wiring: box behind the wheelbase

Pedals -> box PEDAL, box WHEELBASE -> base PEDAL port, nothing on the box's USB.
The tool then talks to the box through the base's port (`--mode ext`). Two
reasons to avoid it here: on 2026-10-27 this wiring made the base report brake
and throttle as not connected, and the base's port is already held by
`moza-dash.py` — only one program can own a serial port.

### Linux alternative

Same commands; the port is `/dev/ttyACM*` and needs the udev rule below.

## Install

```bash
pip install -e ".[dev]"
python -m pytest
sudo tee /etc/udev/rules.d/99-moza.rules <<< 'SUBSYSTEM=="tty", KERNEL=="ttyACM*", ATTRS{idVendor}=="346e", MODE="0666", TAG+="uaccess"'
```

## Capturing Pit House traffic on Windows (not needed for the PHF; kept for other devices)

You need one Windows session with Pit House. Boot Camp on the 2019 MBP is fine.

1. **Wire the box over USB, not through the base.** USB-direct makes the box its
   own USB device, so the capture isolates it. (Pedals → box PEDAL, module → BRAKE,
   12 V in, box USB → PC. Nothing in the WHEELBASE port.)
2. Install Wireshark with USBPcap. Start capturing on the USBPcap interface
   *before* launching Pit House.
3. Record three captures, saving each as legacy **pcap** (File ▸ Save As ▸ pcap,
   not pcapng):
   - `idle.pcap` — Pit House open on the pedal page, hands off, 20 s.
   - `brake_on.pcap` — same, but enable the brake haptic effect / drag its
     intensity slider to 100 and back. Note timestamps.
   - `brake_test.pcap` — hit whatever "test"/"preview" button fires the motor.
4. On Linux:

```bash
moza-phf devices brake_on.pcap            # which USB address is the box
moza-phf diff idle.pcap brake_on.pcap --usb-device N
moza-phf diff idle.pcap brake_test.pcap --usb-device N
moza-phf dump brake_test.pcap --usb-device N | less   # timeline, watch for frames at the moment it buzzed
```

The `diff` output lists frames that only appear while you were doing the thing.
Expect: a new MOZA device id (the box), a write group, and payloads whose last
bytes track the slider (0x64 = 100). If the box instead appears under the pedals
id (25) with command ids > 31, it's an extension of the pedal command set.

5. Confirm from Linux, box on USB:

```bash
moza-phf replay "7e 05 2e 1f 12 00 1e 00 32 3f"   # paste the real frame
```

If the motor buzzes, write `cmdset.json`:

```json
{"device": 31, "write_group": 46, "pedal_ids": {"brake": [18]},
 "freq_bytes": 2, "intensity_bytes": 2}
```

(or with `freq_suffix`/`intensity_suffix` if the capture shows two separate
commands). Then:

```bash
moza-phf run outgauge --cmdset cmdset.json --dry-run    # pulses printed, nothing sent
moza-phf run outgauge --cmdset cmdset.json
```

6. Contribute: add the device id and commands to Boxflat's `serial.yml` and open
   a PR; link the captures.

## Through the wheelbase

Once the command is known, the base relays frames to downstream devices by
device id (that's how Boxflat talks to pedals through the base today), so
the same frames should work with the box on the base's PEDAL port and the
driver talking to the base's ttyACM. Untested until the USB-direct path works.

## Layout

```
moza_phf/protocol.py   frame build/parse, checksum, stream splitter
moza_phf/pcap.py       USBPcap/usbmon reader → frames
moza_phf/discover.py   diff, dump, identify, scan, fuzz, replay
moza_phf/linux.py      doctor, sniff (pty man-in-the-middle)
moza_phf/sim.py        fake device for tests
moza_phf/telemetry.py  UDP sources (ac, codemasters, outgauge)
moza_phf/effects.py    telemetry → per-pedal Pulse
moza_phf/s12.py        the PHF box: frames, query, streaming driver
moza_phf/phf.py        superseded guess-table driver (unused)
moza_phf/cli.py
tests/
```
