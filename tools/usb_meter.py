#!/usr/bin/env python3
"""Measure host-side USB CDC throughput from the bio-stream firmware.

bio-stream sends a repeating 0,1,...,255 byte pattern (3840-byte blocks, a
multiple of 256, so the sequence is continuous across blocks). Console log
lines get interleaved into the same port, so we report:
  total   - every byte received
  pattern - bytes that continue the 0..255 sequence (the real payload)
  breaks  - places the sequence was interrupted (log text or lost data)

Usage: usb_meter.py [--port PORT] [--seconds N] [--json]
"""
import argparse
import glob
import json
import re
import sys
import time

import serial


def find_port():
    ports = sorted(glob.glob("/dev/cu.usbmodem*"))
    if not ports:
        sys.exit("no /dev/cu.usbmodem* port found")
    return ports[-1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port")
    ap.add_argument("--seconds", type=float, default=10.0)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    port = a.port or find_port()

    total = pattern = breaks = 0
    prev = None
    text = bytearray()  # non-pattern bytes, to show device log lines
    per_sec = []
    sec_total = sec_pat = 0

    with serial.Serial(port, 1_000_000, timeout=0.05) as ser:
        ser.reset_input_buffer()
        start = last = time.perf_counter()
        while (now := time.perf_counter()) - start < a.seconds:
            buf = ser.read(65536)
            for b in buf:
                if prev is not None and b == ((prev + 1) & 0xFF):
                    pattern += 1
                    sec_pat += 1
                else:
                    breaks += 1
                    if len(text) < 4096:
                        text.append(b)
                prev = b
            total += len(buf)
            sec_total += len(buf)
            if now - last >= 1.0:
                per_sec.append((sec_total / (now - last), sec_pat / (now - last)))
                if not a.json:
                    print(f"  {len(per_sec):3d}s  total {per_sec[-1][0]/1e6:6.3f} MB/s"
                          f"  pattern {per_sec[-1][1]/1e6:6.3f} MB/s")
                sec_total = sec_pat = 0
                last = now
        elapsed = time.perf_counter() - start

    # Skip the first second (startup / buffered backlog) for the steady-state figure.
    steady = per_sec[1:] or per_sec
    res = {
        "port": port,
        "seconds": round(elapsed, 2),
        "total_bytes": total,
        "pattern_bytes": pattern,
        "breaks": breaks,
        "avg_total_MBps": round(total / elapsed / 1e6, 3),
        "steady_pattern_MBps": round(sum(p for _, p in steady) / max(len(steady), 1) / 1e6, 3),
        "steady_pattern_Mbit": round(sum(p for _, p in steady) / max(len(steady), 1) * 8 / 1e6, 1),
    }
    logs = re.findall(rb"USB TX r=\d+ accepted_Bps=\d+[^\r\n]*", bytes(text))
    res["device_reports"] = [l.decode(errors="replace") for l in logs[-5:]]
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
