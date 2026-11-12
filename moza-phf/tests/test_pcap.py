"""Build a synthetic USBPcap file and check frames come back out, including a
frame split across two URBs and the baseline/action diff."""
import struct

from moza_phf.discover import diff_captures
from moza_phf.pcap import frames_from_capture, usb_devices
from moza_phf.protocol import Frame


def _usbpcap_packet(ts, data, endpoint_in=False, device=3, bus=1):
    ep = 0x02 | (0x80 if endpoint_in else 0)
    hdr = struct.pack("<HQIHBHHBBI", 27, 1, 0, 9, 0, bus, device, ep, 3, len(data))
    pkt = hdr + data
    sec, usec = int(ts), int((ts % 1) * 1e6)
    return struct.pack("<IIII", sec, usec, len(pkt), len(pkt)) + pkt


def _write_pcap(path, packets):
    with open(path, "wb") as f:
        f.write(struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 249))
        for p in packets:
            f.write(p)


def test_extract_split_and_chained(tmp_path):
    a = Frame.write_int(41, 19, [1], 100, 2).raw
    b = Frame.write_int(36, 25, [5], 250, 2).raw
    chained = a + b
    pkts = [
        _usbpcap_packet(1.0, chained[:4]),
        _usbpcap_packet(1.1, chained[4:]),
        _usbpcap_packet(1.2, b, endpoint_in=True),
    ]
    p = tmp_path / "t.pcap"
    _write_pcap(p, pkts)
    frames = frames_from_capture(p)
    reqs = [t.frame for t in frames if not t.direction_in]
    assert [f.raw for f in reqs] == [a, b]
    assert usb_devices(p) == {(1, 3): 3}


def test_diff(tmp_path):
    base = tmp_path / "base.pcap"
    act = tmp_path / "act.pcap"
    common = Frame.read_request(40, 19, [2], 2).raw
    new = Frame.write_int(0x2E, 0x1F, [0x12], 0x3C, 2).raw   # hypothetical PHF cmd
    _write_pcap(base, [_usbpcap_packet(1.0, common)])
    _write_pcap(act, [_usbpcap_packet(1.0, common), _usbpcap_packet(1.5, new), _usbpcap_packet(1.6, new)])
    rep = diff_captures(str(base), str(act))
    assert list(rep.only_in_action.values()) == [2]
    (grp, dev, payload), = rep.only_in_action.keys()
    assert (grp, dev) == (0x2E, 0x1F)
    assert "dev= 31" in rep.render()


def test_usbmon_mmapped_64_byte_header(tmp_path):
    """What `tcpdump -i usbmonN -w` writes on Linux (linktype 220)."""
    raw = Frame.write_int(41, 19, [1], 100, 2).raw
    hdr = struct.pack("<QBBBBH", 1, ord("S"), 3, 0x02, 5, 1) + bytes(22) + struct.pack("<I", len(raw))
    hdr = hdr.ljust(64, b"\0")
    pkt = hdr + raw
    p = tmp_path / "m.pcap"
    with open(p, "wb") as f:
        f.write(struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 220))
        f.write(struct.pack("<IIII", 1, 0, len(pkt), len(pkt)) + pkt)
    assert [t.frame.raw for t in frames_from_capture(p)] == [raw]
