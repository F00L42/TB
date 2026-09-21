# Tool-call paired streaming replay

## Protocol

| Variant | Requests | Protocol pass | Protocol failure | Semantic deviation | Overall failure | E2E median ms | E2E P95 ms | First output median ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| dtb | 4 | 4 | 0 | 2 | 2 | 8619.436 | 15262.639 | 563.401 |
| vanilla | 4 | 3 | 1 | 1 | 2 | 12917.739 | 16679.607 | 823.739 |

## Paired latency

- All pairs: 4
- Protocol-clean pairs: 0
- Fully clean pairs: 0
- Median DTB minus Vanilla: 0.0 ms
- Median DTB speedup: 0.0%
- DTB faster fully clean pairs: 0/0
- Latency ranking valid: False

Latency ranking is valid only when every latency-eligible pair is free of protocol anomalies; run quality probes separately from ranking.
