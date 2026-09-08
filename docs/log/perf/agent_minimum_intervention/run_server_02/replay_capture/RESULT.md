# Tool-call paired streaming replay

## Protocol

| Variant | Requests | Protocol pass | Protocol failure | Semantic deviation | Overall failure | E2E median ms | E2E P95 ms | First output median ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| dtb | 30 | 30 | 0 | 6 | 6 | 12101.859 | 29546.712 | 580.604 |
| vanilla | 30 | 30 | 0 | 6 | 6 | 10732.284 | 21378.668 | 585.322 |

## Paired latency

- All pairs: 30
- Protocol-clean pairs: 30
- Fully clean pairs: 19
- Median DTB minus Vanilla: 943.785 ms
- Median DTB speedup: -8.76%
- DTB faster fully clean pairs: 7/19
- Latency ranking valid: False

Latency ranking is valid only when every latency-eligible pair is free of protocol anomalies; run quality probes separately from ranking.
