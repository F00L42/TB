# Tool-call paired streaming replay

## Protocol

| Variant | Requests | Protocol pass | Protocol failure | Semantic deviation | Overall failure | E2E median ms | E2E P95 ms | First output median ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| dtb | 52 | 51 | 1 | 0 | 1 | 10977.063 | 31300.492 | 424.685 |
| vanilla | 52 | 52 | 0 | 0 | 0 | 19708.4 | 51796.07 | 539.029 |

## Paired latency

- All pairs: 52
- Protocol-clean pairs: 51
- Fully clean pairs: 51
- Median DTB minus Vanilla: -6034.464 ms
- Median DTB speedup: 33.577%
- DTB faster fully clean pairs: 39/51
- Latency ranking valid: False

Latency ranking is valid only when every latency-eligible pair is free of protocol anomalies; run quality probes separately from ranking.
