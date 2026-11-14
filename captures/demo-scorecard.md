# BaoScope demo scorecard — 7/7 PASS

Run 2026-11-14 16:05 on a Dabao (Baochip-1x) with the baoscope firmware; no external wiring (BIO self-test sources).

| Group | Test | Setting | Result | Detail |
|---|---|---|---|---|
| Link | USB throughput (dedup'd) | 3000 x 7 records | **PASS** | 2.472 MB/s unique, 7612 dup, 0 lost |
| Capture | Split pipeline, mode 1 | 8 ch @ 10 MS/s | **PASS** | 8191/8191 counter steps |
| Capture | Split pipeline, mode 2 | 8 ch @ 17.5 MS/s | **PASS** | 16383/16383 counter steps |
| Capture | Split pipeline, mode 8 | 4 ch @ 17.5 MS/s | **PASS** | 16383/16383 counter steps |
| Decode | UART self-test | 2 Mbaud, 4096 bytes | **PASS** | 4096 bytes, 0 errors |
| Decode | I2C self-test | 1 MHz bus, 4096 events | **PASS** | 819 transactions, 0 bad |
| Decode | SPI self-test | 2 MHz SCK, 4096 events | **PASS** | 819 frames, 0 bad |
