# System 身份与原生分流

当前实现来自 `34f39983`，已合入静态优化分支。部署见[手册](dtb_system_routing_deployment.md)。
目的：Agent 多轮请求按稳定身份选择预算或 native，避免扫描全部历史正文以及辅助请求误用预算。

`prepare_request_route()` 在 serving 中仅调用一次，返回配置/route/threshold/text-view 快照。
native 直接进入 vLLM 原生生成；DTB handle 复用快照。native 不创建 DTB context、队列、parser 副本或预算状态机。

优先级：服务/请求关闭 → native；有效 fixed_budget_tokens → DTB 固定预算；
否则按 scene action，拒识按 ROUTING.on_unmatched。单字段 fixed 覆盖不能使 native 变为 DTB。
默认 legacy_text、on_unmatched=budget 保留既有行为。

system_prefix 从原始 messages 提取身份，最多检查 4096 个 role，system 前缀 512 字符，
首 user 前缀 256 字符；精确匹配，不去空格/BOM、不归一化大小写。规则启动编译，重叠前缀启动即报错。
只按 match.system_prefix、可选 first_user_prefix/tools_empty 匹配，不搜索历史工具正文。
system 模式要求 scene budget 开启、history/difficulty/scene_override 关闭。

phase 模式记录一次 DTB_ROUTE（意图），native 合法无 DTB phase。事件丢失不能当作 native；
路由事件也不能证明响应成功。回归覆盖短/超长 messages、空 tools、fixed 优先级、一次准备和 native bypass。
入口为 `config/routing.py`、`budgeting/scene_router.py`、`request_controller.py` 和版本化 serving patch。
