# DTB 静态 CPU 优化与可读性重构

## 1. 目的与交付边界

日期：2026-09-22。起点为开发分支与远端一致的最新提交
`f342e1276a0d8b9c8b2c02f57917b19be936b799`；实现位于独立 worktree 的
`codex/dtb-static-refactor`。原工作区未提交的 system 路由/配置等改动不在本次输入内。

目标是减少并发请求在 Python 层的重复工作，让新 Session 和维护者快速定位真实实现，
同时保持算法执行逻辑、预算、cue、采样参数、child 顺序、parser 契约和取消语义。
本次交付为可复算的本地静态/无模型版本，真实服务器验证状态为 `pending_remote`。

## 2. 源码审计与取舍

| 部位 | 起点问题或已存在的边界 | 本次处理 |
| --- | --- | --- |
| `scene_router.py` | 每次关键词匹配重新做规则准备；每个中文/符号关键词重复 casefold 整段请求 | 有界规则缓存、每次 classify 复用一个局部 casefold 文本 |
| 结构证据 | code block 只需要知道存在；step/count 分数有固定饱和值，却收集全部匹配 | `search` 及截取 `finditer`，保留分数、证据字符串及顺序 |
| 非流式 `aggregate_queue` | 对对象属性逐块 `text += delta`，反复复制增长中的字符串 | 片段列表收集，在交给完整响应 parser 前 join 一次 |
| `_FinalAnswerState.handle` | 边界准备、额度、提交、流消费、记账混在一个长函数 | 提取三个私有方法，保持引擎调用和成功收尾在主流程可见 |
| 置信度 / visible collector | 已有关闭态省略诊断和增量收集优化 | 保留实现与既有等价性测试，不重复改写 |
| prompt / child 所有权 | `own_prompt`、`engine_input`、suffix cache 已分别保护服务输入和 child 快照 | 保留必要复制，不改成共享可变列表或全局 tokenizer 缓存 |
| producer / consumer | 独立队列保证 observer 不因下游 parser 消费而停止观察 | 不合并 DELTA，不改变 backpressure、abort/drain 或 child 调度 |
| history / telemetry | 可选服务级后台写出；off 有明确短路；历史策略有进程锁 | 不改变开关、队列/丢弃策略、评分与持久化顺序 |
| difficulty | 默认关闭的独立算法模块 | 不把估计器改写混入静态优化 |

## 3. 保持的匹配与输出契约

### 3.1 关键词缓存

`_compile_keyword` 以 `str(keyword).strip()` 为 key，LRU 上限 2048。值只有不可变的
casefold 子串或 regex Pattern。缓存不保存请求正文、分类结果、配置对象或请求状态；
多个配置的相同关键词可安全复用，缓存淘汰只会重复构造等价规则。

中文/纯符号仍按 casefold 子串匹配；其余关键词保留 `re.IGNORECASE`、Unicode `\w`
边界和内部 `\s+`。不能统一成小写子串：例如 ASCII 边界、İ/ı/ſ/K 与 Straße 的行为不同。
输出继续保留关键词原始字符串、顺序和重复项。评分/排序、required gate、reject 和 override
分支没有改动。633 条冻结输入比较**完整返回字典**，不限于最终 scene。

结构扫描只省略不会改变结果的后续匹配：step 在 3 次达到 6 分，count field 在 2 次取得
3 分；choice label 集合和其他规则仍保持原来逻辑。每次 classify 至多新增一份局部折叠文本，
共享缓存不随请求正文规模增长。

### 3.2 非流式聚合

输出仍只有一项，复用队列第一项作为响应容器，token IDs 仍按到达顺序 extend，保留最后一个
非空 finish/stop；没有终态时仍按原逻辑填 stop。空队列、错误哨兵、取消和 text-only delta
语义保持。后续队列对象不被修改。此优化仅影响非流式 full-response 聚合，流式 delta
转发路径未改。

字符串反复前缀复制改为 O(总字符数) 的一次 join；片段列表占用 O(chunk 数) 引用空间，
会保留各文本片段直到聚合结束。因此它不是“零额外内存”优化，实际峰值驻留内存仍需在
真实并发与输出分块条件下测量。

### 3.3 final 方法职责

- `_prepare_boundary`：按原顺序校验原因、尝试发边界、追加同一 cue/协议边界。
- `_resolve_limits`：追加 prompt 后计算 context 和外部 completion 剩余额度，预留尚未发送的边界。
- `_consume_stream`：立即转发普通 delta，首块时补发延迟边界；正常 terminal 等 EOF，
  非法尾流仍抛错；error 已透传返回 None；异常/取消交回原 owner。
- `handle`：保持真实 sampling、child ID、prompt/engine kwargs 的可见性；成功时发送原 terminal，
  更新唯一账本，再条件写出 phase。

`_FinalLimits` 是每次 final 一份不可变提交前快照，不是第二套请求账本。
phase 计时仍从准备前到 terminal 发送后；controller CPU 仍在 generate 前截止。
本次不把 final 的方法提取宣称为性能提升，它增加少量函数调用及一个局部对象，收益是职责清晰。
原始设计和异常分支依据见 [final 重构设计](dtb_final_answer_state_refactor.md)。

## 4. 文档一致性整理

新增根 README 的任务入口、DTB AGENTS 的符号/测试/所有权映射，以及覆盖全部设计文件的
[状态索引](README.md)。DTB README 补充 observation 与非流式的不同入口。
历史迁移评审和清理设计加上快照说明；final 设计标注本次实现；收尾交接修正为分析已提交。

没有搬移历史实验文件、改写历史性能/质量结论或更新原工作区的待提交方案。
历史文档中的旧 API/哈希用于解释演进；当前导航明确导向生产包和三份版本化 patch。
统一检查脚本验证维护入口的本地链接目标，历史正文的所有链接和 Markdown anchor 不在该检查范围内。

## 5. 可复现的本地检查

```bash
python -m pip install -r requirements-dev.txt
git submodule update --init --recursive
python tools/dtb/check_static.py
python tools/dtb/static_cpu_benchmark.py --replay --output .pytest_cache/dtb-static-cpu.json
```

`check_static.py` 验证两份上游源码与本提交 gitlink 一致且干净、检查三份 patch、编译源码，
再运行完整 tests。只读检查不会应用 patch。pytest 临时目录由脚本在 `.pytest_cache` 下生成
唯一名称，子进程 stdin 为 DEVNULL；不会删除其他 Session 的临时目录。

本地结果：新增 final 行为测试在旧/新实现上各 41 项通过；重构相关核心测试 247 项通过；
新增静态 CPU 行为测试 16 项通过；完整 tests **472 passed / 1966 subtests passed**。
后续只做文档/检查工具修改时不反复运行整套测试，按变更范围验证。

基准默认从可信 Git 提交 f342e127 读取两份原实现，在独立模块命名空间执行，不复制生产镜像。
两侧使用同一当前配置；先 warmup，交替测量顺序，保存所有样本、源码/配置/数据 SHA 与平台信息。
路由使用三个合成长度；聚合计时包含夹具构造、事件循环和正确性断言。
32 路预装队列批次只用于本地 CPU/隔离检查，不模拟服务器队列或引擎并行。
完整证据见 [本地结果](../log/perf/static_cpu_20260922/RESULT.md)。

## 6. 服务器验收与回退

`pending_remote`：真实 tokenizer/parser/vLLM-Ascend 的流式与非流式回放，实际并发下的
内存、E2E、P50/P95、吞吐、任务质量与取消清理。统一步骤见
[服务器 runbook](../debug/deer_performance_analysis.md) 与
[实验流程](think_budget_generalization_plan.md)。本轮不增加 cue/预算扫描变量。

目标环境升级时保留同一模型、采样/seed、并发/到达节奏与 cache；记录本分支提交和配置/patch
hash。用同身份 `off` 比较 E2E，必要时单独 `phase` 对账，不把本地微基准比例推广为设备收益。
若发现 parser/输出/质量回归，停止该候选并回到 f342e127 的代码制品和相同配置，按请求定位；
不能靠吞掉错误、放宽预算或重新生成不同样本通过验收。

## 7. 变更历史

| 日期 | 变更 | 依据 |
| --- | --- | --- |
| 2026-09-22 | 有界关键词规则缓存、局部 casefold、饱和结构扫描、非流式一次合并；final 私有职责提取；导航与验证入口 | 固定 f342e127、完整回归和本地 CPU/路由差分证据；服务器 pending_remote |
