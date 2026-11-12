"""Telemetry -> per-pedal (frequency, intensity).

Mirrors the Pit House effect list: ABS, traction control, engine RPM, wheel
lock-up, wheel slip, gear shift. Each effect proposes a pulse for one pedal;
the strongest intensity wins per pedal (simple, and what you want for a
single 100 Hz motor — mixing frequencies on one motor just makes mud).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from .telemetry import Telemetry


@dataclass
class Pulse:
    freq_hz: float
    intensity: float  # 0..1


@dataclass
class EffectConfig:
    abs_freq: float = 30.0
    abs_gain: float = 0.9
    tc_freq: float = 45.0
    tc_gain: float = 0.7
    lockup_gain: float = 0.8          # freq scales with slip, 20..60 Hz
    slip_gain: float = 0.6
    rpm_gain: float = 0.3             # engine vibration on throttle: 0..100 Hz with rpm
    rpm_min_frac: float = 0.0
    shift_freq: float = 60.0
    shift_gain: float = 1.0
    shift_ms: float = 80.0
    pedals: dict[str, bool] = field(default_factory=lambda: {"throttle": True, "brake": True, "clutch": False})


class EffectMixer:
    def __init__(self, cfg: EffectConfig | None = None):
        self.cfg = cfg or EffectConfig()
        self._gear = None
        self._shift_until = 0.0

    def mix(self, t: Telemetry, now: float | None = None) -> dict[str, Pulse]:
        now = time.monotonic() if now is None else now
        c = self.cfg
        cand: dict[str, list[Pulse]] = {"throttle": [], "brake": [], "clutch": []}

        if t.abs_active:
            cand["brake"].append(Pulse(c.abs_freq, c.abs_gain))
        if t.lockup > 0.05 and not t.abs_active:
            cand["brake"].append(Pulse(20 + 40 * t.lockup, c.lockup_gain * t.lockup))

        if t.tc_active:
            cand["throttle"].append(Pulse(c.tc_freq, c.tc_gain))
        if t.wheel_slip > 0.05 and t.throttle > 0.2:
            cand["throttle"].append(Pulse(25 + 50 * t.wheel_slip, c.slip_gain * t.wheel_slip))
        if c.rpm_gain > 0 and t.rpm_frac > c.rpm_min_frac:
            cand["throttle"].append(Pulse(100 * t.rpm_frac, c.rpm_gain * t.rpm_frac))

        if self._gear is not None and t.gear != self._gear:
            self._shift_until = now + c.shift_ms / 1000
        self._gear = t.gear
        if now < self._shift_until:
            for p in ("throttle", "clutch"):
                cand[p].append(Pulse(c.shift_freq, c.shift_gain))

        out = {}
        for pedal, pulses in cand.items():
            if not c.pedals.get(pedal, False):
                continue
            out[pedal] = max(pulses, key=lambda p: p.intensity) if pulses else Pulse(0.0, 0.0)
        return out
