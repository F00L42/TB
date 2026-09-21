# Tool-call paired streaming replay

## Protocol

| Variant | Requests | Protocol pass | Protocol failure | Semantic deviation | Overall failure | E2E median ms | E2E P95 ms | First output median ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| dtb | 4 | 4 | 0 | 0 | 0 | 11453.395 | 18643.65 | 628.193 |
| vanilla | 4 | 4 | 0 | 0 | 0 | 6848.882 | 10926.25 | 626.984 |

## Paired latency

- All pairs: 4
- Protocol-clean pairs: 4
- Fully clean pairs: 4
- Median DTB minus Vanilla: 3134.148 ms
- Median DTB speedup: -48.114%
- DTB faster fully clean pairs: 1/4
- Latency ranking valid: True

Latency ranking is valid only when every latency-eligible pair is free of protocol anomalies; run quality probes separately from ranking.
