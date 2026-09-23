# Session A：Noemate system 路由离线验收

日期：2026-09-22。源码基线 `f342e127`；审计使用本次新增的生产路由代码。
这是本地输入路由与 CPU/协议检查，**不是服务器自动模式 run 或 E2E 性能实验**。

## 结论与输入

实测（离线）：12,214 份 detail 中的请求正文全部完成身份路由，唯一请求文件数与前次捕获
分析一致。最大消息数 393，低于 4096 上限。未读取 assistant/tool 正文参与分类。

| 场景 | 模板数 | 请求数 | 动作 | 介入阈值 |
| --- | ---: | ---: | --- | --- |
| noemate_main | 2 | 8,306 | dtb | 750 |
| noemate_followup | 1 | 3,897 | native | 无 |
| noemate_compaction | 1 | 11 | native | 无 |
| 未识别 | — | 0 | native | 无 |
| 总计 | 4 | 12,214 | 命中率 100% | — |

数据来自 `proxy/results/noemate/agent_20260917T123724Z_7bac2ba7/derived/27c30b983f78c49b86f3/details/`。
输入 detail SHA、各组代表 request ID、配置 SHA、全文件 hash 索引的摘要见 [audit.json](audit.json)。
system_sha256 在此定义为 `SHA256(system字符串的UTF-8字节)`，不与其他脚本对 JSON/list 的
system hash 混用。当前只审计 detail 保存的输入，没有重新解析未回传的原始 archive。

生产规则来自真实 system 头：主模板共享 `# Agent 身份` 和首段运维身份；推荐模板为
`你是对话后续问题推荐助手。…`；压缩模板为 `You are the conversation-compaction module…`。
完整、精确的匹配头保存在候选 YAML，不在日志记录正文。

这批模板参与了规则设计，100% 只代表已见样本覆盖；不是未知模板的泛化准确率，也不是正确回答率。
辅助请求的既有非流式解析异常仍保留为历史证据边界。750 的来源是原 fixed 冻结小集，
不证明所有主请求 750 足够；自动路由不估计同身份下的问题难度。

## 拒识与优先级控制

真实捕获没有拒识；以下为明确标记的**合成无模型控制**，见 `tests/test_dtb_system_routing.py`：

| 输入变化 | 预期/已验证结果 |
| --- | --- |
| 空 messages / 首条不是 system | missing_messages / missing_leading_system，native |
| 非字符串 system / 前导空白变化 | unsupported_system_content / unknown_system_prefix，native |
| 后置 system 或 developer | conflicting_instruction_roles，native |
| 消息数 >4096 | message_limit，native |
| 首 user guard 不匹配/非字符串；仅最后 user 含正确前缀 | first_user_guard，native |
| tools_empty guard 失败 | tools_guard，native |
| 用户或工具正文引用已知身份头 | 不建立 system 身份，native |
| 有效 fixed_budget_tokens | 显式 DTB fixed 实验，跳过身份扫描 |
| 只有 fixed_check_length，模板未知 | 保持 native |
| 非法 fixed 参数，模板未知 | 返回配置错误，不静默 native |
| 相同/互为前缀的配置规则 | 启动报错，无运行时争抢 |

另测 dict/类型化消息等价、冻结配置、legacy_text 与产品样本兼容、off 不取遥测时钟/不建事件、
phase 的 route-only native、缺日志/重复日志与 DTB 缺账本的不同归属状态。

## 本地检查结果

- `python -m pytest tests -q -p no:cacheprovider --basetemp <新临时目录>`：
  **468 passed，1,966 subtests passed**。其中新增 system 路由测试 53 个。
- serving patch 在固定上游 `third_party/vllm` 上 `git apply --check` 通过；测试在临时副本
  应用真实 patch 并编译/执行原生 serving 方法，未修改上游 checkout。
- native 对照覆盖 stream=true/false、已知 native/未知、正常输出/engine error/取消、双 enable，
  原生 generate 一次，DTB handle/context/完整预算/recorder 不调用；priority/LoRA/trace/DP 保持。
- DTB 自动路径覆盖短流式不 resolve、非流式保留现有状态机且复用 scene 快照、不二次分类。
- 5 个服务器 smoke 输入已生成并本地校验；两种 stream 模式合计 10 个实际 build_body 的 fixed
  全为 0，路由与 route_expectation 一致。没有发 HTTP 请求。

第一次全量测试遇到旧 pytest 临时目录权限问题；换独立 basetemp 后正常运行。文本选择移到
DTB_ROUTE，既有 telemetry 测试按新的计时间隔更新，不伪造原 decision.select_text 时钟。

## CPU 微基准（本机，不是目标服务器）

见 [local_benchmark.json](local_benchmark.json)，每组预热 500 次、测量 3,000 次，计时区间
只有生产 prepare 调用，配置加载、JSON 读取和构造消息均在区间外。以下单位为毫秒：

| 消息数 | 单个 tool 正文字符数 | P50 | P95 | P99 | max |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 3 | 1 | .0066 | .0124 | .0258 | .2588 |
| 3 | 1,000,000 | .0061 | .0111 | .0150 | .0729 |
| 393 | 1 | .0533 | .1167 | .1513 | .5211 |
| 4096 | 1 | .5510 | 1.0931 | 1.4795 | 4.6894 |

源码确定：成本随消息角色数增长，不随历史正文长度增长；正文不可读取的测试对象另作结构验证。
这一次微基准符合该趋势，不用于宣称零开销。4096 压力组单列，P99 已超过设计中常见请求
1ms 的目标；不将本机结果包装成目标服务 CPU 达标。目标服务微基准、模型/任务 E2E、
并发取消及自动 phase/off 均为 **pending_remote**。

复算、配置选择、patch 升级和逐步服务器验收见
[Session A 部署手册](../../../design/dtb_system_routing_deployment.md)。
