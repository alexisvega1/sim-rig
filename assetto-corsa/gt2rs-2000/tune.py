#!/usr/bin/env python3
"""Turn the Assetto Corsa mod car porsche_911_gt2_rs_wss_2000 into the track build.

The mod's own files are not in this repository. This script makes the same edits
to an installed copy of the car, so the tune can be rebuilt from the original mod:

    slicks      a third tyre compound, cloned from the Cup 2 with more grip
    brakes      brake torque 4150 -> 6000 N·m
    downforce   front, rear and diffuser lift x3, drag on the rear wing
    springs     stiffer springs and dampers to carry the downforce
    power       the boost the car was named for: 2,000 hp, with 2,500 hp on tap
    gearbox     stronger clutch, longer 7th
    redline     9,000 rpm instead of 7,200 (--redline to choose): the torque curve is stretched
                up the rev range and the gearing shortened to match, so power and the speed in
                each gear stay the same and only the revs rise
    high revs   a second rev limit, 12,000 rpm (--high-redline, 0 for none), switched while driving
                with the patch's "Extra F" switch: a script in the car raises the limit and shortens
                the final drive by the same factor. Peak power is held from 9,000 to 12,000 rpm.
    sound       backfire from lower revs

Every file it changes is first copied to <name>.orig-before-tune (once).
Run it with the game closed:  python3 tune.py   (or  --data <path to the car's data folder>)
"""
import argparse
import glob
import re
import shutil
from pathlib import Path

DEFAULT = (Path.home() / "Library/Application Support/CrossOver/Bottles/Steam(try3232)/drive_c/Program Files (x86)"
           / "Steam/steamapps/common/assettocorsa/content/cars/porsche_911_gt2_rs_wss_2000/data")
BACKUP = ".orig-before-tune"


def set_key(text: str, section: str, key: str, value) -> str:
    """KEY=value inside [section], keeping whatever comment follows it."""
    pat = re.compile(r"(\[%s\][^\[]*?^%s[ \t]*=[ \t]*)([^;\r\n\t]*)" % (re.escape(section), re.escape(key)), re.S | re.M)
    new, n = pat.subn(lambda m: m.group(1) + str(value), text, count=1)
    if n != 1:
        raise SystemExit(f"[{section}] {key} not found: is this the right car?")
    return new


def section(text: str, name: str) -> str:
    m = re.search(r"^\[%s\][^\[]*" % re.escape(name), text, re.M)
    if not m:
        raise SystemExit(f"[{name}] not found")
    return m.group(0)


class Car:
    def __init__(self, data: Path, dry: bool):
        self.data, self.dry, self.changed = data, dry, []

    def edit(self, name: str, fn):
        path = self.data / name
        old = path.read_bytes().decode("utf-8", "surrogateescape")
        new = fn(old)
        if new == old:
            return
        self.changed.append(name)
        if self.dry:
            return
        if not (self.data / (name + BACKUP)).exists():
            shutil.copy2(path, self.data / (name + BACKUP))
        path.write_bytes(new.encode("utf-8", "surrogateescape"))

    def write(self, name: str, text: str):
        """A file of our own (not one of the mod's)."""
        path = self.data / name
        if path.exists() and path.read_text() == text:
            return
        self.changed.append(name)
        if not self.dry:
            path.write_text(text)

    def park(self, name: str):
        """Take a file out of the game's sight without deleting it."""
        path = self.data / name
        if path.exists():
            self.changed.append(name + " (parked)")
            if not self.dry:
                path.rename(self.data / (name + BACKUP))


def slicks(t: str) -> str:
    if "[FRONT_2]" in t:
        return t
    nl = "\r\n" if "\r\n" in t else "\n"
    grip = {"FRONT": dict(DY0="1.7600", DX0="1.7500", DX_REF="1.75", DY_REF="1.76"),
            "REAR": dict(DY0="1.7800", DX0="1.8200", DX_REF="1.82", DY_REF="1.78")}
    out = [t.rstrip("\r\n"), "", "; --- racing slicks in the Cup 2 sizes. The default compound is still index 0 (Cup 2)."]
    for name in ("FRONT", "REAR", "THERMAL_FRONT", "THERMAL_REAR"):
        s = section(t, name).rstrip("\r\n").replace(f"[{name}]", f"[{name}_2]", 1)
        if name in grip:
            s = set_key(s, name + "_2", "NAME", "Slick")
            s = set_key(s, name + "_2", "SHORT_NAME", "S")
            for k, v in grip[name].items():
                s = set_key(s, name + "_2", k, v)
        out += [s, ""]
    return nl.join(out)


def brakes(t: str) -> str:
    return set_key(t, "DATA", "MAX_TORQUE", 6000)


def downforce(t: str) -> str:
    for sec, gain in (("WING_1", 4.65), ("WING_2", 4.35), ("WING_3", 4.05)):   # front, rear, diffuser: three times the lift
        t = set_key(t, sec, "CL_GAIN", gain)
    return set_key(t, "WING_2", "CD_GAIN", 3.6)                                # the rear wing now costs drag


def springs(t: str) -> str:
    values = {"FRONT": dict(SPRING_RATE=110000, DAMP_BUMP=7025, DAMP_FAST_BUMP=2045, DAMP_REBOUND=10440, DAMP_FAST_REBOUND=4045),
              "REAR": dict(SPRING_RATE=150000, DAMP_BUMP=11825, DAMP_FAST_BUMP=4245, DAMP_REBOUND=14455, DAMP_FAST_REBOUND=7485)}
    for sec, kv in values.items():
        for k, v in kv.items():
            t = set_key(t, sec, k, v)
    return t


def power(t: str) -> str:
    # Torque is power.lut x (1 + total boost). The game only keeps the boost setting in steps of 10%
    # (0.76 was read back as 0.70), so the wastegate is sized to put both maps on a step:
    # 60% = 2 x 1.784 bar = 2,000 hp at the limiter, 80% = 2 x 2.378 bar = 2,520 hp. MAX_BOOST stops anything above that.
    t = set_key(t, "ENGINE_DATA", "DEFAULT_TURBO_ADJUSTMENT", 0.6)
    for sec in ("TURBO_0", "TURBO_1"):
        for k, v in dict(MAX_BOOST="2.40", WASTEGATE="2.973", DISPLAY_MAX_BOOST="2.40", REFERENCE_RPM=4200, GAMMA=2.2,
                         COCKPIT_ADJUSTABLE=1).items():
            t = set_key(t, sec, k, v)
    return set_key(t, "DAMAGE", "TURBO_BOOST_THRESHOLD", "6.0")


def gearbox(t: str) -> str:
    t = set_key(t, "CLUTCH", "MAX_TORQUE", 4000)        # 2,500 hp makes about 2,700 N·m; the old clutch held 2,500
    return set_key(t, "GEARS", "GEAR_7", 0.55)          # the old 7th ran into the limiter at about 224 mph


STOCK_LIMITER = 7200


SWITCH = "F"            # the Custom Shaders Patch extra switch that selects the high rev limit

SCRIPT = """-- Rev limit switch, written by tune.py. Extra switch %(switch)s off: %(low)d rpm. On: %(high)d rpm, with the
-- final drive shortened by the same factor, so the speed reached in each gear does not change.
if car.isAIControlled then
  return nil
end

local LOW, HIGH, FINAL = %(low)d, %(high)d, %(final)s
local mode, timer = -1, 0

function script.update(dt)
  local want = car.extra%(switch)s and 1 or 0
  timer = timer + dt
  if want == mode and timer < 1 then return end      -- set again once a second: a session restart resets the car
  mode, timer = want, 0
  local limit = want == 1 and HIGH or LOW
  ac.setEngineRPMLimit(limit, true)
  ac.setGearsFinalRatio(FINAL * limit / LOW)
end
"""


def revs(k: float, high: int = 0):
    """Engine side of a higher redline: everything measured in rpm moves up by k."""
    def fn(t: str) -> str:
        t = set_key(t, "ENGINE_DATA", "LIMITER", round(STOCK_LIMITER * k))
        t = set_key(t, "ENGINE_DATA", "MINIMUM", round(1100 * k / 50) * 50)
        t = set_key(t, "COAST_REF", "RPM", round(7000 * k))
        t = set_key(t, "COAST_REF", "TORQUE", round(70 / k, 1))            # same engine braking at the wheels
        for sec in ("TURBO_0", "TURBO_1"):
            t = set_key(t, sec, "REFERENCE_RPM", round(4200 * k))
        return set_key(t, "DAMAGE", "RPM_THRESHOLD", max(round(7500 * k), high + 500))
    return fn


def curve(original: str, k: float, high: int = 0) -> str:
    """power.lut stretched up the rev range: k times the revs, 1/k the torque, so the same power.
    With a high rev limit the curve carries on past the normal one at constant power."""
    nl = "\r\n" if "\r\n" in original else "\n"
    pts = []
    for line in original.replace("\r", "").split("\n"):
        if "|" in line:
            rpm, nm = line.split("|")[:2]
            pts.append((float(rpm) * k, float(nm.split(";")[0]) / k))
    low = STOCK_LIMITER * k
    if high > low:
        (r0, t0), (r1, t1) = max(p for p in pts if p[0] <= low), min(p for p in pts if p[0] > low)
        at_limit = t0 + (t1 - t0) * (low - r0) / (r1 - r0)
        pts = [p for p in pts if p[0] < low] + [(low, at_limit)]
        rpm = (low // 500 + 1) * 500
        while rpm < high:
            pts.append((rpm, at_limit * low / rpm))
            rpm += 500
        top = at_limit * low / high
        pts += [(high, top), (high + 250, top * 0.97), (high + 500, top * 0.90)]   # falls away past the limiter, as the original does
    return nl.join(f"{round(r)}|{nm:.1f}" for r, nm in pts) + nl


def gearing(k: float):
    """Gearbox side: a final drive k times shorter keeps the same road speed in every gear."""
    def fn(t: str) -> str:
        t = set_key(t, "GEARS", "FINAL", round(4.09 * k, 3))
        t = set_key(t, "GEARBOX", "VALID_SHIFT_RPM_WINDOW", round(800 * k))
        t = set_key(t, "AUTOCLUTCH", "MIN_RPM", round(1100 * k))
        t = set_key(t, "AUTOCLUTCH", "MAX_RPM", round(1900 * k))
        t = set_key(t, "AUTO_SHIFTER", "UP", round(7800 * k))
        return set_key(t, "AUTO_SHIFTER", "DOWN", round(3800 * k))
    return fn


def sound(t: str) -> str:
    return set_key(t, "BACKFIRE", "MINRPM", 2800)


def main():
    global BACKUP
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--data", type=Path, default=DEFAULT, help="the car's data folder")
    ap.add_argument("--dry-run", action="store_true", help="say what would change, change nothing")
    ap.add_argument("--redline", type=int, default=9000, help="rev limit in rpm (the mod's own is 7200)")
    ap.add_argument("--high-redline", type=int, default=12000, help="second rev limit on Extra F; 0 for none")
    ap.add_argument("--backup-suffix", default=BACKUP, help="suffix for the copies kept of the files as they were")
    a = ap.parse_args()
    if not (a.data / "engine.ini").exists():
        raise SystemExit(f"no car data at {a.data}")
    BACKUP = a.backup_suffix
    k = a.redline / STOCK_LIMITER
    car = Car(a.data, a.dry_run)
    car.edit("tyres.ini", slicks)
    car.edit("brakes.ini", brakes)
    car.edit("aero.ini", downforce)
    car.edit("suspensions.ini", springs)
    high = a.high_redline if a.high_redline > a.redline else 0
    car.edit("engine.ini", lambda t: revs(k, high)(power(t)))
    car.park("ctrl_turbo0.ini")                         # these capped each turbo at 0.9 bar whatever engine.ini said
    car.park("ctrl_turbo1.ini")
    car.edit("drivetrain.ini", lambda t: gearing(k)(gearbox(t)))
    car.edit("sounds.ini", sound)
    # the curve is always rebuilt from the mod's own: the oldest copy kept of it, or the file itself on a first run
    kept = sorted(glob.glob(str(a.data / "power.lut.*")), key=lambda p: Path(p).stat().st_mtime)
    original = Path(kept[0] if kept else a.data / "power.lut").read_bytes().decode("utf-8", "surrogateescape")
    car.edit("power.lut", lambda _: curve(original, k, high) if high or abs(k - 1) > 1e-9 else original)
    if high:
        car.write("script.lua", SCRIPT % dict(switch=SWITCH, low=a.redline, high=high, final=round(4.09 * k, 3)))
    elif (a.data / "script.lua").exists() and "written by tune.py" in (a.data / "script.lua").read_text():
        car.park("script.lua")
    print(("would change: " if a.dry_run else "changed: ") + (", ".join(car.changed) or "nothing, already tuned"))


if __name__ == "__main__":
    main()
