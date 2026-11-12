"""PHF control box driver.

THE WIRE COMMANDS ARE NOT KNOWN YET. Everything below that touches the serial
port goes through `PhfCommandSet`, a table you fill in from captures (see
README "Finding the command"). Until it is filled, `PhfDriver.set()` raises
`NotImplementedError` rather than sending guessed bytes to the hardware.

Model of what Pit House does, inferred from the product page: per pedal
(THROTTLE / BRAKE / CLUTCH port) there is a frequency (0–100 Hz) and an
intensity (0–100 %). Whether the box takes "set freq+intensity now" as one
command or as two, and whether there is a separate enable/effect-type byte,
is exactly what the capture diff has to answer.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .effects import Pulse
from .protocol import Frame


@dataclass
class PhfCommandSet:
    """Fill from `moza-phf diff` output. All fields required before use."""
    device: int | None = None            # MOZA device id of the control box
    write_group: int | None = None       # request group for writes
    # command id prefix per pedal port; e.g. {"brake": [0x12]} — list because
    # MOZA ids can be multi-byte (see base ffb-curve-* in serial.yml)
    pedal_ids: dict[str, list[int]] = field(default_factory=dict)
    freq_bytes: int = 2
    intensity_bytes: int = 2
    # If freq and intensity are separate commands, set these to the trailing id
    # byte(s) that select each; if one command carries both, leave None and the
    # payload is freq || intensity.
    freq_suffix: list[int] | None = None
    intensity_suffix: list[int] | None = None

    def complete(self) -> bool:
        return self.device is not None and self.write_group is not None and bool(self.pedal_ids)

    def frames(self, pedal: str, pulse: Pulse) -> list[Frame]:
        if not self.complete():
            raise NotImplementedError(
                "PHF command set is unknown. Find it with `scan` + `fuzz` (or a Pit House "
                "capture + `diff`) and fill PhfCommandSet; see README.")
        ids = self.pedal_ids[pedal]
        f = int(round(max(0.0, min(100.0, pulse.freq_hz))))
        i = int(round(max(0.0, min(100.0, pulse.intensity * 100))))
        if self.freq_suffix is None and self.intensity_suffix is None:
            # freq_bytes == 0 means the command carries intensity only
            val = (f.to_bytes(self.freq_bytes, "big") if self.freq_bytes else b"") \
                + i.to_bytes(self.intensity_bytes, "big")
            return [Frame.build(self.write_group, self.device, ids, val)]
        return [
            Frame.write_int(self.write_group, self.device, ids + (self.freq_suffix or []), f, self.freq_bytes),
            Frame.write_int(self.write_group, self.device, ids + (self.intensity_suffix or []), i, self.intensity_bytes),
        ]


class PhfDriver:
    def __init__(self, ser, cmds: PhfCommandSet):
        self.ser = ser
        self.cmds = cmds
        self._last: dict[str, tuple[int, int]] = {}

    def set(self, pedal: str, pulse: Pulse) -> None:
        key = (int(pulse.freq_hz), int(pulse.intensity * 100))
        if self._last.get(pedal) == key:
            return  # don't spam identical frames
        self._last[pedal] = key
        for fr in self.cmds.frames(pedal, pulse):
            self.ser.write(fr.raw)

    def stop(self) -> None:
        for pedal in self.cmds.pedal_ids:
            self._last.pop(pedal, None)
            self.set(pedal, Pulse(0.0, 0.0))
