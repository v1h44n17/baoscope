# BaoScope — BIO-powered logic analyzer for Dabao

Dabao Hackathon · deadline **Tue Dec 15, 9 pm ET** · directions: **BIO** + **Tooling**

**Pitch:** BIO cores sample Dabao pins at high speed and decode SPI/I²C/UART on-chip.
Raw or decoded data streams over USB HS to the host, where a sigrok/PulseView driver opens it.
A $20 open board becomes a fully inspectable logic analyzer and protocol sniffer, open from silicon to UI.

Judging fit: **Works** (live demo) · **New** (no BIO analyzer exists) · **Open** (all code + RTL) ·
**Uses the silicon** (BIO @ 700 MHz, BDMA, USB HS).

## Board status (verified 2026-11-10)

- Dabao, stepping A0, serial `NG5LP8`; boot1 `v0.10.0-61-g5397e1b48`
- Flashed xous-core `v0.10.2-195-g99bbfcc1` + `dabao-console` (dev-signed); console responds
- Ports: `/dev/cu.usbmodem…81` = boot1 (BAOCHIP drive), `…83` = running Xous
- Usable BIO pins (from `dabao-console/src/cmds/bio.rs`): 1–5, 11, 12, 16–19, 23, 24, 26–29

## ⚠ USB packet duplication found (2026-11-10, BaoScope v1)

Frames larger than one 512-byte HS packet arrive with **whole 512-byte packets repeated**:

| Frame bytes sent | Bytes received | Extra |
|---|---|---|
| ≤ 513 | exact | 0 |
| 1,028 | 1,540 | +512 (2nd packet repeated) |
| 3,840 | 5,376 | +1,536 |
| 8,028 | 11,612 | +3,584 |

`bio-stream`'s 0..255 test pattern repeats every 256 bytes, so a duplicated 512-byte packet
is invisible to it → **the 9.99 MB/s figure below likely counts duplicates and is an upper bound.**
Root cause not yet found (the IPC/`serial_write_irq_safe` layer looks correct; suspect the
Corigine IN-endpoint driver in `libs/bao1x-hal/src/usb/`). Worth an upstream issue + fix.

BaoScope v2 works around it: all device→host data is sent as 512-byte records with seq + CRC32;
the host drops repeats and counts losses. `tools/capture.py --stream N` measures the true rate.

## BaoScope v10 — real-peripheral tests, in-place finding, demo (2026-11-10)

- `decode.py uart|i2c --real`: the chip's own UART2 TX (numbered log lines, 1 Mbaud) and I²C0
  controller (address scan 0x08–0x77, all NACK with nothing attached) drive the decoders through
  jumpers: header 15 (PB14) → 9 (PC7), 27 (PB11) → 14 (PC0), 26 (PB12) → 12 (PC1). Before any
  external decode a core forces all BaoScope pins to inputs (`inputs_code`). **Not yet run — needs
  the 3 jumper wires.** With no wire, real mode decodes 0 bytes (clean negative control).
- **In-place sniffing does not work (hardware finding):** decoding PB14/PB11/PB12 directly, without
  remapping them away from UART2/I²C0, reads the pads as constant 0 (UART: one 0x00 + framing error
  then silence; I²C: zero line changes) while the peripherals were demonstrably active. BIO only
  sees pads mapped to it. `--inplace` is kept for reproducibility.
- `tools/demo.py`: 7 wire-free tests → **7/7 PASS** (`captures/demo-scorecard.md`); `--wired` runs
  the real-peripheral tests instead (self-tests skipped so nothing drives wired pins).
- Throughput note: dedup'd USB rate is now 2.48 MB/s (was 4.32) — the streaming reply path
  builds records byte by byte; fine for captures (~50 ms per 131k samples), easy to optimise.
- Submission write-up: `SUBMISSION.md`; upstream drafts: `docs/upstream-issues.md`.

## BaoScope v8 — on-chip SPI decode (2026-11-10) ✅

`decode.py spi [--sck HZ] [--sample HZ] [--base 16] [--n N] [--selftest]` → firmware
`spi <sample_hz> <sck_hz> <base_bit> <n> <timeout> <selftest>`. Mode 0 (CPOL 0, CPHA 0), MSB first;
pins base..base+3 = SCK, MOSI, MISO, CS (default PC0..PC3). Core 0 polls the 4-bit state and acts
on change: CS↓ → ASSERT, SCK↑ with CS low → shift MOSI and MISO (8 bits → BYTE carrying both
directions), CS↑ → RELEASE with the leftover bit count (partial bytes are reported, not dropped).
Core 2 = SPI master test source that also drives MISO, repeating `[9F/FF k/~k A5/k]`.

| SCK | Decoder poll | 4,096-event self-test |
|---|---|---|
| 250 kHz | 10 / 20 MHz | **PASS** — 819/819 frames exact |
| 1 MHz | 10 / 20 MHz | **PASS** |
| 2 MHz | 10 / 20 MHz | **PASS** |
| 3.5 MHz | 10 / 20 MHz | fail — clock edges missed (frames show `+2..4 bits`) |

**Limit:** identical failure at 10 and 20 MHz polling → not poll rate but per-edge work: the
SCK-rise path is ~20 instructions (~150 BIO cycles) vs ~100 cycles per half-period at 3.5 MHz.
**Fix (same as UART):** in-frame, only capture the raw state per SCK edge; assemble bytes later —
or split MOSI and MISO across two decoder cores.

Regression: I²C 400 kHz, UART 1 Mbaud and split capture all pass. Raw: `captures/spi-sweep.txt`.

## BaoScope v7 — on-chip I²C decode (2026-11-10) ✅

`decode.py i2c [--bus HZ] [--sample HZ] [--scl 16] [--n N] [--selftest]` → firmware
`i2c <sample_hz> <bus_hz> <scl_bit> <n> <timeout> <selftest>` (SDA = SCL bit + 1; default PC0/PC1).
Core 0 = I²C RX state machine: polls the 2-bit line state every quantum and acts only on change —
SCL↑ shifts in SDA (9 bits → BYTE + ACK/NACK), SDA↓ with SCL high → START (incl. repeated START),
SDA↑ with SCL high → STOP. Events `tag<<16 | ack<<8 | data` go through the same writer core to RAM.
Core 2 = I²C master test source (push-pull, same pins) repeating `S A0+ k+ ~k- P`.
UART and I²C now share one runner (`decode_run`), ready for SPI.

| Bus rate | Decoder poll | 4,096-event self-test |
|---|---|---|
| 100 kHz (Standard-mode) | 10 MHz | **PASS** — 819/819 transactions exact |
| 400 kHz (Fast-mode) | 10 MHz | **PASS** |
| 1 MHz (Fast-mode Plus) | 10 MHz | **PASS** |
| 2 MHz (beyond Fm+) | 10 MHz | **PASS** |
| 1 MHz | 20 MHz | **PASS** |

Regression: UART 1 Mbaud and split capture (mode 2 @ 17.5 MS/s) still pass.
Caveat: self-test source is push-pull on the decoder's own pins; a real bus (open-drain, pull-ups,
slow rise times, clock stretching) still needs testing against an external device.
Raw: `captures/i2c-sweep.txt`.

## BaoScope v6 — on-chip UART decode (2026-11-10) ✅

`decode.py uart --baud B [--pin 16] [--n N] [--selftest]` → firmware `uart <baud> <pin> <n> <timeout> <selftest>`.
Core 0 = UART RX (8N1, 8× oversampled, glitch-checked start bit, framing-error flag), core 1 = the
same DMA writer as v5 (one u32 per decoded byte), core 2 = UART TX self-test source on the same pin
(0,1,…,255 repeating — no wiring). Pin number lives in each core's IMEM at 0x400.

| Baud | 4,096-byte self-test | Note |
|---|---|---|
| 115,200 | **PASS** (0 framing errors) | actual 115,131 (integer divider) |
| 460,800 | **PASS** | |
| 1,000,000 | **PASS** | |
| 2,000,000 | **PASS** | |
| 3,000,000 | fail | bytes 0x80,0x81,… — bit 7 read from the stop bit |
| 4,000,000 | fail | bits 4–7 wrong |
| 6,000,000 | fail | 2,180 framing errors |

**Why ≥ 3 Mbaud fails:** ~7 instructions of per-bit work after the 8 quantum waits don't fit in one
29-cycle quantum (8 × 3 MHz), so every bit pushes the next sample point later; by bit 7 it lands in
the stop bit. **Fix:** in-frame, do only `mv <reg>, x21` per bit (8 spare regs exist in x1–x15) and
assemble the byte after the stop bit — expected ≥ 6 Mbaud.

Regression check: v5 split capture (mode 2 @ 17.5 MS/s) still passes. Raw: `captures/uart-sweep.txt`.

## BaoScope v5 — sampler/writer core split (2026-11-10) ✅ current best capture

Three BIO cores: **sampler** (quantum-locked, packs, pushes words into FIFO2) → **writer** (free-running,
pops FIFO2, `sw` page-table walk into RAM, reports cycles on FIFO1) + **generator** (self-test counter).
The 8-deep FIFO absorbs store latency, so the sampler no longer misses quanta.
`capture.py --dma --split --mode M`.

| Mode | Channels | Direct (v4) | **Split (v5)** | Limit |
|---|---|---|---|---|
| 1 | 8 | 5 MS/s | **10 MS/s** | writer: ~56.5 BIO cycles per `sw` (≈ 12.4 M words/s) |
| 2 | 8 | 5 MS/s | **17.5 MS/s** | writer (28.3 cycles/sample = 56.6/word) |
| 8 | 4 | 5 MS/s | **25 MS/s*** | sampler instructions (~21–28 cycles/sample) |

\* 25 MS/s mode 8: all 16,383 interior counter steps exact; the only deviation is the first 8 samples
(runs of 1 then 7 — the sampler and generator cores aligning at start). Clean 17.5 MS/s with no caveat.

**Progress so far: 250 kS/s (CPU drains FIFO) → 5 MS/s (BIO DMA) → 17.5 MS/s on 8 ch / 25 MS/s on 4 ch
(split pipeline) — 70–100× in one day.**

Ideas for more: a second writer core (writer-bound modes 1/2), interleave two sampler cores
with a half-quantum phase offset (mode 8), trim the startup glitch by discarding the first word.

Raw: `captures/split-sweep.txt`, `captures/split-m<mode>-<rate>.vcd`.

## BaoScope v4 — packed DMA samplers (2026-11-10)

`dcap <rate> <n> <gen> <mode>` / `capture.py --dma --mode {1,2,8}`. Replies now stream 3.5 KiB at a
time (building full-size copies of a 131k-sample reply ran the app out of memory and killed it).

| Mode | Packing | Channels | Clean (self-test PASS) | Depth (256 KiB buffer) |
|---|---|---|---|---|
| 1 | 1 GPIO word / write | 8 | ≤ 5 MS/s | 65,536 |
| 2 | 2 × GPIO[31:16] / write | 8 | ≤ 5 MS/s (10 MS/s: 155 bad runs of 16,404) | 131,072 |
| 8 | 8 × GPIO[19:16] / write (shift register) | 4 | ≤ 5 MS/s | 524,288 in RAM (reply capped at 131,072) |

**Why packing alone didn't raise the clean rate:** at 10 MS/s mode 8 shows exactly one bad run per
32-bit word and 78.8 cycles/sample = 70 × 9/8. The blocking `sw` after each word outlasts the loop
slack, so the next `mv x20, x0` arrives after the quantum pulse and **waits a whole extra quantum**
(missed quanta are not latched). Instruction cost alone is ~35 BIO cycles/sample in mode 8.

**Next fix:** split the store off — sampler core packs and pushes words into a FIFO, a second "writer"
core pops and does the `sw` page-table walk. The 8-deep FIFO hides store latency. Expected ceiling
≈ 20 MS/s (mode 8, instruction-bound); then interleave two sampler cores for more.

**Lessons (BIO gotchas):**
- Only x1–x15 are plain registers. x16–x31 are hardware; `t3`–`t6`, `a6`, `a7`, `s2`–`s11` alias
  them. Using `t3`/`t4` (x28/x29, write-only event set/clear) as scratch read back 0 → mode 2 had
  every other sample zero, mode 8 was flat. `erratum_check.py` doesn't catch this — worth adding.
- A self-test must demand activity: the first mode-8 "PASS" had 0 transitions. Fixed
  (`expected_transitions`).

Raw: `captures/pack-sweep.txt`, `captures/dma-m<mode>-<rate>.vcd`.

## BaoScope v3 — BIO DMA capture (2026-11-10)

BIO sampler core stores each GPIO word straight into RAM (`dcap` / `capture.py --dma`). The CPU only
sleeps until the core pushes "done + cycle count" into FIFO1. Xous gives non-contiguous 4 KiB pages,
so the firmware resolves each page's physical address (`virt_to_phys`, after touching the page —
`RESERVE` alone left page 0 unmapped → `BadAddress`) and writes the page list into the sampler core's
own IMEM at 0x400; the core walks it. DMA window 0 = all SRAM (to tighten later).

| Rate | Self-test | BIO cycles/sample (700 MHz) |
|---|---|---|
| 1 MS/s | **PASS** | 700.0 |
| 2 MS/s | **PASS** | 350.0 |
| **5 MS/s** | **PASS** (8,190/8,190 steps exact) | 140.0 |
| 10 MS/s | fail — 74/8,200 runs one sample short | 70.1 |
| 17.5–35 MS/s | fail — loop-bound at ~70–80 cycles/sample (≈ 8.75 MS/s) | 68–84 |

**20× over the FIFO path** (250 kS/s → 5 MS/s clean). The limit is now the sample loop itself,
dominated by one blocking `sw` per sample. Next: pack 4 samples (8 bits each) per word in the BIO
core → ¼ the bus writes and 4× the capture depth (262k samples in 256 KiB); then try two
interleaved sampler cores.

Upstream nits found on the way: `load_code` ragged-word shift in `libs/bao1x-hal/src/bio_hw.rs`
uses `<< (4 - len + j)` (bits, not bytes); `port_and_pin_to_bio_bit` maps PB as `15 - pin` while
`bio_bit_to_port_and_pin` is identity — unverified, worth checking.

Raw: `captures/dma-sweep.txt`, `captures/dma-<rate>.vcd`.

## BaoScope v0/v2 results (2026-11-10)

**First BIO capture works.** Sampler core (2 instructions: wait quantum → GPIO to FIFO1) + generator core
(4-bit counter on PC0–PC3) + CPU drain into RAM, 65,536 samples, self-test checks every counter step.

| Sample rate | Self-test | Detail |
|---|---|---|
| 250 kS/s | **PASS** | 8,191/8,191 steps exactly 8 samples |
| 500 kS/s | fail (99.9%) | 7 short runs — CPU preempted, FIFO (8 deep) overflowed, sampler missed quanta |
| 1 MS/s | fail (99.9%) | 8 short runs, 1 skipped value |
| 2 MS/s | fail (99.9%) | 4 short runs, 2 skipped values |
| 5 MS/s | fail | CPU drain loop tops out ≈ 2.5 MS/s |

**Real USB throughput (dedup'd, `capture.py --stream 3000`): 4.32 MB/s unique payload**,
~5.9 MB/s on the wire; **35% of records were duplicates**, 0 lost. v2 record protocol handled it.

**Conclusion:** the CPU-in-the-loop design is the bottleneck. Next: BDMA so the BIO writes samples
straight to RAM (no CPU, no FIFO overflow), then pack samples in the BIO core.

Raw outputs: `captures/selftest-1MSps.{txt,vcd}`, `captures/rate-sweep.txt`, `captures/stream-test.txt`.

## USB throughput (measured 2026-11-10, ⚠ superseded — counted duplicates)

`bio-stream` firmware → `tools/usb_meter.py`, 15 s run, Mac host, USB-C direct:

| Metric | Result |
|---|---|
| Sustained host-side rate | **9.99 MB/s (≈ 80 Mbit/s)**, flat ±0.5% every second |
| Data integrity | 149,817,335 of 149,817,336 bytes in sequence — **zero gaps** (the 1 "break" is the first byte) |
| Meter bottleneck? | No — a count-only reader gets the same 9.99 MB/s |

**What this means for BaoScope**

- Live streaming: 8 ch × 1 byte/sample → **~10 MS/s**; 4 ch packed 2/byte → ~20 MS/s;
  run-length encoding on a BIO core → much higher for typical bursty bus traffic.
- Faster than 10 MS/s: buffered capture into SRAM (≈1.5 MiB usable → ~150 ms at 10 MS/s, 15 ms at 100 MS/s), then upload.
- Probable ceiling cause (unconfirmed): `services/usb-bao1x/src/hw.rs` uses a 2-packet (1 KiB) TX buffer
  and each 3840-byte page is one IPC round trip. USB 2.0 HS bulk allows ~40 MB/s, so a larger TX
  buffer could be both a BaoScope speed-up and an upstream contribution.

## Building blocks already in xous-core

| Need | Existing code |
|---|---|
| USB serial throughput test | `apps-dabao/bio-stream` |
| BIO app pattern, resource claims | `libs/bio-lib` (ws2812, captouch, qspi_to_8080) |
| BIO → memory DMA | `libs/xous-bio-bdma` |
| BIO API / resources traits | `libs/bao1x-api/src/bio.rs`, `bio_resources.rs` |
| Upload BIO code over serial | `dabao-console` `bio` command |

## Milestones

| Week | Dates | Goal | Done when |
|---|---|---|---|
| 1 | Nov 10–16 | **De-risk throughput.** Flash `bio-stream` and measure real host-side B/s. Write a BIO core loop that samples 8 pins into the FIFO and a host reader. | Known MB/s ceiling, first captured square wave |
| 2 | Nov 17–23 | **Capture engine.** BDMA ring buffer, configurable sample rate (quantum divider), trigger on pin edge, buffered mode for bursts above the USB rate | 8 ch captured at ≥ 25 MS/s buffered, streamed at the USB ceiling |
| 3 | Nov 24–30 | **On-chip decode.** Second BIO core decodes UART/SPI/I²C in real time and sends only frames | Sniff the I²C bus of a real sensor live |
| 4 | Dec 1–7 | **Host side.** sigrok/libsigrok driver (or a PulseView-compatible `.sr` exporter as a fallback), plus a Python CLI | Capture opens in PulseView |
| 5 | Dec 8–15 | **Polish & submit.** Docs, demo video, benchmark table, BIO-sim test bench | Submitted by Dec 14 (a day early) |

**Stretch:** sniff a hardware wallet's SPI/I²C bus (fits Devcon); glitch-sensor event log overlay.

## Tools in this folder

- `tools/console.py` — send scripted commands to the board console (`.venv/bin/python tools/console.py --port PORT cmd …`)
- Interactive: `.venv/bin/python xous-core/tools-bao/bao.py monitor -p PORT`
- Flashing on macOS: use `cp -X file.uf2 /Volumes/BAOCHIP/ && sync` (plain `cp` fails on extended attributes)

## Flashing procedure (what actually works on this board)

1. Enter boot1: **hold PROG, tap RESET, release PROG** (RESET alone boots straight back into Xous).
2. `BAOCHIP` drive + `/dev/cu.usbmodem…81` appear.
3. `cp -X` loader.uf2, xous.uf2, apps.uf2 to `/Volumes/BAOCHIP/`, `sync`.
4. `tools/console.py --port /dev/cu.usbmodem…81 boot` → Xous comes up on `…83` within ~1 s.

## Next action

Board runs **baoscope v8** (`apps-dabao/baoscope`).
Capture: `tools/capture.py --dma --split --mode {1,2,8} --rate HZ --n N --vcd out.vcd`.
Decode: `tools/decode.py {uart --baud B | i2c --bus HZ | spi --sck HZ} --selftest`.
Weeks 1–3 goals met: capture ≤ 17.5 MS/s (8 ch) / 25 MS/s (4 ch); on-chip UART ≤ 2 Mbaud,
I²C ≤ 2 MHz, SPI ≤ 2 MHz; `tools/demo.py` 7/7 PASS. Submission: `SUBMISSION.md`.
Remaining before Dec 15: buy jumper wires and run `demo.py --wired`; publish both repos;
record a demo video; file the drafts in `docs/upstream-issues.md`. Optional: faster reply path,
UART/SPI raw-edge decoders for >2 MHz, narrow DMA window, sigrok driver.
