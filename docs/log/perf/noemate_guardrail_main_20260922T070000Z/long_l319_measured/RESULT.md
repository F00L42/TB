# Tool-call paired streaming replay

## Protocol

| Variant | Requests | Protocol pass | Protocol failure | Semantic deviation | Overall failure | E2E median ms | E2E P95 ms | First output median ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| dtb | 10 | 10 | 0 | 0 | 0 | 22489.902 | 22833.003 | 544.594 |
| vanilla | 10 | 10 | 0 | 0 | 0 | 42294.828 | 45318.025 | 541.529 |

## Paired latency

- All pairs: 10
- Protocol-clean pairs: 10
- Fully clean pairs: 10
- Median DTB minus Vanilla: -19913.562 ms
- Median DTB speedup: 47.03%
- DTB faster fully clean pairs: 10/10
- Latency ranking valid: True

Latency ranking is valid only when every latency-eligible pair is free of protocol anomalies; run quality probes separately from ranking.
