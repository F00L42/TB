# Noemate 完整端到端测试证据包

本包对应 [详细实验博客](../../noemate_e2e_20261013.md)。数字来自固定 Git capture，2026-10-13 离线重算；不含新模型执行。
`acceptance.json` 记录用户确认固定初始业务输入后采纳的**系统整体观测验收**；`comparison.json` 保留此前分析快照及其更严格的独立因果效应/正式非劣 flags。数值没有改动，两个文件的判定层次不同。

| 文件 | 作用 |
| --- | --- |
| `vanilla_features.jsonl.gz` / `dtb_features.jsonl.gz` | 全部 12,214 / 11,441 请求的统计投影；不是抽样。保留统计值、输入哈希、原始 request ID、detail 路径和 Git blob/hash，不复制完整 prompt/响应 |
| `*_features.manifest.json` | 本包直接从固定 Git blob 提取的 SHA、行数、参数/场景身份和保留字段 |
| `comparison.json` | 全体描述统计、分层等权比较、bootstrap CI、完整输入交集和 native 负对照 |
| `initial_root_cases.csv` | 1,483 共同初始业务问题的重复计数、思考长度、E2E、prompt 和 finish 汇总 |
| `native_same_input_cases.csv` | 114 个 native 同完整输入的对照，不用 main 问题字符串代替完整输入 |
| `capture_conditions.json` | 网关 capture 配置、源码和库版本，以及同权重/off 的用户确认；不是服务器运行环境 manifest |
| `system_prompt_diff.json` | 两轮 system 的固定哈希、只改日期/model 的 417 行结构核对及初始上下文样例 |
| `vanilla_integrity.json` / `dtb_integrity.json` | 原派生采集完整性：无丢失、写入错误或未完成采集；不表示业务/协议全部成功 |
| `*_analysis_manifest.json` | 原派生记录的 raw 分片路径/hash、tokenizer 文件集合、parser 和分析代码身份；分片未因此复制到本包 |
| `anomalies.json` | 当前 main/辅助异常定位及明确的旧解析器边界 |
| `bundle_manifest.json` | 本目录所有冻结文件的字节数与 SHA256，另记录固定源提交及来源文件 |
| `extract_features.py` / `runtime_normalization.py` | 直接只读 Git blob 重建统计投影；规范化只影响辅助哈希中的两条 system 运行字段 |
| `recompute.py` | 仅依赖 Python 标准库；复算全体统计、分层、重复权重、CI 和 CSV |

## 离线核验

在仓库根目录运行，输出写入 Git 忽略的 artifacts，冻结证据不被覆盖：

```bash
python -X utf8 docs/report/closeout/evidence/noemate_e2e_20261013/recompute.py --verify
```

`--verify` 先检查整个证据包 SHA，再要求原统计快照中的 `descriptive / overlap / normalization / initial_root_comparison / native_same_input_control` 五个子树逐值一致，两个 CSV 逐字节一致。输出只记录本轮采纳的观测口径，仍保留严格因果结论为未识别。
Bootstrap 5,000 次、seed=20261013；每个问题的重复调用先平均，问题同步有放回重采样。计算时间取决于本机 Python 版本。

## 从历史源提交重新提取

需要本地 Git 中已有两个固定提交，或者先从仓库远端取到提交；下面命令自身不联网、不 checkout 大数据、不加载模型/tokenizer。
目标文件必须不存在，脚本不会覆盖已有输出：

```bash
python -X utf8 docs/report/closeout/evidence/noemate_e2e_20261013/extract_features.py \
  --commit 60f2d7c01faa3268874893dc42f850ad86086c7c \
  --derived proxy/results/noemate/agent_20260917T123724Z_7bac2ba7/derived/27c30b983f78c49b86f3 \
  --output artifacts/perf/noemate-e2e-rebuild/vanilla_features.jsonl.gz
python -X utf8 docs/report/closeout/evidence/noemate_e2e_20261013/extract_features.py \
  --commit 03d9ec6c3d7b48dc7925fc024b63462bd9fe7855 \
  --derived proxy/results/noemate/agent_20261012T030114Z_daf6d49b/derived/0dbb9a2fe91155631b1d \
  --output artifacts/perf/noemate-e2e-rebuild/dtb_features.jsonl.gz
python -X utf8 docs/report/closeout/evidence/noemate_e2e_20261013/recompute.py \
  --data-dir artifacts/perf/noemate-e2e-rebuild \
  --output-dir artifacts/perf/noemate-e2e-rebuild-comparison
```

PowerShell 使用单行命令或反引号续行。提取器使用显式 PIPE 读写 Git batch；其他非交互子进程均显式 DEVNULL。原始正文用完整实际请求哈希保留，问题预览最多 240 字符；分组哈希来自完整原字符串，不来自预览。

投影没有更改响应、剔除异常、重新分词或按目标结果挑选 case；它只删除重复的 runtime 元数据列。归档数值和 token-source 身份继承服务器采集。`gzip` 的解压后 SHA 为跨 Python/zlib 版本的主要核验值；压缩字节 SHA 固定本次交付文件。
整个包能离线复算统计，不代表原始事件 archive 已完整交付，或能在无目标设备环境重新运行服务器试验。
