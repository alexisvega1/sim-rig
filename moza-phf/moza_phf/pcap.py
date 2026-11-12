"""Extract MOZA serial frames from USB captures.

Supported link types (legacy .pcap only — in Wireshark use File > Save As > pcap):
  249  USBPcap   (Windows / Boot Camp, where Pit House runs)
  189/220  usbmon (Linux: sudo modprobe usbmon; sudo tcpdump -i usbmonN -w x.pcap)

Only bulk transfers are kept; MOZA bases/boxes speak CDC-ACM, so the serial
stream rides on bulk endpoints. We concatenate payloads per (bus, device,
direction) and run the frame splitter over the result, so chained frames and
frames split across URBs both decode.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from .protocol import Frame, split_frames

LINKTYPE_USBMON = 189        # 48-byte header
LINKTYPE_USBMON_MMAP = 220   # 64-byte header (what tcpdump/Wireshark write on Linux)
LINKTYPE_USBPCAP = 249


@dataclass
class UsbPacket:
    ts: float
    bus: int
    device: int
    endpoint: int        # low 4 bits
    direction_in: bool   # True = device -> host
    transfer: int        # 2 = control, 3 = bulk (USBPcap numbering)
    data: bytes


def _iter_pcap(path: Path) -> Iterator[tuple[int, float, bytes]]:
    with open(path, "rb") as f:
        gh = f.read(24)
        if len(gh) < 24:
            raise ValueError("not a pcap file")
        magic = struct.unpack("<I", gh[:4])[0]
        if magic in (0xA1B2C3D4, 0xA1B23C4D):
            endian = "<"
        elif magic in (0xD4C3B2A1, 0x4D3CB2A1):
            endian = ">"
        elif magic == 0x0A0D0D0A:
            raise ValueError("pcapng not supported: in Wireshark do File > Save As > pcap")
        else:
            raise ValueError("unknown pcap magic")
        nanos = magic in (0xA1B23C4D, 0x4D3CB2A1)
        linktype = struct.unpack(endian + "I", gh[20:24])[0]
        while True:
            ph = f.read(16)
            if len(ph) < 16:
                return
            sec, frac, incl, _orig = struct.unpack(endian + "IIII", ph)
            ts = sec + frac / (1e9 if nanos else 1e6)
            data = f.read(incl)
            yield linktype, ts, data


def _parse_usbpcap(ts: float, pkt: bytes) -> UsbPacket | None:
    # USBPCAP_BUFFER_PACKET_HEADER
    if len(pkt) < 27:
        return None
    hlen, _irp, _status, _func, _info, bus, dev, ep, transfer, dlen = struct.unpack(
        "<HQIHBHHBBI", pkt[:27])
    data = pkt[hlen:hlen + dlen]
    return UsbPacket(ts, bus, dev, ep & 0x0F, bool(ep & 0x80), transfer, data)


def _parse_usbmon(ts: float, pkt: bytes, hlen: int = 48) -> UsbPacket | None:
    # struct usbmon_packet. transfer types: 2=control, 3=bulk
    if len(pkt) < hlen:
        return None
    _id, _type, xfer, ep, dev, bus = struct.unpack("<QBBBBH", pkt[:14])
    dlen = struct.unpack("<I", pkt[36:40])[0]
    data = pkt[hlen:hlen + dlen]
    return UsbPacket(ts, bus, dev, ep & 0x0F, bool(ep & 0x80), xfer, data)


def read_usb_packets(path: str | Path) -> list[UsbPacket]:
    out = []
    for linktype, ts, raw in _iter_pcap(Path(path)):
        if linktype == LINKTYPE_USBPCAP:
            p = _parse_usbpcap(ts, raw)
        elif linktype == LINKTYPE_USBMON:
            p = _parse_usbmon(ts, raw, 48)
        elif linktype == LINKTYPE_USBMON_MMAP:
            p = _parse_usbmon(ts, raw, 64)
        else:
            raise ValueError(f"unsupported link type {linktype}; capture with USBPcap or usbmon")
        if p and p.transfer == 3 and p.data:
            out.append(p)
    return out


@dataclass
class TimedFrame:
    ts: float
    bus: int
    device: int
    direction_in: bool
    frame: Frame


def frames_from_capture(path: str | Path, device: int | None = None, n_ids: int = 1) -> list[TimedFrame]:
    """Decode MOZA frames from a capture. `device` filters by USB device address
    (the number USBPcap shows, not the MOZA device id)."""
    streams: dict[tuple[int, int, bool], list[UsbPacket]] = {}
    for p in read_usb_packets(path):
        if device is not None and p.device != device:
            continue
        streams.setdefault((p.bus, p.device, p.direction_in), []).append(p)

    out: list[TimedFrame] = []
    for (bus, dev, direction), pkts in streams.items():
        # Reassemble: frames may be split across URBs, so walk a rolling buffer
        # and attribute each frame to the timestamp of the URB it started in.
        buf = b""
        buf_ts: list[tuple[int, float]] = []   # (offset, ts)
        for p in pkts:
            buf_ts.append((len(buf), p.ts))
            buf += p.data
        pos = 0
        for raw in split_frames(buf):
            start = buf.find(raw, pos)
            pos = start + len(raw)
            ts = max((t for off, t in buf_ts if off <= start), default=pkts[0].ts)
            try:
                fr = Frame.parse(raw, n_ids)
            except ValueError:
                continue
            out.append(TimedFrame(ts, bus, dev, direction, fr))
    out.sort(key=lambda t: t.ts)
    return out


def usb_devices(path: str | Path) -> dict[tuple[int, int], int]:
    """(bus, device) -> number of bulk packets. Use to find which address is the MOZA box."""
    counts: dict[tuple[int, int], int] = {}
    for p in read_usb_packets(path):
        counts[(p.bus, p.device)] = counts.get((p.bus, p.device), 0) + 1
    return counts
