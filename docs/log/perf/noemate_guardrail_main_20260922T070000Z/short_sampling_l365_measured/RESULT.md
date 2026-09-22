# Tool-call paired streaming replay

## Protocol

| Variant | Requests | Protocol pass | Protocol failure | Semantic deviation | Overall failure | E2E median ms | E2E P95 ms | First output median ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| dtb | 20 | 20 | 0 | 0 | 0 | 1530.189 | 3389.437 | 685.897 |
| vanilla | 20 | 20 | 0 | 0 | 0 | 1525.123 | 1646.533 | 687.337 |

## Paired latency

- All pairs: 20
- Protocol-clean pairs: 20
- Fully clean pairs: 20
- Median DTB minus Vanilla: 3.907 ms
- Median DTB speedup: -0.257%
- DTB faster fully clean pairs: 5/20
- Latency ranking valid: True

Latency ranking is valid only when every latency-eligible pair is free of protocol anomalies; run quality probes separately from ranking.
