# Tool-call paired streaming replay

## Protocol

| Variant | Requests | Protocol pass | Protocol failure | Semantic deviation | Overall failure | E2E median ms | E2E P95 ms | First output median ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| dtb | 4 | 4 | 0 | 0 | 0 | 6187.002 | 6670.917 | 620.572 |
| vanilla | 4 | 4 | 0 | 1 | 1 | 11413.1 | 18746.18 | 622.617 |

## Paired latency

- All pairs: 4
- Protocol-clean pairs: 4
- Fully clean pairs: 3
- Median DTB minus Vanilla: -8994.603 ms
- Median DTB speedup: 57.306%
- DTB faster fully clean pairs: 3/3
- Latency ranking valid: False

Latency ranking is valid only when every latency-eligible pair is free of protocol anomalies; run quality probes separately from ranking.
