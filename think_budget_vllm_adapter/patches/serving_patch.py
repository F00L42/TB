"""
vLLM v0.20.2 — serving.py 补丁说明 (Budget Controller 版)

文件: vllm/entrypoints/openai/chat_completion/serving.py

=== 修改0: 顶部导入 (约第80行附近，在 from deer.cot_controller import CoTController 之后) ===
添加:
    from think_budget_vllm_adapter import BudgetController  # noqa: F401

注意: 必须使用 "from think_budget_vllm_adapter import BudgetController" 而非
      "from think_budget_vllm_adapter.budget_controller import BudgetController"。
      原因: __init__.py 中预导入了内嵌的 deer_think_budget 子模块，
      必须先执行 __init__.py 才能保证后续子模块导入正常。

=== 旧版修改1 (已弃用) ===
    from think_budget_vllm_adapter.budget_controller import BudgetController


=== 修改2: __init__ 方法中初始化 (约第178行附近，在 self.supports_code_interpreter = False 之后) ===
添加:
    self.budget_controller = BudgetController()

  如果已有 deer_think_budget 的 CoTController 初始化:
    self.think_budget_controller = ThinkBudgetController()
  则保留两者共存，Budget 分流优先于 CoT 分流。


=== 修改3: create_chat_completion 方法中添加 Budget 分流逻辑 ===
位置: 在 request_metadata 构建之后、lora_request 获取之前
      (约第264行 "if raw_request:" 块之后)

原始代码:
    request_metadata = RequestResponseMetadata(request_id=request_id)
    if raw_request:
        raw_request.state.request_metadata = request_metadata

    lora_request = self._maybe_get_adapters(request, supports_default_mm_loras=True)
    model_name = self.models.model_name(lora_request)

修改为:
    request_metadata = RequestResponseMetadata(request_id=request_id)
    if raw_request:
        raw_request.state.request_metadata = request_metadata

    # ====== Budget 决策链分流 (最高优先级) ======
    if getattr(request, 'budget_disabled', False) is not True and getattr(request, 'budget_token_limit', 0) > 0:
        logger.info(f"Request {request_id} is using Budget Controller (fixed budget mode).")
        tokenizer = self.renderer.tokenizer
        assert tokenizer is not None
        return await self.budget_controller.handle_request(
            self, conversation, engine_inputs,
            request, request_id, tokenizer, request_metadata)
    # ====== END Budget 决策链分流 ======

    # ====== Think Budget 分流 (次优先级) ======
    if request.enable_think_budget:
        logger.info(f"Request {request_id} is using Think Budget Controller.")
        tokenizer = self.renderer.tokenizer
        assert tokenizer is not None
        return await self.think_budget_controller.handle_request(
            self, conversation, engine_inputs,
            request, request_id, tokenizer, request_metadata)
    # ====== END Think Budget 分流 ======

    lora_request = self._maybe_get_adapters(request, supports_default_mm_loras=True)
    model_name = self.models.model_name(lora_request)


=== 注意事项 ===
1. Budget Controller 分流优先于 Think Budget (CoT) 分流
2. Budget Controller 默认不启用 — 需要 budget_token_limit > 0 或 BUDGET_ENABLED=True
3. Budget Controller 包含完整决策链 (任务类型→难度→历史→参数映射)
4. Think Budget (CoT) Controller 只使用请求参数中的固定值
5. 两者可以共存: Budget 用于高级场景，CoT 用于简单场景
6. tokenizer 在分流分支中单独获取 (self.renderer.tokenizer)
7. engine_inputs 是 list[EngineInput] 类型 (新版 vLLM)


=== Budget Controller vs CoT Controller 对比 ===

| 特性                | Budget Controller                | CoT Controller           |
|---------------------|----------------------------------|--------------------------|
| 决策链              | 有 (任务类型→难度→历史→参数)    | 无 (直接使用请求参数)   |
| config 类           | BudgetConfig (扩展 ThinkBudgetConfig) | ThinkBudgetConfig    |
| context 类          | BudgetRequestContext (扩展 RequestStateContext) | RequestStateContext |
| 状态机              | 复用 deer_think_budget AsyncReasoningOrchestrator | 同左 |
| 启用条件            | budget_token_limit>0 或 BUDGET_ENABLED | enable_think_budget |
| 动态参数            | 根据prompt内容自动调整          | 全部由请求参数指定     |
