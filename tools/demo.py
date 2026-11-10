#!/usr/bin/env python3
"""BaoScope one-command demo: runs every capture, decode and real-peripheral test on a
Dabao running the baoscope firmware, prints a scorecard and saves it as markdown.

  python tools/demo.py [--port /dev/cu.usbmodemXXXX3] [--out captures/demo-scorecard.md]

Default: no wiring - self-tests use a spare BIO core as the signal source on PC0..PC3.
--wired: decode the chip's real UART2 and I2C0 peripherals through 3 jumper wires instead.
"""
import argparse
import datetime
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# (group, name, setting, script, args)
TESTS = [
    ("Link", "USB throughput (dedup'd)", "3000 x 7 records", "capture.py", ["--stream", "3000"]),
    ("Capture", "Split pipeline, mode 1", "8 ch @ 10 MS/s", "capture.py",
     ["--dma", "--split", "--mode", "1", "--rate", "10000000", "--n", "65536"]),
    ("Capture", "Split pipeline, mode 2", "8 ch @ 17.5 MS/s", "capture.py",
     ["--dma", "--split", "--mode", "2", "--rate", "17500000", "--n", "131072"]),
    ("Capture", "Split pipeline, mode 8", "4 ch @ 17.5 MS/s", "capture.py",
     ["--dma", "--split", "--mode", "8", "--rate", "17500000", "--n", "131072"]),
    ("Decode", "UART self-test", "2 Mbaud, 4096 bytes", "decode.py",
     ["uart", "--baud", "2000000", "--n", "4096", "--selftest"]),
    ("Decode", "I2C self-test", "1 MHz bus, 4096 events", "decode.py",
     ["i2c", "--bus", "1000000", "--n", "4096", "--selftest"]),
    ("Decode", "SPI self-test", "2 MHz SCK, 4096 events", "decode.py",
     ["spi", "--sck", "2000000", "--n", "4096", "--selftest"]),
]

# Real chip peripherals as the signal source. Needs 3 jumpers (header pin numbers):
# 15 (PB14 UART2 TX) -> 9 (PC7), 27 (PB11 I2C0 SCL) -> 14 (PC0), 26 (PB12 I2C0 SDA) -> 12 (PC1).
# (Reading those pads in place without wires does NOT work: BIO reads pads it doesn't own as 0.)
WIRED_TESTS = [
    ("Real HW", "UART2 TX (log output)", "PB14->PC7, 1 Mbaud", "decode.py",
     ["uart", "--real", "--n", "512", "--timeout", "1500"]),
    ("Real HW", "I2C0 controller scan", "PB11/12->PC0/1", "decode.py",
     ["i2c", "--real", "--n", "1024", "--timeout", "2000"]),
]


def detail(out):
    """Pull the most informative number out of a tool's output."""
    for pat, fmt in [
        (r"'unique_payload_MBps': ([\d.]+).*?'dup': (\d+).*?'lost': (\d+)", "{} MB/s unique, {} dup, {} lost"),
        (r"'transitions': (\d+), 'expected_transitions': (\d+)", "{}/{} counter steps"),
        (r"'frames': (\d+), 'bad': (\d+)", "{} frames, {} bad"),
        (r"'transactions': (\d+), 'bad': (\d+)", "{} transactions, {} bad"),
        (r"'checked': (\d+), 'sequence_errors': (\d+)", "{} bytes, {} errors"),
        (r"'lines': (\d+).*?'gaps': (\d+)", "{} log lines, {} gaps"),
        (r"'probes': (\d+).*?'order_errors': (\d+)", "{} probes, {} order errors"),
    ]:
        m = re.search(pat, out, re.S)
        if m:
            return fmt.format(*m.groups())
    err = re.search(r"(\w*Error: .*)", out)
    return err.group(1)[:80] if err else ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port")
    ap.add_argument("--out", default=os.path.join(HERE, "..", "captures", "demo-scorecard.md"))
    ap.add_argument("--wired", action="store_true",
                    help="also run real-peripheral tests (needs the 3 jumpers listed in WIRED_TESTS; "
                         "self-tests are skipped so nothing drives the wired pins)")
    a = ap.parse_args()
    py = sys.executable
    port = ["--port", a.port] if a.port else []
    # With jumpers attached, skip self-tests: they drive PC0..PC3 and would fight the peripherals.
    tests = [t for t in TESTS if t[0] in ("Link",)] + WIRED_TESTS if a.wired else TESTS

    rows = []
    for group, name, setting, script, args in tests:
        print(f"-> {group}: {name} ({setting}) ...", flush=True)
        try:
            p = subprocess.run([py, os.path.join(HERE, script)] + port + args, cwd=HERE,
                               capture_output=True, text=True, timeout=120)
            out = p.stdout + p.stderr
        except subprocess.TimeoutExpired:
            out = "TimeoutError: test took over 120 s"
        if "--stream" in args:
            ok = "unique_payload_MBps" in out and "'lost': 0" in out
        else:
            ok = bool(re.search(r"(SELF-TEST|REAL-DEVICE) PASS", out))
        rows.append((group, name, setting, "PASS" if ok else "FAIL", detail(out)))
        print(f"   {'PASS' if ok else 'FAIL'}  {rows[-1][4]}", flush=True)

    passed = sum(r[3] == "PASS" for r in rows)
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    wiring = ("jumpers PB14->PC7, PB11->PC0, PB12->PC1" if a.wired
              else "no external wiring (BIO self-test sources)")
    md = [f"# BaoScope demo scorecard — {passed}/{len(rows)} PASS", "", f"Run {stamp} on a Dabao "
          f"(Baochip-1x) with the baoscope firmware; {wiring}.", "",
          "| Group | Test | Setting | Result | Detail |", "|---|---|---|---|---|"]
    md += [f"| {g} | {n} | {s} | **{r}** | {d} |" for g, n, s, r, d in rows]
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w") as f:
        f.write("\n".join(md) + "\n")
    print("\n" + "\n".join(md))
    print(f"\nsaved {os.path.normpath(a.out)}")
    sys.exit(0 if passed == len(rows) else 1)


if __name__ == "__main__":
    main()
