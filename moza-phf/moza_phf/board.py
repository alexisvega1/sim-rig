"""Control board: a local web page for the rig.

    moza-phf board            ->  http://127.0.0.1:8765

Four tabs: pedal haptics (strength, frequency, on/off and a test buzz per
effect), rev lights (the default window and per-car overrides), lap times drawn
from Content Manager's saved sessions, and a track map with lap-against-lap
analysis drawn from laps this process records itself (laprec) while it runs.
It only edits the settings file
(rigcfg) and drops test requests; the haptics driver and the wheel dashboard
bridge pick those up by themselves, so nothing here touches a serial port.

Listens on 127.0.0.1 only and refuses writes that come from another web page.
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from urllib.parse import parse_qs
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import laprec, lapsan, rigcfg

_BOTTLE = Path("~/Library/Application Support/CrossOver/Bottles/Steam(try3232)/drive_c").expanduser()
SESSIONS = Path(os.environ.get("MOZA_RIG_SESSIONS", _BOTTLE / "content-manager/Data/Progress/Sessions"))
AC_ROOT = Path(os.environ.get("MOZA_RIG_AC", _BOTTLE / "Program Files (x86)/Steam/steamapps/common/assettocorsa"))
PAGE = Path(__file__).with_name("board.html")

_names: dict[tuple[str, str], str] = {}
_laps_cache: tuple[tuple, dict] | None = None


def ui_name(kind: str, ident: str) -> str:
    """The game's display name for a car or track id, or the id if it has none."""
    key = (kind, ident)
    if key not in _names:
        if kind == "car":
            paths = [AC_ROOT / "content/cars" / ident / "ui/ui_car.json"]
        else:
            track, _, layout = ident.partition("-")
            base = AC_ROOT / "content/tracks" / track / "ui"
            paths = ([base / layout / "ui_track.json"] if layout else []) + [base / "ui_track.json"]
        name = ident
        for p in paths:
            try:
                text = p.read_text(encoding="utf-8-sig", errors="replace")
            except OSError:
                continue
            m = re.search(r'"name"\s*:\s*"((?:[^"\\]|\\.)*)"', text)   # these files are often not strict JSON
            if m and m.group(1).strip():
                name = m.group(1).strip()
                break
        _names[key] = name
    return _names[key]


def laps() -> dict:
    """Every lap Content Manager has saved, oldest session first."""
    global _laps_cache
    files = sorted(SESSIONS.glob("*.json"))
    stamp = (len(files), max((f.stat().st_mtime_ns for f in files), default=0))
    if _laps_cache and _laps_cache[0] == stamp:
        return _laps_cache[1]
    sessions = []
    for f in files:
        m = re.match(r"(\d\d)(\d\d)(\d\d)-(\d\d)(\d\d)(\d\d)", f.name)
        try:
            d = json.loads(f.read_text(encoding="utf-8", errors="replace"))
            car, track = d["players"][0]["car"], d["track"]
        except (OSError, ValueError, KeyError, IndexError, TypeError):
            continue
        rows = []
        for s in d.get("sessions") or []:
            for lap in s.get("laps") or []:
                if lap.get("car", 0) != 0 or not isinstance(lap.get("time"), (int, float)):
                    continue                                  # car 0 is the player
                rows.append({"ms": int(lap["time"]), "sectors": [int(x) for x in lap.get("sectors") or []],
                             "cuts": int(lap.get("cuts") or 0)})
        if rows and m:
            y, mo, da, h, mi, se = m.groups()
            sessions.append({"id": f.stem, "date": f"20{y}-{mo}-{da}T{h}:{mi}:{se}",
                             "car": car, "carName": ui_name("car", car),
                             "track": track, "trackName": ui_name("track", track), "laps": rows})
    out = {"sessions": sessions}
    _laps_cache = (stamp, out)
    return out


def state() -> dict:
    dash = rigcfg.read_state("dash")
    if dash and dash.get("car"):
        dash["carName"] = ui_name("car", dash["car"])
    return {"dash": dash, "haptics": rigcfg.read_state("haptics"), "recorder": rigcfg.read_state("recorder", 6.0),
            "effects": {k: {"pedal": p} for k, (p, _) in rigcfg.EFFECTS.items()},
            "defaults": rigcfg.DEFAULTS}


def recorded() -> dict:
    laps = lapsan.index()
    for m in laps:
        m["carName"], m["trackName"] = ui_name("car", m["car"]), ui_name("track", m["track"])
    return {"laps": laps}


def track_report(query: dict) -> dict | None:
    one = lambda k: (query.get(k) or [None])[0]
    a = lapsan.analysis(one("track") or "", one("car") or "", one("lap"), one("ref"), mph=one("units") == "mph")
    if a:
        a["carName"], a["trackName"] = ui_name("car", a["lap"]["car"]), ui_name("track", a["lap"]["track"])
        cuts = {}                                              # Content Manager knows which laps had cuts
        for s in laps()["sessions"]:
            if s["car"] == a["lap"]["car"] and s["track"] == a["lap"]["track"]:
                for lap in s["laps"]:
                    cuts[lap["ms"]] = lap["cuts"]
        for m in a["laps"]:
            m["cuts"] = cuts.get(m["ms"])
    return a


def overlay_report(query: dict) -> dict | None:
    one = lambda k: (query.get(k) or [None])[0]
    try:
        limit = max(2, min(40, int(one("laps") or 12)))
    except ValueError:
        limit = 12
    a = lapsan.overlay(one("track") or "", one("car") or "", limit, clean=one("all") != "1")
    if a:
        a["carName"], a["trackName"] = ui_name("car", a["car"]), ui_name("track", a["track"])
    return a


def request_test(effect: str, seconds: float = 1.0) -> dict:
    if effect not in rigcfg.EFFECTS:
        raise ValueError(f"unknown effect {effect!r}")
    req = {"id": uuid.uuid4().hex, "effect": effect, "seconds": max(0.2, min(5.0, float(seconds)))}
    rigcfg.write_state("test", req)
    return req


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj).encode())

    def _local(self) -> bool:
        """True for requests addressed to this machine by a page this server served.
        Stops another site's script (or a rebinding hostname) from driving the pedals."""
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
        if host not in ("127.0.0.1", "localhost"):
            return False
        origin = self.headers.get("Origin")
        return origin is None or re.fullmatch(r"http://(127\.0\.0\.1|localhost)(:\d+)?", origin) is not None

    def do_GET(self):
        path, _, qs = self.path.partition("?")
        if not self._local():
            return self._json({"error": "local requests only"}, 403)
        if path in ("/", "/index.html"):
            try:
                return self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            except OSError:
                return self._send(500, b"board.html is missing next to board.py", "text/plain")
        if path == "/api/config":
            return self._json(rigcfg.load())
        if path == "/api/state":
            return self._json(state())
        if path == "/api/laps":
            return self._json(laps())
        if path == "/api/rec/laps":
            return self._json(recorded())
        if path == "/api/rec/analysis":
            return self._json(track_report(parse_qs(qs)) or {"error": "no recorded laps for that car and track"})
        if path == "/api/rec/overlay":
            a = overlay_report(parse_qs(qs))
            return self._json(a or {"error": "No recorded laps for that car on that track yet."})
        self._json({"error": "not found"}, 404)

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if not self._local():
            return self._json({"error": "local requests only"}, 403)
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}") if 0 < n < 200_000 else {}
        except ValueError:
            return self._json({"error": "bad JSON"}, 400)
        if path == "/api/config":
            return self._json(rigcfg.save(body))
        if path == "/api/test":
            try:
                return self._json(request_test(str(body.get("effect")), body.get("seconds", 1.0)))
            except (ValueError, TypeError) as e:
                return self._json({"error": str(e)}, 400)
        self._json({"error": "not found"}, 404)


def make_server(port: int = 8765) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def serve(port: int = 8765, record: bool = True) -> None:
    srv = make_server(port)
    print(f"control board: http://127.0.0.1:{srv.server_address[1]}", flush=True)
    if record:
        laprec.Recorder().start()                              # records laps for the Track tab while the board runs
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
