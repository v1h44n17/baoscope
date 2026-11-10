#!/usr/bin/env python3
"""BaoScope on-chip protocol decode (host side).

UART: the BIO decoder core decodes 8N1 frames on one pin and a writer core stores
each byte (plus a framing-error flag) in RAM; this tool requests a run and prints
the result.

  decode.py uart --baud 115200 [--pin 16] [--n 1024] [--timeout 1000] [--selftest]
                 [--out bytes.bin] [--port P]

--selftest makes a third BIO core transmit 0,1,2,...,255,0,... on the same pin
(no wiring needed) and checks the decoded stream is that exact sequence.
Pin numbers are BIO bits: 16..19 = PC0..PC3, 23 = PC7, 24 = PC8, 26 = PC10, 27 = PC11.
"""
import argparse
import struct
import sys

import serial

from capture import Link, find_port

HDR = struct.Struct("<5I")  # version, baud, count, elapsed_ms, finished


def uart(link, baud, pin, n, timeout_ms, selftest):
    link.ser.write(f"uart {baud} {pin} {n} {timeout_ms} {int(selftest)}\n".encode())
    kind, body = link.message(timeout=timeout_ms / 1000 + 10)
    if kind == b"ERR_":
        raise RuntimeError(body.decode(errors="replace"))
    if kind != b"UAR1":
        raise RuntimeError(f"unexpected message {kind!r}")
    ver, baud_act, count, elapsed, finished = HDR.unpack_from(body)
    pairs = body[HDR.size:HDR.size + 2 * count]
    data = bytes(pairs[0::2])
    ferr = [i for i, f in enumerate(pairs[1::2]) if f & 1]
    return dict(version=ver, baud=baud_act, count=count, elapsed_ms=elapsed,
                finished=bool(finished), framing_errors=len(ferr), first_ferr=ferr[:5]), data


def check_sequence(data):
    """Self-test: after an optional junk prefix, bytes must be 0,1,2,... mod 256."""
    start = data.find(b"\x00")
    if start < 0:
        return dict(ok=False, reason="no 0x00 found")
    errors = [i for i in range(start + 1, len(data)) if data[i] != (data[i - 1] + 1) & 0xFF]
    return dict(ok=not errors and len(data) - start > 1, junk_prefix=start,
                checked=len(data) - start, sequence_errors=len(errors), first_errors=errors[:5])


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="proto", required=True)
    u = sub.add_parser("uart")
    u.add_argument("--baud", type=int, default=115200)
    u.add_argument("--pin", type=int, default=16, help="BIO bit number (default 16 = PC0)")
    u.add_argument("--n", type=int, default=1024, help="bytes to decode before stopping")
    u.add_argument("--timeout", type=int, default=1000, help="ms to wait for n bytes")
    u.add_argument("--selftest", action="store_true")
    u.add_argument("--out", help="write decoded bytes to this file")
    ap.add_argument("--port")
    a = ap.parse_args()

    with serial.Serial(a.port or find_port(), 1_000_000, timeout=0.05) as ser:
        link = Link(ser)
        link.drain()
        ser.write(b"ping\n")
        link.message(timeout=3)
        info, data = uart(link, a.baud, a.pin, a.n, a.timeout, a.selftest)

    print("uart:", info)
    print("first bytes:", data[:32].hex(" "))
    if a.out:
        open(a.out, "wb").write(data)
        print("wrote", a.out)
    if a.selftest:
        v = check_sequence(data)
        ok = v["ok"] and info["framing_errors"] == 0 and info["count"] >= a.n
        print("self-test:", v)
        print("SELF-TEST", "PASS" if ok else "FAIL")
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
