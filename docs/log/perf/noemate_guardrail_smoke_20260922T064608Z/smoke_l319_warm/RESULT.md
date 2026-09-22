# Tool-call paired streaming replay

## Protocol

| Variant | Requests | Protocol pass | Protocol failure | Semantic deviation | Overall failure | E2E median ms | E2E P95 ms | First output median ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| dtb | 1 | 1 | 0 | 0 | 0 | 21638.126 | 21638.126 | 546.515 |
| vanilla | 1 | 1 | 0 | 0 | 0 | 45680.636 | 45680.636 | 5302.623 |

## Paired latency

- All pairs: 1
- Protocol-clean pairs: 1
- Fully clean pairs: 1
- Median DTB minus Vanilla: -24042.51 ms
- Median DTB speedup: 52.632%
- DTB faster fully clean pairs: 1/1
- Latency ranking valid: True

Latency ranking is valid only when every latency-eligible pair is free of protocol anomalies; run quality probes separately from ranking.
