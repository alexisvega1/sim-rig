"""Analysis of recorded laps (see laprec): the numbers a race engineer looks at.

Everything works on laps resampled to the same N steps of track position, so
comparing two laps is index-for-index:

  delta        time gained/lost against another lap at every point of the track
  corners      found from the speed trace; braking point, minimum speed, exit speed
  best pass    the quickest you have ever taken each corner, on any lap
  mini-sectors 25 equal slices; their best times add up to an "ideal lap"
  pedals       share of the lap at full throttle, on the brakes, and coasting
  suggestions  the biggest losses turned into plain sentences

Pure Python, no numpy: a lap is 2000 points and a session a few dozen laps.
"""
from __future__ import annotations

import json
import math
import statistics
from pathlib import Path

from . import laprec

MINIS = 25
_cache: dict[str, tuple[int, dict]] = {}


# ------------------------------------------------------------------- files ---
def index() -> list[dict]:
    """Every recorded lap, oldest first, from the file names alone."""
    out = []
    for p in sorted(laprec.LAPS.glob("*/*/*.json"), key=lambda p: p.name):
        stamp, _, ms = p.stem.partition("_")
        if len(stamp) != 13 or not ms.isdigit():
            continue
        y, mo, da, h, mi, se = stamp[0:2], stamp[2:4], stamp[4:6], stamp[7:9], stamp[9:11], stamp[11:13]
        out.append({"id": f"{p.parent.parent.name}/{p.parent.name}/{p.name}", "track": p.parent.parent.name,
                    "car": p.parent.name, "date": f"20{y}-{mo}-{da}T{h}:{mi}:{se}", "ms": int(ms)})
    return out


def load(lap_id: str) -> dict | None:
    p = (laprec.LAPS / lap_id).resolve()
    if laprec.LAPS.resolve() not in p.parents:                 # ids come from the browser
        return None
    try:
        m = p.stat().st_mtime_ns
        if lap_id in _cache and _cache[lap_id][0] == m:
            return _cache[lap_id][1]
        lap = json.loads(p.read_text())
    except (OSError, ValueError):
        return None
    if lap.get("n") and all(len(lap.get(k, ())) == lap["n"] for k in laprec.SERIES):
        _cache[lap_id] = (m, lap)
        return lap
    return None


# ------------------------------------------------------------------ pieces ---
def distance(lap: dict) -> list[float]:
    """Metres from the timing line at each step. From position where the game
    gives one, else by adding up speed x time."""
    n, x, z = lap["n"], lap["x"], lap["z"]
    d = [0.0] * n
    if max(x) - min(x) > 5 or max(z) - min(z) > 5:
        for i in range(1, n):
            d[i] = d[i - 1] + math.hypot(x[i] - x[i - 1], z[i] - z[i - 1])
    else:
        t, v = lap["t"], lap["spd"]
        for i in range(1, n):
            d[i] = d[i - 1] + (v[i] + v[i - 1]) / 7.2 * (t[i] - t[i - 1]) / 1000.0      # km/h -> m/s, averaged
    return d


def _smooth(a: list[float], w: int) -> list[float]:
    n, half, out, run = len(a), w // 2, [], 0.0
    pre = [0.0]
    for v in a:
        run += v; pre.append(run)
    for i in range(n):
        lo, hi = max(0, i - half), min(n, i + half + 1)
        out.append((pre[hi] - pre[lo]) / (hi - lo))
    return out


def corners(ref: dict, dist: list[float]) -> list[dict]:
    """Corners of the track, read off one lap's speed trace: each is a dip of at
    least 12 km/h (or 6 % of top speed). For each: `entry` where the car starts
    slowing, `apex` its slowest point, `exit` 100 m later (where exit speed is
    read), and a segment [`from`, `to`) that runs to the next corner's entry.
    The segments tile the lap, so a slow exit is charged to the corner that
    caused it, straight included, and the losses add up to the whole lap."""
    n = ref["n"]
    v = _smooth(ref["spd"], 9)
    need = max(12.0, 0.06 * max(v))
    win = max(8, n // 80)
    dips = [i for i in range(win, n - win) if v[i] == min(v[i - win:i + win + 1]) and v[i] < v[i - 1]]
    kept = []
    for k, i in enumerate(dips):                                # a dip counts if speed rises `need` on both sides
        lo = dips[k - 1] if k else 0
        hi = dips[k + 1] if k + 1 < len(dips) else n - 1
        if min(max(v[lo:i + 1]), max(v[i:hi + 1])) - v[i] >= need:
            kept.append(i)
    out = []
    for k, apex in enumerate(kept):
        floor = kept[k - 1] if k else 0
        i = apex                                                 # walk back up the slope to where slowing began
        while i > floor and v[i - 1] >= v[i]:
            i -= 1
        j = apex
        while j < n - 1 and dist[j] < dist[apex] + 100.0:
            j += 1
        out.append({"n": k + 1, "entry": i, "apex": apex, "exit": j})
    for k, c in enumerate(out):
        c["from"] = 0 if k == 0 else c["entry"]
        c["to"] = out[k + 1]["entry"] if k + 1 < len(out) else n - 1
        c["exit"] = min(c["exit"], c["to"])
    return out


def _brake_point(lap: dict, c: dict) -> int | None:
    lo = max(0, c["entry"] - lap["n"] // 25)
    for i in range(lo, c["apex"] + 1):
        if lap["brk"][i] > 0.1:
            return i
    return None


def _seg_ms(lap: dict, a: int, b: int) -> float:
    t, n = lap["t"], lap["n"]
    return (lap["ms"] if b >= n - 1 else t[b]) - t[a]


def corner_pass(lap: dict, c: dict, dist: list[float]) -> dict:
    seg = range(c["entry"], c["exit"] + 1)
    bp = _brake_point(lap, c)
    return {"ms": _seg_ms(lap, c["from"], c["to"]), "min": min(lap["spd"][i] for i in seg),
            "exit": lap["spd"][c["exit"]], "brake_m": None if bp is None else dist[bp]}


def mini_times(lap: dict, k: int = MINIS) -> list[float]:
    n = lap["n"]
    edges = [round(i * n / k) for i in range(k + 1)]
    return [_seg_ms(lap, edges[i], min(n - 1, edges[i + 1])) if i < k - 1
            else lap["ms"] - lap["t"][edges[i]] for i in range(k)]


def pedals(lap: dict) -> dict:
    t, n, ms = lap["t"], lap["n"], lap["ms"]
    full = brake = coast = 0.0
    for i in range(n):
        dt = (t[i + 1] if i + 1 < n else ms) - t[i]
        if lap["brk"][i] > 0.05:
            brake += dt
        elif lap["thr"][i] > 0.95:
            full += dt
        elif lap["thr"][i] < 0.05:
            coast += dt
    return {"full": full / ms, "brake": brake / ms, "coast": coast / ms, "coast_ms": coast}


# ---------------------------------------------------------------- the report ---
def analysis(track: str, car: str, lap_id: str | None = None, ref_id: str | None = None, limit: int = 60,
             mph: bool = False) -> dict | None:
    """Everything the Track tab draws for one lap against a reference lap."""
    mine = [m for m in index() if m["track"] == track and m["car"] == car]
    if not mine:
        return None
    best_meta = min(mine, key=lambda m: m["ms"])
    by_id = {m["id"]: m for m in mine}
    lap_meta = by_id.get(lap_id) or mine[-1]
    ref_meta = by_id.get(ref_id) or best_meta
    lap, ref = load(lap_meta["id"]), load(ref_meta["id"])
    if not lap or not ref or lap["n"] != ref["n"]:
        return None
    n = lap["n"]
    # the laps used for "best ever" figures: recent ones, plus the two being shown
    pool_meta = {m["id"]: m for m in mine[-limit:]}
    pool_meta[lap_meta["id"]] = lap_meta; pool_meta[ref_meta["id"]] = ref_meta; pool_meta[best_meta["id"]] = best_meta
    pool = [(m, l) for m in pool_meta.values() if (l := load(m["id"])) and l["n"] == n]

    dist = distance(ref)
    cs = corners(ref, dist)
    rows = []
    for c in cs:
        passes = [(corner_pass(l, c, dist), m, l) for m, l in pool]
        bp, bm, bl = min(passes, key=lambda p: p[0]["ms"])
        me = corner_pass(lap, c, dist)
        # where in this stretch the lap was furthest below the quickest pass's speed
        worst = max(range(c["from"], c["to"] + 1), key=lambda i: bl["spd"][i] - lap["spd"][i])
        rows.append({"n": c["n"], "entry": c["entry"], "apex": c["apex"], "exit": c["exit"], "from": c["from"], "to": c["to"],
                     "at_m": round(dist[c["apex"]]),
                     "lost_ms": round(me["ms"] - bp["ms"]), "min": round(me["min"], 1), "best_min": round(bp["min"], 1),
                     "exit_spd": round(me["exit"], 1), "best_exit": round(bp["exit"], 1),
                     "brake_later_m": None if me["brake_m"] is None or bp["brake_m"] is None else round(me["brake_m"] - bp["brake_m"]),
                     "best_date": bm["date"], "best_is_this": bm["id"] == lap_meta["id"],
                     "gap_at_m": round(dist[worst]), "gap_spd": round(lap["spd"][worst], 1), "gap_best_spd": round(bl["spd"][worst], 1)})

    minis = [mini_times(l) for _, l in pool]
    best_mini = [min(col) for col in zip(*minis)]
    ideal = round(sum(best_mini))
    flying = [mt for (m, _), mt in zip(pool, minis) if m["ms"] <= best_meta["ms"] * 1.07]
    spread = [round(statistics.pstdev(col)) if len(col) > 1 else 0 for col in zip(*flying)]
    my_mini = mini_times(lap)
    mini_rows = [{"k": k + 1, "from_m": round(dist[round(k * n / MINIS)]), "ms": round(my_mini[k]), "best_ms": round(best_mini[k]),
                  "lost_ms": round(my_mini[k] - best_mini[k]), "spread_ms": spread[k]} for k in range(MINIS)]

    ped = pedals(lap)
    delta = [lap["t"][i] - ref["t"][i] for i in range(n)]
    step = max(1, n // 500)                                       # 500 points is plenty to draw
    pick = lambda a: a[::step]
    out = {
        "lap": lap_meta, "ref": ref_meta, "best": best_meta, "laps": mine, "n": len(pick(delta)), "step": step,
        "length_m": round(dist[-1]), "has_map": max(ref["x"]) - min(ref["x"]) > 5 or max(ref["z"]) - min(ref["z"]) > 5,
        "dist": [round(v) for v in pick(dist)],
        "x": pick(ref["x"]), "z": pick(ref["z"]),
        "spd": pick(lap["spd"]), "ref_spd": pick(ref["spd"]),
        "thr": pick(lap["thr"]), "ref_thr": pick(ref["thr"]), "brk": pick(lap["brk"]), "ref_brk": pick(ref["brk"]),
        "gear": pick(lap["gear"]), "delta": pick(delta),
        "glat": pick(lap["glat"]), "glon": pick(lap["glon"]),
        "corners": rows, "minis": mini_rows, "ideal_ms": ideal, "pool": len(pool),
        "top": round(max(lap["spd"]), 1), "slowest": round(min(lap["spd"]), 1),
        "pedals": {k: round(v, 4) if k != "coast_ms" else round(v) for k, v in ped.items()},
    }
    out["tips"] = tips(out, mph)
    return out


def tips(a: dict, mph: bool = False) -> list[dict]:
    """The biggest losses on this lap, as sentences. Each carries the time it is worth."""
    out = []
    k, unit = (0.621371, "mph") if mph else (1.0, "km/h")
    for c in sorted(a["corners"], key=lambda c: -c["lost_ms"])[:4]:
        if c["lost_ms"] < 40 or c["best_is_this"]:
            continue
        dv, de, db = (c["min"] - c["best_min"]) * k, (c["exit_spd"] - c["best_exit"]) * k, c["brake_later_m"]
        if c["lost_ms"] >= 1500:
            # too much for a line or braking error: the car all but stopped somewhere it should have been quick
            why = (f"A big loss. About {c['gap_at_m']:,} m from the line you were down to {c['gap_spd'] * k:.0f} {unit} "
                   f"where your quickest pass was doing {c['gap_best_spd'] * k:.0f}, which looks like a spin, an off "
                   f"or a long lift, not a line problem.")
            if db is not None and 15 <= db <= 120:              # further out than that is the incident itself, not a braking point
                why += f" You also braked {db} m later than on that pass, so it was probably an overshoot."
        elif db is not None and db >= 12 and dv <= -4:
            why = (f"You braked {db} m later than on your quickest pass and had to slow to {c['min'] * k:.0f} {unit} "
                   f"against {c['best_min'] * k:.0f}. Braking that late cost the corner: brake a little earlier and keep the speed up.")
        elif db is not None and db <= -8 and dv <= -2:
            why = (f"You braked {-db} m earlier than on your quickest pass and still took {abs(dv):.0f} {unit} less "
                   f"through the slowest point. Brake later and carry the speed in.")
        elif db is not None and db <= -8:
            why = f"You braked {-db} m earlier than on your quickest pass. Move the braking point back towards it."
        elif dv <= -3:
            why = (f"Your minimum speed was {c['min'] * k:.0f} {unit} against {c['best_min'] * k:.0f} on your quickest pass. "
                   f"Ease off the brake sooner and let the car roll through.")
        elif dv >= 3 and de <= -2:
            why = (f"You carried {dv:.0f} {unit} more in but came out {abs(de):.0f} {unit} slower: too much entry speed "
                   f"cost you the exit. Slow it slightly more and get on the power earlier.")
        elif de <= -3:
            why = (f"Entry matched your quickest pass, but you were {abs(de):.0f} {unit} down at the exit. "
                   f"Get to full throttle earlier.")
        else:
            why = "No single cause: small losses through the whole corner compared with your quickest pass."
        out.append({"title": f"Turn {c['n']}", "at_m": c["at_m"], "gain_ms": c["lost_ms"], "text": why})
    p = a["pedals"]
    if p["coast"] > 0.05:
        out.append({"title": "Coasting", "gain_ms": None,
                    "text": f"You were on neither pedal for {p['coast'] * 100:.0f}% of the lap ({p['coast_ms'] / 1000:.1f} s). "
                            f"Quick laps are almost always on one pedal or the other: brake a touch later, "
                            f"or pick the throttle up as you release the brake."})
    wobble = max(a["minis"], key=lambda m: m["spread_ms"])
    if a["pool"] >= 4 and wobble["spread_ms"] >= 80:
        out.append({"title": "Consistency", "gain_ms": None,
                    "text": f"Your times vary most around {wobble['from_m']:,} m from the line "
                            f"(about ±{wobble['spread_ms'] / 1000:.2f} s lap to lap). That stretch is worth practising on its own."})
    return out


# ------------------------------------------------------------- all the laps ---
def overlay(track: str, car: str, limit: int = 12, clean: bool = True) -> dict | None:
    """Several laps laid over one another: how fast, where the brakes go on, and how
    much each of those varies from lap to lap. `clean` leaves out laps more than 7 %
    off the best (spins and traffic), unless that would leave fewer than two."""
    mine = [m for m in index() if m["track"] == track and m["car"] == car]
    if not mine:
        return None
    best_meta = min(mine, key=lambda m: m["ms"])
    ref = load(best_meta["id"])
    if not ref:
        return None
    n = ref["n"]
    metas = {m["id"]: m for m in mine[-max(2, limit):]}
    metas[best_meta["id"]] = best_meta
    pool = [(m, l) for m in sorted(metas.values(), key=lambda m: m["id"]) if (l := load(m["id"])) and l["n"] == n]
    kept = [(m, l) for m, l in pool if m["ms"] <= best_meta["ms"] * 1.07] if clean else pool
    if len(kept) < 2:
        kept = pool
    dist = distance(ref)
    cs = corners(ref, dist)
    step = max(1, n // 500)
    idx = range(0, n, step)
    cols = lambda key: [[l[key][i] for _, l in kept] for i in idx]
    spd = cols("spd")
    brk = cols("brk")
    seg = [[_seg_ms(l, c["from"], c["to"]) for c in cs] for _, l in kept]
    quick = [min(col) for col in zip(*seg)] if cs else []
    laps = []
    for (m, l), times in zip(kept, seg):
        laps.append({**m, "spd": [round(l["spd"][i], 1) for i in idx],
                     "brake_at": [None if (b := _brake_point(l, c)) is None else b // step for c in cs],
                     "seg_ms": [round(v) for v in times], "lost_ms": [round(v - q) for v, q in zip(times, quick)]})
    rows = []
    for k, c in enumerate(cs):
        lost = [lap["lost_ms"][k] for lap in laps]
        marks = [dist[b] for _, l in kept if (b := _brake_point(l, c)) is not None]
        rows.append({"n": c["n"], "apex": c["apex"] // step, "from": c["from"] // step, "to": c["to"] // step, "at_m": round(dist[c["apex"]]),
                     "best_ms": round(quick[k]), "mean_lost_ms": round(statistics.fmean(lost)),
                     "sd_ms": round(statistics.pstdev([lap["seg_ms"][k] for lap in laps])),
                     "brake_spread_m": round(max(marks) - min(marks)) if len(marks) > 1 else None})
    times = [m["ms"] for m, _ in kept]
    return {"track": track, "car": car, "best": best_meta, "latest": kept[-1][0], "count": len(kept), "left_out": len(pool) - len(kept),
            "total": len(mine), "n": len(idx), "step": step, "length_m": round(dist[-1]),
            "has_map": max(ref["x"]) - min(ref["x"]) > 5 or max(ref["z"]) - min(ref["z"]) > 5,
            "x": [round(ref["x"][i], 1) for i in idx], "z": [round(ref["z"][i], 1) for i in idx], "dist": [round(dist[i]) for i in idx],
            "mean_spd": [round(statistics.fmean(v), 1) for v in spd], "sd_spd": [round(statistics.pstdev(v), 1) for v in spd],
            "min_spd": [round(min(v), 1) for v in spd], "max_spd": [round(max(v), 1) for v in spd],
            "brake": [round(statistics.fmean(v), 2) for v in brk],
            "mean_ms": round(statistics.fmean(times)), "sd_ms": round(statistics.pstdev(times)), "worst_ms": max(times),
            "corners": rows, "laps": laps}
