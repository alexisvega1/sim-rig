import struct

from moza_phf.protocol import Frame, checksum, parse_stream, split_frames, swap_nibbles


def test_build_and_checksum():
    # write ffb-strength (group 41, base 19, id 1, 2 bytes) = 100
    f = Frame.write_int(41, 19, [1], 100, 2)
    assert f.raw[:4] == bytes([0x7E, 3, 41, 19])
    assert f.raw[4:7] == bytes([1, 0, 100])
    assert f.raw[-1] == (13 + sum(f.raw[:-1])) % 256
    assert checksum(f.raw[:-1]) == f.raw[-1]


def test_multi_id_and_float():
    f = Frame.write_float(36, 25, [14], 0.5)
    assert f.raw[1] == 5
    assert struct.unpack(">f", f.values)[0] == 0.5
    g = Frame.write_int(41, 19, [34, 5], 7, 1)
    assert g.ids == (34, 5) and g.raw[1] == 3


def test_read_request_value_is_one():
    f = Frame.read_request(40, 19, [2], 2)
    assert f.values == b"\x00\x01"


def test_parse_roundtrip_and_response_mapping():
    req = Frame.write_int(41, 19, [1], 100, 2)
    assert Frame.parse(req.raw) == req
    # synthesize the mirrored response: group|0x80, device nibble-swapped
    rsp_body = bytes([0x7E, 3, 41 | 0x80, swap_nibbles(19), 1, 0, 100])
    rsp = Frame.parse(rsp_body + bytes([checksum(rsp_body)]))
    assert rsp.is_response
    assert rsp.request_group == 41 and rsp.request_device == 19
    assert rsp.key() == req.key()


def test_split_chained_and_garbage():
    a = Frame.write_int(41, 19, [1], 100, 2).raw
    b = Frame.read_request(40, 19, [2], 2).raw
    stream = b"\x00\x7e" + a + b"\xff\x7e\x03" + b   # junk + a + junk + b
    frames = list(split_frames(stream))
    assert frames == [a, b]
    assert [f.request_group for f in parse_stream(stream)] == [41, 40]


def test_bad_checksum_rejected():
    raw = bytearray(Frame.write_int(41, 19, [1], 100, 2).raw)
    raw[-1] ^= 1
    try:
        Frame.parse(bytes(raw))
    except ValueError:
        return
    assert False, "accepted bad checksum"
