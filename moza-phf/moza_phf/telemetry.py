"""Game telemetry sources (UDP). Each yields Telemetry samples.

Implemented decoders:
  ac           — Assetto Corsa remote telemetry (UDP 9996, handshake; always on)
  codemasters  — DiRT Rally 2.0 / DiRT 4 / GRID "extradata=3" float array
                 (enable in hardware_settings_config.xml: <udp enabled="true"
                 extradata="3" ip="127.0.0.1" port="20777" delay="1"/>)
  outgauge     — BeamNG.drive OutGauge (Options > Other > OutGauge, port 4444)

Both are plain UDP and work from Proton games to a native Linux listener on
localhost. Neither has been validated against a live game here — field offsets
are from the games' published docs; check the `dump` CLI command first.
"""
from __future__ import annotations

import socket
import struct
from dataclasses import dataclass
from typing import Iterator


@dataclass
class Telemetry:
    rpm: float = 0.0
    max_rpm: float = 8000.0
    speed_ms: float = 0.0
    throttle: float = 0.0      # 0..1
    brake: float = 0.0         # 0..1
    gear: int = 0
    wheel_slip: float = 0.0    # 0..1, max over wheels (|wheel speed - car speed| normalised)
    lockup: float = 0.0        # 0..1, brake-side slip only
    abs_active: bool = False
    tc_active: bool = False
    lap_ms: int | None = None  # running lap clock where the game sends one; it stands still while paused

    @property
    def rpm_frac(self) -> float:
        return min(1.0, self.rpm / self.max_rpm) if self.max_rpm else 0.0


def _udp(port: int, host: str = "0.0.0.0") -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((host, port))
    s.settimeout(1.0)
    return s


def codemasters(port: int = 20777) -> Iterator[Telemetry]:
    """extradata=3 packet: 66 floats (264 bytes). Indices per community docs."""
    s = _udp(port)
    while True:
        try:
            data, _ = s.recvfrom(2048)
        except socket.timeout:
            continue
        if len(data) < 264:
            continue
        f = struct.unpack("<66f", data[:264])
        # Index map (DiRT Rally 2.0 extradata=3): 7 speed m/s, 25-28 wheel
        # speeds (RL,RR,FL,FR), 29 throttle, 30 steer, 31 brake, 32 clutch,
        # 33 gear, 37 engine rate (rpm/10), 63 max rpm (/10), 64 idle rpm (/10).
        speed = f[7]
        wheel_speeds = f[25:29]
        slips = [abs(abs(ws) - speed) / max(speed, 1.0) for ws in wheel_speeds]
        slip = min(1.0, max(slips))
        t = Telemetry(
            rpm=f[37] * 10.0, max_rpm=(f[63] * 10.0) or 8000.0,
            speed_ms=speed, throttle=f[29], brake=f[31],
            gear=int(f[33]), wheel_slip=slip,
            lockup=slip if f[31] > 0.1 else 0.0,
        )
        t.tc_active = t.throttle > 0.5 and t.wheel_slip > 0.15
        t.abs_active = t.brake > 0.5 and t.lockup > 0.15
        yield t


def outgauge(port: int = 4444) -> Iterator[Telemetry]:
    """BeamNG OutGauge (LFS-compatible) 92/96-byte packet."""
    s = _udp(port)
    fmt = "<I4sH2c7f2I3f16s16si"
    size = struct.calcsize(fmt)
    while True:
        try:
            data, _ = s.recvfrom(2048)
        except socket.timeout:
            continue
        if len(data) < size:
            continue
        (_t, _car, flags, gear, _plid, speed, rpm, _turbo, _engtemp, _fuel, _oilp,
         _oilt, dash, show, throttle, brake, _clutch, _d1, _d2, _id) = struct.unpack(fmt, data[:size])
        g = gear[0] - 1  # OutGauge: 0=R,1=N,2=1st...
        dl_abs = bool(dash & (1 << 10))
        dl_tc = bool(dash & (1 << 9))
        yield Telemetry(rpm=rpm, speed_ms=speed, throttle=throttle, brake=brake, gear=g,
                        abs_active=dl_abs, tc_active=dl_tc,
                        lockup=1.0 if dl_abs else 0.0, wheel_slip=1.0 if dl_tc else 0.0)


def _ac_parse(data: bytes, max_rpm: float) -> Telemetry:
    """RTCarInfo (328 bytes), layout from Kunos' AC remote telemetry doc."""
    speed_ms = struct.unpack_from("<f", data, 16)[0]
    abs_on, tc_on = bool(data[21]), bool(data[22])
    gas, brake, _clutch, rpm = struct.unpack_from("<4f", data, 56)
    gear = struct.unpack_from("<i", data, 76)[0] - 1          # AC: 0=R, 1=N, 2=1st
    omega = struct.unpack_from("<4f", data, 84)               # wheelAngularSpeed, rad/s
    radius = struct.unpack_from("<4f", data, 276)             # tyreLoadedRadius, m
    if min(radius) > 0.05 and speed_ms > 2:
        # wheel surface speed against car speed: sign-proof, unlike slipRatio
        wheel = [abs(w) * r for w, r in zip(omega, radius)]
        lock = min(1.0, max(0.0, max((speed_ms - w) / speed_ms for w in wheel)))
        spin = min(1.0, max(0.0, max((w - speed_ms) / speed_ms for w in wheel)))
    else:
        slip_ratio = struct.unpack_from("<4f", data, 132)     # FL FR RL RR; <0 locking, >0 spinning
        lock = min(1.0, max(0.0, max(-s for s in slip_ratio)))
        spin = min(1.0, max(0.0, max(slip_ratio)))
    return Telemetry(rpm=rpm, max_rpm=max_rpm, speed_ms=speed_ms, throttle=gas, brake=brake,
                     gear=gear, wheel_slip=spin if gas > 0.05 else 0.0,
                     lockup=lock if brake > 0.05 and speed_ms > 2 else 0.0,
                     abs_active=abs_on, tc_active=tc_on,
                     lap_ms=struct.unpack_from("<i", data, 40)[0])


def assetto_corsa(port: int = 9996, host: str = "127.0.0.1") -> Iterator[Telemetry]:
    """Assetto Corsa built-in UDP telemetry. We are the client: handshake to the
    game on :9996, subscribe, then it streams RTCarInfo. Works across the
    Wine/CrossOver boundary because Wine sockets are host sockets. Nothing to
    enable in the game; it only answers while a session is loaded."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(1.0)
    addr = (host, port)
    max_rpm = 6000.0
    subscribed = False
    try:
        while True:
            if not subscribed:
                s.sendto(struct.pack("<iii", 1, 1, 0), addr)          # HANDSHAKE
                try:
                    data, _ = s.recvfrom(2048)
                except (socket.timeout, ConnectionError):
                    continue                                           # game not in a session yet
                if len(data) < 408:
                    continue
                s.sendto(struct.pack("<iii", 1, 1, 1), addr)          # SUBSCRIBE_UPDATE
                subscribed = True
                continue
            try:
                data, _ = s.recvfrom(2048)
            except (socket.timeout, ConnectionError):
                subscribed = False                                     # session ended; re-handshake
                continue
            if len(data) < 328:
                continue
            t = _ac_parse(data, max_rpm)
            max_rpm = max(max_rpm, t.rpm)                              # AC doesn't send redline
            t.max_rpm = max_rpm
            yield t
    finally:
        try:
            s.sendto(struct.pack("<iii", 1, 1, 3), addr)              # DISMISS
        except OSError:
            pass
        s.close()


SOURCES = {"ac": assetto_corsa, "codemasters": codemasters, "outgauge": outgauge}
