# DTB 静态优化：本地交付结果

日期：2026-09-22。证据类型：**本地合成 CPU 微基准 + 固定输入等价性 + 无模型回归**。
该目录没有服务器请求，不生成 `_SUMMARY.csv` 或 phase 账本，不参加推理性能排名。

## 结论

三个合成输入长度下，路由调用的 Python 中位耗时降低约 26%–32%；长响应非流式聚合也减少了重复字符串复制。
完整路由字典在 633 条冻结输入上与 f342e127 一致。final 的方法提取通过行为回归，未改变预算、cue、状态转移或 parser/terminal 契约。
这里的比例不是模型或端到端加速率，真实并发吞吐、内存、质量与服务器协议仍为 `pending_remote`。

## 身份和复算

基线：`f342e1276a0d8b9c8b2c02f57917b19be936b799`；候选：`codex/dtb-static-refactor`。
微基准在提交前执行，因此 `candidate_head` 仍记录基线 HEAD，`candidate_dirty=true`；候选代码以
[`manifest.json`](manifest.json) 和 [`microbenchmark.json`](microbenchmark.json) 的源码 SHA-256 精确定义，不能将该 HEAD 当作候选完整代码身份。
配置、policy、三个 patch 与基线相同；没有接入原工作区未提交的 system 路由方案。

```bash
python -m pip install -r requirements-dev.txt
git submodule update --init --recursive
python tools/dtb/check_static.py
python -X utf8 tools/dtb/static_cpu_benchmark.py --replay --output .pytest_cache/dtb-static-cpu.json
```

Python 3.14.7 / Windows，本轮版本与源文件哈希在 manifest 中。5 次重复 × 每次 5 次调用；先 warmup、交替基线/候选顺序，报告中位数。原始样本全部保存，不设置易受机器负载影响的耗时测试断言。

## 局部耗时

路由使用重复中英混合模板、结构证据和三个字符长度；单位为 **单次调用毫秒**。

| 合成输入字符数 | 基线 ms | 候选 ms | 耗时减少 |
| --- | --- | --- | --- |
| 1000 | 2.760 | 1.884 | 31.75% |
| 16000 | 36.782 | 27.244 | 25.93% |
| 64000 | 152.961 | 109.071 | 28.69% |

非流式聚合每块 64 个汉字、2 个 token ID；单位为 **整批毫秒**。计时包含预装队列/对象构造、事件循环和输出断言。32 路只是预装独立队列的 async batch，不模拟服务器到达/排队/设备执行；实际交错隔离另由单元测试覆盖。

| 独立请求数 × 每请求块数 | 基线 ms | 候选 ms | 耗时减少 |
| --- | --- | --- | --- |
| 1 × 128 | 1.114 | 0.717 | 35.61% |
| 1 × 2048 | 10.934 | 4.303 | 60.65% |
| 32 × 128 | 10.089 | 7.216 | 28.48% |
| 32 × 2048 | 317.614 | 121.266 | 61.82% |

final 职责提取属于可维护性改动，未单独测量其耗时，不从以上数据推断 final 变快。片段列表会保留文本到聚合结束；未测峰值内存，不声称内存使用下降。

## 行为与交付检查

- final 新增 41 项测试先在原实现通过，再在候选通过：覆盖延迟边界、三种 output kind、流式/非流式、正常/error terminal、无输出、非法尾流、引擎异常、取消和 consumer 异常。
- 新增静态 CPU 测试 16 项：Unicode/空白/符号/重复关键词、配置替换、8 线程冷缓存、32 请求交错聚合、错误/取消。
- 633 条 run_06_v3 冻结输入的完整 route 字典逐项一致，含分数、拒识原因和证据；输入文件 SHA 保存在微基准 JSON。
- 完整 tests：**472 passed、1966 subtests passed**；静态编译、`git diff --check`、三份 patch 应用检查、固定子模块清洁状态通过。实际输出见 [`validation.txt`](validation.txt)。
- 初次基线运行遇到 pytest 共享临时目录权限及未初始化的子模块；修正环境后检查通过。这些环境错误没有通过删测/skip 或改算法规避。
- 导航文档本地链接由 `check_static.py` 检查；链接 anchor 与历史正文的全量链接不在该门禁内。

## 待服务器验收

沿用[静态优化设计](../../../design/dtb_static_cpu_refactor.md#6-服务器验收与回退)的同配置回放与回退要求。
单独归档真实 tokenizer、HTTP/parser、NPU、并发 P50/P95、E2E、任务质量和取消清理。正式耗时用 off，归属/模型总工作量用独立 phase；不把此处合成数据并入服务器报表。

## 变更记录

| 日期 | 变更 |
| --- | --- |
| 2026-09-22 | 保存静态优化源码身份、完整路由差分、CPU 样本与本地门禁结果；服务器 pending_remote |
