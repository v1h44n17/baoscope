#!/usr/bin/env python3
"""BaoScope host tool: request a capture, verify the self-test signal, save a VCD.

Usage:
  capture.py [--port P] [--rate HZ] [--n N] [--gen HZ] [--vcd out.vcd] [--no-verify]

The device's BIO generator core drives a 4-bit binary counter on ch0..ch3
(PC0..PC3), advancing once per generator quantum. With --gen = rate/k every
counter value should last exactly k samples and increment by 1 (mod 16).
--gen 0 disables the generator (capture external signals instead).
VCD files open in PulseView (sigrok) and GTKWave.
"""
import argparse
import glob
import struct
import sys
import time
import zlib
from collections import Counter

import serial

CAP_HDR = struct.Struct("<5I")  # version, rate, n, elapsed_ms, full_hits
REC = 512
REC_PAYLOAD = REC - 8
MAGIC = b"\xb5\x5c"


def find_port():
    ports = sorted(glob.glob("/dev/cu.usbmodem*"))
    if not ports:
        sys.exit("no /dev/cu.usbmodem* port found")
    return ports[-1]


class Link:
    """Reassembles device messages from 512-byte seq+CRC records.

    Duplicated records (same seq as the last accepted one) are dropped, and
    sequence gaps / CRC resyncs are counted, so USB-level packet duplication
    or loss is visible instead of silently corrupting data.
    """

    def __init__(self, ser):
        self.ser = ser
        self.buf = bytearray()
        self.last_seq = None
        self.stats = dict(records=0, dup=0, lost=0, resync_bytes=0)

    def _fill(self, deadline):
        while True:
            chunk = self.ser.read(65536)
            if chunk:
                self.buf += chunk
                return
            if time.time() > deadline:
                raise TimeoutError(f"no data; stats {self.stats}")

    def record(self, deadline):
        while True:
            i = self.buf.find(MAGIC)
            if i < 0:
                self.stats["resync_bytes"] += max(len(self.buf) - 1, 0)
                del self.buf[:-1]
                self._fill(deadline)
                continue
            if i:
                self.stats["resync_bytes"] += i
                del self.buf[:i]
            if len(self.buf) < REC:
                self._fill(deadline)
                continue
            rec = bytes(self.buf[:REC])
            if zlib.crc32(rec[:-4]) != struct.unpack_from("<I", rec, REC - 4)[0]:
                self.stats["resync_bytes"] += 1
                del self.buf[:1]  # not a real record boundary, keep scanning
                continue
            del self.buf[:REC]
            seq = struct.unpack_from("<H", rec, 2)[0]
            if seq == self.last_seq:
                self.stats["dup"] += 1
                continue
            if self.last_seq is not None and seq != (self.last_seq + 1) & 0xFFFF:
                self.stats["lost"] += (seq - self.last_seq - 1) & 0xFFFF
            self.last_seq = seq
            self.stats["records"] += 1
            return seq, rec[4:4 + REC_PAYLOAD]

    def message(self, timeout=20.0):
        deadline = time.time() + timeout
        lost_before = self.stats["lost"]
        _, p = self.record(deadline)
        kind, length = p[:4], struct.unpack_from("<I", p, 4)[0]
        body = bytearray(p[8:])
        while len(body) < length:
            _, p = self.record(deadline)
            body += p
        if self.stats["lost"] != lost_before:
            raise RuntimeError(f"records lost inside {kind!r} message: {self.stats}")
        return kind, bytes(body[:length])

    def drain(self, quiet=0.5):
        q = time.time()
        while time.time() - q < quiet:
            if self.ser.read(65536):
                q = time.time()
        self.buf.clear()
        self.last_seq = None


def capture(link, rate, n, gen, dma=False, mode=1, split=False):
    cmd = f"dcap {rate} {n} {gen} {mode} {int(split)}" if dma else f"cap {rate} {n} {gen}"
    link.ser.write((cmd + "\n").encode())
    kind, body = link.message()
    if kind == b"ERR_":
        raise RuntimeError(body.decode(errors="replace"))
    if kind != b"CAP1":
        raise RuntimeError(f"unexpected message {kind!r}")
    ver, rate_act, n_act, elapsed, full_hits = CAP_HDR.unpack_from(body)
    data = body[CAP_HDR.size:CAP_HDR.size + n_act]
    return dict(version=ver, rate=rate_act, n=n_act, elapsed_ms=elapsed, full_hits=full_hits), data


def stream_test(link, batches):
    """Throughput with duplicates removed: counts unique payload bytes per second."""
    link.ser.write(f"stream {batches}\n".encode())
    t0 = None
    uniq = 0
    for _ in range(batches * 7):
        link.record(time.time() + 5)
        if t0 is None:
            t0 = time.perf_counter()
        else:
            uniq += REC_PAYLOAD
    dt = time.perf_counter() - t0
    return dict(unique_payload_MBps=round(uniq / dt / 1e6, 3),
                wire_MBps_est=round((uniq + link.stats["dup"] * REC_PAYLOAD) / REC_PAYLOAD * REC / dt / 1e6, 3),
                seconds=round(dt, 2), **link.stats)


def verify_counter(data, k):
    """Check ch0..3 form a counter that holds each value for k samples."""
    nib = [b & 0xF for b in data]
    runs, vals = [], []
    start = 0
    for i in range(1, len(nib) + 1):
        if i == len(nib) or nib[i] != nib[start]:
            runs.append(i - start)
            vals.append(nib[start])
            start = i
    inner = runs[1:-1]  # first/last runs are truncated by the capture window
    step_errs = sum(1 for a, b in zip(vals, vals[1:]) if b != (a + 1) & 0xF)
    hist = Counter(inner)
    bad_runs = sum(c for r, c in hist.items() if r != k)
    transitions = len(runs) - 1
    # A flat or nearly flat capture must never pass: demand ~n/k counter steps.
    expected_transitions = len(data) // k - 1
    return dict(expected_run=k, run_hist=dict(sorted(hist.items())),
                bad_runs=bad_runs, step_errors=step_errs, transitions=transitions,
                expected_transitions=expected_transitions,
                ok=bad_runs == 0 and step_errs == 0 and transitions >= expected_transitions)


def write_vcd(path, data, rate):
    ids = [chr(33 + i) for i in range(8)]
    ns = 1e9 / rate
    with open(path, "w") as f:
        f.write("$timescale 1 ns $end\n$scope module baoscope $end\n")
        for i, c in enumerate(ids):
            f.write(f"$var wire 1 {c} ch{i} $end\n")
        f.write("$upscope $end\n$enddefinitions $end\n")
        prev = None
        for t, b in enumerate(data):
            if b != prev:
                f.write(f"#{round(t * ns)}\n")
                for i, c in enumerate(ids):
                    bit = (b >> i) & 1
                    if prev is None or ((prev >> i) & 1) != bit:
                        f.write(f"{bit}{c}\n")
                prev = b
        f.write(f"#{round(len(data) * ns)}\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port")
    ap.add_argument("--rate", type=int, default=1_000_000)
    ap.add_argument("--n", type=int, default=65536)
    ap.add_argument("--gen", type=int, help="generator Hz (default rate/8, 0 = off)")
    ap.add_argument("--vcd")
    ap.add_argument("--no-verify", action="store_true")
    ap.add_argument("--dma", action="store_true",
                    help="BIO writes samples straight to RAM (no CPU in the sample loop)")
    ap.add_argument("--mode", type=int, choices=[1, 2, 8], default=1,
                    help="DMA packing: 1 = 32-bit/sample, 2 = 2 samples/word (8 ch), "
                         "8 = 8 samples/word (ch0-3 only)")
    ap.add_argument("--split", action="store_true",
                    help="DMA: sampler core pushes into a FIFO, a second writer core stores to RAM")
    ap.add_argument("--stream", type=int, metavar="BATCHES",
                    help="run a throughput test of BATCHES x 7 records instead of a capture")
    a = ap.parse_args()
    gen = a.rate // 8 if a.gen is None else a.gen

    with serial.Serial(a.port or find_port(), 1_000_000, timeout=0.05) as ser:
        link = Link(ser)
        link.drain()
        ser.write(b"ping\n")
        print("ping ->", link.message(timeout=3)[0])
        if a.stream:
            print("stream:", stream_test(link, a.stream))
            return
        info, data = capture(link, a.rate, a.n, gen, a.dma, a.mode, a.split)
        print("link:", link.stats)

    seconds = info["n"] / info["rate"]
    info["expected_ms"] = round(seconds * 1000, 2)
    if a.dma:
        # In DMA mode the 5th header field is the BIO cycle count for the whole capture.
        cycles = info.pop("full_hits")
        info["bio_cycles"] = cycles
        info["cycles_per_sample"] = round(cycles / info["n"], 1)
    print("capture:", info)
    print("channel activity:", [sum(1 for x, y in zip(data, data[1:]) if (x ^ y) >> i & 1)
                                for i in range(8)], "(transitions per channel)")
    if gen and not a.no_verify:
        if info["rate"] % gen:
            print(f"note: rate {info['rate']} not a multiple of gen {gen}; run lengths will vary")
        v = verify_counter(data, info["rate"] // gen)
        print("self-test:", v)
        print("SELF-TEST", "PASS" if v["ok"] else "FAIL")
    if a.vcd:
        write_vcd(a.vcd, data, info["rate"])
        print("wrote", a.vcd)


if __name__ == "__main__":
    main()
