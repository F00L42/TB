# 生产参数G0输入包

完整执行顺序、参数、退出码与证据边界见
[run_server_02/RESULT.md 第6.8节](../../run_server_02/RESULT.md#68-可执行交付脚本输入与步骤)。

- `legacy_profiles.json` / `legacy_fixtures.jsonl`：H0/H1单变量对照。
- `vanilla_log_profile.json`：日志推导预检查，不能作为live生产profile。
- `production_fixtures.jsonl`：真实prompt上的合成固定生成序列，不是历史模型生成IDs。
- `request_selection.json`：1条短请求＋13条历史长尾请求来源选择。
- `prepared/requests.jsonl`：14条完整原始请求体，直接传给`tools/dtb/replay_tool_calls.py --file`。
- `prepared/manifest.json`：选择和来源归属；请求体hash及精确locator在每条输入中。

所有输入仅用于G0定位/取证，不构成正式时延数据集。请求中可能包含内部代码和工具参数，按内部实验数据管理。
