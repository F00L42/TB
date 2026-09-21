# Tool-call paired streaming replay

## Protocol

| Variant | Requests | Protocol pass | Protocol failure | Semantic deviation | Overall failure | E2E median ms | E2E P95 ms | First output median ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| dtb | 4 | 4 | 0 | 3 | 3 | 12591.798 | 17970.572 | 613.593 |
| vanilla | 4 | 4 | 0 | 4 | 4 | 17189.556 | 20225.177 | 612.896 |

## Paired latency

- All pairs: 4
- Protocol-clean pairs: 4
- Fully clean pairs: 0
- Median DTB minus Vanilla: 0.0 ms
- Median DTB speedup: 0.0%
- DTB faster fully clean pairs: 0/0
- Latency ranking valid: False

Latency ranking is valid only when every latency-eligible pair is free of protocol anomalies; run quality probes separately from ranking.
