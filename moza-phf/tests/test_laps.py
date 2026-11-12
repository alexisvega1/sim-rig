"""Lap recorder and lap analysis, on laps from the pretend car in sim.drive().
Says nothing about the real game's packets beyond their documented layout."""
import json
import struct
import threading
import urllib.request

import pytest

from moza_phf import board, laprec, lapsan, rigcfg, sim

SLOW_T2 = {"brake_early": {2: 40}, "min_speed": {2: -8}}


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(rigcfg, "HOME", tmp_path)
    monkeypatch.setattr(rigcfg, "STATE", tmp_path / "state")
    monkeypatch.setattr(laprec, "LAPS", tmp_path / "laps")
    lapsan._cache.clear()
    return tmp_path


def record(profiles, mangle=None):
    b, out = laprec.LapBuilder(), []
    for i, s in enumerate(sim.drive(profiles)):
        for s2 in (mangle(i, s) if mangle else [s]):
            lap = b.feed(s2)
            if lap:
                out.append(lap)
    return out


@pytest.fixture
def three_laps():
    laps = record([{}, SLOW_T2, {"min_speed": {4: 6}}])
    for i, lap in enumerate(laps):
        laprec.save(lap, "car_a", "track_x", when=1_794_440_000 + i * 100)
    return laps


# ------------------------------------------------------------------ recorder ---
def test_parse_reads_the_documented_offsets():
    d = bytearray(328)
    struct.pack_into("<f", d, 8, 212.5)                       # speed km/h
    struct.pack_into("<ff", d, 32, 1.5, -0.8)                 # lateral, longitudinal g
    struct.pack_into("<iiii", d, 40, 61234, 105446, 103726, 5)  # lap clock, last, best, lap count
    struct.pack_into("<fff", d, 56, 0.25, 0.75, 0.0)          # throttle, brake, clutch
    struct.pack_into("<ffi", d, 68, 17250.0, -12.0, 5)        # rpm, steer, gear (0 = R, 1 = N)
    struct.pack_into("<f", d, 308, 0.4321)                    # position round the lap
    struct.pack_into("<fff", d, 316, 101.0, 7.0, -202.0)      # x, y, z
    s = laprec.parse(bytes(d))
    assert (s.spd, s.lap_ms, s.last_ms, s.laps, s.gear) == (212.5, 61234, 105446, 5, 4)
    assert (s.thr, s.brk, s.rpm, s.steer) == (0.25, 0.75, 17250.0, -12.0)
    assert s.npos == pytest.approx(0.4321) and (s.x, s.z) == (101.0, -202.0) and (s.glat, s.glon) == (1.5, pytest.approx(-0.8))


def test_per_wheel_data_is_kept_when_the_packet_has_it():
    d = bytearray(328)
    struct.pack_into("<4f", d, 180, 3000.0, 3100.0, 5000.0, 5100.0)   # tyre loads
    struct.pack_into("<4f", d, 292, 0.11, 0.11, 0.10, 0.10)           # suspension heights
    d[21] = 1                                                         # ABS working
    s = laprec.parse(bytes(d))
    assert len(s.ex) == len(laprec.EXTRA) and s.ex[0] == 3000.0 and s.ex[4] == pytest.approx(0.11) and s.ex[16] == 1.0
    b, laps = laprec.LapBuilder(), []
    for smp in sim.drive([{}]):                                       # give every sample the same per-wheel values
        lap = b.feed(smp._replace(ex=s.ex))
        if lap:
            laps.append(lap)
    (lap,) = laps
    assert lap["ver"] == 2 and lap["load_rl"][100] == 5000 and lap["ride_fl"][100] == pytest.approx(0.11) and lap["abs"][5] == 1.0
    assert all(len(lap[k]) == laprec.N for k in laprec.EXTRA)
    laprec.save(lap, "car_a", "track_x", when=1_794_440_000)
    assert lapsan.analysis("track_x", "car_a")["lap"]["ms"] == lap["ms"]     # analysis is unaffected


def test_only_whole_laps_are_kept():
    laps = record([{}, SLOW_T2])
    assert len(laps) == 2                                       # the out-lap (joined half-way round) is dropped
    a, b = laps
    assert b["ms"] > a["ms"] + 500                              # the deliberately slow lap is slower
    for lap in laps:
        assert lap["n"] == laprec.N and all(len(lap[k]) == laprec.N for k in laprec.SERIES)
        assert all(x <= y for x, y in zip(lap["t"], lap["t"][1:])) and lap["t"][-1] <= lap["ms"]
        assert min(lap["spd"]) < 100 < max(lap["spd"]) and max(lap["brk"]) > 0.5


def test_pause_is_ignored_and_a_teleport_drops_the_lap():
    paused = record([{}, {}], mangle=lambda i, s: [s] * (40 if i == 5000 else 1))   # the game repeats one frame while paused
    assert len(paused) == 2 and abs(paused[0]["ms"] - paused[1]["ms"]) < 50

    def to_pits(i, s):                                          # mid-lap jump to another part of the track
        return [s._replace(npos=(s.npos + 0.4) % 1.0)] if 5000 <= i < 5010 else [s]
    assert len(record([{}, {}], mangle=to_pits)) < 2


def test_save_index_and_load(three_laps):
    idx = lapsan.index()
    assert [m["ms"] for m in idx] == [lap["ms"] for lap in three_laps]
    assert idx[0]["track"] == "track_x" and idx[0]["car"] == "car_a" and idx[0]["date"].startswith("20")
    assert lapsan.load(idx[0]["id"])["ms"] == three_laps[0]["ms"]
    assert lapsan.load("../../../etc/passwd") is None and lapsan.load("track_x/car_a/missing.json") is None
    assert laprec.track_id("ks_nordschleife", "touristenfahrten") == "ks_nordschleife-touristenfahrten"
    assert laprec.track_id("lilski_road_america", "") == laprec.track_id("lilski_road_america", "lilski_road_america") == "lilski_road_america"


# ------------------------------------------------------------------ analysis ---
def test_analysis_pins_the_loss_on_the_right_corner(three_laps):
    idx = lapsan.index()
    a = lapsan.analysis("track_x", "car_a", lap_id=idx[1]["id"])            # the lap made slow at turn 2
    assert a["lap"]["ms"] == three_laps[1]["ms"] and a["ref"]["ms"] == min(lap["ms"] for lap in three_laps)
    assert [c["n"] for c in a["corners"]] == [1, 2, 3, 4] and a["has_map"] and 2000 < a["length_m"] < 3000
    worst = max(a["corners"], key=lambda c: c["lost_ms"])
    assert worst["n"] == 2 and worst["lost_ms"] > 800 and worst["min"] < worst["best_min"] - 5 and worst["brake_later_m"] < -8
    lost = sum(c["lost_ms"] for c in a["corners"])
    assert abs(lost - (a["lap"]["ms"] - a["ideal_ms"])) < 120               # corner losses add up to the whole lap
    assert a["tips"][0]["title"] == "Turn 2" and "earlier" in a["tips"][0]["text"] and a["tips"][0]["gain_ms"] == worst["lost_ms"]
    assert a["delta"][-1] == pytest.approx(a["lap"]["ms"] - a["ref"]["ms"], abs=40)
    assert a["ideal_ms"] <= a["best"]["ms"] and abs(sum(m["ms"] for m in a["minis"]) - a["lap"]["ms"]) < 30
    p = a["pedals"]
    assert 0.5 < p["full"] < 0.95 and 0.05 < p["brake"] < 0.4 and p["full"] + p["brake"] + p["coast"] <= 1.0
    assert len(a["spd"]) == len(a["x"]) == len(a["dist"]) == a["n"] <= 500


def test_a_spin_is_called_a_spin():
    laps = record([{}, {"min_speed": {3: -105}}])                           # all but stopped at turn 3
    for i, lap in enumerate(laps):
        laprec.save(lap, "car_a", "track_x", when=1_794_440_000 + i * 100)
    a = lapsan.analysis("track_x", "car_a", lap_id=lapsan.index()[1]["id"], mph=True)
    tip = a["tips"][0]
    assert tip["title"] == "Turn 3" and tip["gain_ms"] >= 1500 and "spin" in tip["text"] and "mph" in tip["text"]
    c = a["corners"][2]
    assert c["gap_spd"] < c["gap_best_spd"] - 50 and c["from"] <= c["apex"] <= c["to"]


def test_best_lap_against_itself_and_units(three_laps):
    a = lapsan.analysis("track_x", "car_a", lap_id=lapsan.index()[0]["id"], ref_id=lapsan.index()[0]["id"])
    assert a["lap"]["id"] == a["ref"]["id"] and set(a["delta"]) == {0}
    assert all(c["lost_ms"] >= 0 for c in a["corners"])
    kmh = lapsan.analysis("track_x", "car_a", lap_id=lapsan.index()[1]["id"])["tips"][0]["text"]
    mph = lapsan.analysis("track_x", "car_a", lap_id=lapsan.index()[1]["id"], mph=True)["tips"][0]["text"]
    assert "km/h" in kmh and "mph" in mph and "km/h" not in mph
    assert lapsan.analysis("nowhere", "car_a") is None


def test_overlay_lays_the_laps_over_one_another(three_laps):
    a = lapsan.overlay("track_x", "car_a", limit=12, clean=False)
    assert a["count"] == 3 and a["left_out"] == 0 and a["best"]["ms"] == min(lap["ms"] for lap in three_laps)
    assert len(a["laps"]) == 3 and all(len(l["spd"]) == a["n"] == len(a["x"]) == len(a["sd_spd"]) for l in a["laps"])
    worst = max(a["corners"], key=lambda c: c["mean_lost_ms"])
    assert worst["n"] == 2 and worst["sd_ms"] > 100                         # the corner one lap was made slow in
    slow = a["laps"][1]                                                     # that lap: all its loss is at turn 2
    assert slow["lost_ms"][1] > 800 and max(a["sd_spd"]) > 2 and 0 <= min(a["brake"]) and max(a["brake"]) <= 1
    for k in range(len(a["corners"])):                                      # every corner has one quickest pass
        assert min(l["lost_ms"][k] for l in a["laps"]) == 0
    assert all(b is None or 0 <= b < a["n"] for l in a["laps"] for b in l["brake_at"])
    clean = lapsan.overlay("track_x", "car_a", limit=12, clean=True)
    assert clean["count"] >= 2 and clean["count"] + clean["left_out"] == 3
    assert lapsan.overlay("nowhere", "car_a") is None


def test_board_serves_recorded_laps(three_laps, tmp_path, monkeypatch):
    monkeypatch.setattr(board, "SESSIONS", tmp_path / "none")
    monkeypatch.setattr(board, "AC_ROOT", tmp_path / "none")
    monkeypatch.setattr(board, "_laps_cache", None)
    srv = board.make_server(0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        get = lambda path: json.loads(urllib.request.urlopen(base + path, timeout=5).read())
        laps = get("/api/rec/laps")["laps"]
        assert len(laps) == 3 and laps[0]["carName"] == "car_a"
        a = get(f"/api/rec/analysis?track=track_x&car=car_a&lap={laps[1]['id']}&units=mph")
        assert a["lap"]["ms"] == three_laps[1]["ms"] and a["tips"] and "mph" in a["tips"][0]["text"] and a["laps"][0]["cuts"] is None
        assert "error" in get("/api/rec/analysis?track=nope&car=car_a")
        o = get("/api/rec/overlay?track=track_x&car=car_a&laps=6&all=1")
        assert o["count"] == 3 and o["carName"] == "car_a" and "error" in get("/api/rec/overlay?track=nope&car=car_a")
        assert get("/api/state")["recorder"] is None
    finally:
        srv.shutdown(); srv.server_close()
