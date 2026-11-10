#!/usr/bin/env python3
"""Send commands to the Dabao USB serial console and print the replies.

Usage: console.py [--port PORT] [--wait SECONDS] CMD [CMD ...]
If --port is omitted, the first /dev/cu.usbmodem* device is used.
For interactive use prefer: python xous-core/tools-bao/bao.py monitor -p PORT
"""
import argparse
import glob
import sys
import time

import serial


def find_port():
    ports = sorted(glob.glob("/dev/cu.usbmodem*"))
    if not ports:
        sys.exit("no /dev/cu.usbmodem* port found - is the board plugged in?")
    return ports[0]


def read_for(ser, seconds):
    end = time.time() + seconds
    out = bytearray()
    while time.time() < end:
        chunk = ser.read(4096)
        if chunk:
            out += chunk
            end = max(end, time.time() + 0.3)  # keep reading while data flows
    return out.decode("utf-8", errors="replace")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port")
    ap.add_argument("--wait", type=float, default=1.5)
    ap.add_argument("cmds", nargs="*")
    a = ap.parse_args()

    port = a.port or find_port()
    with serial.Serial(port, 1_000_000, timeout=0.1) as ser:
        print(f"[port {port}]")
        ser.write(b"\r\n")
        print(read_for(ser, a.wait), end="")
        for cmd in a.cmds:
            print(f"\n>>> {cmd}")
            ser.write(cmd.encode() + b"\r\n")
            print(read_for(ser, a.wait), end="")
    print()


if __name__ == "__main__":
    main()
