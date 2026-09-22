# Tool-call paired streaming replay

## Protocol

| Variant | Requests | Protocol pass | Protocol failure | Semantic deviation | Overall failure | E2E median ms | E2E P95 ms | First output median ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| dtb | 2 | 2 | 0 | 0 | 0 | 3414.892 | 3757.492 | 541.049 |
| vanilla | 2 | 2 | 0 | 0 | 0 | 2932.173 | 3190.41 | 544.176 |

## Paired latency

- All pairs: 2
- Protocol-clean pairs: 2
- Fully clean pairs: 2
- Median DTB minus Vanilla: 482.72 ms
- Median DTB speedup: -16.306%
- DTB faster fully clean pairs: 0/2
- Latency ranking valid: True

Latency ranking is valid only when every latency-eligible pair is free of protocol anomalies; run quality probes separately from ranking.
