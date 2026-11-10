# Upstream issues (betrusted-io/xous-core)

Found while building BaoScope. Filed 2026-11-10 against base revision `99bbfcc1`:

| # | Issue |
|---|---|
| 1 | [#1017 usb-bao1x (Dabao): CDC serial transmit repeats whole 512-byte packets](https://github.com/betrusted-io/xous-core/issues/1017) |
| 2 | [#1018 bio: port_and_pin_to_bio_bit maps port B backwards](https://github.com/betrusted-io/xous-core/issues/1018) |
| 3 | [#1019 bio_hw::load_code: ragged last-word shift uses bits instead of bytes](https://github.com/betrusted-io/xous-core/issues/1019) |
| 4 | [#1020 erratum_check.py: warn on ABI names that alias BIO hardware registers](https://github.com/betrusted-io/xous-core/issues/1020) |
| 5 | Docs note below — not filed (minor) |

The original drafts follow.

---

## 1. USB CDC serial repeats whole 512-byte packets on transmit (bao1x / Dabao)

**Summary.** Data sent with `UsbHid::serial_send()` arrives at the host with complete 512-byte HS
packets duplicated once a send exceeds one packet. Single-packet sends are exact.

**Repro.** Any app sending a known frame of N bytes via `serial_send` (looping on the returned
count), host reading `/dev/cu.usbmodem*` on macOS:

| Bytes sent | Bytes received | Extra |
|---|---|---|
| ≤ 513 | exact | 0 |
| 1,028 | 1,540 | +512 (2nd packet repeated) |
| 3,840 | 5,376 | +1,536 |
| 8,028 | 11,612 | +3,584 |

Sustained streaming (7 × 512-byte records per `serial_send`, each with seq + CRC32): **26–35 % of
records on the wire were duplicates** across runs, 0 lost; unique payload 2.5–4.3 MB/s.

**Why it hides.** `apps-dabao/bio-stream`'s test pattern repeats every 256 bytes, so a duplicated
512-byte packet continues the sequence seamlessly — its throughput figure counts duplicates.

**Where we looked.** `services/usb-bao1x/src/main.rs` (`SerialSendDataBlocking`) and
`hw.rs::serial_write_irq_safe` look correct (only a contiguous accepted prefix is reported), so the
suspect is the Corigine IN-endpoint path in `libs/bao1x-hal/src/usb/`, e.g. a packet re-armed after a
completion that was already counted. Workaround used in BaoScope: seq + CRC per 512-byte record,
host drops repeats.

---

## 2. `port_and_pin_to_bio_bit` maps port B backwards

`libs/bao1x-api/src/bio.rs`:

```rust
IoxPort::PB => { ... let bio_bit = 15 - pin; ... }
```

but `bio_bit_to_port_and_pin` maps BIO bit n ↔ PB(n) (identity), and the Dabao pinout
(`baochip/dabao` `docs/pinout/pins.csv`) agrees: **PB14 = BIO14, PB13 = BIO13, PB11 = BIO11, …**
So the two functions are not inverses for port B; `port_and_pin_to_bio_bit(PB, 14)` returns 1.
Suggested fix: `let bio_bit = pin;` (and a round-trip unit test over all 32 bits).

---

## 3. `load_code` ragged-word shift uses bits instead of bytes

`libs/bao1x-hal/src/bio_hw.rs` (lines ~258 and ~285):

```rust
ragged_word |= (b as u32) << (4 - chunk.len() + j);
```

The shift is in bits, so a trailing 1–3 byte chunk of BIO code is packed into the low bits of the
word instead of byte lanes. Expected `<< (8 * j)` (little-endian, matching the full-word path's
`u32::from_le_bytes`). Latent today because `bio_code!` output is normally word-sized.

---

## 4. `erratum_check.py` could also flag BIO special registers used as scratch

On BIO, x16–x31 are hardware (FIFOs, quantum, GPIO, events, core id), so the ABI names
`t3`–`t6`, `a6`, `a7`, `s2`–`s11` alias hardware registers. Using `t3`/`t4` (x28/x29, write-only
event set/clear) as scratch silently reads back 0 and also sets/clears event bits. It cost us a
debugging round; `libs/bio-lib/erratum_check.py` already parses every `bio_code!` block, so a check
for these register names would catch it. A one-line regex suffices:
`"[^"]*\b(t3|t4|t5|t6|a6|a7|s[2-9]|s1[01])\b[^"]*"`.

---

## 5. (Docs) Developer images wipe factory secrets — worth stating on dabao-intro

Already in `README-baochip.md`, but new hackathon users flashing `cargo xtask dabao …` images may not
realise the first developer-signed boot permanently erases initial secrets and increments the
developer counter. A one-line note on the getting-started page would help.
