"""vLLM 0.23.0 serving.py routing notes for the Think Budget controller.

The authoritative combined patch is ``../../dtb.patch``.  Apply its route as
one unit: DEER and Think Budget are separate controllers, and Think Budget
takes precedence only when both flags are accidentally set on one request.
Adapters, data-parallel rank and the resolved model name must be obtained
before routing, otherwise child requests lose their engine identity.
"""

# 1. Imports (near other serving imports)
from deer.cot_controller import CoTController  # noqa: F401
from think_budget_vllm_adapter import BudgetController  # noqa: F401


# 2. OpenAIServingChat.__init__
# self.cot_controller = CoTController()
# self.think_budget_controller = BudgetController()


# 3. OpenAIServingChat._create_chat_completion, after lora/model/DP routing is
# resolved and before native requests are scheduled. ``reasoning_parser`` and
# ``chat_template_kwargs`` already exist at this point in v0.23.0.
SERVING_ROUTING_PATCH = '''
    # BUDGET_ENABLED 是服务端总开关。关闭时，即使请求带有
    # enable_think_budget=true 也必须直接回到原生 vLLM 路径，不能创建
    # Think Budget 会话、子请求或占用其并发槽位。
    if request.enable_think_budget and self.think_budget_controller.is_enabled():
        print(
            f"TB_ROUTE event=selected request_id={request_id} controller=think_budget",
            flush=True)
        tokenizer = self.renderer.tokenizer
        assert tokenizer is not None
        assert len(engine_inputs) == 1
        prompt_components = self._extract_prompt_components(engine_inputs[0])
        trace_headers = (
            None if raw_request is None
            else await self._get_trace_headers(raw_request.headers)
        )
        if not request.include_reasoning or request._grammar_from_tool_parser:
            reasoning_ended = True
        elif reasoning_parser:
            reasoning_ended = reasoning_parser.is_reasoning_end(
                prompt_components.token_ids or [])
        else:
            reasoning_ended = None

        response_parser = None
        if self.parser_cls is not None:
            response_parser = self.parser_cls(
                tokenizer,
                request.tools,
                chat_template_kwargs=chat_template_kwargs,
            )

        # 固定为旧版 7 参数 ABI；新增上下文只存在服务进程内，不进入 HTTP
        # 模型，也不会因控制器与 serving.py 滚动升级而造成位置参数错位。
        object.__setattr__(request, "_think_budget_route_identity", {
            "model_name": model_name,
            "lora_request": lora_request,
            "data_parallel_rank": data_parallel_rank,
            "trace_headers": trace_headers,
            "prompt_text": prompt_components.text,
            "reasoning_ended": reasoning_ended,
            "reasoning_parser_kwargs": (
                {"chat_template_kwargs": chat_template_kwargs}
                if reasoning_parser else None
            ),
            "reasoning_parser": reasoning_parser,
            "response_parser": response_parser,
            "chat_template_kwargs": chat_template_kwargs,
        })
        return await self.think_budget_controller.handle_request(
            self, conversation, engine_inputs, request, request_id, tokenizer,
            request_metadata)

    if request.enable_deer and not request.enable_think_budget:
        logger.info("Request %s is using DEER CoT Controller.", request_id)
        return await self.cot_controller.handle_request(
            self, conversation, engine_inputs, request, request_id, tokenizer,
            request_metadata, model_name, lora_request, data_parallel_rank)

    # Do not repeat _maybe_get_adapters/model_name below this block.
'''


NOTES = """
控制器接收已解析的模型名（包括 LoRA 选择），并仅在
enable_think_budget=true 时进入 Think Budget 路由。动态策略与会话管理
均为服务端内部实现，不提供请求级覆盖或续接字段。每个 child generate
复用 priority、LoRA、trace headers、DP rank 和 reasoning/structured-output
上下文；外层响应继续使用原生流式 reasoning parser、完整响应 parser 与
chat template kwargs。
"""
