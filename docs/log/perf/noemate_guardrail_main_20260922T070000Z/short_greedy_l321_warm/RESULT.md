# Tool-call paired streaming replay

## Protocol

| Variant | Requests | Protocol pass | Protocol failure | Semantic deviation | Overall failure | E2E median ms | E2E P95 ms | First output median ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| dtb | 1 | 1 | 0 | 0 | 0 | 8384.226 | 8384.226 | 438.768 |
| vanilla | 1 | 1 | 0 | 0 | 0 | 8000.282 | 8000.282 | 2719.878 |

## Paired latency

- All pairs: 1
- Protocol-clean pairs: 1
- Fully clean pairs: 1
- Median DTB minus Vanilla: 383.944 ms
- Median DTB speedup: -4.799%
- DTB faster fully clean pairs: 0/1
- Latency ranking valid: True

Latency ranking is valid only when every latency-eligible pair is free of protocol anomalies; run quality probes separately from ranking.
