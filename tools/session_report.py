#!/usr/bin/env python3
"""Lap log: turn Content Manager's saved sessions into results/README.md and one chart per car and track.

Content Manager keeps every session it launches (lap times, sector times, cuts) in
Data/Progress/Sessions. Those files also hold the driver's name and Steam id, so they are
never copied here: this script reads them and writes only cars, tracks, dates and times.

    python3 tools/session_report.py            (run by backup.sh)
"""
import argparse
import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

DRIVE_C = Path.home() / "Library/Application Support/CrossOver/Bottles/Steam(try3232)/drive_c"
SESSIONS = DRIVE_C / "content-manager/Data/Progress/Sessions"
AC_ROOT = DRIVE_C / "Program Files (x86)/Steam/steamapps/common/assettocorsa"
# Cars and tracks that get every lap listed, not just a line in the summary.
FEATURED_CARS = ("ks_ferrari_f2004", "porsche_911_gt2_rs_wss_2000")
FEATURED_TRACKS = ("ks_nordschleife",)
BLUE, GREY, ORANGE = "#3987e5", "#8b949e", "#d95926"


def clock(ms: float) -> str:
    return f"{int(ms // 60000)}:{ms % 60000 / 1000:06.3f}"


def ui_name(kind: str, ident: str) -> str:
    """The name the game shows, read from the installed car or track; the folder name if there is none."""
    if kind == "car":
        path = AC_ROOT / "content/cars" / ident / "ui/ui_car.json"
    else:
        track, _, layout = ident.partition("-")
        path = AC_ROOT / "content/tracks" / track / "ui" / (layout if layout and layout != track else "") / "ui_track.json"
        if not path.exists():
            path = AC_ROOT / "content/tracks" / track / "ui/ui_track.json"
    try:
        name = re.search(r'"name"\s*:\s*"([^"]+)"', path.read_text(encoding="utf-8-sig", errors="ignore")).group(1)
        name = re.sub(r"[^\x20-\x7e -ɏ]", "", name).replace("|", "/")       # some mods decorate names with symbols
        return re.sub(r"\s+", " ", name).strip() or ident
    except (OSError, AttributeError):
        return ident


_tyres = {}


def tyre_name(car: str, short: str) -> str:
    """The compound's full name for the short code a saved lap carries ("S" -> "Slick"), from the car's own
    tyres.ini. Cars whose data is packed keep the code."""
    if car not in _tyres:
        names = {}
        try:
            text = (AC_ROOT / "content/cars" / car / "data/tyres.ini").read_text(errors="ignore")
            for body in re.findall(r"^\[FRONT(?:_\d+)?\]([^\[]*)", text, re.M):
                n, s = re.search(r"^NAME\s*=\s*([^;\r\n]*)", body, re.M), re.search(r"^SHORT_NAME\s*=\s*([^;\r\n]*)", body, re.M)
                if n and s:
                    names[s.group(1).strip()] = n.group(1).strip()
        except OSError:
            pass
        _tyres[car] = names
    name = _tyres[car].get(short, short) if short else ""
    return name.title() if name.isupper() and len(name) > 3 else name


def load(folder: Path):
    """One row per lap: (track, car, when, session file, lap number, ms, sectors, cuts, tyre)."""
    rows = []
    for path in sorted(folder.glob("*.json")):
        try:
            when = datetime.strptime(path.stem, "%y%m%d-%H%M%S")
            d = json.loads(path.read_text(errors="ignore"))
            car, track = d["players"][0]["car"], d["track"]
        except (ValueError, KeyError, IndexError):
            continue
        for s in d.get("sessions", []):
            for lap in s.get("laps", []):
                if lap.get("car", 0) == 0 and lap.get("time", 0) > 0:
                    rows.append((track, car, when, path.stem, lap.get("lap", 0) + 1, lap["time"],
                                 [x for x in lap.get("sectors", []) if x > 0], lap.get("cuts", 0), tyre_name(car, lap.get("tyre", ""))))
    return rows


def summary(laps):
    clean = [r for r in laps if r[7] == 0] or laps
    best = min(clean, key=lambda r: r[5])
    n = max((len(r[6]) for r in clean), default=0)
    full = [r for r in clean if len(r[6]) == n and n > 0]
    ideal = sum(min(r[6][i] for r in full) for i in range(n)) if full else None
    return best, ideal, n


def chart(laps, title: str) -> str:
    """Lap times in the order they were driven. Laps far off the pace are pinned to the top edge."""
    w, h, left, top, right, bottom = 760, 250, 62, 30, 16, 34
    best = min(r[5] for r in laps)
    cap = best * 1.10
    lo = best - (cap - best) * 0.08
    x = lambda i: left + (w - left - right) * (i + 0.5) / len(laps)
    y = lambda ms: top + (h - top - bottom) * (1 - (min(ms, cap) - lo) / (cap - lo))
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" font-family="-apple-system,Segoe UI,Helvetica,Arial,sans-serif" font-size="11">',
           f'<title>{title}</title>',
           f'<text x="{left}" y="16" fill="{GREY}" font-size="12">{title}: every lap in the order driven</text>']
    for k in range(5):                                             # grid and time labels
        ms = lo + (cap - lo) * k / 4
        out.append(f'<line x1="{left}" x2="{w - right}" y1="{y(ms):.1f}" y2="{y(ms):.1f}" stroke="{GREY}" stroke-opacity="0.25"/>')
        out.append(f'<text x="{left - 6}" y="{y(ms) + 4:.1f}" fill="{GREY}" text-anchor="end">{clock(ms)[:-2]}</text>')
    last = None
    for i, r in enumerate(laps):                                   # a divider and a date where a new session starts
        if r[3] != last:
            last = r[3]
            if i:
                out.append(f'<line x1="{x(i) - (x(1) - x(0)) / 2:.1f}" x2="{x(i) - (x(1) - x(0)) / 2:.1f}" y1="{top}" y2="{h - bottom}" stroke="{GREY}" stroke-opacity="0.35" stroke-dasharray="2 3"/>')
            out.append(f'<text x="{x(i) - 3:.1f}" y="{h - bottom + 14}" fill="{GREY}">{r[2].strftime("%b")} {r[2].day}</text>')
    out.append(f'<line x1="{left}" x2="{w - right}" y1="{y(best):.1f}" y2="{y(best):.1f}" stroke="{ORANGE}" stroke-dasharray="4 3"/>')
    out.append(f'<text x="{w - right}" y="{y(best) + 14:.1f}" fill="{ORANGE}" text-anchor="end">best {clock(best)}</text>')
    pts = " ".join(f"{x(i):.1f},{y(r[5]):.1f}" for i, r in enumerate(laps))
    out.append(f'<polyline points="{pts}" fill="none" stroke="{BLUE}" stroke-opacity="0.45" stroke-width="1.5"/>')
    for i, r in enumerate(laps):
        note = f"{r[2]:%Y-%m-%d} lap {r[4]}: {clock(r[5])}" + (f", {r[7]} cut(s)" if r[7] else "")
        if r[5] > cap:                                             # off the scale: a marker on the top edge
            out.append(f'<path d="M{x(i) - 4:.1f},{top + 7} L{x(i) + 4:.1f},{top + 7} L{x(i):.1f},{top} Z" fill="{GREY}"><title>{note}</title></path>')
        elif r[7]:
            out.append(f'<circle cx="{x(i):.1f}" cy="{y(r[5]):.1f}" r="3.5" fill="none" stroke="{GREY}" stroke-width="1.5"><title>{note}</title></circle>')
        else:
            out.append(f'<circle cx="{x(i):.1f}" cy="{y(r[5]):.1f}" r="{5 if r[5] == best else 3.5}" fill="{ORANGE if r[5] == best else BLUE}"><title>{note}</title></circle>')
    out.append(f'<text x="{left}" y="{h - 4}" fill="{GREY}">filled: clean lap    ring: lap with a cut    triangle: more than 10% off the best</text>')
    out.append("</svg>")
    return "\n".join(out)


def detail(track, car, laps, out_dir: Path) -> list[str]:
    best, ideal, n = summary(laps)
    name = f"{ui_name('car', car)} at {ui_name('track', track)}"
    slug = re.sub(r"[^a-z0-9]+", "-", f"{car}-{track}".lower()).strip("-")
    (out_dir / f"{slug}.svg").write_text(chart(laps, name))
    md = [f"### {name}", "",
          f"Best lap **{clock(best[5])}** on {best[2]:%Y-%m-%d}" + (f", on {best[8]} tyres." if best[8] else ".")
          + (f" Best sectors added together: **{clock(ideal)}**, {(best[5] - ideal) / 1000:.3f} s quicker." if ideal else ""), "",
          f"![Lap times, {name}]({slug}.svg)", ""]
    by_session = defaultdict(list)
    for r in laps:
        by_session[r[3]].append(r)
    md += ["| Date | Laps | Tyres | Best | Median clean lap | Spread of clean laps |", "|---|---|---|---|---|---|"]
    for sid, rows in by_session.items():
        clean = sorted(r[5] for r in rows if r[7] == 0 and r[5] < min(x[5] for x in rows) * 1.10)
        med = clock(clean[len(clean) // 2]) if clean else "-"
        spread = f"{(clean[-1] - clean[0]) / 1000:.1f} s" if len(clean) > 1 else "-"
        tyres = ", ".join(dict.fromkeys(r[8] for r in rows if r[8])) or "-"
        md.append(f"| {rows[0][2]:%Y-%m-%d %H:%M} | {len(rows)} | {tyres} | {clock(min(r[5] for r in rows))} | {med} | {spread} |")
    heads = "".join(f" S{i + 1} |" for i in range(n))
    md += ["", "<details><summary>Every lap</summary>", "", f"| Date | Lap | Time |{heads} Tyres | Cuts |", "|---|---|---|" + "---|" * (n + 2)]
    for r in laps:
        secs = "".join(f" {clock(s) if i < len(r[6]) else '-'} |" for i, s in enumerate(r[6] + [0] * (n - len(r[6]))))
        mark = "**" if r[5] == best[5] else ""
        md.append(f"| {r[2]:%Y-%m-%d} | {r[4]} | {mark}{clock(r[5])}{mark} |{secs} {r[8]} | {r[7] or ''} |")
    md += ["", "</details>", ""]
    return md


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--sessions", type=Path, default=SESSIONS)
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parent.parent / "results")
    a = ap.parse_args()
    rows = load(a.sessions)
    if not rows:
        raise SystemExit(f"no sessions found in {a.sessions}")
    a.out.mkdir(parents=True, exist_ok=True)
    for old in a.out.glob("*.svg"):
        old.unlink()
    groups = defaultdict(list)
    for r in rows:
        groups[(r[0], r[1])].append(r)
    md = ["# Lap log", "",
          f"{len(rows)} laps in {len({r[3] for r in rows})} sessions, {min(r[2] for r in rows):%Y-%m-%d} to {max(r[2] for r in rows):%Y-%m-%d}. "
          "Written by [`tools/session_report.py`](../tools/session_report.py) from Content Manager's saved sessions; "
          "the first lap of a session starts from the pits, so it is always slow.", ""]
    featured = [(k, v) for k, v in groups.items() if k[1] in FEATURED_CARS or k[0].split("-")[0] in FEATURED_TRACKS]
    for title, pick in (("Ferrari F2004", lambda k: k[1] == "ks_ferrari_f2004"),
                        ("Porsche 911 GT2 RS track build", lambda k: k[1] == "porsche_911_gt2_rs_wss_2000"),
                        ("Nürburgring Nordschleife, other cars", lambda k: k[1] not in FEATURED_CARS)):
        chosen = sorted(((k, v) for k, v in featured if pick(k)), key=lambda kv: -len(kv[1]))
        if chosen:
            md += [f"## {title}", ""]
            for (track, car), laps in chosen:
                md += detail(track, car, laps, a.out)
    md += ["## Every car and track", "", "| Track | Car | Sessions | Laps | Best | On tyres | Best sectors together | Last driven |", "|---|---|---|---|---|---|---|---|"]
    for (track, car), laps in sorted(groups.items(), key=lambda kv: (ui_name("track", kv[0][0]), min(r[5] for r in kv[1]))):
        best, ideal, _ = summary(laps)
        md.append(f"| {ui_name('track', track)} | {ui_name('car', car)} | {len({r[3] for r in laps})} | {len(laps)} | {clock(best[5])} | {best[8] or '-'} | "
                  f"{clock(ideal) if ideal else '-'} | {max(r[2] for r in laps):%Y-%m-%d} |")
    (a.out / "README.md").write_text("\n".join(md) + "\n")
    print(f"lap log: {len(rows)} laps, {len(groups)} car and track pairs, {len(featured)} with full detail -> {a.out}")


if __name__ == "__main__":
    main()
