# BaoScope — a logic analyzer and protocol decoder built from the Baochip's BIO cores

**Dabao Hackathon · Devcon 8 · directions: BIO / I/O engine + Tooling**

BaoScope turns a $20 Dabao into an 8-channel logic analyzer that captures at **17.5 MS/s**
(4 channels at 25 MS/s) and decodes **UART, I²C and SPI on the chip itself** — using the four
PicoRV32 BIO cores as a sampling and decoding pipeline, with the main CPU asleep. Every mode ships
with a built-in self-test that uses a spare BIO core as the signal source, so the whole thing is
verifiable on a bare board with **no wiring**: `python tools/demo.py` → **7/7 PASS**.

- **Repo:** https://github.com/v1h44n17/baoscope
- **Firmware:** https://github.com/v1h44n17/xous-core/tree/baoscope (`apps-dabao/baoscope`)
- **Video (2:13, narrated):** https://www.youtube.com/watch?v=88yj7p8dNso
  (copy in the repo: https://github.com/v1h44n17/baoscope/blob/main/media/baoscope-demo.mp4) —
  includes a live run of `tools/demo.py` on the Dabao replayed in real time (7/7 PASS in 9.4 s), plus a
  live 17.5 MS/s capture and live decoder output, all recorded from the board on 2026-11-14.

## What it does

| Capability | Result (measured on hardware) |
|---|---|
| Capture, 8 channels | **17.5 MS/s** clean, 131,072 samples per run |
| Capture, 4 channels | **17.5 MS/s** clean; **25 MS/s** with only the first 8 samples off |
| On-chip UART decode (8N1) | **115,200 baud – 2 Mbaud**, 4,096/4,096 bytes, 0 framing errors |
| On-chip I²C decode | **100 kHz – 2 MHz** bus, START/STOP/repeated START, ACK/NACK, 819/819 transactions |
| On-chip SPI decode (mode 0) | **250 kHz – 2 MHz** SCK, MOSI + MISO, CS framing, 819/819 frames |
| Host link | Seq + CRC32 record protocol; survives the USB packet duplication we found (0 lost) |
| Output | `.vcd` for PulseView / GTKWave; decoded transactions printed (`S a0+ 05+ fa- P`) |

Latest scorecard: [`captures/demo-scorecard.md`](captures/demo-scorecard.md).
Every number above comes from a logged run in [`captures/`](captures/); the design history with
all intermediate results is in [`PLAN.md`](PLAN.md).

## How it uses the silicon

```
            GPIO pads (PC0-PC3, PC7, PC8, PC10, PC11)
                 │ x21
   ┌─────────────▼─────────────┐  FIFO2  ┌──────────────────────┐   sw    ┌──────────┐
   │ BIO core 0: sampler or     │ ──────▶ │ BIO core 1: writer   │ ──────▶ │ SRAM     │
   │ protocol decoder           │ (8 deep)│ walks a page table   │  BDMA   │ 256 KiB  │
   │ quantum-locked (x20)       │         │ held in its own IMEM │         └────┬─────┘
   └────────────────────────────┘         └──────────┬───────────┘              │
   ┌────────────────────────────┐                    │ FIFO1: done + cycles     │ cache flush
   │ BIO core 2: self-test      │                    ▼                          ▼
   │ source (counter / UART /   │           VexRiscv + Xous app ───USB HS───▶ host tools
   │ I²C master / SPI master)   │           (sleeps during capture)
   └────────────────────────────┘
```

- **Quantum-locked sampling.** The sampler core halts on `x20` until its clock-divider quantum, then
  reads all GPIO at once from `x21` — deterministic sample timing, measured exactly
  700 MHz ÷ rate cycles per sample.
- **Packing in the BIO.** Mode 2 stores two 16-bit samples per word; mode 8 runs a 4-bit shift
  register (5 instructions/sample) and stores 8 samples per word — 4× capture depth.
- **Split pipeline.** The blocking BDMA store was the bottleneck (a missed quantum is not latched,
  so a late store silently costs a whole sample). Moving the store to a second core behind the
  8-deep FIFO raised the clean rate from 5 to 17.5 MS/s.
- **Physical page table in IMEM.** Xous hands the app non-contiguous 4 KiB pages; the firmware
  resolves each page's physical address (`virt_to_phys`) and writes the list into the writer core's
  own instruction memory, which the core walks with `lw`. The CPU is not in the data path at all.
- **On-chip decoders** are small state machines that act only on line-state changes, sharing the
  same writer core, so decoded data streams to RAM without CPU involvement.

## Why it's new

- The BIO is new silicon; to our knowledge this is the first BIO-based logic analyzer and the
  first BIO protocol decoders for I²C and SPI.
- Self-verifying by construction: a spare core generates the test signal on the same pads, so
  judges can reproduce every claim with a USB cable and one command.

## How open it is

- All firmware (`apps-dabao/baoscope`, on a branch of xous-core) and host tools are source, with
  every measurement and every failed approach logged in `PLAN.md`.
- Findings filed upstream as betrusted-io/xous-core
  [#1017](https://github.com/betrusted-io/xous-core/issues/1017),
  [#1018](https://github.com/betrusted-io/xous-core/issues/1018),
  [#1019](https://github.com/betrusted-io/xous-core/issues/1019),
  [#1020](https://github.com/betrusted-io/xous-core/issues/1020)
  (details in [`docs/upstream-issues.md`](docs/upstream-issues.md)):
  1. **USB CDC repeats whole 512-byte packets** (26–35 % of records duplicated under load) —
     invisible to the existing `bio-stream` test because its pattern repeats every 256 bytes.
  2. `port_and_pin_to_bio_bit` maps port B as `15 - pin` (pinout says PB14 = BIO14).
  3. `load_code` ragged-word shift uses bits instead of bytes.
  4. `erratum_check.py` could flag `t3`–`t6`/`a6`/`a7`/`s2`–`s11`, which alias BIO hardware registers.
- **Hardware finding:** the BIO reads pads it does not own as 0. We tried decoding the chip's own
  UART2 TX (PB14) and I²C0 (PB11/PB12) in place without remapping them; both read flat. Sniffing an
  on-chip peripheral therefore needs a jumper to a BIO-owned pad (supported: `demo.py --wired`).

## Run it

Code: **https://github.com/v1h44n17/baoscope** (host tools, docs, results) and firmware on
**https://github.com/v1h44n17/xous-core/tree/baoscope** (`apps-dabao/baoscope`).

```bash
git clone https://github.com/v1h44n17/baoscope.git && cd baoscope
git clone -b baoscope https://github.com/v1h44n17/xous-core.git
cd xous-core && cargo xtask dabao baoscope && cd ..
# hold PROG, tap RESET, release → copy loader.uf2 xous.uf2 apps.uf2 to BAOCHIP with `cp -X`
python tools/console.py --port /dev/cu.usbmodem…1 boot
python tools/demo.py                       # 7 wire-free tests, prints the scorecard
python tools/capture.py --dma --split --mode 2 --rate 17500000 --n 131072 --vcd out.vcd
python tools/decode.py uart --baud 115200 --pin 23     # decode a real UART on PC7
python tools/decode.py i2c --scl 16                    # sniff a real I2C bus on PC0/PC1
python tools/decode.py spi --base 16                   # sniff SPI on PC0..PC3
```

## Limits and honest caveats

- **No external device has been tested yet** — every decode result uses the on-chip test source,
  which drives push-pull on the decoder's own pins. A real bus adds open-drain edges, pull-ups and
  clock stretching. The `--real` / `demo.py --wired` path is implemented (chip's own UART2 and
  I²C0 via 3 jumpers) but not yet run.
- Decoder ceilings come from per-bit instruction cost: UART fails at 3 Mbaud, SPI at 3.5 MHz.
  The fix is known (record raw state per edge, assemble after the frame).
- USB unique throughput is 2.5–4.3 MB/s, limited by the duplication bug and byte-wise record
  assembly; captures upload in ~50 ms, so this doesn't limit capture rate.
- The DMA window is opened to all of SRAM; it should be narrowed to the capture pages.
- Development images are developer-signed, which erases the chip's factory secrets on first boot.

## Build log

One day of hardware iteration, each step measured:
250 kS/s (CPU drains FIFO) → 5 MS/s (BIO DMA) → packed modes → **17.5 / 25 MS/s** (split pipeline)
→ UART → I²C → SPI on-chip decode → 7/7 wire-free demo.
