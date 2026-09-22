# Tool-call paired streaming replay

## Protocol

| Variant | Requests | Protocol pass | Protocol failure | Semantic deviation | Overall failure | E2E median ms | E2E P95 ms | First output median ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| dtb | 10 | 10 | 0 | 0 | 0 | 18510.991 | 20841.653 | 822.322 |
| vanilla | 10 | 10 | 0 | 0 | 0 | 20774.476 | 22630.428 | 827.86 |

## Paired latency

- All pairs: 10
- Protocol-clean pairs: 10
- Fully clean pairs: 10
- Median DTB minus Vanilla: -1182.082 ms
- Median DTB speedup: 5.682%
- DTB faster fully clean pairs: 6/10
- Latency ranking valid: True

Latency ranking is valid only when every latency-eligible pair is free of protocol anomalies; run quality probes separately from ranking.
