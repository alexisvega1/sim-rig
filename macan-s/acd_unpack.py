#!/usr/bin/env python3
"""Unpack an Assetto Corsa data.acd into a folder of plain .ini/.lut files.

Usage: acd_unpack.py <car folder> <output folder>

The file format and key derivation follow the open-source reader in
JustaPenguin/assetto-server-manager (pkg/acd/reader.go), itself based on Luigi
Auriemma's QuickBMS "Assetto Corsa ACD" script. The key comes from the car's
folder name, so the archive must be read from (a copy named like) the original folder.
"""
import struct
import sys
from pathlib import Path


def _tdiv(a: int, b: int) -> int:
    """Integer division truncating toward zero, as the reference (Go) does."""
    q = abs(a) // abs(b)
    return q if (a < 0) == (b < 0) else -q


def cipher_key(folder: str) -> str:
    f = folder.encode("latin-1")
    n = len(f)
    p1 = sum(f)
    p2 = 0
    for i in range(0, n - 1, 2):
        p2 = p2 * f[i] - f[i + 1]
    p3, i = 0, 1
    while i < n - 3:
        p3 = _tdiv(p3 * f[i], (f[i + 1] + 0x1B) & 0xFF) - 0x1B - f[i - 1]
        i += 3
    p4 = 0x1683 - sum(f[1:])
    p5, i = 0x42, 1
    while i < n - 4:
        p5 = (f[i - 1] + 0xF) * (((f[i] + 0xF) & 0xFF) * p5) + 0x16
        i += 4
    p6 = 0x65 - sum(f[i] for i in range(0, n - 2, 2))
    p7 = 0xAB
    for i in range(0, n - 2, 2):
        p7 %= f[i]
    p8 = 0xAB
    for i in range(0, n - 1):
        p8 = _tdiv(p8, f[i]) + f[i + 1]
    return "-".join(str(p & 0xFF) for p in (p1, p2, p3, p4, p5, p6, p7, p8))


def read_acd(path: Path, folder: str) -> dict[str, bytes]:
    key = cipher_key(folder).encode()
    data = path.read_bytes()
    out, pos = {}, 0
    while pos + 4 <= len(data):
        (strlen,) = struct.unpack_from("<i", data, pos); pos += 4
        if strlen == -1111:                       # header marker: skip one int, then the real length
            (strlen,) = struct.unpack_from("<i", data, pos + 4); pos += 8
        name = data[pos:pos + strlen].decode("latin-1"); pos += strlen
        (length,) = struct.unpack_from("<i", data, pos); pos += 4
        raw = bytearray(data[pos:pos + length * 4]); pos += length * 4
        x = 0
        for b in range(len(raw)):
            if raw[b]:
                raw[b] = (raw[b] - key[x % len(key)]) & 0xFF
                x += 1
        out[name] = bytes(raw[0::4])              # one character per little-endian int32
    return out


if __name__ == "__main__":
    car, dest = Path(sys.argv[1]), Path(sys.argv[2])
    files = read_acd(car / "data.acd", car.name)
    dest.mkdir(parents=True, exist_ok=True)
    for name, body in files.items():
        (dest / Path(name).name).write_bytes(body)
    print(f"{len(files)} files -> {dest}")
