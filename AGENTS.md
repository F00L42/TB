# Thinking Observer SDK 交付指南

先读根 [README](README.md)、[RESULT](RESULT.md)，再读 [SDK 导航](thinking_observer/AGENTS.md)。
本分支交付观察与受控续写实现，压缩算法可直接在 observer 源码中开发；
`codex/dtb-static-refactor` 的 DTB 是另一套集成方案，两套 serving patch 不叠加。
旧算法、API 转发、LightCode、代理与实验树已退出当前树，历史设计/材料可按[来源索引](thinking_observer/docs/history.md)查阅；不要恢复成当前入口。

## 修改与所有权

- SDK 源码、文档、示例、协议测试、独立 patch 和验证工具均在 `thinking_observer/`。SDK 运行时不依赖 DTB、DEER、vLLM、torch 或 YAML。
- 压缩算法可以直接侵入式修改 `detectors.py`、`controller.py`、`integration.py`；不要求独立外部包或仅通过 detector 回调扩展。下述回调约束适用于现有 detector 接口；直接修改 controller 时继续保持请求、parser、取消与额度语义。
- 每请求 `start(context)` 创建独立 detector state；同请求同步有序 `observe(delta)`；最后 `close(outcome)` 一次。factory 不保存请求文本或窗口。
- 读取 `ReasoningDelta.text/token_ids`，按权威 token ID 做有界增量处理；不要重分词文本伪造原 ID，不假定 token 与字符一一对应。
- 观察回调不持有 engine/parser，不做 I/O、联网或模型调用。回调耗时限制是返回后的软检查，不是硬抢占。
- `StopAndAnswer` 只表达停止与可选 cue 意图；不撤回已发内容，不按 `repeat_start` 回剪历史。框架保留完整已接受 batch 与 abort/drain 尾部。
- 自然 stop/length/tool 边界优先，取消、parser/engine/额度错误传播；不得包装为成功或另启请求掩盖失败。
- 内部 child 保留 priority、LoRA、trace、显式 DP rank、parser 上下文和完整 token prompt；可变状态只属于一个请求。
- 服务开关、factory、配置启动时加载，变更后重启；不支持能力在 detector 启动与 engine 提交前回退原生。

## 版本、部署与验证

生产 patch 唯一来源是 `thinking_observer/patches/vllm-v0.23.0/`，直接向目标 vLLM 0.23 部署源码 `git apply`。
不要求本地完整验收，不初始化或检查 `third_party/`；其中 gitlink 只保留来源信息。不要把上游完整实现复制进 SDK。
vLLM 升级按 [SDK 接口映射](thinking_observer/AGENTS.md)逐项比较，patch 可应用不代表新版本语义已适配。

本地检查按改动范围选择，命令见[验证说明](thinking_observer/docs/validation.md)。单测、观察示例、patch、
原生源码 parser/serving、wheel/sdist 与隔离安装工具均为可选维护工具，不是日常集成前置。
删除数据不能使 fixture 静默 skip。Windows 非交互 Python 子进程显式使用 `stdin=subprocess.DEVNULL`。
源码与结构化文档使用 UTF-8/LF；patch 空白上下文按包内 `.gitattributes` 保留。

本机无模型/推理设备。区分本地实测、源码确定和假设；本地 fake/冻结源码执行不代表实模型、HTTP/SSE、质量或性能。
真实模型、EngineCore 取消、DP/LoRA/trace、Ascend、质量和净 E2E 收益未执行时写 `pending_remote`。
同模型/版本/输入/采样/并发/cache 比较，不沿用旧 DTB baseline；重复检测算法及其效果不是本 SDK 现成功能。

## 产物与交付

运行产物写 `artifacts/perf/<run_id>/` 或 `artifacts/replay/<run_id>/`，发行验证写 `artifacts/`，均默认不进 Git。
保留 manifest、源码/patch/配置/model 身份、请求响应、客户端质量/耗时、诊断与 RESULT；正文/token 仅用于限定的排障采样。
不要递归删除 junction 或未归档的唯一产物，不改其他工作区的 staged/unstaged 内容。
修改行为、默认或结论后同步当前文档与 runbook；推送前分别固定本分支最终 SHA 与未覆盖范围。
