# Tool-call paired streaming replay

## Protocol

| Variant | Requests | Protocol pass | Protocol failure | Semantic deviation | Overall failure | E2E median ms | E2E P95 ms | First output median ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| dtb | 12 | 12 | 0 | 2 | 2 | 18602.361 | 43293.422 | 622.346 |
| vanilla | 12 | 12 | 0 | 2 | 2 | 24426.149 | 37006.838 | 1050.135 |

## Paired latency

- All pairs: 12
- Protocol-clean pairs: 12
- Fully clean pairs: 10
- Median DTB minus Vanilla: -5379.035 ms
- Median DTB speedup: 24.93%
- DTB faster fully clean pairs: 7/10
- Latency ranking valid: False

Latency ranking is valid only when every latency-eligible pair is free of protocol anomalies; run quality probes separately from ranking.
