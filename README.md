# BaoScope

A logic analyzer and protocol decoder for the **Dabao** board (Baochip-1x), built on the chip's
BIO cores. Entry for the Dabao Hackathon (BIO + Tooling directions).

- **Capture:** 8 channels clean at 17.5 MS/s, 4 channels at 25 MS/s, up to 131,072 samples per run.
  BIO cores sample, pack and DMA straight to RAM; the CPU is not in the sample loop.
- **On-chip UART decode:** 8N1, verified from 115,200 baud to 2 Mbaud.
- **On-chip I²C decode:** START/STOP/repeated START, bytes with ACK/NACK, verified 100 kHz – 2 MHz.
- **On-chip SPI decode:** mode 0, MOSI and MISO together, CS framing, verified 250 kHz – 2 MHz SCK.
- **Self-testing:** a spare BIO core generates a known signal on the same pins, so every mode is
  checked end to end without wiring.

**Judges: start with [SUBMISSION.md](SUBMISSION.md)**, then run `python tools/demo.py` (7 wire-free
tests, last run 7/7 PASS — [scorecard](captures/demo-scorecard.md)).
Full design notes, measurements and lessons learned: [PLAN.md](PLAN.md).
Upstream bug drafts: [docs/upstream-issues.md](docs/upstream-issues.md).

## Layout

| Path | What |
|---|---|
| `xous-core/` (separate repo, branch `baoscope`) | Firmware: `apps-dabao/baoscope/src/main.rs` |
| `tools/capture.py` | Host: request captures, self-test, export `.vcd` (PulseView / GTKWave) |
| `tools/demo.py` | One-command demo: every test, markdown scorecard (`--wired` for real peripherals) |
| `tools/decode.py` | Host: on-chip UART, I²C and SPI decode, self-tests, `--real` peripheral tests |
| `tools/usb_meter.py`, `tools/console.py` | USB throughput meter, bootloader/console helper |
| `captures/` | Measurement logs from every sweep, plus a few representative waveforms |

## Reproduce

```bash
git clone https://github.com/betrusted-io/xous-core.git   # then check out the baoscope branch
cd xous-core && cargo xtask dabao baoscope
```

Flash: hold **PROG**, tap **RESET**, release PROG → copy `loader.uf2`, `xous.uf2`, `apps.uf2` from
`target/riscv32imac-unknown-xous-elf/release/` to the `BAOCHIP` drive with `cp -X`, then
`python tools/console.py --port /dev/cu.usbmodem…1 boot`.

```bash
python -m venv .venv && .venv/bin/python -m pip install -r xous-core/tools-bao/requirements.txt
.venv/bin/python tools/capture.py --dma --split --mode 2 --rate 17500000 --n 131072 --vcd out.vcd
cd tools && ../.venv/bin/python decode.py uart --baud 1000000 --selftest
```

The `baoscope` branch is based on upstream xous-core `99bbfcc1`.
