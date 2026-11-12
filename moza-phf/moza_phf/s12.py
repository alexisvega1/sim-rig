"""MOZA S12 pedal vibration module — the PHF kit's Control Box — wire protocol.

The command was decoded from a Pit House capture by giantorth/moza-simhub-plugin
(GPL-3.0); this follows its docs/protocol/devices/pedal-haptics.md.

    7e  LEN  4d  ADDR  [1e]   01  SC  PD  SL  EN   DH DL   FH FL   SH SL   CK

- group 0x4d, replies on 0xcd
- box on its own USB port (346e:002f): ADDR 0x12 (reply 0x21), LEN 11
- box behind a wheelbase: ADDR 0x1f (reply 0xf1) + extended id 0x1e, LEN 12
- SC 01 set / 02 query;  PD 1 throttle, 2 brake, 3 clutch;  SL effect slot 0..8
- EN 1/0; duration ms, frequency Hz (10..100), strength (0..65535), all u16 BE
- checksum = (13 + sum of bytes, counting every 0x7e after the length twice);
  on the wire every 0x7e after the length byte is doubled.

Each pedal has nine concurrent slots (independent oscillators). A slot runs for
its duration from the last enable frame, so an effect is held on by re-sending
and dies by itself if the host stops. Engine (4) and Road texture (8) instead
time out after ~1 s of silence.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

START = 0x7E
MAGIC = 13
GROUP, GROUP_REPLY = 0x4D, 0xCD
ADDR_USB, ADDR_USB_REPLY = 0x12, 0x21
ADDR_EXT, ADDR_EXT_REPLY = 0x1F, 0xF1
EXT_ID = 0x1E
S12_PID = 0x002F

USB, EXT = "usb", "ext"
PEDALS = {"throttle": 1, "brake": 2, "clutch": 3}
SLOTS = {"tc": 0, "abs": 1, "lockup": 2, "threshold": 3, "engine": 4,
         "bite": 5, "shift": 6, "slip": 7, "road": 8}

STREAM_MS = 500        # duration written into a streamed frame
UPDATE_S = 0.020       # shortest gap between updates of one slot
REFRESH_S = 0.100      # an unchanged live slot is re-sent this often


def wire_checksum(body: bytes) -> int:
    return (MAGIC + sum(body) + START * body[2:].count(START)) & 0xFF


def stuff(frame: bytes) -> bytes:
    return frame[:2] + frame[2:].replace(b"\x7e", b"\x7e\x7e")


def build(addressing: str, sub: int, pedal: int, slot: int, enable: int = 0,
          duration_ms: int = 0, freq: int = 0, strength: int = 0) -> bytes:
    """Unstuffed frame; pass through stuff() before writing."""
    if not 1 <= pedal <= 3 or not 0 <= slot <= 8:
        raise ValueError(f"pedal {pedal} / slot {slot} out of range")
    payload = bytes([1, sub, pedal, slot, enable]) \
        + duration_ms.to_bytes(2, "big") + freq.to_bytes(2, "big") + strength.to_bytes(2, "big")
    if addressing == EXT:
        body = bytes([START, len(payload) + 1, GROUP, ADDR_EXT, EXT_ID]) + payload
    else:
        body = bytes([START, len(payload), GROUP, ADDR_USB]) + payload
    return body + bytes([wire_checksum(body)])


def set_frame(addressing: str, pedal: int, slot: int, freq_hz: float, strength01: float,
              duration_ms: int = STREAM_MS) -> bytes:
    freq = int(round(freq_hz))
    freq = max(0, min(100, freq)) if slot == SLOTS["road"] else max(10, min(100, freq))
    strength = max(0, min(0xFFFF, int(round(strength01 * 0xFFFF))))
    return build(addressing, 1, pedal, slot, 1, max(1, min(60000, int(duration_ms))), freq, strength)


def stop_frame(addressing: str, pedal: int, slot: int) -> bytes:
    return build(addressing, 1, pedal, slot)


def query_frame(addressing: str, pedal: int, slot: int) -> bytes:
    return build(addressing, 2, pedal, slot)


@dataclass
class Reply:
    addressing: str
    sub: int
    pedal: int
    slot: int
    enabled: bool
    remaining_ms: int
    freq: int
    strength: int


def unstuffed_frames(stream: bytes) -> list[bytes]:
    """Complete, checksum-valid frames in a raw wire stream, with the 0x7e doubling undone."""
    out = []
    i = 0
    while i + 1 < len(stream):
        if stream[i] != START:
            i += 1
            continue
        need = stream[i + 1] + 3                      # group, address, payload, checksum
        body, j = bytearray(), i + 2
        while len(body) < need and j < len(stream):
            body.append(stream[j])
            j += 2 if stream[j] == START and stream[j + 1:j + 2] == b"\x7e" else 1
        frame = stream[i:i + 2] + bytes(body)
        if len(body) < need or frame[-1] != wire_checksum(frame[:-1]):
            i += 1                                    # truncated or not a frame: resync
            continue
        out.append(frame)
        i = j
    return out


def parse_replies(stream: bytes) -> list[Reply]:
    """Vibration replies found in a raw (stuffed) byte stream; everything else is skipped."""
    out = []
    for frame in unstuffed_frames(stream):
        group, addr, p = frame[2], frame[3], frame[4:-1]
        if group != GROUP_REPLY:
            continue
        if addr == ADDR_EXT_REPLY and p[:1] == bytes([EXT_ID]):
            mode, p = EXT, p[1:]
        elif addr == ADDR_USB_REPLY:
            mode = USB
        else:
            continue
        if len(p) < 11 or p[0] != 1:
            continue
        out.append(Reply(mode, p[1], p[2], p[3], bool(p[4]), int.from_bytes(p[5:7], "big"),
                         int.from_bytes(p[7:9], "big"), int.from_bytes(p[9:11], "big")))
    return out


def query(ser, addressing: str, pedal: int = 2, slot: int = 0, wait: float = 0.25) -> Reply | None:
    """Read one slot back. A query changes nothing; an answer proves the box is there."""
    ser.reset_input_buffer()
    ser.write(stuff(query_frame(addressing, pedal, slot)))
    deadline, data = time.monotonic() + wait, b""
    while time.monotonic() < deadline:
        data += ser.read(ser.in_waiting or 1)
        for r in parse_replies(data):
            if r.addressing == addressing and r.sub == 2 and (r.pedal, r.slot) == (pedal, slot):
                return r
    return None


def find(port: str | None = None) -> tuple[str | None, str | None]:
    """(port, envelope): the box's own port if it is on USB, else a MOZA port to
    route through. Envelope is None when the port is not a recognisable MOZA one."""
    try:
        from serial.tools import list_ports
        moza = [p for p in list_ports.comports() if p.vid == 0x346E]
    except ImportError:
        moza = []
    if port:
        pid = next((p.pid for p in moza if p.device == port), None)
        return port, None if pid is None else USB if pid == S12_PID else EXT
    for p in moza:
        if p.pid == S12_PID:
            return p.device, USB
    return (moza[0].device, EXT) if moza else (None, None)


def detect(ser, hint: str | None = None) -> str | None:
    """The envelope the box answers on this port, or None. Only the envelope that
    fits the port is tried unless nothing is known about it."""
    for mode in ([hint] if hint else [USB, EXT]):
        if query(ser, mode):
            return mode
    return None


class S12Driver:
    """Latest-state streamer: show() what each slot should be doing, flush() sends
    only what is due — enable edges at once, changes at most every 20 ms, an
    unchanged live slot every 100 ms, and an explicit stop when a slot goes quiet."""

    def __init__(self, ser, addressing: str):
        self.ser = ser
        self.addressing = addressing
        self._want: dict[tuple[int, int], tuple[int, int]] = {}
        self._sent: dict[tuple[int, int], tuple[int, int, float]] = {}

    def show(self, want: dict[tuple[int, int], tuple[float, float]]) -> None:
        """Replace the wanted state: {(pedal, slot): (freq Hz, strength 0..1)}. Anything absent stops."""
        self._want = {k: (int(round(f)), int(round(min(1.0, s) * 100)))
                      for k, (f, s) in want.items() if s > 0.005}

    def flush(self, now: float | None = None) -> int:
        now = time.monotonic() if now is None else now
        n = 0
        for key in [k for k in self._sent if k not in self._want]:      # stops first
            self.ser.write(stuff(stop_frame(self.addressing, *key)))
            del self._sent[key]
            n += 1
        for key, (freq, pct) in self._want.items():
            last = self._sent.get(key)
            if last is not None:
                age = now - last[2]
                if age < (UPDATE_S if (freq, pct) != last[:2] else REFRESH_S):
                    continue
            self.ser.write(stuff(set_frame(self.addressing, *key, freq, pct / 100)))
            self._sent[key] = (freq, pct, now)
            n += 1
        if self.ser.in_waiting:
            self.ser.reset_input_buffer()                               # drop the echoes
        return n

    def stop_all(self) -> None:
        self._want.clear()
        self.flush()
