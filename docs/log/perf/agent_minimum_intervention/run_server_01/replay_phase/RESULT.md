# Tool-call paired streaming replay

## Protocol

| Variant | Requests | Protocol pass | Protocol failure | Semantic deviation | Overall failure | E2E median ms | E2E P95 ms | First output median ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| dtb | 32 | 31 | 1 | 3 | 4 | 10581.087 | 32628.404 | 548.526 |
| vanilla | 32 | 32 | 0 | 3 | 3 | 11280.155 | 29598.794 | 580.311 |

## Paired latency

- All pairs: 32
- Protocol-clean pairs: 19
- Fully clean pairs: 16
- Median DTB minus Vanilla: 465.27 ms
- Median DTB speedup: -7.136%
- DTB faster fully clean pairs: 7/16
- Latency ranking valid: False

Latency ranking is valid only when every latency-eligible pair is free of protocol anomalies; run quality probes separately from ranking.
