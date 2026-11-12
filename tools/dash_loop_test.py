"""The wheel script's receive loop against a stand-in game sending telemetry at a steady rate.
Old loop: waits for 20 packets before every update. New loop: takes whatever is waiting and moves on."""
import socket, struct, threading, time, sys

def game(port, hz, stop):
    sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); sk.bind(("127.0.0.1", port)); sk.settimeout(0.2)
    client = None; nxt = time.time()
    while not stop.is_set():
        try:
            d, who = sk.recvfrom(64)
            op = struct.unpack("<iii", d)[2]
            if op == 0: sk.sendto(b"\0" * 408, who)
            elif op == 1: client = who; sk.settimeout(0.0005)
            elif op == 3: client = None
        except (socket.timeout, BlockingIOError): pass
        now = time.time()
        while client and now >= nxt:
            sk.sendto(b"\0" * 328, client); nxt += 1 / hz
        if not client: nxt = now

def connect(port):
    sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); sk.settimeout(0.5)
    sk.sendto(struct.pack("<iii", 1, 1, 0), ("127.0.0.1", port)); sk.recvfrom(4096)
    sk.sendto(struct.pack("<iii", 1, 1, 1), ("127.0.0.1", port)); return sk

def run(kind, port, seconds):
    s = connect(port)
    if kind == "old": s.settimeout(0.05)
    else: s.setblocking(False)
    ticks = packets = 0; start = time.time(); next_tick = start; worst = 0; last = start
    while time.time() - start < seconds:
        now = time.time()
        try:
            for _ in range(20 if kind == "old" else 500):
                d, _ = s.recvfrom(4096)
                if len(d) != 328: continue
                packets += 1
        except (socket.timeout, BlockingIOError): pass
        if now < next_tick: time.sleep(min(0.005, next_tick - now)); continue
        next_tick += 1 / 30
        if kind == "new" and now - next_tick > 0.5: next_tick = now
        ticks += 1; worst = max(worst, now - last); last = now
    s.sendto(struct.pack("<iii", 1, 1, 3), ("127.0.0.1", port)); s.close()
    return ticks / seconds, packets / seconds, worst

for hz in (60, 85, 144):
    for kind in ("old", "new"):
        stop = threading.Event(); port = 19996 + hz + (1 if kind == "new" else 0)
        t = threading.Thread(target=game, args=(port, hz, stop), daemon=True); t.start(); time.sleep(0.2)
        r = run(kind, port, 4.0); stop.set(); t.join()
        print("game sending %3d packets/s, %s loop: %5.1f wheel updates/s (packets read %5.1f/s, longest gap %.0f ms)" % (hz, kind, r[0], r[1], r[2] * 1000))
