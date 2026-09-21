# Tool-call paired streaming replay

## Protocol

| Variant | Requests | Protocol pass | Protocol failure | Semantic deviation | Overall failure | E2E median ms | E2E P95 ms | First output median ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| dtb | 4 | 4 | 0 | 3 | 3 | 10562.736 | 15416.618 | 552.409 |
| vanilla | 4 | 4 | 0 | 1 | 1 | 14560.985 | 17401.693 | 558.852 |

## Paired latency

- All pairs: 4
- Protocol-clean pairs: 4
- Fully clean pairs: 1
- Median DTB minus Vanilla: -10493.404 ms
- Median DTB speedup: 61.062%
- DTB faster fully clean pairs: 1/1
- Latency ranking valid: False

Latency ranking is valid only when every latency-eligible pair is free of protocol anomalies; run quality probes separately from ranking.
