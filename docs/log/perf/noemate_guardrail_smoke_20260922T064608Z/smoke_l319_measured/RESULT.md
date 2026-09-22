# Tool-call paired streaming replay

## Protocol

| Variant | Requests | Protocol pass | Protocol failure | Semantic deviation | Overall failure | E2E median ms | E2E P95 ms | First output median ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| dtb | 2 | 2 | 0 | 0 | 0 | 22296.024 | 22318.282 | 542.271 |
| vanilla | 2 | 2 | 0 | 0 | 0 | 39926.091 | 40617.017 | 544.306 |

## Paired latency

- All pairs: 2
- Protocol-clean pairs: 2
- Fully clean pairs: 2
- Median DTB minus Vanilla: -17630.068 ms
- Median DTB speedup: 44.135%
- DTB faster fully clean pairs: 2/2
- Latency ranking valid: True

Latency ranking is valid only when every latency-eligible pair is free of protocol anomalies; run quality probes separately from ranking.
