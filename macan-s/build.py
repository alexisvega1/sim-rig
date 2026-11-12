#!/usr/bin/env python3
"""Build three Macan S cars for Assetto Corsa from the official Macan Turbo.

    macan_s_2021          stock US-spec 2021 Macan S   (348 hp, 480 Nm)
    macan_s_2021_stage2   software-tuned               (~440 hp, ~607 Nm)
    macan_s_2021_1000     big-turbo build              (~1,000 hp), geared, tyred and locked for traction

Each is a separate car folder: model, skins and sound are cloned from
ks_porsche_macan (which is only read), physics come from kunos-data/ (unpacked
with acd_unpack.py) with the changes below. Re-running rebuilds all three.

Engine model, as the game applies it: wheel torque = power.lut x (1 + boost), and
the spec sheet quotes crank figures = wheel / 0.85 (the official car's own ratio).
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

CARS = Path.home() / ("Library/Application Support/CrossOver/Bottles/Steam(try3232)/drive_c/"
                      "Program Files (x86)/Steam/steamapps/common/assettocorsa/content/cars")
BASE = CARS / "ks_porsche_macan"
HERE = Path(__file__).parent
DATA = HERE / "kunos-data"
LOSS = 0.85


# ------------------------------------------------------------------ helpers ---
def set_ini(text: str, section: str, key: str, value) -> str:
    """Replace KEY's value inside [section], keeping any trailing comment."""
    m = re.search(rf"(?ms)^\[{re.escape(section)}\][^\[]*", text)
    if not m:
        raise KeyError(f"[{section}] missing")
    block, n = re.subn(rf"(?m)^({re.escape(key)}\s*=)[^;\r\n]*", lambda k: f"{k.group(1)}{value}\t", m.group(0), count=1)
    if not n:
        raise KeyError(f"{key} missing in [{section}]")
    return text[:m.start()] + block + text[m.end():]


def drop_section(text: str, section: str) -> str:
    return re.sub(rf"(?ms)^\[{re.escape(section)}\][^\[]*", "", text)


def edit(data: Path, name: str, *changes):
    p = data / name
    t = p.read_bytes().decode("latin-1")
    for c in changes:
        t = c(t) if callable(c) else set_ini(t, *c)
    p.write_bytes(t.encode("latin-1"))


def write_lut(data: Path, name: str, points):
    (data / name).write_text("".join(f"{x:g}|{y:g}\r\n" for x, y in points))


def scale_lut(data: Path, name: str, k: float):
    pts = [l.split("|") for l in (data / name).read_text().split() if "|" in l]
    write_lut(data, name, [(float(x), float(y) * k) for x, y in pts])


def boost_at(rpm, t):
    return min(t["wastegate"], t["max_boost"] * (rpm / t["ref_rpm"]) ** t["gamma"])


def crank_curve(lut, turbo, limiter):
    """[(rpm, crank Nm, crank hp)] every 250 rpm up to the limiter."""
    out = []
    for rpm in range(1000, int(limiter) + 1, 250):
        lo = max(p for p in lut if p[0] <= rpm); hi = min(p for p in lut if p[0] >= rpm)
        base = lo[1] if hi[0] == lo[0] else lo[1] + (hi[1] - lo[1]) * (rpm - lo[0]) / (hi[0] - lo[0])
        nm = base * (1 + boost_at(rpm, turbo)) / LOSS
        out.append((rpm, nm, nm * rpm * 2 * 3.14159265 / 60 / 745.7))
    return out


# ----------------------------------------------------------------- variants ---
# Base (off-boost) wheel torque. The S's 3.0 V6 holds 480 Nm from 1,360 to 4,800 rpm
# and 348 hp from 5,400 to 6,400, so the curve is flat, then falls to keep power level.
LUT_S = [(0, 50), (500, 130), (1000, 185), (1360, 240), (1500, 233), (4800, 233), (5400, 223),
         (5800, 207), (6400, 188), (6800, 165), (7000, 146), (7500, 0)]
# Built engine for the 1000: breathes higher, revs to 7,400.
LUT_1000 = [(0, 50), (500, 130), (1000, 190), (1500, 230), (2000, 250), (2500, 262), (3000, 270),
            (3500, 272), (5500, 272), (6000, 267), (6500, 258), (7000, 240), (7400, 220), (7800, 150), (8200, 0)]

VARIANTS = {
    "macan_s_2021": dict(
        name="Porsche Macan S (2021)", short="Macan S", lut=LUT_S, limiter=6800, mass=1865,
        turbo=dict(wastegate=0.75, max_boost=0.85, ref_rpm=1500, gamma=2, lag_up=0.996),
        blurb="Stock 2021 Macan S: 3.0-litre single-turbo V6, 348 hp and 354 lb-ft, 7-speed PDK, "
              "all-wheel drive. Built from the official Macan Turbo's chassis with the S's engine and weight."),
    "macan_s_2021_stage2": dict(
        name="Porsche Macan S Stage 2", short="Macan S St2", lut=LUT_S, limiter=6800, mass=1865,
        turbo=dict(wastegate=1.2125, max_boost=1.36, ref_rpm=1900, gamma=2, lag_up=0.996),
        blurb="The 2021 Macan S with a software tune and supporting intake/exhaust: more boost on the "
              "stock turbo, everything else as it left the factory."),
    "macan_s_2021_1000": dict(
        name="Porsche Macan S 1000", short="Macan S 1000", lut=LUT_1000, limiter=7400, mass=1790,
        turbo=dict(wastegate=2.6, max_boost=2.9, ref_rpm=3800, gamma=2.5, lag_up=0.992),
        blurb="A built 3.0 with one very large turbo: about 1,000 hp from 6,500 rpm. Longer final drive, "
              "wide semi-slick tyres, a much tighter all-wheel-drive coupling, locked-up diffs, bigger "
              "brakes, stiffer springs and a wing so it can put the power down. Laggy below 3,500 rpm."),
}


def physics(data: Path, key: str, v: dict):
    t = v["turbo"]
    write_lut(data, "power.lut", v["lut"])
    edit(data, "engine.ini",
         ("ENGINE_DATA", "LIMITER", v["limiter"]), ("COAST_REF", "RPM", v["limiter"]),
         ("TURBO_0", "WASTEGATE", t["wastegate"]), ("TURBO_0", "MAX_BOOST", t["max_boost"]),
         ("TURBO_0", "DISPLAY_MAX_BOOST", t["wastegate"]), ("TURBO_0", "REFERENCE_RPM", t["ref_rpm"]),
         ("TURBO_0", "GAMMA", t["gamma"]), ("TURBO_0", "LAG_UP", t["lag_up"]),
         lambda s: drop_section(s, "TURBO_1"),                     # the S has one turbo, not two
         ("DAMAGE", "TURBO_BOOST_THRESHOLD", round(t["max_boost"] + 0.5, 2)),
         ("DAMAGE", "RPM_THRESHOLD", v["limiter"] + 250))
    edit(data, "car.ini", ("INFO", "SCREEN_NAME", v["name"]), ("INFO", "SHORT_NAME", v["short"]),
         ("BASIC", "TOTALMASS", v["mass"]))
    edit(data, "drivetrain.ini", ("AUTO_SHIFTER", "UP", v["limiter"] - 300))

    if key != "macan_s_2021_1000":
        return
    edit(data, "drivetrain.ini",
         ("GEARS", "FINAL", 4.00),                                 # 2nd gear becomes usable at full boost
         ("AWD2", "FRONT_DIFF_POWER", 0.12), ("AWD2", "REAR_DIFF_POWER", 0.45),
         ("AWD2", "REAR_DIFF_COAST", 0.30), ("AWD2", "REAR_DIFF_PRELOAD", 40),
         ("CLUTCH", "MAX_TORQUE", 2200))
    scale_lut(data, "gear_start.lut", 950 / 350)                   # centre coupling sends far more to the front
    scale_lut(data, "rear_ratio_vs_lock.lut", 2.7)
    edit(data, "electronics.ini", ("TRACTION_CONTROL", "MIN_SPEED_KMH", 8))
    edit(data, "brakes.ini", ("DATA", "MAX_TORQUE", 5600))
    edit(data, "car.ini", ("FUEL", "CONSUMPTION", 0.0060))
    for end, width in (("FRONT", 0.295), ("REAR", 0.335)):         # wide semi-slicks, about 22 % more grip
        edit(data, "tyres.ini", (end, "NAME", "Semislick"), (end, "SHORT_NAME", "SM"), (end, "WIDTH", width),
             (end, "DX_REF", 1.50), (end, "DY_REF", 1.45), (end, "DX0", 1.51), (end, "DY0", 1.57))
    for end in ("FRONT", "REAR"):
        s = (data / "suspensions.ini").read_bytes().decode("latin-1")
        for k in ("SPRING_RATE", "DAMP_BUMP", "DAMP_REBOUND", "DAMP_FAST_BUMP", "DAMP_FAST_REBOUND"):
            cur = float(re.search(rf"(?ms)^\[{end}\][^\[]*?^{k}\s*=\s*([\d.]+)", s).group(1))
            s = set_ini(s, end, k, round(cur * 1.3))
        (data / "suspensions.ini").write_bytes(s.encode("latin-1"))
    # a rear wing and a splitter, written in the same shape as the car's own aero sections
    write_lut(data, "wing_built_AOA_CL.lut", [(-10, 0), (0, 0.6), (5, 0.9), (10, 1.15), (15, 1.25), (20, 1.1)])
    write_lut(data, "wing_built_AOA_CD.lut", [(-10, 0.02), (0, 0.03), (5, 0.05), (10, 0.08), (15, 0.12), (20, 0.18)])
    aero = (data / "aero.ini").read_bytes().decode("latin-1")
    rear = re.search(r"(?ms)^\[WING_2\][^\[]*", aero).group(0)
    def wing(n, name, chord, span, pos, angle):
        w = rear.replace("[WING_2]", f"[WING_{n}]")
        for k, val in (("NAME", name), ("CHORD", chord), ("SPAN", span), ("POSITION", pos),
                       ("LUT_AOA_CL", "wing_built_AOA_CL.lut"), ("LUT_AOA_CD", "wing_built_AOA_CD.lut"),
                       ("CL_GAIN", 1), ("CD_GAIN", 1), ("ANGLE", angle),
                       ("ZONE_REAR_CL", 0), ("ZONE_REAR_CD", 0)):
            w = set_ini(w, f"WING_{n}", k, val)
        return w.rstrip() + "\r\n\r\n"
    n = len(re.findall(r"(?m)^\[WING_\d+\]", aero))
    aero = aero.rstrip() + "\r\n\r\n" + wing(n, "BUILT_REAR_WING", 0.35, 1.60, "0,0.95,-2.20", 8) \
        + wing(n + 1, "BUILT_SPLITTER", 0.30, 1.70, "0,-0.25,2.05", 3)
    (data / "aero.ini").write_bytes(aero.encode("latin-1"))


def ui(dest: Path, key: str, v: dict, curve):
    p = dest / "ui" / "ui_car.json"
    d = json.loads(p.read_text(encoding="utf-8-sig"), strict=False)
    hp = max(c[2] for c in curve); nm = max(c[1] for c in curve)
    d.update(name=v["name"], year=2021, author="Kunos (chassis), custom engine data", version="1.0",
             description=v["blurb"], parent=None,
             torqueCurve=[[str(r), str(round(t))] for r, t, _ in curve],
             powerCurve=[[str(r), str(round(h))] for r, _, h in curve])
    d["tags"] = [t for t in d.get("tags", []) if t != "#SUV"] + ["#SUV", "macan s", "custom"]
    d["specs"] = {"bhp": f"{round(hp)}hp", "torque": f"{round(nm * 0.7376)} lb-ft", "weight": f"{v['mass']}kg",
                  "topspeed": "", "acceleration": "", "pwratio": f"{v['mass'] / hp:.2f}kg/hp"}
    d.pop("parent", None)
    p.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
    return hp, nm


def sound(dest: Path, key: str):
    """Reuse the Macan's engine sound: its bank under the new name, events remapped to the new car."""
    sfx = dest / "sfx"
    (sfx / "ks_porsche_macan.bank").rename(sfx / f"{key}.bank")
    lines = [l.strip() for l in (CARS.parent / "sfx" / "GUIDs.txt").read_text(errors="ignore").splitlines()]
    mine = [l.replace("ks_porsche_macan", key) for l in lines if "ks_porsche_macan" in l]
    common = [l for l in lines if l.endswith("bank:/common")]
    (sfx / "GUIDs.txt").write_text("\r\n".join(common + mine) + "\r\n")


def build(key: str, v: dict):
    dest = CARS / key
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir()
    for item in BASE.iterdir():
        if item.name in ("data.acd", "data", ".DS_Store"):
            continue
        subprocess.run(["cp", "-Rc", str(item), str(dest / item.name)], check=True)   # APFS clone: no extra disk
    shutil.copytree(DATA, dest / "data")
    physics(dest / "data", key, v)
    curve = crank_curve(v["lut"], v["turbo"], v["limiter"])
    hp, nm = ui(dest, key, v, curve)
    sound(dest, key)
    peak = max(curve, key=lambda c: c[2])
    print(f"{key:22s} {hp:5.0f} hp @ {peak[0]} rpm   {nm:5.0f} Nm ({nm * 0.7376:.0f} lb-ft)   {v['mass']} kg")


if __name__ == "__main__":
    assert (DATA / "engine.ini").exists(), "run acd_unpack.py first"
    for k, spec in VARIANTS.items():
        build(k, spec)
