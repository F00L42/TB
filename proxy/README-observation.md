# 通用 Agent 观测代理

独立入口 `agent_observation_proxy.py`，用于观察 LightCode 以外的 Agent。支持 Chat Completions 的 JSON/SSE、工具调用和并发请求；其他 HTTP 接口原样透传，统计标为未支持。WebSocket/CONNECT 不支持。

代理不修改模型、prompt、采样、DTB 参数或 `stream_options`，不重试生成。按请求到达时间以 UTC 小时归档；报告默认以北京时间显示。原有 `agent_dtb_proxy.py` 保持原入口和 session 产物格式。

## 启动

在仓库根目录执行，Python 3.10+：

```powershell
python -m pip install -r proxy/requirements-observation.txt
python -m proxy.agent_observation_proxy --config proxy/observation.example.yaml
```

先编辑 `observation.example.yaml`：

- `upstream.base_url`：实际模型服务，例如 `http://server:9001`。
- `listen.host/port`：代理监听地址。Agent 的 base URL 改为 `http://127.0.0.1:9000/v1`，由客户端自行附加 `chat/completions`。
- `capture.labels`：用户为本次运行声明的 agent、scenario、variant 等静态标签，不解析请求特征，也不读取 header 覆盖标签。需要分别统计多个 Agent 时，可为每个 Agent 启动不同端口的代理并分别设置 labels；共用入口但无法区分来源时填写 unknown。
- `tokenizers`：以响应中的 model 名称配置本地 tokenizer。所有别名需分别配置，不能把不同模型的 tokenizer 混用。

配置中的相对路径相对 YAML 所在目录。上游 base URL 已带 `/v1` 时，用显式映射避免重复，例如 `path_mapping: {incoming_prefix: /v1, upstream_prefix: /}`。重复 query 键及其原始编码保留。

本地 Token 重计数为可选依赖：

```powershell
python -m pip install transformers tokenizers
```

示例配置已给出本地 Qwen tokenizer 路径写法。缺少 tokenizer 时仍可代理、保存、阅读和使用上游 usage；本地计数显示未知，不记为 0。不会在线下载 tokenizer/模型，不执行远程 tokenizer 代码。

认证默认使用客户端原有 header。若需要固定上游 Bearer 凭据，配置 `upstream.auth_mode: bearer_env` 和 `upstream.auth_env: YOUR_ENV_NAME`；环境变量值不写入 manifest。代理读取配置时不修改进程代理环境变量，默认 `trust_env=false`。

## 结果与阅读

启动时打印新建的 `docs/log/perf/agent_<UTC时间>_<随机ID>/`。每次启动独立采集，避免混入此前运行；当前不提供原目录续写参数。

| 产物 | 用途 |
| --- | --- |
| `manifest.json` / `processes/*.json` | 配置、依赖版本、仓库 commit/dirty、进程完成与丢失计数 |
| `archive/YYYY-MM-DD/HH/part-*.jsonl` | 原始请求/响应 body 字节及生命周期事实；body 用 base64 保存 |
| `derived/live/SUMMARY.md` | 运行时自动刷新的阅读入口，不必等下一请求或关闭代理 |
| `derived/<analysis_id>/SUMMARY.md` | 离线不可变快照报告 |
| `timeline/…` / `requests/…` | 按到达时间排列的总览；每请求的输入、思考、正文、工具及 Token 来源 |
| `trajectories/*.md` | 按多轮消息阅读的轨迹，历史只展开一次，实采响应与历史补全分别标注 |
| `requests.jsonl` / `details/*.json` | 轻量请求账本及完整派生明细，包含原始日志位置 |
| `token_usage.csv` / `distribution.json` / `threshold_scan.csv` | Token 统计、分层分布、阈值覆盖与长尾贡献 |
| `integrity.json` / `analysis_manifest.json` | 缺口、排除项、快照输入 hash 与分析规则 |

`derived/live` 是后台周期快照，可能短暂落后，文件之间也可能处于更新中；正式分析使用离线报告。`report.live_interval_seconds: 0` 可关闭实时分析，仅采集后离线处理。首版实时分析重新扫描归档并使用磁盘 SQLite 索引，不在网络路径运行 tokenizer，但仍会消耗主机 CPU/I/O，正式对比必须统一两侧设置。

完整 body 默认保存，可能含代码及业务数据；Authorization/Cookie/API key 类 header 和常见凭据 query 的日志副本已脱敏。每个采集目录生成 `.gitignore`，避免正文被意外加入仓库；原始数据分享需由操作者审阅。

## 离线分析和检查单个请求

```powershell
python -m proxy.observation.analysis <capture_dir>
python -m proxy.observation.analysis <capture_dir> --from 2026-09-16T14:00:00+08:00 --to 2026-09-16T16:00:00+08:00 --thresholds 256,512,850,1024,2048
python -m proxy.observation.inspect_request <capture_dir> --request-id <request_id>
python -m proxy.observation.inspect_request <capture_dir> --request-id <request_id> --raw-events
```

时间筛选按开始时间 `[from,to)`；必须带时区。轨迹仅在选定的输入范围内匹配，阅读跨窗轨迹时扩大分析范围。可用 `--annotations <jsonl>` 附上外部账本，每行含 capture_id、request_id 和身份/来源；重复或未知键在完整性报告中记录。外部 annotations 当前仅作为附加证据保存，不覆盖原标签或自动裁决质量。

归档不会因为未知协议或解析失败而改变转发结果。语义重建单请求每方向默认最多解码 32 MiB，超出时保留原始 body，但标 `analysis_body_limit` 并排除统计；可配置 `report.max_body_bytes`，应考虑解析及渲染内存。gzip/deflate 支持离线解析，其他 Content-Encoding 仍透传，但其语义解析标为不支持。

## 多轮轨迹

轨迹无需 session ID：先直接显示 prompt 中有序历史，再检查另一实采请求的 `输入历史 + assistant 响应` 是否精确出现在这个 prompt 中，补上相应调用的时间、思考与 Token。

- 同一历史有多个不同延续时保留独立分支；每条页面有共享前缀请求链接。
- 相同输出能对应多次重试时保留候选，不强选最近请求。
- 未采集到的历史轮次显示文本，但时间、原始 usage 未知；历史文本不再加进生成 Token。
- 工具结果按当前历史中的 `tool_call_id` 关联。结果排列不代表真实工具执行顺序。
- 默认严格匹配。只有确认客户端删除历史 reasoning 时，才设置 `trajectories.projection_profiles: [omit_reasoning]`；匹配页明确显示这种转换，可从原响应补看思考。
- 上下文摘要/裁剪后的模糊延续不自动合并；首版展示分离的历史片段。轨迹不证明官方 task/session/父子 Agent 身份，也不证明任务已经结束。

## 统计边界

API usage 与本地文本重计数单列。本地生成文本分项为 reasoning、content、工具名、arguments；比例分母包含工具文本。缺失 reasoning 字段、缺 tokenizer、断流、长度截断、采集缺口和多 choice 请求不会混入单 choice 完整思考长度分布。

P50/P95、直方图和阈值扫描用于筛选实验候选；隐藏 probe、服务端 queue/prefill/decode、真实工具耗时及任务质量需要外部证据。`excess_tokens` 是超过阈值的文本量，不是已经验证能节省的 Token。`first_semantic_delta_ms` 是首完整语义事件的接收时间；压缩响应无法直接映射压缩前事件偏移时为 null，不冒充模型 TTFT。

有界队列满或磁盘失败时优先继续转发，并将采集标为不完整；不会因记录失败伪造成功流结束。进程强杀可能丢失未持久化事件，缺少正常关闭标记的归档只能作为不完整/运行中快照使用。Ctrl+C 由 Uvicorn 等待活跃请求（最长 30 秒）后取消并关闭日志。

## 本地验证

```powershell
New-Item -ItemType Directory -Force proxy/.test-output | Out-Null
$testRun = Join-Path (Get-Location) ('proxy/.test-output/run-' + [guid]::NewGuid().ToString('N'))
python -m pytest tests/test_agent_observation_proxy.py tests/test_full_log_writer.py -q -p no:cacheprovider --basetemp $testRun
```

测试覆盖无模型的真实 HTTP 连接、SSE/JSON、压缩、重复 header、Cookie 隔离、取消/断流、并发、缺口、轨迹与旧 writer 兼容。需要另装 pytest。真实 Agent、模型服务性能和质量仍须在目标环境验证；本地测试不代表这些运行时验收已完成。
