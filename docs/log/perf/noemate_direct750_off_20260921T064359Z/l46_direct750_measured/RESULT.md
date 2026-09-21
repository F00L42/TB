# Tool-call paired streaming replay

## Protocol

| Variant | Requests | Protocol pass | Protocol failure | Semantic deviation | Overall failure | E2E median ms | E2E P95 ms | First output median ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| dtb | 4 | 4 | 0 | 0 | 0 | 10215.2 | 10497.395 | 633.823 |
| vanilla | 4 | 4 | 0 | 0 | 0 | 10212.863 | 10876.166 | 634.495 |

## Paired latency

- All pairs: 4
- Protocol-clean pairs: 4
- Fully clean pairs: 4
- Median DTB minus Vanilla: 201.985 ms
- Median DTB speedup: -1.976%
- DTB faster fully clean pairs: 1/4
- Latency ranking valid: True

Latency ranking is valid only when every latency-eligible pair is free of protocol anomalies; run quality probes separately from ranking.
