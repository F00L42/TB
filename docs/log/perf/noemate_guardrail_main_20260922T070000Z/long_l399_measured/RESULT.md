# Tool-call paired streaming replay

## Protocol

| Variant | Requests | Protocol pass | Protocol failure | Semantic deviation | Overall failure | E2E median ms | E2E P95 ms | First output median ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| dtb | 10 | 10 | 0 | 0 | 0 | 11963.411 | 13637.01 | 556.359 |
| vanilla | 10 | 10 | 0 | 0 | 0 | 15111.148 | 16646.585 | 556.651 |

## Paired latency

- All pairs: 10
- Protocol-clean pairs: 10
- Fully clean pairs: 10
- Median DTB minus Vanilla: -2394.986 ms
- Median DTB speedup: 16.052%
- DTB faster fully clean pairs: 10/10
- Latency ranking valid: True

Latency ranking is valid only when every latency-eligible pair is free of protocol anomalies; run quality probes separately from ranking.
