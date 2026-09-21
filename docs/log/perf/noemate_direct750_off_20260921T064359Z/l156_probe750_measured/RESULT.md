# Tool-call paired streaming replay

## Protocol

| Variant | Requests | Protocol pass | Protocol failure | Semantic deviation | Overall failure | E2E median ms | E2E P95 ms | First output median ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| dtb | 4 | 4 | 0 | 0 | 0 | 27176.705 | 28923.305 | 882.989 |
| vanilla | 4 | 4 | 0 | 0 | 0 | 24311.516 | 25057.601 | 885.659 |

## Paired latency

- All pairs: 4
- Protocol-clean pairs: 4
- Fully clean pairs: 4
- Median DTB minus Vanilla: 3273.556 ms
- Median DTB speedup: -13.344%
- DTB faster fully clean pairs: 0/4
- Latency ranking valid: True

Latency ranking is valid only when every latency-eligible pair is free of protocol anomalies; run quality probes separately from ranking.
