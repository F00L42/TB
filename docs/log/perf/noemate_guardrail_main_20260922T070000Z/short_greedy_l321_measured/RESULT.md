# Tool-call paired streaming replay

## Protocol

| Variant | Requests | Protocol pass | Protocol failure | Semantic deviation | Overall failure | E2E median ms | E2E P95 ms | First output median ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| dtb | 20 | 20 | 0 | 0 | 0 | 6566.727 | 8679.948 | 438.161 |
| vanilla | 20 | 20 | 0 | 0 | 0 | 7475.832 | 13263.485 | 437.81 |

## Paired latency

- All pairs: 20
- Protocol-clean pairs: 20
- Fully clean pairs: 20
- Median DTB minus Vanilla: -220.135 ms
- Median DTB speedup: 2.835%
- DTB faster fully clean pairs: 11/20
- Latency ranking valid: True

Latency ranking is valid only when every latency-eligible pair is free of protocol anomalies; run quality probes separately from ranking.
