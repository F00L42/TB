# Tool-call paired streaming replay

## Protocol

| Variant | Requests | Protocol pass | Protocol failure | Semantic deviation | Overall failure | E2E median ms | E2E P95 ms | First output median ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| dtb | 4 | 4 | 0 | 1 | 1 | 8009.617 | 12270.454 | 625.815 |
| vanilla | 4 | 4 | 0 | 0 | 0 | 6648.255 | 8400.568 | 631.548 |

## Paired latency

- All pairs: 4
- Protocol-clean pairs: 4
- Fully clean pairs: 3
- Median DTB minus Vanilla: 2311.08 ms
- Median DTB speedup: -34.87%
- DTB faster fully clean pairs: 0/3
- Latency ranking valid: False

Latency ranking is valid only when every latency-eligible pair is free of protocol anomalies; run quality probes separately from ranking.
