# Tool-call paired streaming replay

## Protocol

| Variant | Requests | Protocol pass | Protocol failure | Semantic deviation | Overall failure | E2E median ms | E2E P95 ms | First output median ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| dtb | 52 | 52 | 0 | 0 | 0 | 20181.535 | 68837.175 | 421.363 |
| vanilla | 52 | 52 | 0 | 0 | 0 | 17803.651 | 28350.764 | 540.838 |

## Paired latency

- All pairs: 52
- Protocol-clean pairs: 52
- Fully clean pairs: 52
- Median DTB minus Vanilla: 1244.232 ms
- Median DTB speedup: -6.85%
- DTB faster fully clean pairs: 23/52
- Latency ranking valid: True

Latency ranking is valid only when every latency-eligible pair is free of protocol anomalies; run quality probes separately from ranking.
