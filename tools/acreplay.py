"""Read the player's car out of an Assetto Corsa replay (version 16, as written with Custom Shaders Patch).
Layout worked out from one file: after the header and the per-frame track block, each car frame is 292 bytes with
position (3 float32) at +20, revs (float16) at +190, the lap clock in ms (int32) at +240, gear (byte, 0 = R, 1 = N)
at +254, throttle and brake (bytes, 0..255) at +264 and +265."""
import struct
import numpy as np

FRAME = 292


def read(path):
    b = open(path, "rb").read()
    o = 0
    ver, = struct.unpack_from("<i", b, o); o += 4
    interval, = struct.unpack_from("<d", b, o); o += 8

    def s():
        nonlocal o
        n, = struct.unpack_from("<i", b, o); o += 4
        v = b[o:o + n]; o += n
        return v.decode("utf-8", "replace")
    weather, track, layout = s(), s(), s()
    cars, _, frames, objects = struct.unpack_from("<4i", b, o); o += 16
    o += frames * (4 + 12 * objects)                       # the sun and moving track objects, once per frame
    car = s(); o_names = [s() for _ in range(4)]            # driver, nation, team, skin: not used
    n, wings = struct.unpack_from("<2i", b, o); o += 8
    if ver != 16 or FRAME != 276 + 4 * wings:
        raise ValueError(f"unexpected replay layout: version {ver}, {wings} wings")
    a = np.frombuffer(b, dtype=np.uint8, count=FRAME * n, offset=o).reshape(n, FRAME)
    col = lambda off, kind, k=1: a[:, off:off + np.dtype(kind).itemsize * k].copy().view(kind).reshape(n, k) if k > 1 else a[:, off:off + np.dtype(kind).itemsize].copy().view(kind).reshape(n)
    return {"track": track + ("-" + layout if layout else ""), "car": car, "dt": interval / 1000.0, "n": n,
            "pos": col(20, "<f4", 3).astype(np.float64), "rpm": col(190, "<f2").astype(np.float64),
            "clock": col(240, "<i4").astype(np.int64), "gear": a[:, 254].astype(int) - 1,
            "thr": a[:, 264] / 255.0, "brk": a[:, 265] / 255.0}
