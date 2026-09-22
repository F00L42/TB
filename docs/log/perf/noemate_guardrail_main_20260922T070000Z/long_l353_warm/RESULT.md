# Tool-call paired streaming replay

## Protocol

| Variant | Requests | Protocol pass | Protocol failure | Semantic deviation | Overall failure | E2E median ms | E2E P95 ms | First output median ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| dtb | 1 | 1 | 0 | 0 | 0 | 25461.065 | 25461.065 | 509.758 |
| vanilla | 1 | 1 | 0 | 0 | 0 | 32281.325 | 32281.325 | 6308.156 |

## Paired latency

- All pairs: 1
- Protocol-clean pairs: 1
- Fully clean pairs: 1
- Median DTB minus Vanilla: -6820.26 ms
- Median DTB speedup: 21.128%
- DTB faster fully clean pairs: 1/1
- Latency ranking valid: True

Latency ranking is valid only when every latency-eligible pair is free of protocol anomalies; run quality probes separately from ranking.
