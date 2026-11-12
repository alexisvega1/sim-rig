"""Lap recorder: Assetto Corsa's UDP telemetry -> one file per completed lap.

Saved sessions only keep lap and sector times. To draw a track map or compare
two laps corner by corner you need the car's position, speed and pedals all the
way round, so this listens to the same telemetry stream as the haptics and
writes each lap you complete to ~/dev/moza-rig/laps/<track>/<car>/.

A lap is stored resampled onto N equal steps of track position (not time), in
driving order from the timing line. Two laps of the same track then line up
index for index, which is what makes "where did I lose time" a subtraction.
Only laps seen from line to line are kept; an out-lap or a lap you joined
half-way is dropped.
"""
from __future__ import annotations

import json
import os
import socket
import struct
import threading
import time
from collections import namedtuple

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
                self.buf = self.buf[k:]
                self.whole = True
            elif s.laps != p.laps:                                      # new session, restart
                self.buf, self.whole = [], False
            if s.lap_ms == p.lap_ms:                                    # paused: the lap clock stands still
                self.prev = s
                return out
            step = ((s.npos - p.npos + 0.5) % 1.0) - 0.5                # signed progress round the lap
            if abs(step) > 0.05:                                        # teleport (back to pits, reset)
                self.buf, self.whole = [], False
            elif step < 0:                                              # rolling backwards: ignore
                self.prev = s
                return out
        self.prev = s
        self.buf.append(s)
        return out

    def _finish(self, buf: list[Sample], lap_ms: int) -> dict | None:
        if len(buf) < 50 or buf[0].lap_ms > 2000:
            return None
        p0 = buf[0].npos
        q, rows, last = [], [], -1.0
        for s in buf:
            v = (s.npos - p0) % 1.0
            if v > last:                                                # keep it strictly increasing
                q.append(v); rows.append(s); last = v
        if q[-1] < 0.97:
            return None
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
        # pin both ends so every step has data: the line is t=0 and t=lap time
        q = [0.0] + q + [1.0]
        for k, c in cols.items():
            cols[k] = [0.0 if k == "t" else c[0]] + c + [float(lap_ms) if k == "t" else c[-1]]
        out = {k: [] for k in names}
        j = 0
        for i in range(self.n):
            target = (i + 0.5) / self.n
            while j + 2 < len(q) and q[j + 1] < target:
                j += 1
            span = q[j + 1] - q[j]
            w = (target - q[j]) / span if span > 0 else 0.0
            for k in names:
                c = cols[k]
                out[k].append(c[j] if k == "gear" else c[j] + (c[j + 1] - c[j]) * w)
        digits = {"t": 0, "spd": 1, "thr": 2, "brk": 2, "gear": 0, "rpm": 0, "steer": 1, "x": 1, "z": 1, "glat": 2, "glon": 2}
        for k in EXTRA:
            digits[k] = _EXTRA_DIGITS[k.split("_")[0]]
        lap = {k: [int(round(v)) if digits[k] == 0 else round(v, digits[k]) for v in out[k]] for k in names}
        lap.update(ver=2 if names is not SERIES else 1, n=self.n, ms=int(lap_ms), p0=round(p0, 4))
        return lap


def track_id(track: str, config: str) -> str:
    """The id Content Manager uses, so recorded laps and saved sessions line up."""
    return track if not config or config == track else f"{track}-{config}"


def save(lap: dict, car: str, track: str, when: float | None = None) -> str:
    when = time.time() if when is None else when
    lap = {**lap, "car": car, "track": track, "date": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(when))}
    d = LAPS / track / car
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{time.strftime('%y%m%d-%H%M%S', time.localtime(when))}_{lap['ms']}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(lap, separators=(",", ":")))
    os.replace(tmp, path)
    return str(path)


class Recorder(threading.Thread):
    """Background thread: reconnects by itself, saves laps as you cross the line."""

    def __init__(self, host: str = "127.0.0.1", port: int = 9996):
        super().__init__(daemon=True)
        self.addr = (host, port)
        self.saved = 0
        self.stop = False

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
                car, track = text(d[0:100]), track_id(text(d[208:308]), text(d[308:408]))
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
                    lap = b.feed(parse(d))
                    if lap:
                        save(lap, car, track)
                        self.saved += 1
                        print(f"recorded lap {lap['ms'] / 1000:.3f} s  {car} @ {track}", flush=True)
                    if b.prev and len(b.buf) % 200 == 1:
                        rigcfg.write_state("recorder", {"connected": True, "car": car, "track": track,
                                                        "saved": self.saved, "whole": b.whole})
            except (OSError, struct.error):
                time.sleep(2.0)                                                    # no game: try again shortly
            finally:
                try:
                    sk.sendto(struct.pack("<iii", 1, 1, 3), self.addr)             # dismiss
                except OSError:
                    pass
                sk.close()
