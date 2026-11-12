#!/usr/bin/env python3
"""moza-dash: Assetto Corsa (UDP 9996) -> MOZA KS Pro (W18) rev LEDs + dashboard screen, over the R5 base serial port (macOS).
Protocol per giantorth/moza-simhub-plugin docs; verified live on W18 + R5 2026-09-04.
Usage: python3 moza-dash.py [--port /dev/cu.usbmodemXXXX] [--max-rpm N] [--verbose]"""
import argparse, socket, struct, time, glob, sys, threading, zlib
import serial

START, MAGIC = 0x7E, 0x0D
WHEEL, BASE, DASH, W2 = 0x17, 0x13, 0x14, 0x15
NLED = 18; WINDOW = (1 << NLED) - 1
COLORS = [(0, 255, 0)] * 8 + [(255, 0, 0)] * 6 + [(0, 0, 255)] * 4
# compression name -> (code, bits, encoder)
def f32(v): return struct.unpack('<I', struct.pack('<f', float(v)))[0]
COMP = {
    'bool': (0x00, 1, lambda v: 1 if v else 0),
    'uint8_t': (0x01, 8, lambda v: int(v) & 0xFF), 'int8_t': (0x02, 8, lambda v: int(v) & 0xFF),
    'uint16_t': (0x04, 16, lambda v: max(0, min(65535, int(v)))), 'int16_t': (0x05, 16, lambda v: int(v) & 0xFFFF),
    'float': (0x07, 32, f32),
    'int30': (0x0D, 5, lambda v: (int(v) & 0x1F) if v < 0 else min(int(v), 30)),
    'percent_1': (0x0E, 10, lambda v: int(max(0, min(1000, v * 10)))),
    'float_001': (0x17, 10, lambda v: int(max(0, min(1000, v * 1000)))),
    'float_6000_1': (0x0F, 16, lambda v: int(max(0, min(65535, v * 10)))),
    'uint32_t': (0x06, 32, lambda v: int(v) & 0xFFFFFFFF),
}
# url suffix -> (compression, package_level, value-fn(state))
CHANNELS = {
    'CurrentLap':     ('int8_t',       500, lambda s: s['lap']),
    'CurrentLapTime': ('float',         30, lambda s: s['laptime']),
    'GAP':            ('float',         30, lambda s: s['gap']),
    'Gear':           ('int30',         30, lambda s: s['gear']),
    'Rpm':            ('uint16_t',      30, lambda s: s['rpm']),
    'SpeedKmh':       ('float_6000_1',  30, lambda s: s['speed'] * 0.621371),  # feed MPH into the speed widget
    'SpeedMph':       ('float_6000_1',  30, lambda s: s['speed'] * 0.621371),
    'Throttle':       ('float_001',     30, lambda s: s['gas']),
    'Brake':          ('float_001',     30, lambda s: s['brake']),
    'Clutch':         ('float_001',     30, lambda s: s['clutch']),
    'BestLapTime':    ('float',       2000, lambda s: s['best']),
    'LastLapTime':    ('float',       2000, lambda s: s['last']),
    'MaxRpm':         ('uint16_t',    2000, lambda s: s['maxrpm']),
    'CarSettings_CurrentDisplayedRPMPercent': ('percent_1', 30, lambda s: 100.0 * s['rpm'] / max(1.0, s['maxrpm'])),
    'EngineStarted':  ('bool',         500, lambda s: s['rpm'] > 300),
    'FuelPercent':    ('percent_1',    500, lambda s: s.get('fuel_pct', 0.0)),
    'TCActive':       ('bool',          60, lambda s: s.get('tc', 0) > 0.5),
    'ABSActive':      ('bool',          60, lambda s: s.get('abs', 0) > 0.5),
    'WaterTemp':          ('float', 500, lambda s: s.get('water', 0.0)),
    'TyreTempFrontLeft':  ('float', 500, lambda s: s.get('tyre_fl', 0.0)),
    'TyreTempFrontRight': ('float', 500, lambda s: s.get('tyre_fr', 0.0)),
    'TyreTempRearLeft':   ('float', 500, lambda s: s.get('tyre_rl', 0.0)),
    'TyreTempRearRight':  ('float', 500, lambda s: s.get('tyre_rr', 0.0)),
}

import os
try:
    from moza_phf import rigcfg     # control-board settings: per-car rev-light windows, status for the board
except Exception:
    rigcfg = None
LED_DEFAULT = {'start': 55, 'full': 90, 'flash': 92}   # percent of the car's limiter
TELEM_FILE = os.path.expanduser("~/Library/Application Support/CrossOver/Bottles/Steam(try3232)/drive_c/Program Files (x86)/Steam/steamapps/common/assettocorsa/nohesi_extra_telem.txt")

def read_extra(st):
    """Read oil/water/tyre temps written by the in-game CSP app (nohesi_dash)."""
    try:
        with open(TELEM_FILE) as f: line = f.read().strip()
        parts = line.split(",")
        if len(parts) >= 9:
            st["oil"]=float(parts[0]); st["water"]=float(parts[1])
            st["tyre_fl"]=float(parts[2]); st["tyre_fr"]=float(parts[3])
            st["tyre_rl"]=float(parts[4]); st["tyre_rr"]=float(parts[5])
            st["extra_seq"]=int(parts[8])
            if len(parts) >= 10: st["limiter"]=float(parts[9])
            if len(parts) >= 13:
                st["tc"]=float(parts[10]); st["abs"]=float(parts[11]); st["fuel_pct"]=float(parts[12])
            if len(parts) >= 14:
                st["gap"]=float(parts[13])   # live delta-to-best-lap → feeds GAP/DLT widget
            return True
    except Exception:
        pass
    return False

def frame(group, dev, payload):
    body = bytes([START, len(payload), group, dev]) + bytes(payload)
    s = MAGIC + sum(body) + sum(START for b in body[2:] if b == START)
    wire = bytearray([START, body[1]])
    for b in body[2:]:                      # 0x7E inside body is doubled on the wire
        wire.append(b)
        if b == START: wire.append(START)
    ck = s & 0xFF; wire.append(ck)
    if ck == START: wire.append(START)
    return bytes(wire)

def hx(b): return ' '.join(f'{x:02x}' for x in b)

class Wheel:
    def __init__(self, port, verbose):
        self.sp = serial.Serial(port, 115200, timeout=0.005)
        self.lock = threading.Lock(); self.verbose = verbose
        self.buf = b''; self.catalog = {}; self.end_marker = None; self.catalog_session = None
        self.seq = {1: 2, 2: 2, 3: 2}; self.last_ack_seq = {1: 0, 2: 0}
        self.close_acked = False; self.opens_acked = set()
        self.rx_seen = {}
        self.tlvbuf = {}   # sess -> (time, tail of a TLV record that continues in the next chunk)
        self.alive = True
        threading.Thread(target=self._reader, daemon=True).start()
    def tx(self, f, note=''):
        with self.lock: self.sp.write(f)
        if self.verbose and note: print(f'>> {note} {hx(f)[:80]}', flush=True)
    # ---- inbound ----
    def _reader(self):
        while self.alive:
            try: data = self.sp.read(4096)
            except Exception: time.sleep(0.05); continue
            if not data: continue
            self.buf += data
            self._parse()
    def _parse(self):
        while True:
            i = self.buf.find(b'\x7e')
            if i < 0: self.buf = b''; return
            if i > 0: self.buf = self.buf[i:]
            if len(self.buf) < 5: return
            n = self.buf[1]
            if n < 1 or n > 64: self.buf = self.buf[1:]; continue
            j = 2; raw = bytearray(); need = n + 2
            while len(raw) < need and j < len(self.buf):
                b = self.buf[j]; raw.append(b); j += 1
                if b == 0x7E and j < len(self.buf) and self.buf[j] == 0x7E: j += 1
            if len(raw) < need or j >= len(self.buf): return
            j += 1  # checksum
            if self.buf[j-1] == 0x7E and j < len(self.buf) and self.buf[j] == 0x7E: j += 1
            self.buf = self.buf[j:]
            try: self._handle(raw[0], raw[1], bytes(raw[2:]))
            except Exception as e: print('rx error', e, flush=True)
    def _handle(self, group, dev, pl):
        if group != 0xC3 or len(pl) < 2: return
        if pl[0] == 0xFC and pl[1] == 0x00 and len(pl) >= 5:
            sess, seq = pl[2], pl[3] | (pl[4] << 8)
            if seq == 0: self.close_acked = True
            else: self.opens_acked.add(sess)
            if self.verbose: print(f'<< ack sess={sess} seq={seq}', flush=True)
            return
        if pl[0] == 0x7C and pl[1] == 0x00 and len(pl) >= 6:
            sess, typ, seq = pl[2], pl[3], pl[4] | (pl[5] << 8)
            if typ == 0x81:
                self.tx(frame(0x43, WHEEL, [0xFC, 0x00, sess, seq & 0xFF, seq >> 8]))
                if self.verbose: print(f'<< device open sess={sess}', flush=True)
            elif typ == 0x01:
                self.tx(frame(0x43, WHEEL, [0xFC, 0x00, sess, seq & 0xFF, seq >> 8]))
                self.last_ack_seq[sess] = seq
                if self.rx_seen.get((sess, seq)): return
                self.rx_seen[(sess, seq)] = True
                data = pl[6:-4]
                if self.verbose: print(f'<< data sess={sess} seq={seq} {hx(data)[:70]}', flush=True)
                self._tlv(sess, data)
    def _tlv(self, sess, data):
        # records are split across 54-byte chunks: carry the unfinished tail over to
        # the next chunk, or a long URL arrives cut short and its channel never maps
        t, tail = self.tlvbuf.pop(sess, (0, b''))
        if time.time() - t < 2.0: data = tail + data
        i = 0
        while i < len(data):
            tag = data[i]
            if tag == 0xFF: i += 1; continue
            if i + 5 > len(data): break
            size = int.from_bytes(data[i+1:i+5], 'little')
            if size > 512: return                      # not a record boundary: drop and resync on the next burst
            if i + 5 + size > len(data): break
            body = data[i+5:i+5+size]
            if tag == 0x04 and size >= 2:
                # a catalog entry arriving after a sealed catalog = a new dashboard: start fresh
                if getattr(self, '_catalog_sealed', False):
                    self.catalog = {}; self._catalog_sealed = False
                idx = body[0]; url = body[1:].decode('ascii', 'ignore')
                if url.startswith('\x01'): url = 'v1/gameData/' + url[1:]
                self.catalog[idx] = url; self.catalog_session = sess
                print(f'catalog idx={idx} {url}', flush=True)
            elif tag == 0x06 and size == 4:
                self.end_marker = int.from_bytes(body, 'little'); self.catalog_session = sess
                self._catalog_sealed = True   # next 0x04 burst is a different dashboard
                # commit this completed burst ONLY if it names real channels; bump version on change
                names = tuple(sorted(u for u in self.catalog.values() if u and '/' in u))
                if names and names != getattr(self, '_last_sig', None):
                    self._last_sig = names
                    self.catalog_committed = dict(self.catalog)
                    self.catalog_version = getattr(self, 'catalog_version', 0) + 1
                if self.verbose: print(f'<< END marker {self.end_marker} on sess {sess}', flush=True)
            i += 5 + size
        if i < len(data): self.tlvbuf[sess] = (time.time(), data[i:])
    # ---- session layer ----
    def send_chunks(self, sess, message):
        frames = []
        for off in range(0, len(message), 54):
            part = message[off:off+54]; seq = self.seq[sess]; self.seq[sess] += 1
            payload = part + zlib.crc32(part).to_bytes(4, 'little')
            frames.append(frame(0x43, WHEEL, [0x7C, 0x00, sess, 0x01, seq & 0xFF, seq >> 8] + list(payload)))
        for f in frames: self.tx(f)
        return frames
    def close_sessions(self):
        self.close_acked = False
        for s in (1, 2, 3): self.tx(frame(0x43, WHEEL, [0x7C, 0x00, s, 0x00, 0x00, 0x00]), f'close {s}')
    def open_sessions(self):
        self.tx(b''.join(frame(0x43, WHEEL, [0x7C, 0x00, s, 0x81, s, 0, s, 0, 0xFD, 0x02]) for s in (1, 2)), 'open 1+2')

def build_tierdef(tiers, catalog_idx, end_marker, flag_base=0):
    """tiers: list of [(idx, comp_name)] ordered fastest first; single broadcast, END echo"""
    msg = bytearray()
    for t, chans in enumerate(tiers):
        if not chans: continue
        msg += bytes([0x01]) + (1 + 16 * len(chans)).to_bytes(4, 'little') + bytes([flag_base + t])
        for idx, comp in chans:
            code, bits, _ = COMP[comp]
            msg += struct.pack('<IIII', idx, code, bits, 0)
    msg += bytes([0x06, 4, 0, 0, 0]) + int(end_marker).to_bytes(4, 'little')
    return bytes(msg)

def pack_bits(values):  # values: list of (encoded_uint, bits) -> bytes LSB-first
    total = sum(b for _, b in values); out = bytearray((total + 7) // 8); pos = 0
    for v, bits in values:
        v = int(v) & ((1 << bits) - 1)
        for k in range(bits):
            if (v >> k) & 1: out[(pos + k) >> 3] |= 1 << ((pos + k) & 7)
        pos += bits
    return bytes(out)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port'); ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--max-rpm', type=float, default=0); ap.add_argument('--verbose', action='store_true')
    ap.add_argument('--flag-base', type=int, default=0)
    a = ap.parse_args()
    port = a.port or (glob.glob('/dev/cu.usbmodem*') or [None])[0]
    if not port: print('no MOZA serial port'); sys.exit(1)
    w = Wheel(port, a.verbose); print('serial open', port, flush=True)
    # --- display probe + session bring-up ---
    w.tx(frame(0x43, WHEEL, [0x07, 0x01]), 'display probe'); time.sleep(0.3)
    w.close_sessions(); time.sleep(0.6)
    if w.close_acked:
        print('stale sessions on wheel: holding 11 s of silence before re-opening', flush=True)
        time.sleep(11); w.close_sessions(); time.sleep(0.6)
    def bring_up():
        w.open_sessions(); t0 = time.time()
        while time.time() - t0 < 6 and w.end_marker is None: time.sleep(0.05)
        return bool(w.catalog)
    if not bring_up():
        # stale-session wedge (prior instance left sessions open): full reclaim then retry
        print('no catalog — reclaiming stale sessions (close, 11 s silence, reopen)', flush=True)
        w.close_sessions(); time.sleep(15); w.close_sessions(); time.sleep(0.6)
        w.catalog = {}; w.end_marker = None
        bring_up()
    if not w.catalog:
        print('no catalog from wheel (display asleep?) — LEDs only', flush=True)
    else:
        print(f'catalog: {len(w.catalog)} channels, END={w.end_marker}, session={w.catalog_session}', flush=True)
    # --- LED colours ---
    w.tx(frame(0x3F, WHEEL, [0x1C, 0x00, 0x01]))
    for i in range(0, NLED, 5):
        w.tx(frame(0x3F, WHEEL, [0x19, 0x00] + [x for j in range(i, min(i+5, NLED)) for x in (j, *COLORS[j])]))
    # --- tiers from catalog (rebuilt whenever the active dashboard changes) ---
    T = {'tier_list': [], 'pkgs': []}
    def rebuild_tiers():
        cat = getattr(w, 'catalog_committed', None) or w.catalog
        tiers = {}
        for idx, url in sorted(cat.items()):
            name = url.rsplit('/', 1)[-1]
            if name not in CHANNELS: continue
            comp, pkg, _ = CHANNELS[name]; tiers.setdefault(pkg, []).append((idx, name))
        nl = [tiers[p] for p in sorted(tiers)]
        if not nl: return False          # empty/partial burst — keep the last good map
        T['tier_list'] = nl; T['pkgs'] = sorted(tiers)
        print(f'  channels mapped: {[n for t in T["tier_list"] for _, n in t]}', flush=True)
        return True
    rebuild_tiers()
    def emit_tierdef(with_preamble):
        sess = w.catalog_session or 2
        if with_preamble: w.send_chunks(sess, bytes([0x07, 4, 0, 0, 0, 2, 0, 0, 0, 0x03, 0, 0, 0, 0]))
        fr = w.send_chunks(sess, build_tierdef([[(i, CHANNELS[n][0]) for i, n in t] for t in T['tier_list']], None, w.end_marker or 0, a.flag_base))
        print(f'tier-def sent on sess {sess}: {[(p, [n for _, n in t]) for p, t in zip(T["pkgs"], T["tier_list"])]} END={w.end_marker}', flush=True)
        return fr
    retx = []
    if T['tier_list']:
        retx = emit_tierdef(True); retx_times = [time.time() + d / 1000 for d in (100, 200, 400, 700, 1100, 1500)]
        # channel-config burst (0x40)
        for p in [0x09, 0x00]: pass
        w.tx(frame(0x40, WHEEL, [0x09, 0x00]))
        for page in (0, 1):
            for cc in range(2, 7): w.tx(frame(0x40, WHEEL, [0x1E, page, cc, 0, 0]))
        w.tx(frame(0x40, WHEEL, [0x28, 0x00, 0x00])); w.tx(frame(0x40, WHEEL, [0x28, 0x01, 0x00])); w.tx(frame(0x40, WHEEL, [0x28, 0x02, 0x01, 0x00]))
    else:
        retx_times = []
    # --- main loop ---
    st = dict(rpm=0.0, speed=0.0, gear=0, lap=0, laptime=0.0, last=0.0, best=0.0, gap=0.0, gas=0.0, brake=0.0, clutch=0.0, maxrpm=a.max_rpm if a.max_rpm > 0 else 6000.0, oil=0.0, water=0.0, tyre_fl=0.0, tyre_fr=0.0, tyre_rl=0.0, tyre_rr=0.0, extra_seq=0, tc=0.0, abs=0.0, fuel_pct=0.0)
    last_end = w.end_marker; applied_ver = getattr(w, 'catalog_version', 0); seqctr = 0; tick = 0; last_led = None; blink = False
    seq_at_connect = -1
    car = ''; live = rigcfg.Live() if rigcfg else None; led = LED_DEFAULT
    addr = (a.host, 9996); s = None
    def ac_connect():
        sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); sk.settimeout(0.5)
        sk.sendto(struct.pack('<iii', 1, 1, 0), addr); d, _ = sk.recvfrom(4096)
        name = d[0:100].decode('utf-16le', 'ignore').split('%')[0]
        print('connected to AC, car', name, flush=True)
        sk.sendto(struct.pack('<iii', 1, 1, 1), addr); sk.settimeout(0.05); return sk, name  # last_rx reset by caller
    next_tick = time.time(); ac_retry = 0; last_rx = time.time()
    try:
        while True:
            now = time.time()
            if s is None and now >= ac_retry:
                try:
                    s, car = ac_connect(); last_rx = now
                    # new session may be a different car: forget learned max, re-read limiter
                    st['maxrpm'] = a.max_rpm if a.max_rpm > 0 else 6000.0; st['limiter'] = 0
                    # the telemetry file still holds the previous car until the in-game app rewrites it
                    read_extra(st); seq_at_connect = st['extra_seq']
                except Exception: ac_retry = now + 2
            if s is not None:
                got = False
                try:
                    for _ in range(20):
                        d, _ = s.recvfrom(4096)
                        if len(d) != 328: continue          # RTCarInfo only (408 = handshake, 212 = lap)
                        got = True; last_rx = now
                        st['speed'] = struct.unpack_from('<f', d, 8)[0]
                        st['laptime'] = struct.unpack_from('<i', d, 40)[0] / 1000.0
                        st['last'] = struct.unpack_from('<i', d, 44)[0] / 1000.0
                        st['best'] = struct.unpack_from('<i', d, 48)[0] / 1000.0
                        st['lap'] = struct.unpack_from('<i', d, 52)[0] + 1
                        st['gas'], st['brake'], st['clutch'] = struct.unpack_from('<fff', d, 56)
                        r = struct.unpack_from('<f', d, 68)[0]; st['rpm'] = r if 0 <= r < 30000 else st['rpm']
                        g = struct.unpack_from('<i', d, 76)[0]; st['gear'] = -1 if g == 0 else g - 1
                        if a.max_rpm <= 0 and st.get('limiter', 0) <= 1000 and st['rpm'] > st['maxrpm']: st['maxrpm'] = st['rpm']
                except socket.timeout: pass
                except Exception: s = None
                if s is not None and now - last_rx > 3.0:   # game switched / AC restarted → rebind
                    print('AC telemetry stale — reconnecting', flush=True)
                    try: s.sendto(struct.pack('<iii', 1, 1, 3), addr); s.close()
                    except Exception: pass
                    s = None; ac_retry = now + 0.5
            if now < next_tick: time.sleep(min(0.005, next_tick - now)); continue
            next_tick += 1 / 30; tick += 1
            if tick % 2 == 0: read_extra(st)   # ~15 Hz
            # authoritative rev limiter from the in-game CSP app beats auto-learn.
            # ratchet UP only — some mods report a flickering limiter (e.g. 8400<->9000);
            # taking the peak stops the LED strip from jumping around.
            if a.max_rpm <= 0 and s is not None and st['extra_seq'] != seq_at_connect and st.get('limiter', 0) > 1000 and st['limiter'] > st['maxrpm'] + 1:
                st['maxrpm'] = st['limiter']; print(f"maxrpm from car limiter: {st['maxrpm']:.0f}", flush=True)
            # tier-def blind retransmit + END change re-emit
            if retx_times and now >= retx_times[0]:
                retx_times.pop(0)
                for f in retx: w.tx(f)
            if getattr(w, 'catalog_version', 0) != applied_ver:   # dashboard layout actually changed
                applied_ver = w.catalog_version
                if rebuild_tiers():
                    retx = emit_tierdef(True); retx_times = [now + d/1000 for d in (150, 400, 800, 1300)]
                    w.tx(frame(0x40, WHEEL, [0x28, 0x02, 0x01, 0x00]))
            # value frames
            for t, chans in enumerate(T['tier_list']):
                pkg = T['pkgs'][t]; every = max(1, round(pkg / 33.3))
                if tick % every: continue
                vals = [(COMP[CHANNELS[n][0]][2](CHANNELS[n][2](st)), COMP[CHANNELS[n][0]][1]) for _, n in chans]
                data = pack_bits(vals)
                w.tx(frame(0x43, WHEEL, [0x7D, 0x23, 0x32, 0x00, 0x23, 0x32, a.flag_base + t, 0x20] + list(data)))
            # enable + sequence counter
            w.tx(frame(0x41, WHEEL, [0xFD, 0xDE, 0, 0, 0, 0]))
            w.tx(frame(0x41, BASE, [0xFD, 0xDE] + list(int(max(0, min(65535, st['rpm']))).to_bytes(4, 'big'))))
            seqctr = (seqctr + 1) & 0xFF; w.tx(frame(0x2D, BASE, [0xF5, 0x31, 0, 0, 0, seqctr]))
            # rev LEDs: fill the strip between the window's start and full marks (percent of
            # the limiter) and flash the whole bar from its flash mark up. The window is the
            # control board's default unless this car has its own.
            if live: led = rigcfg.led_window(live.get(), car)
            pct = 100.0 * st['rpm'] / st['maxrpm']
            frac = (pct - led['start']) / max(1.0, led['full'] - led['start']); n = 0 if frac <= 0 else min(NLED, int(frac * NLED + 0.5))
            if pct >= led['flash']: blink = not blink; active = WINDOW if blink else 0
            else: active = (1 << n) - 1
            if active != last_led or tick % 15 == 0:
                w.tx(frame(0x3F, WHEEL, [0x1A, 0x00] + list(active.to_bytes(4, 'little') + WINDOW.to_bytes(4, 'little')))); last_led = active
            if tick % 10 == 0: w.tx(frame(0x40, WHEEL, [0x28, 0x02, 0x01, 0x00]))
            if tick % 30 == 0:   # ~1 Hz housekeeping
                if rigcfg: rigcfg.write_state('dash', {'connected': s is not None, 'car': car, 'maxrpm': st['maxrpm'], 'rpm': st['rpm'], 'gear': st['gear'], 'led': led})
                for dv in (BASE, WHEEL): w.tx(frame(0x00, dv, []))
                for dv in (DASH, W2, WHEEL): w.tx(frame(0x43, dv, [0x00]))
                w.tx(frame(0x43, WHEEL, [0x7C, 0x27, 0x0F, 0x80, 0x05, 0x00, 0x03, 0x00, 0xFE, 0x01]))
                w.tx(frame(0x43, WHEEL, [0x7C, 0x27, 0x0F, 0x00, 0x06, 0x00]))
                w.tx(frame(0x43, WHEEL, [0x7C, 0x23, 0x46, 0x80, 0x07, 0x00, 0x05, 0x00, 0xFE, 0x01]))
                aseq = w.last_ack_seq.get(2, 0); w.tx(frame(0x43, WHEEL, [0xFC, 0x00, 0x02, aseq & 0xFF, aseq >> 8]))
            if tick % 150 == 0:
                print(f"rpm={st['rpm']:.0f} speed={st['speed']*0.621371:.0f}mph gear={st['gear']} oil={st['oil']:.0f} water={st['water']:.0f} tyres={st['tyre_fl']:.0f}/{st['tyre_fr']:.0f}/{st['tyre_rl']:.0f}/{st['tyre_rr']:.0f} extra_seq={st['extra_seq']} END={w.end_marker}", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        w.tx(frame(0x3F, WHEEL, [0x1A, 0x00] + [0]*4 + list(WINDOW.to_bytes(4, 'little'))))
        w.close_sessions(); time.sleep(0.3); w.alive = False; print('bye', flush=True)

if __name__ == '__main__':
    main()
