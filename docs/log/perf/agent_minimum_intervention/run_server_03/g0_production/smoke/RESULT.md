# Tool-call paired streaming replay

## Protocol

| Variant | Requests | Protocol pass | Protocol failure | Semantic deviation | Overall failure | E2E median ms | E2E P95 ms | First output median ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| dtb | 1 | 1 | 0 | 1 | 1 | 2599.624 | 2599.624 | 839.95 |
| vanilla | 1 | 1 | 0 | 1 | 1 | 17197.348 | 17197.348 | 9656.128 |

## Paired latency

- All pairs: 1
- Protocol-clean pairs: 0
- Fully clean pairs: 0
- Median DTB minus Vanilla: 0.0 ms
- Median DTB speedup: 0.0%
- DTB faster fully clean pairs: 0/0
- Latency ranking valid: False

Latency ranking is valid only when every latency-eligible pair is free of protocol anomalies; run quality probes separately from ranking.
