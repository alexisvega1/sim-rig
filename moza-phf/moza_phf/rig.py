"""The live haptics loop: game telemetry + the control board's settings -> the PHF box.

Runs at 50 Hz whether or not a game is up, so a test buzz from the control board
works any time, and re-reads the settings file as it changes.
"""
from __future__ import annotations

import json
import threading
import time

from . import rigcfg, s12
from .telemetry import SOURCES, Telemetry

SHIFT_S = 0.08                     # how long a gear change kicks
TEST_FILE = "test"                 # state/test.json, written by the board
# frequency for a test buzz of effects whose frequency normally follows the car
TEST_FREQ = {"lockup": 40, "slip": 50, "engine": 60}


def pulses(t: Telemetry, fx: dict, shifting: bool) -> dict[str, tuple[float, float]]:
    """effect -> (frequency Hz, strength 0..1) for one telemetry sample, before on/off and master."""
    out = {}
    if t.abs_active:
        out["abs"] = (fx["abs"]["freq"], fx["abs"]["gain"])
    elif t.lockup > 0.05:
        out["lockup"] = (20 + 40 * t.lockup, fx["lockup"]["gain"] * t.lockup)
    if t.tc_active:
        out["tc"] = (fx["tc"]["freq"], fx["tc"]["gain"])
    if t.wheel_slip > 0.05 and t.throttle > 0.2:
        out["slip"] = (25 + 50 * t.wheel_slip, fx["slip"]["gain"] * t.wheel_slip)
    if t.rpm_frac > 0:
        out["engine"] = (10 + 90 * t.rpm_frac, fx["engine"]["gain"] * t.rpm_frac)
    if shifting:
        out["shift"] = (fx["shift"]["freq"], fx["shift"]["gain"])
    return out


class Haptics:
    def __init__(self, drv, pedals, live: rigcfg.Live | None = None, clock=time.monotonic):
        self.drv, self.pedals, self.clock = drv, set(pedals), clock
        self.live = live or rigcfg.Live()
        self._gear = None
        self._shift_until = 0.0
        self._test_id = self._pending_test().get("id")       # ignore a request left from last run
        self._test = (None, 0.0)
        self._polled = self._stated = 0.0
        self._limiter = 0.0
        self._sig, self._sig_moved = None, 0.0

    @staticmethod
    def _pending_test() -> dict:
        try:
            return json.loads((rigcfg.STATE / f"{TEST_FILE}.json").read_text())
        except (OSError, ValueError):
            return {}

    def step(self, t: Telemetry | None) -> dict[str, float]:
        """One tick. `t` is None when no fresh game data. Returns effect -> strength being played."""
        now = self.clock()
        h = self.live.get()["haptics"]
        fx, master = h["effects"], h["master"]

        if now - self._polled > 0.2:                          # test requests + the car's rev limiter
            self._polled = now
            req = self._pending_test()
            if req.get("id") != self._test_id and req.get("effect") in rigcfg.EFFECTS:
                self._test_id = req.get("id")
                self._test = (req["effect"], now + min(5.0, float(req.get("seconds", 1.0))))
            dash = rigcfg.read_state("dash")
            self._limiter = float(dash.get("maxrpm", 0)) if dash else 0.0

        if t is not None:                                     # AC keeps streaming while paused, but every
            sig = (t.lap_ms, t.rpm, t.speed_ms)               # value freezes; a running engine never does
            if sig != self._sig:
                self._sig, self._sig_moved = sig, now
            elif now - self._sig_moved > 0.5:
                t = None

        playing: dict[str, tuple[float, float]] = {}
        if t is not None:
            if self._limiter > 1000:
                t.max_rpm = self._limiter                     # the car's real limiter beats "highest seen"
            if self._gear is not None and t.gear != self._gear:
                self._shift_until = now + SHIFT_S
            self._gear = t.gear
            playing = {n: p for n, p in pulses(t, fx, now < self._shift_until).items() if fx[n]["on"]}
        name, until = self._test
        if name and now < until:                              # a test plays even if the effect is switched off
            playing[name] = (fx[name].get("freq", TEST_FREQ.get(name, 50)), fx[name]["gain"])

        want, active = {}, {}
        for n, (freq, strength) in playing.items():
            pedal, slot = rigcfg.EFFECTS[n]
            if pedal in self.pedals and strength * master > 0.005:
                want[(s12.PEDALS[pedal], slot)] = (freq, strength * master)
                active[n] = round(min(1.0, strength * master), 3)
        self.drv.show(want)
        self.drv.flush()
        if now - self._stated > 0.4:
            self._stated = now
            rigcfg.write_state("haptics", {"game": t is not None, "active": active,
                                           "pedals": sorted(self.pedals)})
        return active


class PrintDriver:
    """--dry-run: show what would be sent instead of opening the box."""
    def __init__(self):
        self._shown = None

    def show(self, want):
        line = {k: (round(f), round(s, 2)) for k, (f, s) in want.items()}
        if line != self._shown:
            print(line or "quiet", flush=True)
            self._shown = line

    def flush(self):
        pass

    def stop_all(self):
        pass


def run(source: str, drv, pedals, udp_port: int | None = None) -> None:
    h = Haptics(drv, pedals)
    latest: list = [None, 0.0]

    def feed():
        for t in SOURCES[source](**({"port": udp_port} if udp_port else {})):
            latest[0], latest[1] = t, time.monotonic()

    threading.Thread(target=feed, daemon=True).start()
    try:
        while True:
            fresh = time.monotonic() - latest[1] < 0.5        # game paused or gone: go quiet
            h.step(latest[0] if fresh else None)
            time.sleep(0.02)
    except KeyboardInterrupt:
        pass
    finally:
        drv.stop_all()
