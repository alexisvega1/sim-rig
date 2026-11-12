"""MOZA Racing serial frame codec.

Framing is taken from Boxflat's moza-protocol.md (GPL-3.0, Tomasz Pakuła):

    | 0x7e | len | group | device | cmd id(s) | value bytes | checksum |

- `len`      : number of payload bytes (command ids + values)
- `group`    : request group. Responses add 0x80.
- `device`   : device id. Responses nibble-swap it (0x13 -> 0x31).
- checksum   : (MAGIC + sum(all preceding bytes)) mod 256, MAGIC = 13
- values are big-endian.

Known device ids (from Boxflat data/serial.yml):
    main 18, base 19, dash 20, wheel 21/23, pedals 25, shifter 26,
    handbrake 27, estop 28

The PHF control box id is NOT known yet. Candidates: an unused id in the
0x12..0x3f range, or new command ids under pedals (25). See discover.py.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Iterable, Iterator

START = 0x7E
MAGIC = 13
RESPONSE_BIT = 0x80

DEVICE_IDS = {
    "main": 18,
    "base": 19,
    "dash": 20,
    "wheel": 23,
    "pedals": 25,
    "hpattern": 26,
    "sequential": 26,
    "handbrake": 27,
    "estop": 28,
}
ID_TO_NAME = {18: "main", 19: "base", 20: "dash", 21: "wheel", 23: "wheel",
              25: "pedals", 26: "shifter", 27: "handbrake", 28: "estop"}

# Read/write request groups per device, from serial.yml. Used by the prober
# to know which groups are plausibly "read" groups (they come in r/w pairs).
KNOWN_GROUPS = {
    "main":   {"read": [30, 31], "write": [31]},
    "pedals": {"read": [35, 37], "write": [36, 38]},
    "base":   {"read": [40, 42, 43], "write": [41, 42]},
    "dash":   {"read": [51], "write": [50]},
}


def checksum(body: bytes) -> int:
    return (MAGIC + sum(body)) & 0xFF


def swap_nibbles(b: int) -> int:
    return ((b & 0x0F) << 4) | ((b & 0xF0) >> 4)


@dataclass
class Frame:
    group: int
    device: int
    ids: tuple[int, ...]
    values: bytes = b""
    raw: bytes = field(default=b"", repr=False)

    # ---- construction -------------------------------------------------
    @classmethod
    def build(cls, group: int, device: int, ids: Iterable[int], values: bytes = b"") -> "Frame":
        ids = tuple(ids)
        payload = bytes(ids) + bytes(values)
        body = bytes([START, len(payload), group & 0xFF, device & 0xFF]) + payload
        raw = body + bytes([checksum(body)])
        return cls(group, device, ids, bytes(values), raw)

    @classmethod
    def read_request(cls, group: int, device: int, ids: Iterable[int], nbytes: int) -> "Frame":
        """Boxflat note: read commands 'only work with a value of 1'."""
        val = (1).to_bytes(nbytes, "big") if nbytes > 0 else b""
        return cls.build(group, device, ids, val)

    @classmethod
    def write_int(cls, group: int, device: int, ids: Iterable[int], value: int, nbytes: int) -> "Frame":
        return cls.build(group, device, ids, int(value).to_bytes(nbytes, "big", signed=value < 0))

    @classmethod
    def write_float(cls, group: int, device: int, ids: Iterable[int], value: float) -> "Frame":
        return cls.build(group, device, ids, struct.pack(">f", value))

    # ---- parsing -------------------------------------------------------
    @classmethod
    def parse(cls, data: bytes, n_ids: int = 1) -> "Frame":
        """Parse exactly one frame. `n_ids` tells how many leading payload bytes
        are command ids (the wire format doesn't distinguish ids from values)."""
        if len(data) < 5 or data[0] != START:
            raise ValueError("not a MOZA frame")
        plen = data[1]
        if len(data) != 5 + plen:
            raise ValueError(f"length mismatch: header says {plen}, have {len(data) - 5}")
        if data[-1] != checksum(data[:-1]):
            raise ValueError("bad checksum")
        payload = data[4:-1]
        n_ids = max(0, min(n_ids, len(payload)))
        return cls(data[2], data[3], tuple(payload[:n_ids]), bytes(payload[n_ids:]), bytes(data))

    @property
    def is_response(self) -> bool:
        return bool(self.group & RESPONSE_BIT)

    @property
    def request_group(self) -> int:
        return self.group & ~RESPONSE_BIT

    @property
    def request_device(self) -> int:
        """Device id as the *request* would have carried it."""
        return swap_nibbles(self.device) if self.is_response else self.device

    @property
    def payload(self) -> bytes:
        return bytes(self.ids) + self.values

    def key(self) -> tuple[int, int, bytes]:
        """Grouping key used by the diff tool: (req group, req device, payload)."""
        return (self.request_group, self.request_device, self.payload)

    def hex(self) -> str:
        return self.raw.hex(" ")

    def __str__(self) -> str:
        kind = "RSP" if self.is_response else "REQ"
        dev = ID_TO_NAME.get(self.request_device, f"dev{self.request_device}")
        return (f"{kind} grp={self.request_group:3d} dev={self.request_device:3d}({dev}) "
                f"payload={self.payload.hex(' ')}")


def split_frames(stream: bytes) -> Iterator[bytes]:
    """Split a byte stream into frames using the length byte, resyncing on 0x7e
    when the checksum fails. Devices chain multiple frames in one USB packet."""
    i = 0
    n = len(stream)
    while i < n:
        if stream[i] != START:
            i += 1
            continue
        if i + 1 >= n:
            return
        plen = stream[i + 1]
        end = i + 5 + plen
        if end <= n and stream[end - 1] == checksum(stream[i:end - 1]):
            yield stream[i:end]
            i = end
        else:
            i += 1  # resync


def parse_stream(stream: bytes, n_ids: int = 1) -> list[Frame]:
    return [Frame.parse(f, n_ids) for f in split_frames(stream)]
