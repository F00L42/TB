# Tool-call paired streaming replay

## Protocol

| Variant | Requests | Protocol pass | Protocol failure | Semantic deviation | Overall failure | E2E median ms | E2E P95 ms | First output median ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| dtb | 20 | 20 | 0 | 0 | 0 | 13297.393 | 17058.968 | 462.319 |
| vanilla | 20 | 20 | 0 | 0 | 0 | 12996.022 | 18883.017 | 460.782 |

## Paired latency

- All pairs: 20
- Protocol-clean pairs: 20
- Fully clean pairs: 20
- Median DTB minus Vanilla: 90.611 ms
- Median DTB speedup: -0.646%
- DTB faster fully clean pairs: 9/20
- Latency ranking valid: True

Latency ranking is valid only when every latency-eligible pair is free of protocol anomalies; run quality probes separately from ranking.
