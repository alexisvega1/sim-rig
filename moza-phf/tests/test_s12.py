"""S12 (PHF Control Box) protocol. The byte vectors are the worked examples in
giantorth/moza-simhub-plugin docs/protocol/devices/pedal-haptics.md, which come
from a Pit House capture — so the frame format is checked against real traffic,
though nothing here has driven a real box."""
import time

import pytest

from moza_phf import discover, s12
from moza_phf.sim import FakeS12

h = bytes.fromhex


def test_documented_frames():
    assert s12.set_frame(s12.USB, 2, 0, 50, 0x8000 / 0xFFFF, 500) == h("7e0b4d12010102000101f400328000a1")
    assert s12.set_frame(s12.EXT, 2, 0, 50, 0x8000 / 0xFFFF, 500) == h("7e0c4d1f1e010102000101f400328000cd")
    assert s12.stop_frame(s12.USB, 2, 0) == h("7e0b4d120101020000000000000000f9")
    assert s12.query_frame(s12.USB, 2, 0) == h("7e0b4d120102020000000000000000fa")


def test_documented_reply_parses():
    (r,) = s12.parse_replies(h("7e0bcd21010202000000000000000089"))
    assert (r.addressing, r.sub, r.pedal, r.slot, r.enabled) == (s12.USB, 2, 2, 0, False)


def test_0x7e_is_counted_twice_and_doubled_on_the_wire():
    fr = s12.build(s12.USB, 1, 2, 1, 1, 0x7E, 50, 0x7E7E)       # duration 126 ms, strength 0x7e7e
    wire = s12.stuff(fr)
    assert len(wire) == len(fr) + 3 and wire.count(b"\x7e\x7e") == 3
    assert s12.unstuffed_frames(b"\x00junk" + wire + wire) == [fr, fr]
    assert s12.unstuffed_frames(wire[:-2]) == []                # truncated: nothing, no crash


def test_clamps():
    assert s12.set_frame(s12.USB, 2, 1, 500, 2.0)[11:15] == h("0064ffff")     # 100 Hz, full scale
    assert s12.set_frame(s12.USB, 2, 1, 1, 0.2)[11:15] == h("000a3333")       # 10 Hz floor; 20 % = 0x3333
    with pytest.raises(ValueError):
        s12.build(s12.USB, 1, 4, 0)


@pytest.mark.parametrize("mode", [s12.USB, s12.EXT])
def test_driver_against_fake_box(mode):
    box = FakeS12(mode)
    try:
        with discover.open_port(box.port) as ser:
            assert s12.detect(ser) == mode
            other = s12.EXT if mode == s12.USB else s12.USB
            assert s12.query(ser, other, wait=0.05) is None     # wrong envelope: silence
            drv = s12.S12Driver(ser, mode)
            t0 = time.monotonic()
            while time.monotonic() - t0 < 0.35:
                drv.show({(2, 1): (30, 0.8)})
                drv.flush()
                time.sleep(0.005)
            time.sleep(0.05)
            sets = [e for e in box.log if e[0] == "set"]
            assert 3 <= len(sets) <= 6                          # enable + ~100 ms refreshes, not a flood
            assert sets[0] == ("set", 2, 1, 500, 30, round(0.8 * 0xFFFF))
            live = s12.query(ser, mode, 2, 1)
            assert live.enabled and live.freq == 30
            drv.show({(2, 1): (45, 0.5)})                       # a change goes out without waiting for refresh
            time.sleep(0.03); drv.flush(); time.sleep(0.05)
            assert box.log[-1][:5] == ("set", 2, 1, 500, 45)
            drv.stop_all(); time.sleep(0.05)
            assert box.log[-1] == ("stop", 2, 1) and not box.slots
    finally:
        box.close()
