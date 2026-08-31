# Tool-call paired streaming replay

## Protocol

| Variant | Requests | Pass | Failure | E2E median ms | E2E P95 ms | First output median ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| dtb | 300 | 134 | 166 | 6046.051 | 22974.61 | 687.738 |
| vanilla | 300 | 270 | 30 | 9495.048 | 27674.961 | 598.949 |

## Paired latency

- All pairs: 300
- Protocol-clean pairs: 128
- Median DTB minus Vanilla: 546.507 ms
- Median DTB speedup: -15.773%
- DTB faster clean pairs: 43/128
- Latency ranking valid: False

Latency ranking is valid only when every latency-eligible pair is free of protocol anomalies; run quality probes separately from ranking.
