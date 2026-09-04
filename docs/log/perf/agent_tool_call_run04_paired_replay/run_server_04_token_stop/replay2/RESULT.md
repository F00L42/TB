# Tool-call paired streaming replay

## Protocol

| Variant | Requests | Protocol pass | Protocol failure | Semantic deviation | Overall failure | E2E median ms | E2E P95 ms | First output median ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| dtb | 60 | 60 | 0 | 22 | 22 | 8998.594 | 24829.16 | 765.05 |
| vanilla | 60 | 60 | 0 | 30 | 30 | 7774.506 | 22308.206 | 732.041 |

## Paired latency

- All pairs: 60
- Protocol-clean pairs: 60
- Fully clean pairs: 19
- Median DTB minus Vanilla: -3.621 ms
- Median DTB speedup: 0.041%
- DTB faster fully clean pairs: 10/19
- Latency ranking valid: False

Latency ranking is valid only when every latency-eligible pair is free of protocol anomalies; run quality probes separately from ranking.
