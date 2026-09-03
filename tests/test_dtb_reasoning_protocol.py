from __future__ import annotations

import unittest
from types import SimpleNamespace

from dtb.config.schema import (
    DtbReasoningProtocolConfig,
    ReasoningProtocolAdapter,
)
from dtb.reasoning.parser_boundary import (
    bind_reasoning_boundary_protocol,
    configure_thinking_stop,
    detect_reasoning_boundary,
)
from dtb.reasoning.request_state import ReasoningBoundary


THINK_END_ID = 248069
TOOL_CALL_BEGIN_ID = 248058


def reasoning_parser(*, end_token_id: int = THINK_END_ID):
    parser_type = type("Qwen3ReasoningParser", (), {})
    parser = parser_type()
    parser.end_token_id = end_token_id
    parser.is_reasoning_end_streaming = lambda previous, current: bool(
        current
        and current[-1] in {THINK_END_ID, TOOL_CALL_BEGIN_ID}
    )
    return parser


def tool_parser(*, start_token_id: int = TOOL_CALL_BEGIN_ID):
    parser_type = type("Qwen3CoderToolParser", (), {})
    parser = parser_type()
    parser.tool_call_start_token_id = start_token_id
    return parser


def config() -> DtbReasoningProtocolConfig:
    return DtbReasoningProtocolConfig(
        adapter=ReasoningProtocolAdapter.QWEN3,
        think_end_token_id=THINK_END_ID,
        tool_call_begin_token_id=TOOL_CALL_BEGIN_ID,
    )


def bind_protocol(
    *,
    request_reasoner=None,
    unified_reasoner=None,
    tools=None,
    vocab: dict[str, int] | None = None,
):
    request_reasoner = request_reasoner or reasoning_parser()
    unified_reasoner = unified_reasoner or reasoning_parser()
    tools = tools or tool_parser()
    tokenizer = SimpleNamespace(
        get_vocab=lambda: vocab or {
            "</think>": THINK_END_ID,
            "<tool_call>": TOOL_CALL_BEGIN_ID,
        }
    )
    response_parser = SimpleNamespace(
        reasoning_parser=unified_reasoner,
        tool_parser=tools,
    )
    return bind_reasoning_boundary_protocol(
        config(), tokenizer, request_reasoner, response_parser
    )


def stream_context(protocol):
    return SimpleNamespace(
        request=SimpleNamespace(stream=True),
        require_reasoning_protocol=lambda: protocol,
        thinking_stop_token_ids=(),
        thinking_stop_strings=(),
        thinking_include_stop_str_in_output=None,
    )


class ReasoningProtocolBindingTests(unittest.TestCase):
    def test_qwen_protocol_binds_config_to_both_native_parsers(self) -> None:
        protocol = bind_protocol()

        self.assertIs(protocol.adapter, ReasoningProtocolAdapter.QWEN3)
        self.assertEqual(
            protocol.natural_boundary_token_ids,
            (THINK_END_ID, TOOL_CALL_BEGIN_ID),
        )
        self.assertEqual(protocol.forced_close_token_ids, (THINK_END_ID,))
        self.assertEqual(protocol.forced_close_text, "")
        self.assertTrue(protocol.tokenizer_class.endswith("SimpleNamespace"))
        self.assertTrue(
            protocol.reasoning_parser_class.endswith("Qwen3ReasoningParser")
        )
        self.assertTrue(
            protocol.tool_parser_class.endswith("Qwen3CoderToolParser")
        )

    def test_binding_fails_closed_on_tokenizer_or_parser_mismatch(self) -> None:
        cases = {
            "tokenizer": {
                "vocab": {"</think>": THINK_END_ID},
            },
            "request_reasoner": {
                "request_reasoner": reasoning_parser(end_token_id=7),
            },
            "unified_reasoner": {
                "unified_reasoner": reasoning_parser(end_token_id=7),
            },
            "tool_parser": {
                "tools": tool_parser(start_token_id=7),
            },
        }
        for name, kwargs in cases.items():
            with self.subTest(name=name), self.assertRaises(ValueError):
                bind_protocol(**kwargs)

    def test_cached_tokenizer_binding_skips_rebuilding_vocab_ids(self) -> None:
        tokenizer = SimpleNamespace(
            get_vocab=lambda: (_ for _ in ()).throw(
                AssertionError("cached tokenizer vocabulary was re-read")
            )
        )
        response_parser = SimpleNamespace(
            reasoning_parser=reasoning_parser(),
            tool_parser=tool_parser(),
        )

        protocol = bind_reasoning_boundary_protocol(
            config(),
            tokenizer,
            reasoning_parser(),
            response_parser,
            validate_tokenizer_vocab=False,
        )

        self.assertEqual(
            protocol.natural_boundary_token_ids,
            (THINK_END_ID, TOOL_CALL_BEGIN_ID),
        )

    def test_cached_tokenizer_binding_skips_rebuilding_vocab_ids(self) -> None:
        tokenizer = SimpleNamespace(
            get_vocab=lambda: (_ for _ in ()).throw(
                AssertionError("cached tokenizer vocabulary was re-read")
            )
        )
        response_parser = SimpleNamespace(
            reasoning_parser=reasoning_parser(),
            tool_parser=tool_parser(),
        )

        protocol = bind_reasoning_boundary_protocol(
            config(),
            tokenizer,
            reasoning_parser(),
            response_parser,
            validate_tokenizer_vocab=False,
        )

        self.assertEqual(
            protocol.natural_boundary_token_ids,
            (THINK_END_ID, TOOL_CALL_BEGIN_ID),
        )


class StreamingBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.protocol = bind_protocol()
        self.context = stream_context(self.protocol)

    def test_controller_ids_merge_with_native_post_init_stop_state(self) -> None:
        params = SimpleNamespace(
            stop=["USER_STOP"],
            stop_token_ids=[77],
            _all_stop_token_ids={77, 88},
            include_stop_str_in_output=False,
            min_tokens=9,
        )

        configure_thinking_stop(params, self.context)

        self.assertEqual(params.stop, ["USER_STOP"])
        self.assertEqual(
            params.stop_token_ids,
            [77, THINK_END_ID, TOOL_CALL_BEGIN_ID],
        )
        self.assertEqual(
            params._all_stop_token_ids,
            {77, 88, THINK_END_ID, TOOL_CALL_BEGIN_ID},
        )
        self.assertFalse(params.include_stop_str_in_output)
        self.assertEqual(params.min_tokens, 0)
        self.assertEqual(
            self.context.thinking_stop_token_ids,
            (77, THINK_END_ID, TOOL_CALL_BEGIN_ID),
        )
        self.assertEqual(self.context.thinking_stop_strings, ("USER_STOP",))

    def test_invalid_native_stop_cache_fails_before_public_mutation(self) -> None:
        params = SimpleNamespace(
            stop=[],
            stop_token_ids=[77],
            _all_stop_token_ids=(77,),
            include_stop_str_in_output=False,
            min_tokens=0,
        )

        with self.assertRaisesRegex(RuntimeError, "not a mutable set"):
            configure_thinking_stop(params, self.context)

        self.assertEqual(params.stop_token_ids, [77])
        self.assertEqual(params._all_stop_token_ids, (77,))

    def test_token_only_and_marker_text_boundaries_classify_identically(self) -> None:
        for text in ("", "</think>"):
            with self.subTest(text=text):
                boundary = detect_reasoning_boundary(
                    self.context,
                    text,
                    [THINK_END_ID],
                    THINK_END_ID,
                )
                self.assertIs(boundary, ReasoningBoundary.THINK_END)

    def test_literal_split_marker_text_is_not_a_structural_boundary(self) -> None:
        self.assertIsNone(detect_reasoning_boundary(
            self.context,
            "quoted </thi",
            [101, 102],
            None,
        ))
        self.assertIsNone(detect_reasoning_boundary(
            self.context,
            "nk>",
            [103],
            None,
        ))

    def test_tool_token_classifies_without_marker_text(self) -> None:
        boundary = detect_reasoning_boundary(
            self.context,
            "",
            [TOOL_CALL_BEGIN_ID],
            TOOL_CALL_BEGIN_ID,
        )
        self.assertIs(boundary, ReasoningBoundary.TOOL_CALL)

    def test_boundary_token_without_integer_stop_reason_fails_closed(self) -> None:
        with self.assertRaisesRegex(
            RuntimeError, "without matching integer stop_reason"
        ):
            detect_reasoning_boundary(
                self.context,
                "",
                [THINK_END_ID],
                None,
            )

    def test_boundary_must_be_the_final_source_token(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "not the final source token"):
            detect_reasoning_boundary(
                self.context,
                "",
                [THINK_END_ID, 123],
                THINK_END_ID,
            )


if __name__ == "__main__":
    unittest.main()
