# Tool-call paired streaming replay

## Protocol

| Variant | Requests | Protocol pass | Protocol failure | Semantic deviation | Overall failure | E2E median ms | E2E P95 ms | First output median ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| dtb | 1 | 1 | 0 | 0 | 0 | 12211.519 | 12211.519 | 558.831 |
| vanilla | 1 | 1 | 0 | 0 | 0 | 18519.671 | 18519.671 | 3592.568 |

## Paired latency

- All pairs: 1
- Protocol-clean pairs: 1
- Fully clean pairs: 1
- Median DTB minus Vanilla: -6308.152 ms
- Median DTB speedup: 34.062%
- DTB faster fully clean pairs: 1/1
- Latency ranking valid: True

Latency ranking is valid only when every latency-eligible pair is free of protocol anomalies; run quality probes separately from ranking.
