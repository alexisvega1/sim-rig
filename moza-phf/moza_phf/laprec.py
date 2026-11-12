"""Lap recorder: Assetto Corsa's UDP telemetry -> one file per completed lap.

Saved sessions only keep lap and sector times. To draw a track map or compare
two laps corner by corner you need the car's position, speed and pedals all the
way round, so this listens to the same telemetry stream as the haptics and
writes each lap you complete to ~/dev/moza-rig/laps/<track>/<car>/.

A lap is stored resampled onto N equal steps of track position (not time), in
driving order from the timing line. Two laps of the same track then line up
index for index, which is what makes "where did I lose time" a subtraction.
A lap seen from line to line is saved as a lap. A run that did not get that far
is kept as well, marked so it never mixes with the laps: one that ended in a
reset to the pits, a crash or the end of the session is a DNF, and the stretch
from the pits to the line is an out-lap. Those hold the same data, on the same
steps of track position, for the part of the lap they covered.
"""
from __future__ import annotations

import json
import os
import re
import socket
import struct
import threading
import time
from collections import namedtuple
from pathlib import Path

from . import rigcfg

N = 2000                         # steps round the lap (Road America: ~3 m each)
LAPS = rigcfg.HOME / "laps"
SERIES = ("t", "spd", "thr", "brk", "gear", "rpm", "steer", "x", "z", "glat", "glon")
# Recorded when the game sends them, one value per wheel in the order front-left,
# front-right, rear-left, rear-right. Older laps don't have these, so nothing may rely on them.
WHEELS = ("fl", "fr", "rl", "rr")
EXTRA = tuple(f"{k}_{w}" for k in ("load", "ride", "slip", "slipang") for w in WHEELS) + ("abs", "tc", "gvert")
_EXTRA_DIGITS = {"load": 0, "ride": 4, "slip": 3, "slipang": 2, "abs": 2, "tc": 2, "gvert": 2}

Sample = namedtuple("Sample", "spd glat glon lap_ms last_ms laps thr brk rpm steer gear npos x z ex", defaults=((),))


def parse(d: bytes) -> Sample:
    """RTCarInfo (328 bytes), layout from Kunos' remote telemetry doc."""
    f = struct.unpack_from
    g = f("<i", d, 76)[0]
    return Sample(spd=f("<f", d, 8)[0], glat=f("<f", d, 32)[0], glon=f("<f", d, 36)[0],
                  lap_ms=f("<i", d, 40)[0], last_ms=f("<i", d, 44)[0], laps=f("<i", d, 52)[0],
                  thr=f("<f", d, 56)[0], brk=f("<f", d, 60)[0], rpm=f("<f", d, 68)[0],
                  steer=f("<f", d, 72)[0], gear=-1 if g == 0 else g - 1,
                  npos=f("<f", d, 308)[0], x=f("<f", d, 316)[0], z=f("<f", d, 324)[0],
                  # tyre load (N), suspension height (m), slip ratio, slip angle per wheel; then ABS, TC, vertical g
                  ex=f("<4f", d, 180) + f("<4f", d, 292) + f("<4f", d, 132) + f("<4f", d, 100)
                     + (float(d[21]), float(d[22]), f("<f", d, 28)[0]))


class LapBuilder:
    """Feed samples in order; returns a lap dict each time a full lap completes."""

    def __init__(self, n: int = N):
        self.n = n
        self.buf: list[Sample] = []
        self.whole = False            # the buffer starts at the timing line
        self.prev: Sample | None = None
        self.partials: list[dict] = []   # runs that ended short of a lap; whoever feeds the builder collects these
        self.t0 = 0                   # lap clock when this lap passed the line (only a session's first lap: see feed)

    def cut(self, reason: str) -> None:
        """The run in the buffer is over without a lap: keep it as a partial and start afresh."""
        run = self._partial(self.buf, reason)
        if run:
            self.partials.append(run)
        self.buf, self.whole, self.t0 = [], False, 0

    def feed(self, s: Sample) -> dict | None:
        out = None
        p = self.prev
        if p is not None:
            if s.laps == p.laps + 1 and s.last_ms > 0:                 # crossed the line
                # samples already on the new lap (its clock restarted) stay in the buffer
                k = len(self.buf)
                while k > 0 and self.buf[k - 1].lap_ms < 3000 and self.buf[k - 1].lap_ms < s.last_ms - 3000:
                    k -= 1
                if self.whole:
                    out = self._finish(self.buf[:k], s.last_ms)
                else:                                                   # from the pits to the line: an out-lap
                    run = self._partial(self.buf[:k], "line")
                    if run:
                        self.partials.append(run)
                self.buf = self.buf[k:]
                self.whole, self.t0 = True, 0
            elif s.laps != p.laps:                                      # new session, restart
                self.cut("session")
            elif (not self.whole and p.npos > 0.9 and s.npos < 0.1
                  and 0 < ((s.npos - p.npos + 0.5) % 1.0) - 0.5 < 0.05):
                # Passed the line without the game counting it. A hotlap session starts the car on its grid slot
                # some way before the line with the clock already running, and ends that first "lap" at the next
                # crossing, so the game's first lap is a standing start plus the run-up. Time it from the line
                # instead, like every other lap: note the clock here and measure from it.
                run = self._partial(self.buf, "line")
                if run:
                    self.partials.append(run)
                self.buf, self.whole, self.t0 = [], True, s.lap_ms
            if s.lap_ms == p.lap_ms:                                    # paused: the lap clock stands still
                self.prev = s
                return out
            step = ((s.npos - p.npos + 0.5) % 1.0) - 0.5                # signed progress round the lap
            if abs(step) > 0.05:                                        # teleport (back to pits, reset)
                self.cut("reset")
            elif step < 0:                                              # rolling backwards: ignore
                self.prev = s
                return out
        self.prev = s
        self.buf.append(s)
        return out

    @staticmethod
    def _columns(rows: list[Sample]) -> tuple[dict, tuple]:
        cols = {
            "t": [float(s.lap_ms) for s in rows], "spd": [s.spd for s in rows],
            "thr": [s.thr for s in rows], "brk": [s.brk for s in rows],
            "gear": [float(s.gear) for s in rows], "rpm": [s.rpm for s in rows],
            "steer": [s.steer for s in rows], "x": [s.x for s in rows], "z": [s.z for s in rows],
            "glat": [s.glat for s in rows], "glon": [s.glon for s in rows],
        }
        names = SERIES
        if all(len(s.ex) == len(EXTRA) for s in rows):                # the fuller packet: keep the per-wheel data too
            for i, k in enumerate(EXTRA):
                cols[k] = [s.ex[i] for s in rows]
            names = SERIES + EXTRA
        return cols, names

    @staticmethod
    def _resample(q: list[float], cols: dict, names: tuple, targets: list[float]) -> dict:
        """Each series read off at the given track positions (q and targets on the same scale, both rising)."""
        out = {k: [] for k in names}
        j = 0
        for target in targets:
            while j + 2 < len(q) and q[j + 1] < target:
                j += 1
            span = q[j + 1] - q[j]
            w = min(1.0, max(0.0, (target - q[j]) / span)) if span > 0 else 0.0
            for k in names:
                c = cols[k]
                out[k].append(c[j] if k == "gear" else c[j] + (c[j + 1] - c[j]) * w)
        digits = {"t": 0, "spd": 1, "thr": 2, "brk": 2, "gear": 0, "rpm": 0, "steer": 1, "x": 1, "z": 1, "glat": 2, "glon": 2}
        for k in EXTRA:
            digits[k] = _EXTRA_DIGITS[k.split("_")[0]]
        return {k: [int(round(v)) if digits[k] == 0 else round(v, digits[k]) for v in out[k]] for k in names}

    def _finish(self, buf: list[Sample], lap_ms: int) -> dict | None:
        t0 = self.t0                                                    # not zero only on a session's first lap
        if len(buf) < 50 or buf[0].lap_ms - t0 > 2000:
            return None
        official, lap_ms = lap_ms, lap_ms - t0
        p0 = buf[0].npos
        q, rows, last = [], [], -1.0
        for s in buf:
            v = (s.npos - p0) % 1.0
            if v > last:                                                # keep it strictly increasing
                q.append(v); rows.append(s); last = v
        if q[-1] < 0.97:
            return None
        cols, names = self._columns(rows)
        cols["t"] = [v - t0 for v in cols["t"]]
        # pin both ends so every step has data: the line is t=0 and t=lap time
        q = [0.0] + q + [1.0]
        for k, c in cols.items():
            cols[k] = [0.0 if k == "t" else c[0]] + c + [float(lap_ms) if k == "t" else c[-1]]
        lap = self._resample(q, cols, names, [(i + 0.5) / self.n for i in range(self.n)])
        lap.update(ver=2 if names is not SERIES else 1, n=self.n, ms=int(lap_ms), p0=round(p0, 4))
        if t0:                                                          # timed from the line; the game's own figure kept too
            lap.update(first_lap=True, official_ms=int(official))
        return lap

    def _partial(self, buf: list[Sample], reason: str) -> dict | None:
        """A run that covered only part of the lap, on the same steps of track position as a lap. `i0` is the
        step it starts at and each series has `len` values; `t` counts from the run's first sample."""
        if len(buf) < 100:
            return None
        p0 = buf[0].npos
        q, rows, last = [], [], -1.0
        for s in buf:
            v = (s.npos - p0) % 1.0
            if v > last:
                q.append(v); rows.append(s); last = v
        took = rows[-1].lap_ms - rows[0].lap_ms
        if q[-1] < 0.03 or took < 5000 or max(s.spd for s in rows) < 40:   # too little to be worth keeping
            return None
        cols, names = self._columns(rows)
        cols["t"] = [v - cols["t"][0] for v in cols["t"]]
        first = int(-(-(p0 * self.n - 0.5) // 1))                       # first step at or after the run's start
        count = int(((p0 + q[-1]) * self.n - 0.5) // 1) - first + 1
        if count < 10:
            return None
        run = self._resample(q, cols, names, [(first + i + 0.5) / self.n - p0 for i in range(count)])
        tail = [s.spd for s in buf[-60:]]                                # the last moments, including standing still
        if reason == "line":
            status, how = "out-lap", "reached the line"
        else:
            status = "dnf"
            how = {"session": "session ended"}.get(reason, "crash" if min(tail) < 25 else "reset to the pits")
        run.update(ver=3, partial=True, status=status, how=how, from_line=bool(self.whole and rows[0].lap_ms - self.t0 < 2000),
                   n=self.n, i0=first % self.n, len=count, ms=int(took), p_from=round(p0, 4), p_to=round((p0 + q[-1]) % 1.0, 4),
                   end_spd=round(sum(tail) / len(tail), 1))
        return run


def track_id(track: str, config: str) -> str:
    """The id Content Manager uses, so recorded laps and saved sessions line up."""
    return track if not config or config == track else f"{track}-{config}"


# The game's own note of the session it is running. The telemetry names the track but not always its layout.
RACE_INI = Path(os.environ.get("MOZA_RIG_RACE_INI", Path.home() / "Library/Application Support/CrossOver/Bottles/Steam(try3232)"
                               / "drive_c/users/crossover/Documents/Assetto Corsa/cfg/race.ini"))


def layout_of(track: str) -> str:
    """The layout being driven on this track, from race.ini; empty if it has none or the file says otherwise."""
    try:
        text = RACE_INI.read_text(errors="ignore")
    except OSError:
        return ""
    race = re.search(r"^\[RACE\][^\[]*", text, re.M)
    if not race:
        return ""
    find = lambda key: (re.search(r"^%s\s*=\s*([^;\r\n]*)" % key, race.group(0), re.M) or [None, ""])[1].strip()
    return find("CONFIG_TRACK") if find("TRACK") == track else ""


def save(lap: dict, car: str, track: str, when: float | None = None) -> str:
    when = time.time() if when is None else when
    lap = {**lap, "car": car, "track": track, "date": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(when))}
    d = LAPS / track / car
    d.mkdir(parents=True, exist_ok=True)
    # A run that is not a lap carries its kind in the name, so anything listing laps passes over it.
    kind = "" if not lap.get("partial") else ("_dnf" if lap.get("status") == "dnf" else "_out")
    path = d / f"{time.strftime('%y%m%d-%H%M%S', time.localtime(when))}_{lap['ms']}{kind}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(lap, separators=(",", ":")))
    os.replace(tmp, path)
    return str(path)


class Live:
    """The lap being driven right now, for the board's live view: the latest reading, and this lap so far on
    G steps of track position (filled in as the car goes round, cleared at the line or on a reset)."""
    G = 500
    KEYS = ("t", "spd", "thr", "brk", "gear", "rpm")

    def __init__(self):
        self.lock = threading.Lock()
        self.car = self.track = ""
        self.seq = 0
        self.clear()

    def clear(self) -> None:
        self.head, self.start, self.now = -1, -1, None
        self.cur = {k: [None] * self.G for k in self.KEYS}

    def update(self, s: Sample, t0: int, car: str, track: str) -> None:
        with self.lock:
            if (car, track) != (self.car, self.track):
                self.car, self.track = car, track
                self.clear()
            i = min(self.G - 1, max(0, int(s.npos * self.G)))
            if self.head >= 0:
                gap = i - self.head
                if gap < -self.G * 0.5:                     # over the line: a new lap
                    self.clear()
                elif gap < -2 or gap > self.G * 0.05:        # a jump: back to the pits, or a reset
                    self.clear()
            vals = {"t": s.lap_ms - t0, "spd": round(s.spd, 1), "thr": round(s.thr, 2), "brk": round(s.brk, 2),
                    "gear": s.gear, "rpm": int(s.rpm)}
            first = i if self.head < 0 else self.head + 1
            if self.head < 0:
                self.start = i
            for j in range(first, i + 1):                    # fill every step passed since the last reading
                for k in self.KEYS:
                    self.cur[k][j] = vals[k]
            self.head = max(self.head, i)
            self.seq += 1
            self.now = {**vals, "npos": round(s.npos, 5), "x": round(s.x, 1), "z": round(s.z, 1), "laps": s.laps,
                        "glat": 0, "at": time.time()}

    def snapshot(self) -> dict:
        with self.lock:
            fresh = self.now is not None and time.time() - self.now["at"] < 2.0
            return {"live": fresh, "car": self.car, "track": self.track, "seq": self.seq, "g": self.G, "head": self.head,
                    "start": self.start, "now": self.now, "cur": {k: list(v) for k, v in self.cur.items()}}


class Recorder(threading.Thread):
    """Background thread: reconnects by itself, saves laps as you cross the line."""

    def __init__(self, host: str = "127.0.0.1", port: int = 9996):
        super().__init__(daemon=True)
        self.addr = (host, port)
        self.saved = 0
        self.stop = False
        self.live = Live()

    def _keep(self, b: LapBuilder, car: str, track: str) -> None:
        """Save whatever unfinished runs the builder has put aside."""
        while b.partials:
            run = b.partials.pop(0)
            save(run, car, track)
            print(f"recorded {run['status']} ({run['how']}): {run['ms'] / 1000:.1f} s over {run['len'] / run['n']:.0%} of the lap  "
                  f"{car} @ {track}", flush=True)

    def run(self):
        text = lambda b: b.decode("utf-16le", "ignore").split("%")[0].strip("\x00 ")
        while not self.stop:
            sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sk.settimeout(1.0)
            try:
                sk.sendto(struct.pack("<iii", 1, 1, 0), self.addr)                 # handshake
                d, _ = sk.recvfrom(4096)
                if len(d) < 408:
                    continue
                name, config = text(d[208:308]), text(d[308:408])
                if not config or config == name:                                   # the telemetry gave no layout
                    config = layout_of(name)
                car, track = text(d[0:100]), track_id(name, config)
                sk.sendto(struct.pack("<iii", 1, 1, 1), self.addr)                 # subscribe
                b, quiet = LapBuilder(), 0
                while not self.stop and quiet < 3:
                    try:
                        d, _ = sk.recvfrom(4096)
                    except socket.timeout:
                        quiet += 1
                        rigcfg.write_state("recorder", {"connected": False, "saved": self.saved})
                        continue
                    quiet = 0
                    if len(d) != 328:
                        continue
                    sample = parse(d)
                    lap = b.feed(sample)
                    self.live.update(sample, b.t0, car, track)
                    if lap:
                        save(lap, car, track)
                        self.saved += 1
                        print(f"recorded lap {lap['ms'] / 1000:.3f} s  {car} @ {track}", flush=True)
                    self._keep(b, car, track)
                    if b.prev and len(b.buf) % 200 == 1:
                        rigcfg.write_state("recorder", {"connected": True, "car": car, "track": track,
                                                        "saved": self.saved, "whole": b.whole})
                b.cut("session")                                                # the game went quiet mid-run
                self._keep(b, car, track)
            except (OSError, struct.error):
                rigcfg.write_state("recorder", {"connected": False, "saved": self.saved})   # alive, waiting for a session
                time.sleep(2.0)                                                    # no game: try again shortly
            finally:
                try:
                    sk.sendto(struct.pack("<iii", 1, 1, 3), self.addr)             # dismiss
                except OSError:
                    pass
                sk.close()
