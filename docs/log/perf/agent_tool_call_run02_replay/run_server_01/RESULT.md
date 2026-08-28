# Tool-call paired streaming replay

## Protocol

| Variant | Requests | Pass | Failure | E2E median ms | E2E P95 ms | First output median ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| dtb | 40 | 40 | 0 | 12201.095 | 47507.573 | 645.18 |
| vanilla | 40 | 40 | 0 | 19780.855 | 46737.517 | 540.394 |

## Paired latency

- All pairs: 40
- Protocol-clean pairs: 40
- Median DTB minus Vanilla: 567.793 ms
- Median DTB speedup: -1.772%
- DTB faster clean pairs: 17/40
- Latency ranking valid: True

Latency ranking is valid only when every pair is free of protocol anomalies.
