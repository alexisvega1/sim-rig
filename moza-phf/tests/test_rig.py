"""Settings file, the live haptics loop and the control board API. Hardware is
the fake S12 box; the game is hand-built telemetry samples."""
import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from moza_phf import board, discover, rig, rigcfg, s12
from moza_phf.sim import FakeS12
from moza_phf.telemetry import Telemetry


@pytest.fixture(autouse=True)
def rig_home(tmp_path, monkeypatch):
    monkeypatch.setattr(rigcfg, "HOME", tmp_path)
    monkeypatch.setattr(rigcfg, "CONFIG", tmp_path / "config.json")
    monkeypatch.setattr(rigcfg, "STATE", tmp_path / "state")
    return tmp_path


class Recorder:
    """Stands in for S12Driver: keeps what the loop asked for."""
    def __init__(self):
        self.want = {}

    def show(self, want):
        self.want = dict(want)

    def flush(self):
        pass

    def stop_all(self):
        self.want = {}


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def moving(i, **kw):
    """A telemetry sample from a car that is actually running (values change every sample)."""
    return Telemetry(rpm=5000 + i, speed_ms=40 + i * 0.01, lap_ms=1000 + i * 20, **kw)


# ------------------------------------------------------------------ settings ---
def test_clean_fills_defaults_and_clamps():
    c = rigcfg.clean({"haptics": {"master": 9, "effects": {"abs": {"gain": -3, "freq": 500, "on": 0}, "bogus": {}}},
                      "leds": {"default": {"start": 70, "full": 60, "flash": "x"}, "cars": {"car_a": {"start": 80, "full": 97, "flash": 98.5}, "bad": 3}}})
    assert c["haptics"]["master"] == 2.0
    assert c["haptics"]["effects"]["abs"] == {"on": False, "gain": 0.0, "freq": 100.0}
    assert c["haptics"]["effects"]["tc"] == rigcfg.DEFAULTS["haptics"]["effects"]["tc"]      # untouched effects keep defaults
    assert "bogus" not in c["haptics"]["effects"] and "freq" not in c["haptics"]["effects"]["lockup"]
    d = c["leds"]["default"]
    assert d["start"] == 70 and d["full"] > d["start"] and d["flash"] >= d["full"]             # never an inverted window
    assert c["leds"]["cars"] == {"car_a": {"start": 80.0, "full": 97.0, "flash": 98.5}}
    assert rigcfg.clean(None) == rigcfg.clean({}) == rigcfg.clean("junk")


def test_save_load_and_per_car_window():
    assert rigcfg.load() == rigcfg.clean({})                                                   # no file yet
    cfg = rigcfg.load()
    cfg["leds"]["cars"]["ks_ferrari_f2004"] = {"start": 80, "full": 97, "flash": 98.5}
    rigcfg.save(cfg)
    back = rigcfg.load()
    assert rigcfg.led_window(back, "ks_ferrari_f2004") == {"start": 80.0, "full": 97.0, "flash": 98.5}
    assert rigcfg.led_window(back, "any_other_car") == {"start": 55.0, "full": 90.0, "flash": 92.0}
    rigcfg.CONFIG.write_text("{ not json")
    assert rigcfg.load() == rigcfg.clean({})                                                   # a broken file can't stop a drive


def test_state_files_expire():
    rigcfg.write_state("dash", {"car": "x"})
    assert rigcfg.read_state("dash")["car"] == "x"
    assert rigcfg.read_state("dash", max_age=-1) is None and rigcfg.read_state("missing") is None


# -------------------------------------------------------------- haptics loop ---
def test_effects_follow_settings():
    drv, clock = Recorder(), Clock()
    h = rig.Haptics(drv, ["brake", "throttle"], clock=clock)
    assert set(h.step(moving(0, abs_active=True, tc_active=True))) == {"abs", "tc", "engine"}
    assert drv.want[(2, 1)] == (30.0, 0.9) and drv.want[(1, 0)] == (45.0, 0.7)                 # brake/ABS, throttle/TC

    cfg = rigcfg.load()
    cfg["haptics"]["master"] = 0.5
    cfg["haptics"]["effects"]["tc"]["on"] = False
    cfg["haptics"]["effects"]["abs"].update(gain=0.4, freq=70)
    rigcfg.save(cfg)
    time.sleep(0.3)                                                                            # the loop re-reads within 0.25 s
    active = h.step(moving(1, abs_active=True, tc_active=True))
    assert "tc" not in active and drv.want[(2, 1)] == (70.0, 0.2)                              # 0.4 gain x 0.5 master

    brake_only = rig.Haptics(Recorder(), ["brake"], clock=clock)
    assert set(brake_only.step(moving(2, abs_active=True, tc_active=True))) == {"abs"}         # no motor, no effect


def test_lockup_instead_of_abs_and_gear_shift_kick():
    drv, clock = Recorder(), Clock()
    h = rig.Haptics(drv, ["brake", "throttle"], clock=clock)
    h.step(moving(0, lockup=0.5, brake=0.8, gear=3))
    assert drv.want[(2, 2)] == (40.0, pytest.approx(0.4))                                      # 20 + 40*0.5 Hz, 0.8 x 0.5
    assert "shift" in h.step(moving(1, gear=4))                                                # gear changed: kick
    clock.t += 0.2
    assert "shift" not in h.step(moving(2, gear=4))                                            # and it ends by itself


def test_quiet_when_paused_or_no_game():
    drv, clock = Recorder(), Clock()
    h = rig.Haptics(drv, ["brake", "throttle"], clock=clock)
    frozen = Telemetry(rpm=18543, speed_ms=86.8, lap_ms=4473, abs_active=True)                # AC streams this while paused
    assert h.step(frozen)                                                                      # first sight: plays
    clock.t += 0.6
    assert h.step(frozen) == {} and drv.want == {}                                             # nothing moved for 0.5 s: paused
    assert h.step(moving(5, abs_active=True))                                                  # resumes at once
    assert h.step(None) == {} and drv.want == {}                                               # no fresh telemetry


def test_test_buzz_plays_without_a_game_and_even_when_switched_off():
    cfg = rigcfg.load()
    cfg["haptics"]["effects"]["lockup"]["on"] = False
    rigcfg.save(cfg)
    board.request_test("abs")                                                                  # left over from an earlier run
    drv, clock = Recorder(), Clock()
    h = rig.Haptics(drv, ["brake", "throttle"], clock=clock)
    clock.t += 0.3
    assert h.step(None) == {}                                                                  # stale request is ignored
    board.request_test("lockup", 1.0)
    clock.t += 0.3
    assert h.step(None) == {"lockup": 0.8} and drv.want[(2, 2)] == (40, 0.8)
    clock.t += 1.2
    assert h.step(None) == {}                                                                  # one second, then stop
    with pytest.raises(ValueError):
        board.request_test("nope")


def test_engine_uses_the_dash_limiter():
    drv, clock = Recorder(), Clock()
    h = rig.Haptics(drv, ["throttle"], clock=clock)
    rigcfg.write_state("dash", {"maxrpm": 18800, "car": "ks_ferrari_f2004"})
    clock.t += 0.3
    h.step(Telemetry(rpm=9400, max_rpm=6000, speed_ms=50, lap_ms=1))
    freq, strength = drv.want[(1, 4)]
    assert freq == pytest.approx(55.0) and strength == pytest.approx(0.15)                     # half of 18800, not 6000


def test_loop_drives_the_fake_box():
    box = FakeS12(s12.USB)
    try:
        with discover.open_port(box.port) as ser:
            h = rig.Haptics(s12.S12Driver(ser, s12.USB), ["brake"])
            for i in range(8):
                h.step(moving(i, abs_active=True)); time.sleep(0.02)
            time.sleep(0.05)
            assert box.slots[(2, 1)][1:] == (30, round(0.9 * 0xFFFF))                          # 30 Hz at 90 %
            h.step(None); time.sleep(0.05)
            assert not box.slots                                                               # explicit stop went out
    finally:
        box.close()


# ---------------------------------------------------------------- board API ---
@pytest.fixture
def server(tmp_path, monkeypatch):
    sessions = tmp_path / "sessions"; sessions.mkdir()
    (sessions / "260915-233438.json").write_text(json.dumps({
        "track": "lilski_road_america", "players": [{"name": "x", "car": "ks_ferrari_f2004"}],
        "sessions": [{"laps": [{"lap": 0, "car": 0, "sectors": [74256, 39010, 33737], "time": 147003, "cuts": 0},
                               {"lap": 1, "car": 0, "sectors": [37846, 37039, 28841], "time": 103726, "cuts": 0},
                               {"lap": 1, "car": 1, "sectors": [1, 1, 1], "time": 3, "cuts": 0}]}]}))
    (sessions / "261111-181727.json").write_text(json.dumps({"track": "t", "players": [{"car": "c"}], "sessions": [{"laps": []}]}))
    (sessions / "junk.json").write_text("{")
    monkeypatch.setattr(board, "SESSIONS", sessions)
    monkeypatch.setattr(board, "AC_ROOT", tmp_path / "no-game")
    monkeypatch.setattr(board, "_laps_cache", None)
    srv = board.make_server(0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown(); srv.server_close()


def call(url, body=None, headers=None):
    req = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_board_serves_page_config_state_and_laps(server):
    code, page = call(server + "/")
    assert code == 200 and b"<title>MOZA Rig</title>" in page
    assert json.loads(call(server + "/api/config")[1]) == rigcfg.clean({})
    st = json.loads(call(server + "/api/state")[1])
    assert st["dash"] is None and st["effects"]["abs"] == {"pedal": "brake"} and st["defaults"] == rigcfg.DEFAULTS
    (s,) = json.loads(call(server + "/api/laps")[1])["sessions"]                               # empty and broken files skipped
    assert (s["car"], s["carName"], s["date"]) == ("ks_ferrari_f2004", "ks_ferrari_f2004", "2026-09-15T23:34:38")
    assert [l["ms"] for l in s["laps"]] == [147003, 103726]                                    # other cars' laps left out
    assert call(server + "/api/nope")[0] == 404


def test_board_saves_config_and_requests_tests(server):
    code, out = call(server + "/api/config", {"haptics": {"master": 1.5}, "leds": {"cars": {"car_a": {"start": 80, "full": 97, "flash": 98.5}}}})
    assert code == 200 and json.loads(out)["haptics"]["master"] == 1.5
    assert rigcfg.load()["leds"]["cars"]["car_a"]["start"] == 80
    code, out = call(server + "/api/test", {"effect": "tc"})
    assert code == 200 and json.loads((rigcfg.STATE / "test.json").read_text())["id"] == json.loads(out)["id"]
    assert call(server + "/api/test", {"effect": "rm -rf"})[0] == 400


def test_board_refuses_other_sites(server):
    before = rigcfg.load()
    assert call(server + "/api/config", {"haptics": {"master": 2}}, {"Origin": "https://evil.example"})[0] == 403
    assert call(server + "/api/test", {"effect": "abs"}, {"Origin": "https://evil.example"})[0] == 403
    assert call(server + "/api/config", {"haptics": {"master": 2}}, {"Host": "rebind.example"})[0] == 403
    assert rigcfg.load() == before and not (rigcfg.STATE / "test.json").exists()
    assert call(server + "/api/config", {"haptics": {"master": 2}}, {"Origin": server})[0] == 200   # the board's own page
