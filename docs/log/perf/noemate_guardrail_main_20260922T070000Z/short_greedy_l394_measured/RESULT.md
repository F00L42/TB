# Tool-call paired streaming replay

## Protocol

| Variant | Requests | Protocol pass | Protocol failure | Semantic deviation | Overall failure | E2E median ms | E2E P95 ms | First output median ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| dtb | 20 | 20 | 0 | 0 | 0 | 9120.407 | 13854.974 | 460.858 |
| vanilla | 20 | 20 | 0 | 0 | 0 | 9061.149 | 14285.914 | 460.948 |

## Paired latency

- All pairs: 20
- Protocol-clean pairs: 20
- Fully clean pairs: 20
- Median DTB minus Vanilla: -201.108 ms
- Median DTB speedup: 2.101%
- DTB faster fully clean pairs: 11/20
- Latency ranking valid: True

Latency ranking is valid only when every latency-eligible pair is free of protocol anomalies; run quality probes separately from ranking.
