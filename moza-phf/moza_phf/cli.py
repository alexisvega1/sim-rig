from __future__ import annotations

import argparse
import json
import sys
import time

from . import discover
from . import rig, s12
from .protocol import Frame
from .telemetry import SOURCES


def main(argv=None):
    ap = argparse.ArgumentParser(prog="moza-phf", description="MOZA PHF without Pit House: driver + RE tools")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("ports", help="list MOZA serial ports")

    p = sub.add_parser("devices", help="USB addresses seen in a capture")
    p.add_argument("pcap")

    p = sub.add_parser("dump", help="decode every MOZA frame in a capture")
    p.add_argument("pcap")
    p.add_argument("--usb-device", type=int, help="USB device address filter")

    p = sub.add_parser("diff", help="frames present in ACTION but not BASELINE")
    p.add_argument("baseline")
    p.add_argument("action")
    p.add_argument("--usb-device", type=int)
    p.add_argument("--limit", type=int, default=60)

    p = sub.add_parser("decode", help="decode a hex frame")
    p.add_argument("hex")
    p.add_argument("--n-ids", type=int, default=1)

    p = sub.add_parser("encode", help="build a frame: group device id[,id] value nbytes")
    p.add_argument("group", type=int); p.add_argument("device", type=int)
    p.add_argument("ids"); p.add_argument("value", type=int); p.add_argument("nbytes", type=int)

    p = sub.add_parser("doctor", help="show MOZA USB devices, interfaces, tty access")

    p = sub.add_parser("identify", help="documented READ commands only: which known devices answer")
    p.add_argument("--port")

    p = sub.add_parser("listen", help="count frames the device streams unprompted")
    p.add_argument("--port"); p.add_argument("--seconds", type=float, default=5)

    p = sub.add_parser("scan", help="value-1 sweep of UNKNOWN device ids (may store 1; see README)")
    p.add_argument("--port")
    p.add_argument("--devices", default="29-40")
    p.add_argument("--groups", default="0-127")
    p.add_argument("--ids", default="0-3")
    p.add_argument("--out", default="scan.json")
    p.add_argument("--i-accept-risk", action="store_true")

    p = sub.add_parser("fuzz", help="write a value to each command scan found; Ctrl-C on buzz")
    p.add_argument("hits", help="scan.json")
    p.add_argument("--port"); p.add_argument("--value", type=int, default=50)
    p.add_argument("--interval", type=float, default=0.6)
    p.add_argument("--groups", help="only these groups")
    p.add_argument("--i-accept-risk", action="store_true")

    p = sub.add_parser("sniff", help="serial man-in-the-middle: log traffic between a program and the port")
    p.add_argument("--port"); p.add_argument("--link", default="~/.wine/dosdevices/com1")
    p.add_argument("--log", default="sniff.log")

    p = sub.add_parser("replay", help="send one captured frame (hex) to the box")
    p.add_argument("hex"); p.add_argument("--port"); p.add_argument("--repeat", type=int, default=1)

    p = sub.add_parser("telemetry", help="print decoded telemetry from a game (no hardware)")
    p.add_argument("source", choices=sorted(SOURCES))
    p.add_argument("--port", type=int)

    p = sub.add_parser("probe", help="ask the PHF box for its slot state (changes nothing)")
    _s12_args(p)

    p = sub.add_parser("buzz", help="run one pedal's motor briefly, then stop it")
    _s12_args(p)
    p.add_argument("--pedal", choices=sorted(s12.PEDALS), default="brake")
    p.add_argument("--slot", choices=sorted(s12.SLOTS), default="abs")
    p.add_argument("--freq", type=float, default=50)
    p.add_argument("--strength", type=float, default=0.5, help="0..1")
    p.add_argument("--seconds", type=float, default=1.5)

    p = sub.add_parser("run", help="telemetry -> effects -> PHF box (settings: the control board)")
    p.add_argument("source", choices=sorted(SOURCES))
    p.add_argument("--udp-port", type=int)
    _s12_args(p)
    p.add_argument("--pedals", default="brake", help="pedals that have a motor fitted, comma separated")
    p.add_argument("--dry-run", action="store_true", help="print pulses instead of writing")

    p = sub.add_parser("board", help="control board: haptics, rev lights and lap analytics in the browser")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-record", action="store_true", help="don't record laps from the game (a second, look-only copy)")

    a = ap.parse_args(argv)

    if a.cmd == "ports":
        print("\n".join(discover.find_ports()) or "none")
    elif a.cmd == "devices":
        from .pcap import usb_devices
        for (bus, dev), n in sorted(usb_devices(a.pcap).items()):
            print(f"bus {bus} device {dev}: {n} bulk packets")
    elif a.cmd == "dump":
        print(discover.dump_capture(a.pcap, a.usb_device))
    elif a.cmd == "diff":
        print(discover.diff_captures(a.baseline, a.action, a.usb_device).render(a.limit))
    elif a.cmd == "decode":
        print(Frame.parse(bytes.fromhex(a.hex.replace(" ", "")), a.n_ids))
    elif a.cmd == "encode":
        ids = [int(x) for x in a.ids.split(",")]
        print(Frame.write_int(a.group, a.device, ids, a.value, a.nbytes).hex())
    elif a.cmd == "doctor":
        from .linux import doctor
        print(doctor())
    elif a.cmd == "identify":
        discover.identify(a.port)
    elif a.cmd == "listen":
        for (g, d, n), c in discover.listen(a.port, a.seconds).most_common():
            print(f"x{c:<5d} grp={g} dev={d} payload_len={n}")
    elif a.cmd == "scan":
        if not a.i_accept_risk:
            sys.exit("scan is not read-only on unknown devices (it sends value 1 to every "
                     "group/id and some may be writes). Re-run with --i-accept-risk.")
        hits = discover.scan(a.port, _ints(a.devices), _ints(a.groups), _ints(a.ids), out=a.out)
        print(f"{len(hits)} hits -> {a.out}")
    elif a.cmd == "fuzz":
        if not a.i_accept_risk:
            sys.exit("fuzz writes to undocumented commands. Re-run with --i-accept-risk.")
        discover.fuzz(json.load(open(a.hits)), a.port, a.value, a.interval,
                      only_groups=set(_ints(a.groups)) if a.groups else None)
    elif a.cmd == "sniff":
        from .linux import sniff
        sniff(a.port or discover.find_ports()[0], a.link, a.log)
    elif a.cmd == "replay":
        for r in discover.replay(a.hex, a.port, a.repeat):
            print(r, r.hex())
    elif a.cmd == "telemetry":
        src = SOURCES[a.source](**({"port": a.port} if a.port else {}))
        for t in src:
            print(t)
    elif a.cmd == "probe":
        ser, mode = _open_s12(a)
        with ser:
            print(f"{ser.port}: PHF box answers, {'own USB port' if mode == s12.USB else 'routed through the base'}")
            for name, pedal in s12.PEDALS.items():
                live = [f"{slot}@{r.freq}Hz" for slot, n in s12.SLOTS.items()
                        if (r := s12.query(ser, mode, pedal, n)) and r.enabled]
                print(f"  {name:8s} {'live: ' + ', '.join(live) if live else 'idle'}")
    elif a.cmd == "buzz":
        ser, mode = _open_s12(a)
        key = (s12.PEDALS[a.pedal], s12.SLOTS[a.slot])
        with ser:
            drv = s12.S12Driver(ser, mode)
            try:
                end = time.monotonic() + a.seconds
                while time.monotonic() < end:
                    drv.show({key: (a.freq, a.strength)})
                    drv.flush()
                    time.sleep(0.02)
            finally:
                drv.stop_all()
        print(f"sent {a.pedal}/{a.slot} {a.freq:g} Hz at {a.strength:.0%} for {a.seconds:g} s, then stop")
    elif a.cmd == "run":
        ser = None
        if a.dry_run:
            drv = rig.PrintDriver()
        else:
            ser, mode = _open_s12(a)
            drv = s12.S12Driver(ser, mode)
        try:
            rig.run(a.source, drv, a.pedals.split(","), a.udp_port)
        finally:
            if ser:
                ser.close()
    elif a.cmd == "board":
        from . import board
        board.serve(a.port, record=not a.no_record)


def _s12_args(p):
    p.add_argument("--serial", help="serial port (default: find the box, else the base)")
    p.add_argument("--mode", choices=["auto", s12.USB, s12.EXT], default="auto",
                   help="usb = box on its own USB port, ext = box behind the wheelbase")


def _open_s12(a):
    port, hint = s12.find(a.serial)
    if port is None:
        sys.exit("no MOZA serial port. Plug the PHF box (or the base it hangs off) into this machine.")
    ser = discover.open_port(port)
    mode = a.mode if a.mode != "auto" else s12.detect(ser, hint)
    if mode is None:
        ser.close()
        sys.exit(f"{port}: no answer from the PHF box. Check its 12 V supply and cabling; "
                 "if another program holds this port (e.g. the wheel dash bridge), stop it first.")
    return ser, mode


def _ints(spec: str) -> list[int]:
    out = []
    for part in spec.split(","):
        if "-" in part:
            lo, hi = part.split("-")
            out += list(range(int(lo), int(hi) + 1))
        else:
            out.append(int(part))
    return out


if __name__ == "__main__":
    sys.exit(main())
