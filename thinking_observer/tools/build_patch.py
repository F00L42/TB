"""Rebuild the serving patch from the read-only pinned vLLM checkout."""
import argparse
import difflib
from pathlib import Path
import subprocess

VLLM_SHA = "0fc695fc6d1d82e9a5ac6835ac8e4e1c83703665"


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f"patch anchor is not unique: {old[:100]!r}")
    return text.replace(old, new, 1)


def build(upstream):
    sha = subprocess.check_output(["git", "-C", str(upstream), "rev-parse", "HEAD"],
                                  stdin=subprocess.DEVNULL, text=True).strip()
    if sha != VLLM_SHA:
        raise ValueError(f"expected vLLM v0.23.0 {VLLM_SHA}; got {sha}")
    protocol = "vllm/entrypoints/openai/chat_completion/protocol.py"
    serving = "vllm/entrypoints/openai/chat_completion/serving.py"
    sources = {name: (upstream / name).read_text(encoding="utf-8")
               for name in (protocol, serving)}
    updated = dict(sources)
    updated[protocol] = replace_once(sources[protocol],
        "    # --8<-- [end:chat-completion-extra-params]",
        "    enable_thinking_observer: bool = Field(default=False)\n"
        "    # --8<-- [end:chat-completion-extra-params]")
    text = sources[serving]
    text = replace_once(text, "if TYPE_CHECKING:",
        "from thinking_observer.integration import VllmObserver, close_with_response\n\n"
        "if TYPE_CHECKING:")
    text = replace_once(text, "        self.python_tool = None",
        "        self.python_tool = None\n"
        "        self.thinking_observer = VllmObserver.from_env()")
    text = replace_once(text, "        # Schedule the request and get the result generator.",
        "        observation_context = None\n"
        "        # Schedule the request and get the result generator.")
    old = '''                generator = self.engine_client.generate(
                    engine_input,
                    sampling_params,
                    sub_request_id,
                    lora_request=lora_request,
                    trace_headers=trace_headers,
                    priority=request.priority,
                    data_parallel_rank=data_parallel_rank,
                    reasoning_ended=reasoning_ended,
                    reasoning_parser_kwargs={
                        "chat_template_kwargs": chat_template_kwargs,
                    }
                    if reasoning_parser
                    else None,
                )'''
    new = '''                engine_kwargs = dict(
                    lora_request=lora_request,
                    trace_headers=trace_headers,
                    priority=request.priority,
                    data_parallel_rank=data_parallel_rank,
                    reasoning_ended=reasoning_ended,
                    reasoning_parser_kwargs={
                        "chat_template_kwargs": chat_template_kwargs,
                    } if reasoning_parser else None,
                )
                if (self.thinking_observer.enabled
                        and request.enable_thinking_observer):
                    response_parser = (
                        self.parser_cls(tokenizer, request.tools,
                                        chat_template_kwargs=chat_template_kwargs)
                        if self.parser_cls is not None else None
                    )
                    observation_context = self.thinking_observer.prepare(
                        request=request, prompt=engine_input, params=sampling_params,
                        engine=self.engine_client, reasoning_ended=reasoning_ended,
                        tokenizer=tokenizer, reasoning_parser=reasoning_parser,
                        response_parser=response_parser, request_id=sub_request_id,
                        model=model_name, max_model_len=max_model_len,
                        data_parallel_rank=data_parallel_rank,
                    )
                if observation_context is not None:
                    generator = self.thinking_observer.generate(
                        self.engine_client, engine_input, sampling_params,
                        observation_context, engine_kwargs, stream=request.stream,
                    )
                else:
                    generator = self.engine_client.generate(
                        engine_input, sampling_params, sub_request_id, **engine_kwargs,
                    )'''
    text = replace_once(text, old, new)
    text = replace_once(text, "            return self.chat_completion_stream_generator(",
                        "            response = self.chat_completion_stream_generator(")
    old = '''        return await self.chat_completion_full_generator(
            request,
            result_generator,
            request_id,
            model_name,
            conversation,
            tokenizer,
            request_metadata,
            parser,
        )'''
    new = '''            if observation_context is not None:
                return close_with_response(response, result_generator)
            return response

        try:
            return await self.chat_completion_full_generator(
                request,
                result_generator,
                request_id,
                model_name,
                conversation,
                tokenizer,
                request_metadata,
                parser,
            )
        finally:
            if observation_context is not None:
                await result_generator.aclose()'''
    text = replace_once(text, old, new)
    # Beam requests remain native, with a visible reason when SDK was requested.
    text = replace_once(text, "            if isinstance(sampling_params, BeamSearchParams):",
        "            if isinstance(sampling_params, BeamSearchParams):\n"
        "                if (self.thinking_observer.enabled\n"
        "                        and request.enable_thinking_observer):\n"
        "                    logger.warning(\n"
        "                        \"THINKING_OBSERVER_FALLBACK request_id=%s reason=beam_search\",\n"
        "                        sub_request_id,\n"
        "                    )")
    updated[serving] = text
    for name, source in updated.items():
        compile(source, name, "exec")
    return "".join("".join(difflib.unified_diff(
        sources[name].splitlines(keepends=True), updated[name].splitlines(keepends=True),
        fromfile="a/" + name, tofile="b/" + name,
    )) for name in sources)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("upstream", type=Path)
    args = parser.parse_args()
    target = Path(__file__).resolve().parents[1] / "patches/vllm-v0.23.0/observer_serving.patch"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(build(args.upstream), encoding="utf-8", newline="\n")
    print(target)
