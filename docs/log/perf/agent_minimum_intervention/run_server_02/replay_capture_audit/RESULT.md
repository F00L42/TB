# Tool-call paired streaming replay

## Protocol

| Variant | Requests | Protocol pass | Protocol failure | Semantic deviation | Overall failure | E2E median ms | E2E P95 ms | First output median ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| dtb | 30 | 30 | 0 | 7 | 7 | 10639.071 | 19612.131 | 565.013 |
| vanilla | 30 | 30 | 0 | 7 | 7 | 11077.299 | 18436.08 | 583.394 |

## Paired latency

- All pairs: 30
- Protocol-clean pairs: 30
- Fully clean pairs: 20
- Median DTB minus Vanilla: -541.979 ms
- Median DTB speedup: 4.879%
- DTB faster fully clean pairs: 12/20
- Latency ranking valid: False

Latency ranking is valid only when every latency-eligible pair is free of protocol anomalies; run quality probes separately from ranking.
