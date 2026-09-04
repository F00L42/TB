# Tool-call paired streaming replay

## Protocol

| Variant | Requests | Protocol pass | Protocol failure | Semantic deviation | Overall failure | E2E median ms | E2E P95 ms | First output median ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| dtb | 60 | 60 | 0 | 27 | 27 | 8339.024 | 21998.389 | 783.126 |
| vanilla | 60 | 60 | 0 | 30 | 30 | 8304.028 | 23154.114 | 732.681 |

## Paired latency

- All pairs: 60
- Protocol-clean pairs: 60
- Fully clean pairs: 19
- Median DTB minus Vanilla: 724.042 ms
- Median DTB speedup: -8.347%
- DTB faster fully clean pairs: 7/19
- Latency ranking valid: False

Latency ranking is valid only when every latency-eligible pair is free of protocol anomalies; run quality probes separately from ranking.
