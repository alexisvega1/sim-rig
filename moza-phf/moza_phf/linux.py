"""Linux-only pieces: USB/tty inspection and a serial man-in-the-middle.

sniff() sits between a program and the real port:

    Pit House (Wine)  <->  pty  <->  [sniff: forward + log]  <->  /dev/ttyACM0

so Pit House traffic can be recorded on Linux without usbmon or root. Whether
Pit House under Wine accepts a pty as its COM port is untested.
"""
from __future__ import annotations

import glob
import os
import select
import time
from pathlib import Path

from .pcap import TimedFrame
from .protocol import Frame, split_frames


def doctor() -> str:
    import sys
    if sys.platform == "darwin":
        return _doctor_mac()
    lines = []
    found = False
    for d in sorted(glob.glob("/sys/bus/usb/devices/*")):
        vid = _read(d + "/idVendor")
        if vid != "346e":
            continue
        found = True
        lines.append(f"{os.path.basename(d)}: {_read(d + '/manufacturer')} {_read(d + '/product')} "
                     f"[346e:{_read(d + '/idProduct')}] serial={_read(d + '/serial')}")
        for itf in sorted(glob.glob(d + ":*")):
            cls = _read(itf + "/bInterfaceClass")
            name = {"02": "CDC control", "0a": "CDC data", "03": "HID", "ff": "vendor"}.get(cls, cls)
            ttys = [os.path.basename(t) for t in glob.glob(itf + "/tty/tty*")]
            hid = [os.path.basename(h) for h in glob.glob(itf + "/*/hidraw/hidraw*")]
            lines.append(f"   itf {os.path.basename(itf)} class={name} driver={_driver(itf)}"
                         + (f" tty={','.join(ttys)}" if ttys else "") + (f" {','.join(hid)}" if hid else ""))
            for t in ttys:
                p = "/dev/" + t
                ok = os.access(p, os.R_OK | os.W_OK)
                lines.append(f"      {p}: {'read/write OK' if ok else 'NO ACCESS (install the udev rule, replug)'}")
    if not found:
        lines.append("no USB device with vendor 346e (MOZA). Check cable/power; `dmesg | tail`.")
    return "\n".join(lines)


def _doctor_mac() -> str:
    from serial.tools import list_ports
    lines = []
    for p in list_ports.comports():
        if p.vid == 0x346E:
            ok = os.access(p.device, os.R_OK | os.W_OK)
            lines.append(f"{p.device}: {p.manufacturer} {p.product} [346e:{p.pid:04x}] serial={p.serial_number} "
                         f"{'read/write OK' if ok else 'NO ACCESS'}")
    return "\n".join(lines) or ("no MOZA serial port (vendor 346e). Plug the base or PHF box into the Mac; "
                                "it should appear as /dev/cu.usbmodem*.")


def _read(p: str) -> str:
    try:
        return Path(p).read_text().strip()
    except OSError:
        return ""


def _driver(itf: str) -> str:
    try:
        return os.path.basename(os.readlink(itf + "/driver"))
    except OSError:
        return "none"


def sniff(real_port: str, link: str, log: str, seconds: float | None = None) -> None:
    import serial
    import termios
    import tty

    master, slave = os.openpty()
    tty.setraw(slave)
    attrs = termios.tcgetattr(slave)
    attrs[3] &= ~termios.ECHO
    termios.tcsetattr(slave, termios.TCSANOW, attrs)
    slave_name = os.ttyname(slave)
    link = os.path.expanduser(link)
    if os.path.islink(link):
        os.unlink(link)
    os.symlink(slave_name, link)
    ser = serial.Serial(real_port, 115200, timeout=0)
    print(f"sniffing: {link} -> {slave_name} <-> {real_port}; logging to {log}  (Ctrl-C to stop)")
    end = None if seconds is None else time.monotonic() + seconds
    try:
        with open(log, "a") as lf:
            while end is None or time.monotonic() < end:
                r, _, _ = select.select([master, ser.fileno()], [], [], 0.2)
                for fd in r:
                    if fd == master:
                        try:
                            data = os.read(master, 4096)
                        except OSError:
                            data = b""   # client closed; keep waiting for reopen
                            time.sleep(0.05)
                        if data:
                            ser.write(data)
                            lf.write(f"{time.time():.6f} > {data.hex()}\n")
                    else:
                        data = ser.read(4096)
                        if data:
                            os.write(master, data)
                            lf.write(f"{time.time():.6f} < {data.hex()}\n")
                lf.flush()
    except KeyboardInterrupt:
        pass
    finally:
        ser.close()
        if os.path.islink(link):
            os.unlink(link)
        os.close(master); os.close(slave)


def frames_from_log(path: str) -> list[TimedFrame]:
    """Reassemble per direction, like the pcap reader."""
    bufs = {">": b"", "<": b""}
    marks: dict[str, list[tuple[int, float]]] = {">": [], "<": []}
    for line in open(path):
        parts = line.split()
        if len(parts) != 3 or parts[1] not in bufs:
            continue
        marks[parts[1]].append((len(bufs[parts[1]]), float(parts[0])))
        bufs[parts[1]] += bytes.fromhex(parts[2])
    out = []
    for d, buf in bufs.items():
        pos = 0
        for raw in split_frames(buf):
            start = buf.find(raw, pos)
            pos = start + len(raw)
            ts = max((t for off, t in marks[d] if off <= start), default=0.0)
            out.append(TimedFrame(ts, 0, 0, d == "<", Frame.parse(raw)))
    out.sort(key=lambda t: t.ts)
    return out
