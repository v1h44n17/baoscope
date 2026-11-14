#!/usr/bin/env python3
"""Render the narrated BaoScope demo video (~2.5 min) from real data captured on the board.

Footage:
  - a live run of tools/demo.py on the Dabao, replayed at its real speed
    (captures/demo-live.jsonl: one {"t": seconds, "line": text} per output line)
  - a live 17.5 MS/s capture (captures/live-m2-17500000.vcd)
  - live UART / I2C / SPI decoder output (captures/live-decode.txt)
Narration: macOS `say`; mixed with the ffmpeg bundled in imageio-ffmpeg.

  .venv/bin/python tools/make_video.py   ->  media/baoscope-demo.mp4  (1280x720, 24 fps, AAC)
"""
import json
import os
import re
import subprocess
import tempfile

import imageio_ffmpeg
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
CAP = os.path.join(ROOT, "captures")
OUT = os.path.join(ROOT, "media", "baoscope-demo.mp4")
W, H, FPS, DPI = 1280, 720, 24, 100
FF = imageio_ffmpeg.get_ffmpeg_exe()
VOICE_PREFS = ["Samantha", "Daniel", "Alex"]

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


def caption(fig, text):
    fig.text(0.5, 0.035, text, ha="center", fontsize=12, color=DIM)


# ---------------------------------------------------------------- data from captures/
def load_vcd(path, rate_hz=17.5e6, max_samples=4000):
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


def read(path):
    try:
        return open(path).read()
    except OSError:
        return ""


LIVE = [json.loads(l) for l in read(os.path.join(CAP, "demo-live.jsonl")).splitlines() if l.strip()]
LIVE_LEN = LIVE[-1]["t"] if LIVE else 10.0
SAMPLES, NS_PER = load_vcd(os.path.join(CAP, "live-m2-17500000.vcd"))
DEC = read(os.path.join(CAP, "live-decode.txt"))
UART = (re.findall(r"first bytes: ([0-9a-f ]+)", DEC) or ["00 01 02 03"])[0][:47]
I2C = re.findall(r"S a0\+ [0-9a-f]{2}\+ [0-9a-f]{2}- P", DEC)[:3]
SPI = re.findall(r"\[9f/ff [0-9a-f/ ]+\]", DEC)[:3]


# ---------------------------------------------------------------- scenes: draw(fig, t in 0..1, duration)
def s_title(fig, t, d):
    fig.text(0.5, 0.60, "BaoScope", ha="center", fontsize=76, weight="bold", alpha=ease(t * 3))
    fig.text(0.5, 0.47, "a logic analyzer + on-chip protocol decoder built from the Baochip-1x BIO cores",
             ha="center", fontsize=20, color=DIM, alpha=ease(t * 3 - 0.3))
    fig.text(0.5, 0.34, "running on the Dabao  ·  Dabao Hackathon  ·  Devcon 8", ha="center", fontsize=16,
             color=ACC, alpha=ease(t * 3 - 0.6))
    fig.text(0.5, 0.12, "all footage below is real data from the board", ha="center", fontsize=13,
             color=DIM, alpha=ease(t * 3 - 0.9), style="italic")


def s_why(fig, t, d):
    fig.text(0.5, 0.86, "Why the Baochip?", ha="center", fontsize=30, weight="bold")
    rows = [("Typical logic analyzer", "dedicated FPGA or MCU next to the target", DIM),
            ("Baochip-1x", "4 × PicoRV32 BIO cores @ 700 MHz, FIFOs, DMA — on the same open chip", OK),
            ("BaoScope", "BIO samples, packs, DMAs and decodes; the main CPU sleeps", ACC)]
    for i, (a, b, c) in enumerate(rows):
        al = ease(t * 4 - i * 0.8)
        fig.text(0.10, 0.64 - i * 0.16, a, fontsize=20, weight="bold", color=c, alpha=al)
        fig.text(0.10, 0.58 - i * 0.16, b, fontsize=17, alpha=al)
    caption(fig, "open RTL, IRIS-inspectable die, and now an open logic analyzer built from it")


def s_arch(fig, t, d):
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
        a = ease(t * 6 - i * 0.6)
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.08", fc=BG, ec=col, lw=2, alpha=a))
        ax.text(x + w / 2, y + h / 2, label, ha="center", va="center", fontsize=12, alpha=a)
    arrows = [((4.3, 5.4), (5.0, 5.4), "x21"), ((8.4, 5.4), (9.2, 5.4), "FIFO2"),
              ((12.4, 5.4), (13.0, 5.4), "BDMA"), ((6.7, 3.2), (6.7, 4.6), "drives pads"),
              ((14.2, 4.6), (14.2, 3.2), "")]
    for i, (p, q, lab) in enumerate(arrows):
        a = ease(t * 6 - 3.5 - i * 0.4)
        ax.add_patch(FancyArrowPatch(p, q, arrowstyle="-|>", mutation_scale=18, color=FG, alpha=a, lw=1.6))
        if lab:
            ax.text((p[0] + q[0]) / 2, (p[1] + q[1]) / 2 + 0.35, lab, ha="center", fontsize=10, color=ACC, alpha=a)
    caption(fig, "sampling is cycle-exact: measured exactly 700 MHz ÷ rate BIO cycles per sample")


def s_live(fig, t, d):
    """Replay the recorded demo.py run at real speed, starting 1.5 s into the scene."""
    elapsed = t * d - 1.5
    fig.text(0.5, 0.93, "Live on the Dabao: python tools/demo.py", ha="center", fontsize=24, weight="bold")
    live = elapsed < LIVE_LEN + 0.5
    fig.text(0.95, 0.893, "● LIVE RECORDING · real time" if live else "● recorded run complete",
             ha="right", fontsize=12, color="#f85149" if live else DIM)
    ax = fig.add_axes([0.05, 0.08, 0.90, 0.79], facecolor="#010409")
    ax.set_xticks([]); ax.set_yticks([])
    for s in ax.spines.values():
        s.set_color("#30363d")
    lines = [r["line"] for r in LIVE if r["t"] <= elapsed][-24:]
    for i, line in enumerate(lines):
        col = OK if "PASS" in line else (WARN if "FAIL" in line else
                                         (ACC if line.startswith(("->", "$")) else FG))
        ax.text(0.015, 0.965 - i * 0.04, line[:118], transform=ax.transAxes, fontsize=10.5,
                family="DejaVu Sans Mono", color=col, va="top")
    fig.text(0.05, 0.035, f"t = {max(0.0, min(elapsed, LIVE_LEN)):5.2f} s", fontsize=12, color=DIM,
             family="DejaVu Sans Mono")
    caption(fig, "USB link check, three capture modes and three on-chip decoders — each verified against a known signal")


def s_speed(fig, t, d):
    fig.text(0.5, 0.90, "Clean capture rate, one day of iteration", ha="center", fontsize=26, weight="bold")
    ax = fig.add_axes([0.12, 0.16, 0.8, 0.64], facecolor=BG)
    steps = [("CPU drains\nFIFO", 0.25), ("BIO DMA\nto RAM", 5), ("split pipeline\n8 ch", 17.5),
             ("split pipeline\n4 ch", 25)]
    for i, (lab, v) in enumerate(steps):
        g = ease(t * 4 - i * 0.7)
        ax.bar(i, v * g, color=[DIM, ACC, OK, OK][i], width=0.6)
        if g > 0.95:
            ax.text(i, v + 0.6, f"{v:g} MS/s" + ("*" if v == 25 else ""), ha="center", fontsize=15, weight="bold")
    ax.set_xticks(range(4)); ax.set_xticklabels([s[0] for s in steps], fontsize=12)
    ax.set_ylim(0, 29); ax.set_ylabel("MS/s (self-test verified)", fontsize=12)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    caption(fig, "* 4-channel 25 MS/s: all 16,383 counter steps exact after the first 8 samples")


def s_wave(fig, t, d):
    fig.text(0.5, 0.92, "A live 17.5 MS/s capture (8 channels)", ha="center", fontsize=24, weight="bold")
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
    caption(fig, "ch0–ch3: the BIO test counter, each bit half the rate of the last — no missed steps")


def s_decode(fig, t, d):
    fig.text(0.5, 0.90, "Decoded on the chip — live output", ha="center", fontsize=26, weight="bold")
    blocks = [("UART 8N1 · 2 Mbaud · 4096/4096 bytes", ["bytes: " + UART]),
              ("I²C · 1 MHz · 819/819 transactions", I2C or ["S a0+ 00+ ff- P"]),
              ("SPI mode 0 · 2 MHz · 819/819 frames (MOSI/MISO)", SPI or ["[9f/ff 00/ff a5/00]"])]
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
    caption(fig, "decoders are small BIO state machines that act only on line changes; bytes stream to RAM")


def s_find(fig, t, d):
    fig.text(0.5, 0.88, "Open: what we found and filed upstream", ha="center", fontsize=26, weight="bold")
    items = ["#1017  USB CDC repeats whole 512-byte packets under load (26–35 % duplicates)",
             "#1018  port_and_pin_to_bio_bit maps port B backwards (pinout: PB14 = BIO14)",
             "#1019  load_code ragged last word shifted by bits, not bytes",
             "#1020  erratum_check.py: t3–t6 / a6 / a7 / s2–s11 alias BIO hardware registers",
             "Finding: BIO reads pads it doesn't own as 0 — sniffing on-chip peripherals needs a jumper"]
    for i, s in enumerate(items):
        fig.text(0.07, 0.72 - i * 0.105, s, fontsize=15, alpha=ease(t * 5 - i * 0.7),
                 color=FG if s[0] == "#" else WARN)
    caption(fig, "issues filed on betrusted-io/xous-core")


def s_outro(fig, t, d):
    fig.text(0.5, 0.62, "BaoScope", ha="center", fontsize=54, weight="bold", alpha=ease(t * 3))
    fig.text(0.5, 0.48, "github.com/v1h44n17/baoscope", ha="center", fontsize=26, color=ACC, weight="bold",
             alpha=ease(t * 3 - 0.4))
    fig.text(0.5, 0.40, "firmware: github.com/v1h44n17/xous-core  (branch baoscope)", ha="center",
             fontsize=16, color=DIM, alpha=ease(t * 3 - 0.7))
    fig.text(0.5, 0.26, "Apache-2.0  ·  reproduce: python tools/demo.py", ha="center", fontsize=15,
             color=DIM, alpha=ease(t * 3 - 1.0))


# (draw, narration, minimum seconds). Spellings are tuned for text-to-speech.
SCENES = [
    (s_title, "This is Bao Scope: a logic analyzer and on-chip protocol decoder, built entirely from the "
              "Baochip's B I O cores, and running on the Dabao board.", 8),
    (s_why, "Logic analyzers usually need a dedicated F P G A or microcontroller. The Baochip has four "
            "seven hundred megahertz RISC-V I O cores on the same open chip, so it can do the whole job "
            "itself, while its main C P U sleeps.", 12),
    (s_arch, "Core zero waits on its clock quantum and reads every pin at once, so sampling is cycle exact. "
             "It packs the samples and pushes them through a FIFO to core one, which writes them straight "
             "into RAM, by walking a table of physical page addresses kept in its own instruction memory. "
             "Core two generates test signals on the same pins, so every mode verifies itself, with no wiring.", 22),
    (s_live, "Here is the one-command demo, recorded live on the board and shown in real time. It checks the "
             "U S B link, captures eight channels at seventeen and a half mega-samples per second in three "
             "modes, then runs the UART, I two C and S P I decoders on the chip. Every test checks itself "
             "against a known signal. Seven out of seven pass, in under ten seconds.", LIVE_LEN + 6),
    (s_speed, "Over one day of iteration, the clean capture rate went from a quarter of a mega-sample per "
              "second with the C P U in the loop, to five with B I O DMA, to seventeen and a half with a "
              "split sampler and writer pipeline, and twenty five on four channels.", 14),
    (s_wave, "This is a live seventeen and a half mega-sample capture. Channels zero to three are the test "
             "counter, and every single step lands exactly where it should.", 11),
    (s_decode, "The decoders are small state machines on the B I O, streaming decoded bytes to RAM through "
               "the same pipeline: UART up to two megabaud, and I two C and S P I up to two megahertz.", 12),
    (s_find, "Along the way, we found and filed four bugs upstream, including U S B serial repeating whole "
             "five hundred and twelve byte packets. We also documented that the B I O cannot see pins it "
             "does not own.", 15),
    (s_outro, "Everything is open source under Apache two point oh, and reproducible with one command. "
              "Thanks for watching.", 7),
]


def pick_voice():
    have = subprocess.run(["say", "-v", "?"], capture_output=True, text=True).stdout
    for v in VOICE_PREFS:
        if re.search(rf"^{v}\s", have, re.M):
            return v
    return None


def tts(text, path, voice):
    subprocess.run(["say", "-r", "172", "-o", path] + (["-v", voice] if voice else []) + [text], check=True)
    info = subprocess.run(["afinfo", path], capture_output=True, text=True).stdout
    return float(re.search(r"estimated duration: ([\d.]+)", info).group(1))


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    voice = pick_voice()
    with tempfile.TemporaryDirectory() as tmp:
        plan, inputs, filters = [], [], []
        for i, (draw, text, min_s) in enumerate(SCENES):
            clip = os.path.join(tmp, f"n{i}.aiff")
            dur = max(min_s, tts(text, clip, voice) + 1.2)
            plan.append((draw, dur))
            inputs += ["-i", clip]
            # 0.4 s lead-in, then pad/trim each narration clip to exactly its scene length
            filters.append(f"[{i}:a]adelay=400:all=1,apad,atrim=0:{dur:.3f},asetpts=PTS-STARTPTS[a{i}]")
        concat = "".join(f"[a{i}]" for i in range(len(SCENES))) + f"concat=n={len(SCENES)}:v=0:a=1[aout]"
        narration = os.path.join(tmp, "narration.m4a")
        subprocess.run([FF, "-y", "-loglevel", "error"] + inputs +
                       ["-filter_complex", ";".join(filters + [concat]), "-map", "[aout]",
                        "-c:a", "aac", "-b:a", "160k", narration], check=True)

        silent = os.path.join(tmp, "video.mp4")
        proc = subprocess.Popen([FF, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgba",
                                 "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-c:v", "libx264",
                                 "-pix_fmt", "yuv420p", "-crf", "20", silent], stdin=subprocess.PIPE)
        total = 0
        for draw, dur in plan:
            n = int(round(dur * FPS))
            for k in range(n):
                fig = new_fig()
                draw(fig, k / max(n - 1, 1), dur)
                proc.stdin.write(frame_bytes(fig))
            total += n
            print(f"rendered {draw.__name__:9s} {dur:5.1f} s", flush=True)
        proc.stdin.close()
        proc.wait()
        subprocess.run([FF, "-y", "-loglevel", "error", "-i", silent, "-i", narration, "-c:v", "copy",
                        "-c:a", "copy", "-shortest", "-movflags", "+faststart", OUT], check=True)
    print(f"wrote {OUT}: {total / FPS:.1f} s, voice={voice or 'default'}")


if __name__ == "__main__":
    main()
