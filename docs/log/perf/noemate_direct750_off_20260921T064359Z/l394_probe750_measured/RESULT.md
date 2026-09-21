# Tool-call paired streaming replay

## Protocol

| Variant | Requests | Protocol pass | Protocol failure | Semantic deviation | Overall failure | E2E median ms | E2E P95 ms | First output median ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| dtb | 4 | 4 | 0 | 0 | 0 | 10592.0 | 14069.606 | 538.362 |
| vanilla | 4 | 4 | 0 | 0 | 0 | 14311.842 | 15171.917 | 543.639 |

## Paired latency

- All pairs: 4
- Protocol-clean pairs: 4
- Fully clean pairs: 4
- Median DTB minus Vanilla: -4311.865 ms
- Median DTB speedup: 28.834%
- DTB faster fully clean pairs: 3/4
- Latency ranking valid: True

Latency ranking is valid only when every latency-eligible pair is free of protocol anomalies; run quality probes separately from ranking.
