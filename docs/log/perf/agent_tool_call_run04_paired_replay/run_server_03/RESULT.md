# Tool-call paired streaming replay

## Protocol

| Variant | Requests | Protocol pass | Protocol failure | Semantic deviation | Overall failure | E2E median ms | E2E P95 ms | First output median ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| dtb | 60 | 58 | 2 | 27 | 27 | 8273.868 | 25589.717 | 766.901 |
| vanilla | 60 | 60 | 0 | 24 | 24 | 8743.06 | 24125.246 | 733.015 |

## Paired latency

- All pairs: 60
- Protocol-clean pairs: 58
- Fully clean pairs: 26
- Median DTB minus Vanilla: -1121.504 ms
- Median DTB speedup: 9.722%
- DTB faster fully clean pairs: 17/26
- Latency ranking valid: False

Latency ranking is valid only when every latency-eligible pair is free of protocol anomalies; run quality probes separately from ranking.
