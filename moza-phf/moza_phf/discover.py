"""Reverse-engineering helpers.

Capture side (works on logs from `sniff`, usbmon pcaps, or USBPcap pcaps):
  diff_captures()  frames present in ACTION but not BASELINE
  dump_capture()   timeline of every frame

Live side (Linux, serial port):
  identify()   query documented READ commands only -> which known devices answer.
               This is the only step that is truly read-only.
  scan()       sweep (device, group, id) on UNKNOWN device ids with value 1 and
               record what answers. NOT read-only: the wire format does not mark
               read vs write — the group decides — so on an unknown device any
               group may be a write group and this may store the value 1.
               Known devices (base, pedals, ...) are never swept.
  fuzz()       write a chosen value to commands `scan` found; you feel for the buzz.
  replay()     send one raw frame.
"""
from __future__ import annotations

import glob
import json
import time
from collections import Counter
from dataclasses import dataclass

from .protocol import ID_TO_NAME, Frame, parse_stream, split_frames

MOZA_VID = "346e"
BAUD = 115200
KNOWN_DEVICE_IDS = set(ID_TO_NAME)

# (device, group, ids, nbytes, label) — documented reads from Boxflat serial.yml
IDENTIFY_READS = [
    (18, 31, [23], 1, "main: compat-mode"),
    (18, 31, [52], 1, "main: work-mode"),
    (19, 43, [1], 2, "base: state"),
    (19, 40, [2], 2, "base: ffb-strength"),
    (25, 37, [1], 2, "pedals: throttle-output"),
    (25, 37, [2], 2, "pedals: brake-output"),
    (25, 35, [5], 2, "pedals: brake-min"),
    (27, 91, [1], 2, "handbrake (guess)"),
]


# ---------------------------------------------------------------- captures ---
@dataclass
class DiffReport:
    only_in_action: Counter
    only_in_baseline: Counter
    common: Counter
    action_requests_by_device: Counter

    def render(self, limit: int = 60) -> str:
        lines = []
        lines.append("== requests in ACTION capture, by MOZA device id ==")
        for dev, n in self.action_requests_by_device.most_common():
            lines.append(f"  dev {dev:3d} ({ID_TO_NAME.get(dev, '?')}): {n}")
        lines.append("")
        lines.append(f"== frames only in ACTION ({len(self.only_in_action)} distinct) ==")
        for (grp, dev, payload), n in self.only_in_action.most_common(limit):
            lines.append(f"  x{n:<4d} grp={grp:3d} dev={dev:3d}({ID_TO_NAME.get(dev, '?')}) "
                         f"payload={payload.hex(' ')}")
        lines.append("")
        lines.append(f"== frames only in BASELINE ({len(self.only_in_baseline)} distinct) ==")
        for (grp, dev, payload), n in self.only_in_baseline.most_common(20):
            lines.append(f"  x{n:<4d} grp={grp:3d} dev={dev:3d} payload={payload.hex(' ')}")
        return "\n".join(lines)


def load_frames(path: str, device: int | None = None):
    """Frames from a pcap (USBPcap/usbmon) or a `sniff` .log file."""
    if str(path).endswith(".log"):
        from .linux import frames_from_log
        return frames_from_log(path)
    from .pcap import frames_from_capture
    return frames_from_capture(path, device)


def diff_captures(baseline: str, action: str, device: int | None = None) -> DiffReport:
    b = Counter(t.frame.key() for t in load_frames(baseline, device) if not t.frame.is_response)
    a = Counter(t.frame.key() for t in load_frames(action, device) if not t.frame.is_response)
    by_dev = Counter(dev for (_, dev, _) in a.elements())
    return DiffReport(
        only_in_action=Counter({k: v for k, v in a.items() if k not in b}),
        only_in_baseline=Counter({k: v for k, v in b.items() if k not in a}),
        common=Counter({k: v for k, v in a.items() if k in b}),
        action_requests_by_device=by_dev,
    )


def dump_capture(path: str, device: int | None = None) -> str:
    frames = load_frames(path, device)
    t0 = frames[0].ts if frames else 0
    return "\n".join(f"{t.ts - t0:9.4f}  {'<-' if t.direction_in else '->'}  {t.frame}  [{t.frame.hex()}]"
                     for t in frames)


# ------------------------------------------------------------------ serial ---
def find_ports() -> list[str]:
    try:
        from serial.tools import list_ports
        ports = [p.device for p in list_ports.comports()
                 if p.vid is not None and f"{p.vid:04x}" == MOZA_VID]
        if ports:
            return ports
    except ImportError:
        pass
    return sorted(glob.glob("/dev/ttyACM*") + glob.glob("/dev/cu.usbmodem*"))


def open_port(port: str | None = None):
    import serial  # pyserial
    port = port or (find_ports() or [None])[0]
    if port is None:
        raise RuntimeError("no MOZA serial device found (run `moza-phf doctor`)")
    return serial.Serial(port, BAUD, timeout=0.005)


def transact(ser, frame: Frame, wait: float = 0.04) -> list[Frame]:
    """Send, then collect until a mirrored response shows up or `wait` elapses."""
    ser.reset_input_buffer()
    ser.write(frame.raw)
    deadline = time.monotonic() + wait
    data = b""
    while time.monotonic() < deadline:
        chunk = ser.read(ser.in_waiting or 1)
        if chunk:
            data += chunk
            for raw in split_frames(data):
                if raw[2] == (frame.group | 0x80):
                    return parse_stream(data, n_ids=len(frame.ids))
    return parse_stream(data, n_ids=len(frame.ids)) if data else []


def _answers(req: Frame, rsps: list[Frame]) -> Frame | None:
    for r in rsps:
        if r.is_response and r.request_group == req.group and r.request_device == req.device:
            return r
    return None


def listen(port: str | None = None, seconds: float = 5.0) -> Counter:
    """Unsolicited frames the device streams on its own."""
    seen: Counter = Counter()
    with open_port(port) as ser:
        end = time.monotonic() + seconds
        buf = b""
        while time.monotonic() < end:
            buf += ser.read(ser.in_waiting or 1)
        for fr in parse_stream(buf):
            seen[(fr.group, fr.device, len(fr.payload))] += 1
    return seen


def identify(port: str | None = None) -> list[tuple[str, Frame]]:
    """Documented READ commands only. Safe."""
    out = []
    with open_port(port) as ser:
        for dev, grp, ids, nbytes, label in IDENTIFY_READS:
            req = Frame.read_request(grp, dev, ids, nbytes)
            rsp = _answers(req, transact(ser, req, 0.08))
            print(f"{'ANSWER' if rsp else '  --  '}  {label:28s} {req.hex()}"
                  + (f"  ->  {rsp.hex()}" if rsp else ""))
            if rsp:
                out.append((label, rsp))
    return out


def scan(port: str | None = None, devices=range(29, 41), groups=range(0, 128),
         ids=range(0, 4), sizes=(2, 1), wait: float = 0.03, out: str | None = None,
         allow_known: bool = False) -> list[dict]:
    """Value-1 sweep over unknown device ids. See module docstring: may write 1."""
    hits: list[dict] = []
    with open_port(port) as ser:
        for dev in devices:
            if dev in KNOWN_DEVICE_IDS and not allow_known:
                print(f"skip dev {dev} ({ID_TO_NAME[dev]}): known device, not swept")
                continue
            for grp in groups:
                for cid in ids:
                    for nb in sizes:
                        req = Frame.read_request(grp, dev, [cid], nb)
                        rsp = _answers(req, transact(ser, req, wait))
                        if rsp:
                            h = {"device": dev, "group": grp, "id": cid, "nbytes": nb,
                                 "request": req.hex(), "response": rsp.hex(),
                                 "value": rsp.values.hex()}
                            hits.append(h)
                            print(f"HIT dev={dev} grp={grp} id={cid} nbytes={nb} -> {rsp.hex()}")
                            break
    if out:
        json.dump(hits, open(out, "w"), indent=1)
    return hits


def fuzz(hits: list[dict], port: str | None = None, value: int = 50, interval: float = 0.6,
         log: str = "fuzz.log", only_groups: set[int] | None = None) -> None:
    """Write `value` to each command `scan` found, one at a time, slowly.
    Ctrl-C the moment the pedal buzzes: the last frames sent are printed."""
    sent: list[str] = []
    with open_port(port) as ser, open(log, "a") as lf:
        try:
            for h in hits:
                if only_groups and h["group"] not in only_groups:
                    continue
                fr = Frame.write_int(h["group"], h["device"], [h["id"]], value, h["nbytes"])
                rsp = _answers(fr, transact(ser, fr, 0.05))
                line = (f"{time.time():.3f} dev={h['device']} grp={h['group']} id={h['id']} "
                        f"val={value} {fr.hex()} ack={'y' if rsp else 'n'}")
                print(line)
                lf.write(line + "\n"); lf.flush()
                sent.append(fr.hex())
                time.sleep(interval)
        except KeyboardInterrupt:
            print("\n-- stopped. last frames sent (newest last); replay each to find the one:")
            for s in sent[-5:]:
                print(f'   moza-phf replay "{s}"')


def probe(port=None, devices=range(29, 41), groups=range(0, 128), cmd_ids=range(0, 4),
          nbytes: int = 2, verbose: bool = False):
    """Back-compat alias for scan()."""
    return scan(port, devices, groups, cmd_ids, (nbytes,))


def replay(hex_frame: str, port: str | None = None, repeat: int = 1, interval: float = 0.1) -> list[Frame]:
    raw = bytes.fromhex(hex_frame.replace(" ", ""))
    fr = Frame.parse(raw)  # validates checksum
    out = []
    with open_port(port) as ser:
        for _ in range(repeat):
            out += transact(ser, fr, 0.08)
            time.sleep(interval)
    return out
