"""Behavioral checks for routing and non-stream response CPU changes."""
from __future__ import annotations

import asyncio
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace

import pytest

from dtb.budgeting.scene_router import (
    _compile_keyword, _matches, _structural_scores, classify_scene,
)
from dtb.config.loader import load_dtb_service_config
from dtb.integration.vllm.response_adapter import aggregate_queue
from test_dtb_state_machine import result


def legacy_matches(text, keyword):
    """Original rule from f342e127; keep independent of compiled-rule helpers."""
    keyword = str(keyword).strip()
    if not keyword:
        return False
    if re.search(r"[\u3400-\u9fff]", keyword) or not any(c.isalnum() for c in keyword):
        return keyword.casefold() in text.casefold()
    phrase = r"\s+".join(re.escape(part) for part in keyword.split())
    if keyword[0].isalnum():
        phrase = r"(?<![\w])" + phrase
    if keyword[-1].isalnum():
        phrase += r"(?![\w])"
    return re.search(phrase, text, flags=re.IGNORECASE) is not None


@pytest.mark.parametrize("text", [
    "", "search searchable _search search_", "SEARCH\tIndex\nnext",
    "İıſK Straße STRASSE 中文SS中文", "中文 模板：() [x] +++",
    "a.b C++ foo_bar abc123 _count 123", "## Heading\n step\t1\n step2",
])
def test_matching_keeps_unicode_whitespace_symbols_and_boundaries(text):
    keywords = ["", "  ", "search", "search index", "Index\nnext", "中文",
                "SS中文", "İ", "i", "s", "k", "straße", "STRASSE", "a.b",
                "C++", "()", "[x]", "+++", "foo_bar", "123", "## Heading"]
    for keyword in keywords:
        expected = legacy_matches(text, keyword)
        assert _matches(text, keyword) == expected
        assert _matches(text, keyword, folded_text=text.casefold()) == expected


def test_route_preserves_original_keywords_duplicates_and_config_replacement():
    config = load_dtb_service_config()
    scene = {"typical_tokens": 17, "token_range": [5, 25], "indicators": {
        "required_all": [" 中文 ", " 中文 "], "required_any": ["MISSING", "中文"],
        "strong": ["search", "search"], "weak": [" 中文 "]}}
    one = replace(config, scenes={"general": {}, "custom": scene}, scene_priority=("custom",))
    routed = classify_scene("中文 search", service_config=one)
    assert routed["required_matches"]["custom"]["all"] == [" 中文 ", " 中文 "]
    assert routed["strong_matches"]["custom"] == ["search", "search"]
    assert routed["scores"]["custom"] == 107.0
    assert routed["token_range"] == [5, 25]
    two = replace(one, scenes={"general": {}, "custom": {"indicators": {
        "required_all": ["other"]}}})
    assert classify_scene("中文 search", service_config=two)["scene"] == "general"
    routed["required_matches"]["custom"]["all"].clear()
    assert classify_scene("中文 search", service_config=one)["scores"]["custom"] == 107.0


def test_long_prompt_is_casefolded_once_and_structural_scores_saturate():
    class CountedText(str):
        folds = 0

        def casefold(self):
            self.folds += 1
            return super().casefold()

    text = CountedText("中文 ABC step1\nx_count\ny_count\n```x```\n" * 1000)
    classify_scene(text)
    assert text.folds == 1
    scores = _structural_scores(text)
    assert scores["planning"] == [("structure:numbered_steps", 6.0)]
    assert scores["statistics_aggregation"] == [("structure:count_fields", 3.0)]
    assert scores["code_generation"] == [("structure:code_block", 4.0)]


def test_concurrent_cold_cache_routes_keep_independent_results():
    texts = ["请规划并检索信息", "searchable", "def sample():\n pass", "step1 step2",
             "NextOperationRecommendation\nCheckedInformation"] * 16
    expected = [classify_scene(text) for text in texts]
    _compile_keyword.cache_clear()
    with ThreadPoolExecutor(max_workers=8) as pool:
        actual = list(pool.map(classify_scene, texts))
    assert actual == expected
    actual[0]["scores"].clear()
    assert actual[5] == expected[5]
    for index in range(2100):
        _compile_keyword(f"keyword{index}")
    assert _compile_keyword.cache_info().currsize <= 2048


def test_concurrent_aggregates_keep_text_ids_terminal_and_first_object():
    async def request(index):
        queue = asyncio.Queue()
        context = SimpleNamespace(orchestration_error=None)
        chunks = [result(f"{index}:开始", [index], finish_reason=None)]
        for i in range(200):
            chunks.append(result("字" if i % 2 else "", [i], finish_reason=None))
        chunks += [result("text-only", [], finish_reason=None),
                   result("终", [], finish_reason="length", stop_reason="limit"),
                   result("", [], finish_reason=None)]
        expected_text = "".join(item.outputs[0].text for item in chunks)
        expected_ids = [token for item in chunks for token in item.outputs[0].token_ids]
        following_before = [(c.outputs[0].text, list(c.outputs[0].token_ids)) for c in chunks[1:]]

        async def produce():
            for chunk in chunks:
                await queue.put(chunk)
                await asyncio.sleep(0)
            await queue.put(None)

        producer = asyncio.create_task(produce())
        values = [item async for item in aggregate_queue(queue, context)]
        await producer
        assert len(values) == 1 and values[0] is chunks[0]
        output = values[0].outputs[0]
        assert output.text == expected_text and output.token_ids == expected_ids
        assert (output.finish_reason, output.stop_reason) == ("length", "limit")
        assert values[0].finished is True
        assert [(c.outputs[0].text, c.outputs[0].token_ids) for c in chunks[1:]] == following_before

    async def run():
        await asyncio.gather(*(request(i) for i in range(32)))

    asyncio.run(run())


@pytest.mark.parametrize("empty", [False, True])
@pytest.mark.parametrize("error", [False, True])
def test_aggregate_empty_default_terminal_and_error(empty, error):
    async def run():
        queue = asyncio.Queue()
        if not empty:
            queue.put_nowait(result("partial", [1], finish_reason=None))
        queue.put_nowait(None)
        values = []
        context = SimpleNamespace(orchestration_error="engine" if error else None)
        if error:
            with pytest.raises(RuntimeError, match="DTB orchestration failed"):
                async for item in aggregate_queue(queue, context):
                    values.append(item)
            assert values == []
        else:
            values = [item async for item in aggregate_queue(queue, context)]
            if empty:
                assert values == [None]
            else:
                assert values[0].outputs[0].finish_reason == "stop"

    asyncio.run(run())


def test_cancelling_aggregate_does_not_yield_partial_success():
    async def run():
        queue = asyncio.Queue()
        queue.put_nowait(result("partial", [1], finish_reason=None))
        values = []

        async def consume():
            async for item in aggregate_queue(queue, SimpleNamespace(orchestration_error=None)):
                values.append(item)

        task = asyncio.create_task(consume())
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert values == []

    asyncio.run(run())
