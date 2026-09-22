# Tool-call paired streaming replay

## Protocol

| Variant | Requests | Protocol pass | Protocol failure | Semantic deviation | Overall failure | E2E median ms | E2E P95 ms | First output median ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| dtb | 10 | 10 | 0 | 0 | 0 | 24930.635 | 26017.643 | 497.928 |
| vanilla | 10 | 10 | 0 | 0 | 0 | 25741.565 | 27558.659 | 497.854 |

## Paired latency

- All pairs: 10
- Protocol-clean pairs: 10
- Fully clean pairs: 10
- Median DTB minus Vanilla: -1276.037 ms
- Median DTB speedup: 4.859%
- DTB faster fully clean pairs: 7/10
- Latency ranking valid: True

Latency ranking is valid only when every latency-eligible pair is free of protocol anomalies; run quality probes separately from ranking.
