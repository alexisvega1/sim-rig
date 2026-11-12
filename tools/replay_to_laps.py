#!/usr/bin/env python3
"""Turn one of the game's replays into recorded laps, as if the lap recorder had been running.

For a session the recorder missed, or one from before it kept unfinished runs. The replay is fed through the
recorder's own lap builder, so it yields the same things: laps, out-laps and DNFs, in the same files.

    python3 tools/replay_to_laps.py "<...>/Documents/Assetto Corsa/replay/temp/AC_....acreplay"

A replay has the car's position but not how far round the lap it is. That comes from a lap already recorded
on the track if there is one (so the new laps line up with it step for step), else from the first complete
lap in the replay itself. Needs numpy, and the moza-phf folder next to this repository's live copy.
"""
import argparse
import datetime
import os
import re
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, os.path.expanduser("~/dev/moza-phf"))
import acreplay                      # noqa: E402
from moza_phf import laprec, lapsan  # noqa: E402


def reference(track: str, car: str, r: dict):
    """Points round the lap with their track position (0..1): from a recorded lap, else from the replay."""
    laps = [m for m in lapsan.index() if m["track"] == track]
    laps.sort(key=lambda m: (m["car"] != car, m["ms"]))
    for m in laps:
        lap = lapsan.load(m["id"])
        if lap and max(lap["x"]) - min(lap["x"]) > 5:
            n = lap["n"]
            return np.column_stack([lap["x"], lap["z"]]), (np.arange(n) + 0.5) / n, "a recorded lap"
    # From the replay itself: one loop of the track, from the line round to the line. The lap clock restarts at
    # the line, so the frame after a restart is on it; the loop is the stretch of driving that ends there and
    # begins where the car last passed that same spot.
    xz = r["pos"][:, [0, 2]]
    jumps = set(np.where(np.linalg.norm(np.diff(xz, axis=0), axis=1) > 25)[0].tolist())
    drops = [int(i) for i in np.where(np.diff(r["clock"]) < -1000)[0] if int(i) not in jumps]
    if not drops:
        raise SystemExit("this replay has no complete lap, and no lap is recorded on this track yet to place it against")
    end = drops[0]
    near = np.linalg.norm(xz[:max(1, end - 200)] - xz[end + 1], axis=1)
    start = int(np.argmin(near))
    if near[start] > 30 or any(j in jumps for j in range(start, end)):
        raise SystemExit("could not find a full loop of the track in this replay")
    pts = xz[start:end + 1]
    d = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(pts, axis=0), axis=1))])
    return pts, d / (d[-1] + float(np.linalg.norm(pts[0] - pts[-1]))), "the replay's own first lap"


def positions(xz, step, pts, where):
    """How far round the lap each frame is: the nearest reference point, looked for near the last match."""
    n, m, j = len(xz), len(pts), None
    npos = np.empty(n)
    along = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(pts, axis=0), axis=1))])   # metres round the reference
    total = along[-1] + float(np.linalg.norm(pts[0] - pts[-1]))
    for i in range(n):
        if j is None or (i and step[i - 1] > 25):
            j = int(np.argmin(((pts - xz[i]) ** 2).sum(axis=1)))  # start, or after a reset: look everywhere
        else:
            # Forwards from the last match as far as 80 m of track (however many points that is: where the
            # reference lap was slow its points are close together), wrapping at the line. A few points back too,
            # but never back across the line: the end of the lap lies on top of its start.
            reach = along[j] + 80
            ahead = int(np.searchsorted(along, reach)) - j if reach <= along[-1] else (m - j) + int(np.searchsorted(along, reach - total))
            idx = np.maximum(j + np.arange(-3, max(50, ahead)), 0) % m
            d2 = ((pts[idx] - xz[i]) ** 2).sum(axis=1)
            j = int(idx[np.nonzero(d2 <= d2.min() + 1e-4)[0][-1]])   # of equally near points take the furthest on:
                                                                    # a car standing still has many
        npos[i] = where[j]
    return npos


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("replay", type=Path)
    ap.add_argument("--dry-run", action="store_true", help="say what would be saved, save nothing")
    a = ap.parse_args()
    r = acreplay.read(a.replay)
    track, car, dt, n = r["track"], r["car"], r["dt"], r["n"]
    pts, where, source = reference(track, car, r)
    xz = r["pos"][:, [0, 2]]
    step = np.linalg.norm(np.diff(xz, axis=0), axis=1)
    spd = np.append(step, step[-1]) / dt * 3.6
    spd[spd > 600] = 0                                            # the jump of a reset is not a speed
    npos = positions(xz, step, pts, where)
    stamp = re.search(r"AC_(\d{6})-(\d{6})", a.replay.name)        # when the replay was saved: its last frame
    end = (datetime.datetime.strptime(stamp.group(1) + stamp.group(2), "%d%m%y%H%M%S").timestamp() if stamp
           else a.replay.stat().st_mtime)
    b, laps, last_ms, saved = laprec.LapBuilder(), 0, 0, []

    def keep(item, frame):
        when = end - (n - frame) * dt
        kind = "lap" if not item.get("partial") else f"{item['status']} ({item['how']})"
        saved.append(f"{kind}: {item['ms'] / 1000:.3f} s" + ("" if not item.get("partial") else f" over {item['len'] / item['n']:.0%} of the lap"))
        if not a.dry_run:
            laprec.save({**item, "source": "replay"}, car, track, when=when)

    clock = r["clock"]
    for i in range(n):
        if i and clock[i] < clock[i - 1] - 1000 and step[i - 1] <= 25:    # the lap clock restarted: the line
            laps, last_ms = laps + 1, int(clock[i - 1] + dt * 1000)
        s = laprec.Sample(spd=float(spd[i]), glat=0.0, glon=0.0, lap_ms=int(clock[i]), last_ms=last_ms, laps=laps,
                          thr=float(r["thr"][i]), brk=float(r["brk"][i]), rpm=float(r["rpm"][i]), steer=0.0,
                          gear=int(r["gear"][i]), npos=float(npos[i]), x=float(xz[i, 0]), z=float(xz[i, 1]))
        lap = b.feed(s)
        if lap:
            keep(lap, i)
        while b.partials:
            keep(b.partials.pop(0), i)
    b.cut("session")
    while b.partials:
        keep(b.partials.pop(0), n)
    print(f"{track} / {car}: {n * dt / 60:.1f} minutes, placed against {source}")
    for line in saved:
        print(("would save " if a.dry_run else "saved ") + line)
    if not saved:
        print("nothing worth keeping in this replay")


if __name__ == "__main__":
    main()
