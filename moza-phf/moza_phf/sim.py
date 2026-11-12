"""Fake MOZA device on a pty, for tests. NOT a model of the real PHF box — the
device id, groups and ids here are invented to exercise the tools."""
from __future__ import annotations

import os
import select
import threading
import tty

from .protocol import Frame, checksum, split_frames, swap_nibbles

BOX, RD, WR, BUZZ_ID = 31, 45, 46, 3


class FakeBox:
    def __init__(self):
        self.master, self.slave = os.openpty()
        tty.setraw(self.slave); tty.setraw(self.master)
        self.port = os.ttyname(self.slave)
        self.store = {i: 0 for i in range(6)}
        self.buzz_events: list[int] = []
        self.received: list[bytes] = []
        self._stop = False
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()

    def _reply(self, grp, dev, payload):
        body = bytes([0x7E, len(payload), grp | 0x80, swap_nibbles(dev)]) + payload
        os.write(self.master, body + bytes([checksum(body)]))

    def _loop(self):
        buf = b""
        while not self._stop:
            if not select.select([self.master], [], [], 0.05)[0]:
                continue
            try:
                buf += os.read(self.master, 4096)
            except OSError:
                return
            for raw in split_frames(buf):
                self.received.append(raw)
                grp, dev, payload = raw[2], raw[3], raw[4:-1]
                cid, val = payload[0], payload[1:]
                if dev == 25 and grp in (35, 37) and 1 <= cid <= 9 and len(val) == 2:
                    self._reply(grp, dev, bytes([cid, 0x03, 0xE8]))
                elif dev == BOX and cid in self.store and len(val) == 2:
                    if grp == RD:
                        self._reply(grp, dev, bytes([cid]) + self.store[cid].to_bytes(2, "big"))
                    elif grp == WR:
                        self.store[cid] = int.from_bytes(val, "big")
                        if cid == BUZZ_ID and self.store[cid] > 1:
                            self.buzz_events.append(self.store[cid])
                        self._reply(grp, dev, payload)
            buf = buf[buf.rfind(b"\x7e", 0, len(buf)):] if not list(split_frames(buf)) else b""

    def close(self):
        self._stop = True
        self._t.join(1.0)        # macOS blocks close() on an fd another thread is reading
        os.close(self.master); os.close(self.slave)


class FakeS12:
    """Answers the documented S12 vibration command (see s12.py) in one envelope.
    It models the frame format only, not the firmware's timing or the motor."""

    def __init__(self, addressing: str = "usb"):
        from . import s12
        self.s12, self.addressing = s12, addressing
        self.master, self.slave = os.openpty()
        tty.setraw(self.slave); tty.setraw(self.master)
        self.port = os.ttyname(self.slave)
        self.slots: dict[tuple[int, int], tuple[int, int, int]] = {}   # live: (duration, freq, strength)
        self.log: list[tuple] = []                                     # ("set"|"stop", pedal, slot, ...)
        self._stop = False
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()

    def _loop(self):
        s12, buf = self.s12, b""
        ext = self.addressing == s12.EXT
        while not self._stop:
            if not select.select([self.master], [], [], 0.05)[0]:
                continue
            try:
                buf += os.read(self.master, 4096)
            except OSError:
                return
            frames = s12.unstuffed_frames(buf)
            if frames:
                buf = b""
            for fr in frames:
                p = fr[4:-1]
                if fr[2] != s12.GROUP or fr[3] != (s12.ADDR_EXT if ext else s12.ADDR_USB):
                    continue
                if ext:
                    if p[:1] != bytes([s12.EXT_ID]):
                        continue
                    p = p[1:]
                if len(p) != 11 or p[0] != 1:
                    continue                                           # truncated requests get no answer
                sub, key = p[1], (p[2], p[3])
                vals = tuple(int.from_bytes(p[k:k + 2], "big") for k in (5, 7, 9))
                if sub == 1 and p[4]:
                    self.slots[key] = vals
                    self.log.append(("set", *key, *vals))
                elif sub == 1:
                    self.slots.pop(key, None)
                    self.log.append(("stop", *key))
                elif sub == 2:
                    live = self.slots.get(key)
                    p = p[:4] + bytes([1 if live else 0]) + b"".join(v.to_bytes(2, "big") for v in live or (0, 0, 0))
                body = bytes([0x7E, len(p) + ext, s12.GROUP_REPLY,
                              s12.ADDR_EXT_REPLY if ext else s12.ADDR_USB_REPLY]) \
                    + (bytes([s12.EXT_ID]) if ext else b"") + p
                os.write(self.master, s12.stuff(body + bytes([s12.wire_checksum(body)])))

    def close(self):
        self._stop = True
        self._t.join(1.0)        # macOS blocks close() on an fd another thread is reading
        os.close(self.master); os.close(self.slave)


# ----------------------------------------------------------------- fake laps ---
def drive(profiles, start_u: float = 0.6, hz: float = 85.0):
    """Telemetry samples (laprec.Sample) from a pretend car on a pretend track,
    for tests and for trying the Track tab without the game. Not a physics model.

    The track is a wavy oval about 2.9 km round with four corners. `profiles` is
    one dict per lap after the out-lap: {"brake_early": {corner: metres},
    "min_speed": {corner: km/h change}} to make a lap worse in a chosen place.
    The car joins part-way round (an out-lap), then drives the laps in order.
    """
    import math
    from .laprec import Sample

    def pos(u):
        a = 2 * math.pi * u
        return 400 * math.cos(a) + 120 * math.cos(2 * a), 260 * math.sin(a) + 70 * math.sin(3 * a)

    steps = 4000
    pts = [pos(i / steps) for i in range(steps + 1)]
    seg = [math.dist(pts[i], pts[i + 1]) for i in range(steps)]
    length = sum(seg)
    apexes = {1: 0.13, 2: 0.38, 3: 0.62, 4: 0.87}                       # corner -> where its slowest point is
    slow = {1: 95.0, 2: 70.0, 3: 120.0, 4: 85.0}                        # km/h there on a clean lap

    def target(u, prof):
        v = 255.0
        for c, a in apexes.items():
            lo = slow[c] + prof.get("min_speed", {}).get(c, 0.0)
            early = prof.get("brake_early", {}).get(c, 0.0) / length     # as a fraction of the lap
            d = ((u - a + 0.5) % 1.0) - 0.5                              # signed distance to the apex
            width_in, width_out = 0.045 + early, 0.07
            w = width_in if d < 0 else width_out
            if abs(d) < w:
                v = min(v, lo + (255.0 - lo) * (abs(d) / w) ** 1.5)
        return v

    u, lap_ms, laps, last_ms, dt = start_u, 0.0, 0, 0, 1.0 / hz
    plan = [{}] + list(profiles)                                         # the out-lap, then the asked-for laps
    prev_v = target(u, plan[0])
    while laps < len(plan) or lap_ms < 300:                              # a little past the line, so the last lap closes
        prof = plan[min(laps, len(plan) - 1)]
        v = target(u, prof)
        acc = (v - prev_v) / dt                                          # km/h per second
        thr = 1.0 if acc > -2 else 0.0
        brk = min(1.0, -acc / 150.0) if acc < -12 else 0.0
        if brk:
            thr = 0.0
        x, z = pos(u)
        yield Sample(spd=v, glat=0.0, glon=acc / 35.3, lap_ms=int(lap_ms), last_ms=last_ms, laps=laps,
                     thr=thr, brk=brk, rpm=3000 + v * 20, steer=0.0, gear=min(7, 1 + int(v // 45)),
                     npos=u, x=x, z=z)
        prev_v = v
        u += (v / 3.6) * dt / length
        lap_ms += dt * 1000
        if u >= 1.0:                                                     # across the line
            u -= 1.0
            laps += 1
            last_ms, lap_ms = int(round(lap_ms)), 0.0
