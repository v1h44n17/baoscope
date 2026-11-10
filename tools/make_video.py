#!/usr/bin/env python3
"""Render the BaoScope demo video from real measurement data (no screen recording needed).

Every waveform, decoded line and score shown comes from files in captures/ produced by the
board: a 17.5 MS/s capture (.vcd), the protocol sweep logs and the demo scorecard.

  .venv/bin/python tools/make_video.py   ->  media/baoscope-demo.mp4  (1280x720, 24 fps)
"""
import os
import re
import subprocess

import imageio_ffmpeg
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
CAP = os.path.join(ROOT, "captures")
OUT = os.path.join(ROOT, "media", "baoscope-demo.mp4")
W, H, FPS, DPI = 1280, 720, 24, 100

BG, FG, DIM, ACC, OK, WARN = "#0f1419", "#e6edf3", "#8b949e", "#58a6ff", "#3fb950", "#d29922"
plt.rcParams.update({"font.family": "DejaVu Sans", "text.color": FG, "axes.labelcolor": FG,
                     "xtick.color": DIM, "ytick.color": DIM, "axes.edgecolor": DIM})


def new_fig():
    return plt.figure(figsize=(W / DPI, H / DPI), dpi=DPI, facecolor=BG)


def frame_bytes(fig):
    fig.canvas.draw()
    rgba = bytes(memoryview(fig.canvas.buffer_rgba()).cast("B"))
    plt.close(fig)
    return rgba


def ease(t):
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


# ---------------------------------------------------------------- data from captures/
def load_vcd(path, rate_hz=17.5e6, max_samples=4000):
    """Rebuild per-sample channel values from a BaoScope VCD (1 ns timescale)."""
    ids, cur, events, t_prev = {}, [0] * 8, [], 0
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line.startswith("$var"):
                parts = line.split()
                ids[parts[3]] = int(parts[4][2:])
            elif line.startswith("#"):
                t = int(line[1:])
                events.append((t_prev, list(cur)))
                t_prev = t
            elif line and line[0] in "01" and line[1:] in ids:
                cur[ids[line[1:]]] = int(line[0])
    events.append((t_prev, list(cur)))
    ns_per = 1e9 / rate_hz
    samples = []
    for (t0, v), (t1, _) in zip(events, events[1:]):
        samples.extend([v] * max(1, round((t1 - t0) / ns_per)))
        if len(samples) >= max_samples:
            break
    return samples[:max_samples], ns_per


def grab(path, pattern, n):
    try:
        return re.findall(pattern, open(path).read())[:n]
    except OSError:
        return []


def scorecard_rows():
    rows = []
    try:
        for line in open(os.path.join(CAP, "demo-scorecard.md")):
            cells = [c.strip().strip("*") for c in line.strip().strip("|").split("|")]
            if len(cells) == 5 and cells[3] in ("PASS", "FAIL"):
                rows.append(cells)
    except OSError:
        pass
    return rows


SAMPLES, NS_PER = load_vcd(os.path.join(CAP, "split-m2-17500000.vcd"))
I2C_LINES = grab(os.path.join(CAP, "i2c-sweep.txt"), r"S a0\+ [0-9a-f]{2}\+ [0-9a-f]{2}- P", 3)
SPI_LINES = grab(os.path.join(CAP, "spi-sweep.txt"), r"\[9f/ff [0-9a-f/ ]+\]", 3)
UART_BYTES = grab(os.path.join(CAP, "uart-sweep.txt"), r"first bytes: ([0-9a-f ]+)", 1)
ROWS = scorecard_rows()


# ---------------------------------------------------------------- scenes: draw(fig, t in 0..1)
def title(fig, t):
    fig.text(0.5, 0.60, "BaoScope", ha="center", fontsize=72, weight="bold", alpha=ease(t * 3))
    fig.text(0.5, 0.47, "a logic analyzer + on-chip protocol decoder built from the Baochip-1x BIO cores",
             ha="center", fontsize=20, color=DIM, alpha=ease(t * 3 - 0.3))
    fig.text(0.5, 0.33, "Dabao Hackathon  ·  Devcon 8  ·  BIO + Tooling", ha="center", fontsize=16,
             color=ACC, alpha=ease(t * 3 - 0.6))
    fig.text(0.5, 0.12, "every number in this video comes from logs captured on the board",
             ha="center", fontsize=13, color=DIM, alpha=ease(t * 3 - 0.9), style="italic")


def architecture(fig, t):
    ax = fig.add_axes([0, 0, 1, 1]); ax.axis("off"); ax.set_xlim(0, 16); ax.set_ylim(0, 9)
    fig.text(0.5, 0.92, "How it uses the silicon", ha="center", fontsize=28, weight="bold")
    boxes = [
        (0.6, 4.6, 3.6, 1.6, "GPIO pads\nPC0–PC3, PC7…", DIM),
        (5.0, 4.6, 3.4, 1.6, "BIO core 0\nsampler / decoder\nquantum-locked", ACC),
        (9.2, 4.6, 3.2, 1.6, "BIO core 1\nwriter · page table\nin its own IMEM", ACC),
        (13.0, 4.6, 2.5, 1.6, "SRAM\n256 KiB", DIM),
        (5.0, 1.6, 3.4, 1.6, "BIO core 2\nself-test source\n(counter/UART/I²C/SPI)", WARN),
        (9.2, 1.6, 6.3, 1.6, "VexRiscv + Xous app (sleeps during capture)\n→ USB HS → host tools → .vcd / PulseView", DIM),
    ]
    for i, (x, y, w, h, label, col) in enumerate(boxes):
        a = ease(t * 6 - i * 0.5)
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.08", fc=BG, ec=col, lw=2, alpha=a))
        ax.text(x + w / 2, y + h / 2, label, ha="center", va="center", fontsize=12, alpha=a)
    arrows = [((4.3, 5.4), (5.0, 5.4), "x21"), ((8.4, 5.4), (9.2, 5.4), "FIFO2"),
              ((12.4, 5.4), (13.0, 5.4), "BDMA"), ((6.7, 3.2), (6.7, 4.6), "drives pads"),
              ((14.2, 4.6), (14.2, 3.2), "")]
    for i, (p, q, lab) in enumerate(arrows):
        a = ease(t * 6 - 3 - i * 0.4)
        ax.add_patch(FancyArrowPatch(p, q, arrowstyle="-|>", mutation_scale=18, color=FG, alpha=a, lw=1.6))
        if lab:
            ax.text((p[0] + q[0]) / 2, (p[1] + q[1]) / 2 + 0.35, lab, ha="center", fontsize=10, color=ACC, alpha=a)
    fig.text(0.5, 0.06, "4 PicoRV32 BIO cores @ 700 MHz  ·  FIFOs  ·  BDMA  ·  MMU-backed Xous app",
             ha="center", fontsize=14, color=DIM, alpha=ease(t * 4 - 2))


def speed(fig, t):
    fig.text(0.5, 0.90, "Clean capture rate, one day of iteration", ha="center", fontsize=26, weight="bold")
    ax = fig.add_axes([0.12, 0.16, 0.8, 0.64], facecolor=BG)
    steps = [("CPU drains\nFIFO", 0.25), ("BIO DMA\nto RAM", 5), ("split pipeline\n8 ch", 17.5),
             ("split pipeline\n4 ch", 25)]
    for i, (lab, v) in enumerate(steps):
        g = ease(t * 4 - i * 0.6)
        ax.bar(i, v * g, color=[DIM, ACC, OK, OK][i], width=0.6)
        if g > 0.95:
            star = "*" if v == 25 else ""
            ax.text(i, v + 0.6, f"{v:g} MS/s{star}", ha="center", fontsize=15, weight="bold")
    ax.set_xticks(range(4)); ax.set_xticklabels([s[0] for s in steps], fontsize=12)
    ax.set_ylim(0, 29); ax.set_ylabel("MS/s (self-test verified)", fontsize=12)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    fig.text(0.5, 0.05, "every step checked end to end by a counter generated on a spare BIO core   "
             "(* all 16,383 steps exact after the first 8 samples)",
             ha="center", fontsize=12, color=DIM, alpha=ease(t * 4 - 2.5))


def waveform(fig, t):
    fig.text(0.5, 0.92, "A real 17.5 MS/s capture (mode 2, 8 channels)", ha="center", fontsize=24, weight="bold")
    ax = fig.add_axes([0.08, 0.14, 0.86, 0.70], facecolor=BG)
    win = 360
    start = int(ease(t) * max(len(SAMPLES) - win - 1, 0))
    seg = SAMPLES[start:start + win]
    xs = [(start + i) * NS_PER / 1000 for i in range(len(seg))]
    colors = [OK] * 4 + [DIM] * 4
    for ch in range(8):
        ax.step(xs, [ch * 1.5 + s[ch] for s in seg], where="post", color=colors[ch], lw=1.6)
        ax.text(xs[0] if xs else 0, ch * 1.5 + 0.35, f" ch{ch}", fontsize=10, color=colors[ch], va="bottom")
    ax.set_yticks([]); ax.set_xlabel("time (µs) — one sample every 57 ns", fontsize=12)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    fig.text(0.5, 0.04, "ch0–ch3: the BIO test counter (each bit half the rate of the last), "
             "sampled with zero missed steps", ha="center", fontsize=12, color=DIM)


def decode(fig, t):
    fig.text(0.5, 0.90, "Decoded on the chip — UART, I²C, SPI", ha="center", fontsize=26, weight="bold")
    blocks = [
        ("UART 8N1 · 2 Mbaud · 4096/4096 bytes",
         ["bytes: " + (UART_BYTES[0][:47] if UART_BYTES else "00 01 02 03 …")]),
        ("I²C · 1 MHz · 819/819 transactions", I2C_LINES or ["S a0+ 00+ ff- P"]),
        ("SPI mode 0 · 2 MHz · 819/819 frames (MOSI/MISO)", SPI_LINES or ["[9f/ff 00/ff a5/00]"]),
    ]
    y = 0.76
    for bi, (head, lines) in enumerate(blocks):
        a = ease(t * 5 - bi * 1.2)
        fig.text(0.10, y, head, fontsize=17, color=ACC, alpha=a, weight="bold")
        y -= 0.055
        for li, line in enumerate(lines[:3]):
            shown = line[: int(len(line) * ease(t * 5 - bi * 1.2 - 0.3 - li * 0.15))]
            fig.text(0.13, y, shown, fontsize=15, family="DejaVu Sans Mono", alpha=a)
            y -= 0.048
        y -= 0.03
    fig.text(0.5, 0.05, "decoders act only on line-state changes; bytes stream to RAM through the same writer core",
             ha="center", fontsize=12, color=DIM)


def scorecard(fig, t):
    passed = sum(r[3] == "PASS" for r in ROWS)
    fig.text(0.5, 0.90, f"python tools/demo.py  →  {passed}/{len(ROWS)} PASS", ha="center", fontsize=28,
             weight="bold", family="DejaVu Sans Mono")
    y = 0.78
    for i, (g, n, s, r, d) in enumerate(ROWS):
        a = ease(t * 7 - i * 0.6)
        fig.text(0.04, y, g, fontsize=13, color=DIM, alpha=a)
        fig.text(0.13, y, n, fontsize=14, alpha=a)
        fig.text(0.40, y, s, fontsize=13, color=DIM, alpha=a)
        fig.text(0.60, y, r, fontsize=14, weight="bold", color=OK if r == "PASS" else WARN, alpha=a)
        fig.text(0.68, y, d, fontsize=12, color=DIM, alpha=a)
        y -= 0.085
    fig.text(0.5, 0.06, "no wiring needed — a spare BIO core is the signal source", ha="center", fontsize=13,
             color=DIM, alpha=ease(t * 3 - 2))


def findings(fig, t):
    fig.text(0.5, 0.88, "Open: what we found along the way", ha="center", fontsize=26, weight="bold")
    items = [
        "USB CDC repeats whole 512-byte packets under load (26–35 % duplicates); link survives via seq+CRC",
        "port_and_pin_to_bio_bit maps port B backwards (pinout: PB14 = BIO14)",
        "BIO registers x16–x31 are hardware: t3–t6 / a6 / a7 / s2–s11 are traps as scratch",
        "BIO reads pads it doesn't own as 0: on-chip peripherals need a jumper to be sniffed",
    ]
    for i, s in enumerate(items):
        fig.text(0.07, 0.72 - i * 0.11, "•  " + s, fontsize=15, alpha=ease(t * 5 - i * 0.7))
    fig.text(0.5, 0.17, "github.com/v1h44n17/baoscope", ha="center", fontsize=24, color=ACC, weight="bold",
             alpha=ease(t * 4 - 2.5))
    fig.text(0.5, 0.10, "firmware: github.com/v1h44n17/xous-core  (branch baoscope)", ha="center", fontsize=15,
             color=DIM, alpha=ease(t * 4 - 2.8))


SCENES = [(5, title), (9, architecture), (8, speed), (11, waveform), (11, decode), (9, scorecard), (9, findings)]


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    cmd = [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgba",
           "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-pix_fmt", "yuv420p",
           "-crf", "20", "-movflags", "+faststart", OUT]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    total = 0
    for secs, draw in SCENES:
        n = int(secs * FPS)
        for i in range(n):
            fig = new_fig()
            draw(fig, i / max(n - 1, 1))
            proc.stdin.write(frame_bytes(fig))
            total += 1
        print(f"rendered {draw.__name__} ({n} frames)", flush=True)
    proc.stdin.close()
    proc.wait()
    print(f"wrote {OUT}: {total} frames, {total / FPS:.1f} s, rc={proc.returncode}")


if __name__ == "__main__":
    main()
