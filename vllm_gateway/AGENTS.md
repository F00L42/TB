# vLLM 透传与请求记录网关

入口 `python -m vllm_gateway --config vllm_gateway/example.yaml`；详细接口见 [README](README.md)。
仅保留通用 HTTP 透传与观测，默认 append_body={} 不改请求体；不执行 DTB 算法，不重试生成，不执行返回的工具。
旧 proxy/agent_dtb_proxy 与 LightCode 专属接口已删除，没有兼容启动入口。

职责：transport.py 负责 HTTP 生命周期；storage.py 有界采集；chat_completions.py 解析观测副本；analysis/report/trajectories
负责离线解释与阅读。实际文件名以 observation/ 为准。不能让采集/分析失败改变上游响应。
不把推断的消息轨迹当权威 Agent session/task 身份。原样透传保持字节与结束/取消；append_body 显式开启才解析覆盖。
队列溢出 drop_capture，报告缺口；不能静默宣称完整。认证日志脱敏，正文按配置保存，artifacts/ 不进 Git。
一个进程对应一个实际模型，tokenizer 启动可选加载一次，缺失计数为 unknown 而非 0；不在线下载或运行远程代码。
历史 agent_observation.* schema 标识保留以兼容采集格式，不代表旧模块启动路径仍有效。
验证运行 `python -m pytest -q tests/test_vllm_gateway.py`，覆盖并发、错误、取消、SSE/null、观测溢出与关闭。
