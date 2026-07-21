"""vLLM 0.20.2 serving.py routing notes for the Think Budget controller.

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
# self.budget_controller = BudgetController()


# 3. OpenAIServingChat.create_chat_completion, directly after request_metadata
# is assigned.  Replace the original lora/model block with the following and
# keep the remainder of the original method unchanged.
SERVING_ROUTING_PATCH = '''
    lora_request = self._maybe_get_adapters(
        request, supports_default_mm_loras=True)
    model_name = self.models.model_name(lora_request)

    data_parallel_rank = self._get_data_parallel_rank(raw_request)

    # BUDGET_ENABLED 是服务端总开关。关闭时，即使请求带有
    # enable_think_budget=true 也必须直接回到原生 vLLM 路径，不能创建
    # Think Budget 会话、子请求或占用其并发槽位。
    if request.enable_think_budget and self.budget_controller.is_enabled():
        print(
            f"TB_ROUTE event=selected request_id={request_id} controller=think_budget",
            flush=True)
        tokenizer = self.renderer.tokenizer
        assert tokenizer is not None
        # 固定为旧版 7 参数 ABI，避免 serving.py 与已部署适配器不同步时
        # 在请求路由后发生 TypeError。该属性只在服务端进程内写入。
        object.__setattr__(request, "_think_budget_route_identity", (
            model_name, lora_request, data_parallel_rank))
        return await self.budget_controller.handle_request(
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
均为服务端内部实现，不提供请求级覆盖或续接字段。
"""
