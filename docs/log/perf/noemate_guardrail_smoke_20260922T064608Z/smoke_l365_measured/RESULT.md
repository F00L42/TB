# Tool-call paired streaming replay

## Protocol

| Variant | Requests | Protocol pass | Protocol failure | Semantic deviation | Overall failure | E2E median ms | E2E P95 ms | First output median ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| dtb | 2 | 2 | 0 | 0 | 0 | 1525.513 | 1531.6 | 683.005 |
| vanilla | 2 | 2 | 0 | 0 | 0 | 1530.168 | 1534.971 | 691.947 |

## Paired latency

- All pairs: 2
- Protocol-clean pairs: 2
- Fully clean pairs: 2
- Median DTB minus Vanilla: -4.655 ms
- Median DTB speedup: 0.301%
- DTB faster fully clean pairs: 1/2
- Latency ranking valid: True

Latency ranking is valid only when every latency-eligible pair is free of protocol anomalies; run quality probes separately from ranking.
