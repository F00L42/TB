# Tool-call paired streaming replay

## Protocol

| Variant | Requests | Protocol pass | Protocol failure | Semantic deviation | Overall failure | E2E median ms | E2E P95 ms | First output median ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| dtb | 20 | 20 | 0 | 0 | 0 | 8276.035 | 12780.361 | 440.01 |
| vanilla | 20 | 20 | 0 | 0 | 0 | 6979.567 | 14962.905 | 439.574 |

## Paired latency

- All pairs: 20
- Protocol-clean pairs: 20
- Fully clean pairs: 20
- Median DTB minus Vanilla: 59.077 ms
- Median DTB speedup: -0.849%
- DTB faster fully clean pairs: 9/20
- Latency ranking valid: True

Latency ranking is valid only when every latency-eligible pair is free of protocol anomalies; run quality probes separately from ranking.
