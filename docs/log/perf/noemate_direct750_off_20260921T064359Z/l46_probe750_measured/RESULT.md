# Tool-call paired streaming replay

## Protocol

| Variant | Requests | Protocol pass | Protocol failure | Semantic deviation | Overall failure | E2E median ms | E2E P95 ms | First output median ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| dtb | 4 | 4 | 0 | 2 | 2 | 17108.472 | 26130.456 | 638.346 |
| vanilla | 4 | 4 | 0 | 1 | 1 | 12965.728 | 17834.656 | 638.66 |

## Paired latency

- All pairs: 4
- Protocol-clean pairs: 4
- Fully clean pairs: 2
- Median DTB minus Vanilla: -833.51 ms
- Median DTB speedup: 7.188%
- DTB faster fully clean pairs: 2/2
- Latency ranking valid: False

Latency ranking is valid only when every latency-eligible pair is free of protocol anomalies; run quality probes separately from ranking.
