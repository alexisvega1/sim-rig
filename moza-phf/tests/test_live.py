"""Live-path tools against the fake device (pty). Proves the tools work end to
end; says nothing about the real PHF box."""
import socket
import struct
import threading
import time

import pytest

from moza_phf import discover
from moza_phf.effects import Pulse
from moza_phf.phf import PhfCommandSet, PhfDriver
from moza_phf.protocol import Frame
from moza_phf.sim import BOX, BUZZ_ID, RD, WR, FakeBox
from moza_phf.telemetry import assetto_corsa


@pytest.fixture
def box():
    b = FakeBox()
    yield b
    b.close()


def test_identify_finds_pedals(box):
    labels = [l for l, _ in discover.identify(box.port)]
    assert any("pedals" in l for l in labels) and not any("base" in l for l in labels)


def test_scan_then_fuzz_finds_buzz(box, tmp_path):
    hits = discover.scan(box.port, devices=[25, 30, BOX], groups=range(40, 50), ids=range(0, 6), wait=0.02)
    assert {h["device"] for h in hits} == {BOX}                 # pedals (known id) never swept
    assert {h["group"] for h in hits} == {RD, WR}
    # the caveat, demonstrated: the value-1 sweep WROTE 1 into the write group
    assert box.store[0] == 1 and not box.buzz_events
    discover.fuzz(hits, box.port, value=60, interval=0, log=str(tmp_path / "f.log"), only_groups={WR})
    assert box.buzz_events == [60]
    assert f"id={BUZZ_ID} val=60" in (tmp_path / "f.log").read_text()


def test_driver_end_to_end(box):
    cmds = PhfCommandSet(device=BOX, write_group=WR, pedal_ids={"brake": [BUZZ_ID]},
                         freq_bytes=0, intensity_bytes=2)
    with discover.open_port(box.port) as ser:
        drv = PhfDriver(ser, cmds)
        drv.set("brake", Pulse(30, 0.8)); time.sleep(0.05)
        drv.stop(); time.sleep(0.05)
    assert box.buzz_events == [80] and box.store[BUZZ_ID] == 0


def test_sniff_logs_and_diff(box, tmp_path):
    from moza_phf.linux import frames_from_log, sniff
    link, log = str(tmp_path / "com1"), str(tmp_path / "s.log")
    t = threading.Thread(target=sniff, args=(box.port, link, log, 1.0), daemon=True)
    t.start(); time.sleep(0.3)
    req = Frame.write_int(WR, BOX, [BUZZ_ID], 70, 2)
    with discover.open_port(link) as ser:                       # stands in for Pit House
        rsp = discover.transact(ser, req, 0.3)
    t.join()
    assert rsp and rsp[0].is_response and box.buzz_events == [70]
    frames = frames_from_log(log)
    assert [f.frame.raw for f in frames if not f.direction_in] == [req.raw]
    assert any(f.direction_in and f.frame.is_response for f in frames)
    base = tmp_path / "idle.log"; base.write_text("")
    rep = discover.diff_captures(str(base), log)
    assert list(rep.only_in_action) == [(WR, BOX, bytes([BUZZ_ID, 0, 70]))]


def test_assetto_corsa_client():
    srv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); srv.bind(("127.0.0.1", 0))
    port = srv.getsockname()[1]
    pkt = bytearray(328)
    struct.pack_into("<f", pkt, 16, 40.0)
    pkt[21] = 1                                                # isAbsInAction
    struct.pack_into("<4f", pkt, 56, 0.0, 0.9, 0.0, 5500.0)    # gas brake clutch rpm
    struct.pack_into("<i", pkt, 76, 4)                         # 3rd gear
    struct.pack_into("<4f", pkt, 132, -0.4, -0.1, 0.0, 0.0)    # slipRatio

    def serve():
        ops = []
        while len(ops) < 2:
            d, a = srv.recvfrom(64); ops.append(struct.unpack("<iii", d)[2])
            if ops[-1] == 0:
                srv.sendto(bytes(408), a)
        srv.sendto(bytes(pkt), a)
        assert ops == [0, 1]
    threading.Thread(target=serve, daemon=True).start()
    g = assetto_corsa(port)
    t = next(g); g.close()
    assert t.abs_active and t.gear == 3 and t.rpm == 5500.0
    assert abs(t.lockup - 0.4) < 1e-6 and t.wheel_slip == 0.0 and abs(t.brake - 0.9) < 1e-6
