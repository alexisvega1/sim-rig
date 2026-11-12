"""Shared settings for the rig: pedal haptics and rev lights.

One JSON file, edited by the control board (`moza-phf board`) and re-read by the
haptics driver and the wheel dashboard bridge whenever it changes, so a slider
takes effect without restarting anything. Anything missing or out of range falls
back to DEFAULTS, so a hand-edited or half-written file can't break a session.
"""
from __future__ import annotations

import copy
import json
import os
import time
from pathlib import Path

HOME = Path(os.environ.get("MOZA_RIG_HOME", "~/dev/moza-rig")).expanduser()
CONFIG = HOME / "config.json"
STATE = HOME / "state"          # small status files each process writes for the board

# effect -> (pedal, S12 slot). The strength a pedal feels is gain x master.
EFFECTS = {
    "abs":    ("brake", 1),
    "lockup": ("brake", 2),
    "tc":     ("throttle", 0),
    "slip":   ("throttle", 7),
    "engine": ("throttle", 4),
    "shift":  ("throttle", 6),
}

DEFAULTS = {
    "haptics": {
        "master": 1.0,
        "effects": {
            "abs":    {"on": True, "gain": 0.9, "freq": 30},
            "lockup": {"on": True, "gain": 0.8},
            "tc":     {"on": True, "gain": 0.7, "freq": 45},
            "slip":   {"on": True, "gain": 0.6},
            "engine": {"on": True, "gain": 0.3},
            "shift":  {"on": True, "gain": 1.0, "freq": 60},
        },
    },
    # percent of the car's rev limiter: lights start filling, bar is full, bar flashes
    "leds": {
        "default": {"start": 55, "full": 90, "flash": 92},
        "cars": {},
    },
}


def _num(v, lo, hi, fallback):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return fallback
    return fallback if v != v else float(max(lo, min(hi, v)))


def _window(raw, base):
    raw = raw if isinstance(raw, dict) else {}
    start = _num(raw.get("start"), 0, 98, base["start"])
    full = _num(raw.get("full"), start + 1, 100, max(base["full"], start + 1))
    flash = _num(raw.get("flash"), full, 101, max(base["flash"], full))    # > 100 = never flash
    return {"start": start, "full": full, "flash": flash}


def clean(raw) -> dict:
    """A complete, in-range settings dict from whatever was loaded or posted."""
    raw = raw if isinstance(raw, dict) else {}
    out = copy.deepcopy(DEFAULTS)
    h = raw.get("haptics") if isinstance(raw.get("haptics"), dict) else {}
    out["haptics"]["master"] = _num(h.get("master"), 0, 2, 1.0)
    fx = h.get("effects") if isinstance(h.get("effects"), dict) else {}
    for name, base in DEFAULTS["haptics"]["effects"].items():
        e = fx.get(name) if isinstance(fx.get(name), dict) else {}
        o = out["haptics"]["effects"][name]
        o["on"] = bool(e.get("on", base["on"]))
        o["gain"] = _num(e.get("gain"), 0, 1, base["gain"])
        if "freq" in base:
            o["freq"] = _num(e.get("freq"), 10, 100, base["freq"])
    leds = raw.get("leds") if isinstance(raw.get("leds"), dict) else {}
    out["leds"]["default"] = _window(leds.get("default"), DEFAULTS["leds"]["default"])
    cars = leds.get("cars") if isinstance(leds.get("cars"), dict) else {}
    out["leds"]["cars"] = {str(k)[:80]: _window(v, out["leds"]["default"])
                           for k, v in cars.items() if isinstance(v, dict)}
    return out


def load() -> dict:
    try:
        return clean(json.loads(CONFIG.read_text()))
    except (OSError, ValueError):
        return clean({})


def save(cfg) -> dict:
    cfg = clean(cfg)
    HOME.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=1))
    os.replace(tmp, CONFIG)                       # readers never see a half-written file
    return cfg


class Live:
    """load(), but only re-reads the file when it changed (checked a few times a second)."""

    def __init__(self):
        self._at = 0.0
        self._mtime = None
        self.cfg = load()

    def get(self) -> dict:
        now = time.monotonic()
        if now - self._at > 0.25:
            self._at = now
            try:
                m = CONFIG.stat().st_mtime_ns
            except OSError:
                m = None
            if m != self._mtime:
                self._mtime = m
                self.cfg = load()
        return self.cfg


def led_window(cfg: dict, car: str) -> dict:
    return cfg["leds"]["cars"].get(car) or cfg["leds"]["default"]


def write_state(name: str, data: dict) -> None:
    try:
        STATE.mkdir(parents=True, exist_ok=True)
        tmp = STATE / f"{name}.{os.getpid()}.tmp"
        tmp.write_text(json.dumps({**data, "at": time.time()}))
        os.replace(tmp, STATE / f"{name}.json")
    except OSError:
        pass                                      # status is best-effort; never stop a drive for it


def read_state(name: str, max_age: float = 3.0) -> dict | None:
    try:
        d = json.loads((STATE / f"{name}.json").read_text())
    except (OSError, ValueError):
        return None
    return d if time.time() - d.get("at", 0) <= max_age else None
