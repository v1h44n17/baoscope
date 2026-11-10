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
    # selftest: False/0 = external, True/1 = BIO source, 2 = real UART2 log traffic
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


I2C_START, I2C_STOP, I2C_BYTE = 1, 2, 3


def i2c(link, sample_hz, bus_hz, scl, n, timeout_ms, selftest):
    link.ser.write(f"i2c {sample_hz} {bus_hz} {scl} {n} {timeout_ms} {int(selftest)}\n".encode())
    kind, body = link.message(timeout=timeout_ms / 1000 + 10)
    if kind == b"ERR_":
        raise RuntimeError(body.decode(errors="replace"))
    if kind != b"I2C1":
        raise RuntimeError(f"unexpected message {kind!r}")
    ver, rate, count, elapsed, finished = HDR.unpack_from(body)
    words = struct.unpack_from(f"<{count}I", body, HDR.size)
    events = [(w >> 16, w & 0xFF, (w >> 8) & 1) for w in words]  # (tag, data, nack)
    return dict(version=ver, sample_hz=rate, events=count, elapsed_ms=elapsed,
                finished=bool(finished)), events


SPI_ASSERT, SPI_RELEASE, SPI_BYTE = 1, 2, 3


def spi(link, sample_hz, sck_hz, base, n, timeout_ms, selftest):
    link.ser.write(f"spi {sample_hz} {sck_hz} {base} {n} {timeout_ms} {int(selftest)}\n".encode())
    kind, body = link.message(timeout=timeout_ms / 1000 + 10)
    if kind == b"ERR_":
        raise RuntimeError(body.decode(errors="replace"))
    if kind != b"SPI1":
        raise RuntimeError(f"unexpected message {kind!r}")
    ver, rate, count, elapsed, finished = HDR.unpack_from(body)
    words = struct.unpack_from(f"<{count}I", body, HDR.size)
    events = [(w >> 16, w & 0xFF, (w >> 8) & 0xFF) for w in words]  # (tag, mosi, miso/leftover)
    return dict(version=ver, sample_hz=rate, events=count, elapsed_ms=elapsed,
                finished=bool(finished)), events


def spi_frames(events):
    """Group events into CS-assert..release frames of (mosi, miso) bytes; note partial bytes."""
    frames, cur = [], None
    for tag, mosi, miso in events:
        if tag == SPI_ASSERT:
            cur = {"bytes": [], "leftover_bits": 0}
        elif tag == SPI_RELEASE and cur is not None:
            cur["leftover_bits"] = mosi  # release event carries the partial bit count in low bits
            frames.append(cur)
            cur = None
        elif tag == SPI_BYTE and cur is not None:
            cur["bytes"].append((mosi, miso))
    return frames


def fmt_frame(f):
    s = " ".join(f"{a:02x}/{b:02x}" for a, b in f["bytes"])
    return f"[{s}]" + (f" +{f['leftover_bits']} bits" if f["leftover_bits"] else "")


def check_spi(frames):
    """Self-test: each frame is 9F/FF, k/~k, A5/k with k counting up (first frame may be cut)."""
    bad, prev_k = [], None
    for i, f in enumerate(frames):
        b = f["bytes"]
        ok = (len(b) == 3 and not f["leftover_bits"] and b[0] == (0x9F, 0xFF)
              and b[1][1] == (~b[1][0]) & 0xFF and b[2] == (0xA5, b[1][0]))
        if ok and prev_k is not None and b[1][0] != (prev_k + 1) & 0xFF:
            ok = False
        if not ok:
            bad.append(i)
        prev_k = b[1][0] if len(b) > 1 else None
    real_bad = [i for i in bad if i != 0]
    return dict(ok=len(frames) > 1 and not real_bad, frames=len(frames),
                bad=len(real_bad), first_bad=real_bad[:5])


def i2c_transactions(events):
    """Group events into START..STOP transactions of (data, nack) bytes."""
    txns, cur = [], None
    for tag, data, nack in events:
        if tag == I2C_START:
            if cur is not None:
                txns.append(cur)  # repeated START closes the previous one
            cur = []
        elif tag == I2C_STOP:
            if cur is not None:
                txns.append(cur)
            cur = None
        elif tag == I2C_BYTE and cur is not None:
            cur.append((data, nack))
    return txns


def fmt_txn(t):
    return "S " + " ".join(f"{d:02x}{'-' if n else '+'}" for d, n in t) + " P"


def check_i2c(txns):
    """Self-test: each transaction is [A0+, k+, ~k-] with k counting up by 1 (mod 256)."""
    bad, prev_k = [], None
    for i, t in enumerate(txns):
        ok = len(t) == 3 and t[0] == (0xA0, 0) and t[1][1] == 0 and t[2] == ((~t[1][0]) & 0xFF, 1)
        if ok and prev_k is not None and t[1][0] != (prev_k + 1) & 0xFF:
            ok = False
        if not ok:
            bad.append(i)
        prev_k = t[1][0] if len(t) > 1 else None
    # the first transaction may be cut off by the capture start
    real_bad = [i for i in bad if i != 0]
    return dict(ok=len(txns) > 1 and not real_bad, transactions=len(txns),
                bad=len(real_bad), first_bad=real_bad[:5])


def check_real_uart(data):
    """Real UART2 log traffic: lines 'BSCP-REAL NNNNNN' must be numbered consecutively."""
    import re
    nums = [int(m) for m in re.findall(rb"BSCP-REAL (\d{6})", data)]
    gaps = [(a, b) for a, b in zip(nums, nums[1:]) if b != a + 1]
    return dict(ok=len(nums) >= 5 and not gaps, lines=len(nums),
                first=nums[0] if nums else None, last=nums[-1] if nums else None,
                gaps=len(gaps), first_gaps=gaps[:5])


def check_real_i2c(txns):
    """Real I2C0 scan with no device attached: every transaction is one address byte
    (addr<<1 | W) answered with NACK, and addresses step 0x08..0x77 in order."""
    addrs, malformed = [], []
    for i, t in enumerate(txns):
        if len(t) == 1 and t[0][1] == 1 and t[0][0] & 1 == 0:
            addrs.append(t[0][0] >> 1)
        else:
            malformed.append(i)
    malformed = [i for i in malformed if i != 0]  # first one may be cut by the capture start
    nxt = lambda a: 0x08 if a == 0x77 else a + 1
    order_errors = sum(1 for a, b in zip(addrs, addrs[1:]) if b != nxt(a))
    return dict(ok=len(addrs) > 10 and not malformed and order_errors == 0,
                transactions=len(txns), probes=len(addrs), malformed=len(malformed),
                first_malformed=malformed[:5], order_errors=order_errors,
                addr_range=(hex(min(addrs)), hex(max(addrs))) if addrs else None)


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
    u.add_argument("--real", action="store_true",
                   help="decode the chip's real UART2 TX (log output, 1 Mbaud). Jumper header "
                        "pin 15 (PB14) -> pin 9 (PC7); uses --pin 23 and --baud 1000000")
    i = sub.add_parser("i2c")
    i.add_argument("--bus", type=int, default=100_000, help="self-test source bus rate (Hz)")
    i.add_argument("--sample", type=int, default=10_000_000, help="decoder poll rate (Hz)")
    i.add_argument("--scl", type=int, default=16, help="SCL BIO bit (SDA = SCL+1), default PC0/PC1")
    i.add_argument("--n", type=int, default=1024, help="events to decode before stopping")
    i.add_argument("--timeout", type=int, default=2000)
    i.add_argument("--selftest", action="store_true")
    i.add_argument("--real", action="store_true",
                   help="decode the chip's real I2C0 controller scanning 0x08-0x77. Jumper header "
                        "pin 27 (PB11 SCL) -> pin 14 (PC0), pin 26 (PB12 SDA) -> pin 12 (PC1)")
    s = sub.add_parser("spi", help="mode 0; pins base..base+3 = SCK, MOSI, MISO, CS")
    s.add_argument("--sck", type=int, default=1_000_000, help="self-test source SCK rate (Hz)")
    s.add_argument("--sample", type=int, default=10_000_000, help="decoder poll rate (Hz)")
    s.add_argument("--base", type=int, default=16, help="SCK BIO bit (default 16 = PC0..PC3)")
    s.add_argument("--n", type=int, default=1025, help="events to decode before stopping")
    s.add_argument("--timeout", type=int, default=2000)
    s.add_argument("--selftest", action="store_true")
    ap.add_argument("--port")
    a = ap.parse_args()

    with serial.Serial(a.port or find_port(), 1_000_000, timeout=0.05) as ser:
        link = Link(ser)
        link.drain()
        ser.write(b"ping\n")
        link.message(timeout=3)
        real = getattr(a, "real", False)
        if real and a.selftest:
            sys.exit("--real and --selftest are exclusive (self-tests drive the pins)")
        if a.proto == "i2c":
            info, events = i2c(link, a.sample, a.bus, a.scl, a.n, a.timeout, 2 if real else a.selftest)
        elif a.proto == "spi":
            info, events = spi(link, a.sample, a.sck, a.base, a.n, a.timeout, a.selftest)
        else:
            if real:
                a.pin, a.baud = 23, 1_000_000
            info, data = uart(link, a.baud, a.pin, a.n, a.timeout, 2 if real else a.selftest)

    if a.proto == "spi":
        frames = spi_frames(events)
        print("spi:", info)
        for f in frames[:4]:
            print("  ", fmt_frame(f))
        if a.selftest:
            v = check_spi(frames)
            print("self-test:", v)
            print("SELF-TEST", "PASS" if v["ok"] else "FAIL")
            sys.exit(0 if v["ok"] else 1)
        return
    if a.proto == "i2c":
        txns = i2c_transactions(events)
        print("i2c:", info)
        for t in txns[:4]:
            print("  ", fmt_txn(t))
        if real:
            v = check_real_i2c(txns)
            print("real-device check:", v)
            print("REAL-DEVICE", "PASS" if v["ok"] else "FAIL")
            sys.exit(0 if v["ok"] else 1)
        if a.selftest:
            v = check_i2c(txns)
            print("self-test:", v)
            print("SELF-TEST", "PASS" if v["ok"] else "FAIL")
            sys.exit(0 if v["ok"] else 1)
        return

    print("uart:", info)
    print("first bytes:", data[:32].hex(" "))
    if a.out:
        open(a.out, "wb").write(data)
        print("wrote", a.out)
    if real:
        text = data.decode("ascii", errors="replace")
        print("decoded text sample:", repr(text[:160]))
        v = check_real_uart(data)
        ok = v["ok"] and info["framing_errors"] == 0
        print("real-device check:", v)
        print("REAL-DEVICE", "PASS" if ok else "FAIL")
        sys.exit(0 if ok else 1)
    if a.selftest:
        v = check_sequence(data)
        ok = v["ok"] and info["framing_errors"] == 0 and info["count"] >= a.n
        print("self-test:", v)
        print("SELF-TEST", "PASS" if ok else "FAIL")
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
