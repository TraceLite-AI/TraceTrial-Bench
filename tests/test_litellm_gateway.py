from __future__ import annotations

import asyncio
from collections import deque

import pytest

from tracetrial_bench.litellm_gateway import (
    build_gateway_config,
    flatten_responses_namespace_tools,
    install_litellm_anthropic_reasoning_stream_compat,
    install_litellm_namespace_tool_compat,
    install_litellm_responses_input_sanitizer,
    normalize_tool_call_messages,
    qualify_namespaced_function_call,
    restore_namespace_on_response_calls,
    restore_namespace_on_stream_events,
    sanitize_chat_completion_messages,
)
from tracetrial_bench.model_registry import ModelRuntimeSpec


def _runtime_model(*, disable_web_search: bool = False) -> ModelRuntimeSpec:
    return ModelRuntimeSpec(
        alias="test",
        provider="test-provider",
        model="vendor/model",
        base_url="https://model.example/v1",
        api_mode="chat_completions",
        harness="codex",
        harness_model="vendor/model",
        harness_provider=None,
        api_key="test-secret",
        litellm_disable_web_search=disable_web_search,
    )


@pytest.mark.parametrize("empty_content", ["", []])
def test_tool_call_only_empty_content_is_normalized_to_null(
    empty_content: object,
) -> None:
    messages = [
        {
            "role": "assistant",
            "content": empty_content,
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "ping", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call-1", "content": "ok"},
    ]

    normalized = normalize_tool_call_messages(messages)

    assert normalized[0]["content"] is None
    assert messages[0]["content"] == empty_content
    assert normalized[1] is messages[1]


def test_tool_call_text_content_parts_are_normalized_to_string() -> None:
    messages = [
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "I will "},
                {"type": "text", "text": "use the tool."},
            ],
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "ping", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call-1", "content": "ok"},
    ]

    normalized = normalize_tool_call_messages(messages)

    assert normalized[0]["content"] == "I will use the tool."
    assert messages[0]["content"] == [
        {"type": "text", "text": "I will "},
        {"type": "text", "text": "use the tool."},
    ]


def test_tool_call_non_text_content_parts_are_preserved() -> None:
    content = [
        {"type": "text", "text": "Inspecting image"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}},
    ]
    message = {
        "role": "assistant",
        "content": content,
        "tool_calls": [
            {
                "id": "call-1",
                "type": "function",
                "function": {"name": "inspect", "arguments": "{}"},
            }
        ],
    }

    normalized = normalize_tool_call_messages([message])

    assert normalized == [message]
    assert normalized[0] is message


@pytest.mark.parametrize("empty_content", [None, "", []])
def test_empty_assistant_placeholder_without_tool_calls_is_removed(
    empty_content: object,
) -> None:
    messages = [
        {"role": "user", "content": "before"},
        {"role": "assistant", "content": empty_content},
        {"role": "user", "content": "after"},
    ]

    normalized = normalize_tool_call_messages(messages)

    assert normalized == [messages[0], messages[2]]


def test_empty_non_assistant_or_metadata_bearing_message_is_preserved() -> None:
    messages = [
        {"role": "user", "content": ""},
        {"role": "assistant", "content": "", "reasoning_content": "kept"},
    ]

    normalized = normalize_tool_call_messages(messages)

    assert normalized == messages
    assert normalized[0] is messages[0]
    assert normalized[1] is messages[1]


def test_gateway_drops_web_search_only_for_opted_out_model() -> None:
    default_params = build_gateway_config(_runtime_model())["model_list"][0][
        "litellm_params"
    ]
    opted_out_params = build_gateway_config(
        _runtime_model(disable_web_search=True)
    )["model_list"][0]["litellm_params"]

    assert "additional_drop_params" not in default_params
    assert opted_out_params["additional_drop_params"] == ["web_search_options"]


def test_codex_namespace_tools_are_flattened_without_losing_contracts() -> None:
    tools = [
        {
            "type": "function",
            "name": "exec_command",
            "description": "Run a command.",
            "parameters": {"type": "object", "properties": {}},
        },
        {
            "type": "namespace",
            "name": "mcp__harnessbench",
            "description": "Task tools.",
            "tools": [
                {
                    "name": "violet_relay",
                    "description": "Return the protected token.",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "additionalProperties": False,
                    },
                }
            ],
        },
        {
            "type": "namespace",
            "name": "collaboration",
            "tools": [
                {
                    "name": "spawn_agent",
                    "description": "Create a child agent.",
                    "parameters": {
                        "type": "object",
                        "properties": {"task_name": {"type": "string"}},
                        "required": ["task_name"],
                    },
                }
            ],
        },
    ]

    flattened = flatten_responses_namespace_tools(tools)

    assert flattened is not None
    assert [tool["name"] for tool in flattened] == [
        "exec_command",
        "mcp__harnessbench__violet_relay",
        "collaboration__spawn_agent",
    ]
    assert flattened[1]["description"] == "Return the protected token."
    assert flattened[1]["parameters"] == tools[1]["tools"][0]["parameters"]
    assert flattened[2]["parameters"]["required"] == ["task_name"]
    assert tools[1]["type"] == "namespace"


@pytest.mark.parametrize(
    "tools, message",
    [
        ([{"type": "namespace", "name": "group", "tools": []}], "has no tools"),
        (
            [
                {
                    "type": "namespace",
                    "name": "group",
                    "tools": [{"name": "call", "parameters": []}],
                }
            ],
            "invalid schema",
        ),
        (
            [
                {"type": "function", "name": "group__call", "parameters": {}},
                {
                    "type": "namespace",
                    "name": "group",
                    "tools": [{"name": "call", "parameters": {}}],
                },
            ],
            "duplicate flattened",
        ),
    ],
)
def test_namespace_flattening_fails_closed(
    tools: list[dict[str, object]], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        flatten_responses_namespace_tools(tools)


def test_litellm_compat_installer_wraps_transform_once() -> None:
    class FakeResponsesConfig:
        tool_calls = 0
        input_calls = 0
        input_message_calls = 0
        output_calls = 0

        @staticmethod
        def transform_responses_api_tools_to_chat_completion_tools(tools: object) -> object:
            FakeResponsesConfig.tool_calls += 1
            return tools

        @staticmethod
        def _transform_responses_api_function_call_to_chat_completion_message(
            function_call: object,
        ) -> object:
            FakeResponsesConfig.input_calls += 1
            return function_call

        @staticmethod
        def transform_responses_api_input_to_messages(
            input: object,
            responses_api_request: object,
        ) -> object:
            del responses_api_request
            FakeResponsesConfig.input_message_calls += 1
            return input

        @staticmethod
        def transform_chat_completion_tools_to_responses_tools(
            response: object,
            request: object = None,
        ) -> object:
            del request
            FakeResponsesConfig.output_calls += 1
            return response

    class FakeStreamingIterator:
        delta_calls = 0
        final_calls = 0

        def _queue_tool_call_delta_events(self, tool_calls: object) -> None:
            FakeStreamingIterator.delta_calls += 1
            self._pending_tool_events.extend(tool_calls)

        def _queue_final_tool_call_done_events(self, response: object) -> None:
            FakeStreamingIterator.final_calls += 1
            self._pending_tool_events.extend(response)

    install_litellm_namespace_tool_compat(
        FakeResponsesConfig,
        FakeStreamingIterator,
    )
    install_litellm_namespace_tool_compat(
        FakeResponsesConfig,
        FakeStreamingIterator,
    )
    result = FakeResponsesConfig.transform_responses_api_tools_to_chat_completion_tools(
        [
            {
                "type": "namespace",
                "name": "collaboration",
                "tools": [{"name": "spawn_agent", "parameters": {}}],
            }
        ]
    )

    assert FakeResponsesConfig.tool_calls == 1
    assert result[0]["name"] == "collaboration__spawn_agent"

    replayed = FakeResponsesConfig._transform_responses_api_function_call_to_chat_completion_message(
        {"type": "function_call", "namespace": "collaboration", "name": "spawn_agent"}
    )
    assert replayed["name"] == "collaboration__spawn_agent"
    assert FakeResponsesConfig.input_calls == 1

    normalized_messages = FakeResponsesConfig.transform_responses_api_input_to_messages(
        [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": ""},
        ],
        {},
    )
    assert normalized_messages == [{"role": "user", "content": "hello"}]
    assert FakeResponsesConfig.input_message_calls == 1

    restored = FakeResponsesConfig.transform_chat_completion_tools_to_responses_tools(
        [{"type": "function_call", "name": "collaboration__spawn_agent"}],
        {
            "tools": [
                {
                    "type": "namespace",
                    "name": "collaboration",
                    "tools": [{"name": "spawn_agent", "parameters": {}}],
                }
            ]
        },
    )
    assert restored[0]["name"] == "spawn_agent"
    assert restored[0]["namespace"] == "collaboration"
    assert FakeResponsesConfig.output_calls == 1

    stream = FakeStreamingIterator()
    stream.responses_api_request = {
        "tools": [
            {
                "type": "namespace",
                "name": "collaboration",
                "tools": [{"name": "spawn_agent", "parameters": {}}],
            }
        ]
    }
    stream._pending_tool_events = []
    stream._queue_tool_call_delta_events(
        [
            {
                "item": {
                    "type": "function_call",
                    "name": "collaboration__spawn_agent",
                }
            }
        ]
    )
    stream._queue_final_tool_call_done_events(
        [
            {
                "item": {
                    "type": "function_call",
                    "name": "collaboration__spawn_agent",
                }
            }
        ]
    )
    assert FakeStreamingIterator.delta_calls == 1
    assert FakeStreamingIterator.final_calls == 1
    assert stream._pending_tool_events == [
        {
            "item": {
                "type": "function_call",
                "name": "spawn_agent",
                "namespace": "collaboration",
            }
        },
        {
            "item": {
                "type": "function_call",
                "name": "spawn_agent",
                "namespace": "collaboration",
            }
        },
    ]


class _VulnerableAnthropicResponsesStreamWrapper:
    """Small LiteLLM 1.93.0-shaped fixture for the reasoning stream bug."""

    def __init__(self) -> None:
        self._sent_message_start = False
        self._current_block_index = -1
        self._item_id_to_block_index: dict[str, int] = {}
        self._chunk_queue: deque[dict[str, object]] = deque()

    def _next_block_index(self) -> int:
        self._current_block_index += 1
        return self._current_block_index

    def _process_event(self, event: dict[str, object]) -> None:
        event_type = event.get("type")
        if event_type == "response.created":
            self._sent_message_start = True
            self._chunk_queue.append({"type": "message_start"})
            return
        if event_type == "response.output_item.added":
            item = event.get("item")
            if not isinstance(item, dict):
                return
            item_type = item.get("type")
            item_id = item.get("id")
            if item_type in {"message", "reasoning"}:
                block_index = self._next_block_index()
                if isinstance(item_id, str):
                    self._item_id_to_block_index[item_id] = block_index
                content_block = (
                    {"type": "text", "text": ""}
                    if item_type == "message"
                    else {"type": "thinking", "thinking": ""}
                )
                self._chunk_queue.append(
                    {
                        "type": "content_block_start",
                        "index": block_index,
                        "content_block": content_block,
                    }
                )
            return
        if event_type in {
            "response.output_text.delta",
            "response.reasoning_summary_text.delta",
        }:
            item_id = event.get("item_id")
            block_index = (
                self._item_id_to_block_index.get(
                    item_id,
                    self._current_block_index,
                )
                if isinstance(item_id, str)
                else self._current_block_index
            )
            self._chunk_queue.append(
                {
                    "type": "content_block_delta",
                    "index": block_index,
                    "delta": (
                        {"type": "text_delta", "text": event.get("delta", "")}
                        if event_type == "response.output_text.delta"
                        else {
                            "type": "thinking_delta",
                            "thinking": event.get("delta", ""),
                        }
                    ),
                }
            )
            return
        if event_type == "response.output_item.done":
            self._chunk_queue.append(
                {
                    "type": "content_block_stop",
                    "index": self._current_block_index,
                }
            )
            return
        if event_type == "response.completed":
            self._chunk_queue.append({"type": "message_delta"})
            self._chunk_queue.append({"type": "message_stop"})


def _reasoning_first_bridge_events() -> list[dict[str, object]]:
    return [
        {"type": "response.created"},
        {
            "type": "response.output_item.added",
            "item": {"type": "message", "id": "msg_1"},
        },
        {
            "type": "response.reasoning_summary_text.delta",
            "item_id": "reasoning_1",
            "delta": "thinking",
        },
        {
            "type": "response.output_text.delta",
            "item_id": "msg_1",
            "delta": "done",
        },
        {
            "type": "response.output_item.done",
            "item": {"type": "message", "id": "msg_1"},
        },
        {"type": "response.completed"},
    ]


def test_anthropic_reasoning_stream_uses_type_matched_blocks() -> None:
    class StreamWrapper(_VulnerableAnthropicResponsesStreamWrapper):
        pass

    install_litellm_anthropic_reasoning_stream_compat(StreamWrapper)
    wrapper = StreamWrapper()
    for event in _reasoning_first_bridge_events():
        wrapper._process_event(event)
    chunks = list(wrapper._chunk_queue)

    assert [chunk["type"] for chunk in chunks] == [
        "message_start",
        "content_block_start",
        "content_block_delta",
        "content_block_stop",
        "content_block_start",
        "content_block_delta",
        "content_block_stop",
        "message_delta",
        "message_stop",
    ]
    assert chunks[1]["content_block"] == {
        "type": "thinking",
        "thinking": "",
    }
    assert chunks[2]["delta"] == {
        "type": "thinking_delta",
        "thinking": "thinking",
    }
    assert chunks[4]["content_block"] == {"type": "text", "text": ""}
    assert chunks[5]["delta"] == {"type": "text_delta", "text": "done"}
    assert chunks[1]["index"] == chunks[2]["index"] == 0
    assert chunks[4]["index"] == chunks[5]["index"] == 1


def test_anthropic_reasoning_stream_emits_message_start_once() -> None:
    class StreamWrapper(_VulnerableAnthropicResponsesStreamWrapper):
        pass

    install_litellm_anthropic_reasoning_stream_compat(StreamWrapper)
    install_litellm_anthropic_reasoning_stream_compat(StreamWrapper)
    wrapper = StreamWrapper()
    wrapper._sent_message_start = True
    wrapper._chunk_queue.append({"type": "message_start"})
    wrapper._process_event({"type": "response.created"})

    assert list(wrapper._chunk_queue) == [{"type": "message_start"}]


def test_namespace_call_round_trip_helpers_preserve_unrelated_calls() -> None:
    original = {"type": "function_call", "name": "exec_command"}
    assert qualify_namespaced_function_call(original) is original

    calls = [
        {"type": "function_call", "name": "exec_command"},
        {"type": "function_call", "name": "mcp__lab__inspect"},
    ]
    restored = restore_namespace_on_response_calls(
        calls,
        [
            {
                "type": "namespace",
                "name": "mcp__lab",
                "tools": [{"name": "inspect", "parameters": {}}],
            }
        ],
    )

    assert restored[0] == {"type": "function_call", "name": "exec_command"}
    assert restored[1] == {
        "type": "function_call",
        "name": "inspect",
        "namespace": "mcp__lab",
    }


def test_stream_event_helper_restores_only_namespaced_items() -> None:
    events = [
        {"type": "response.created"},
        {"item": {"type": "function_call", "name": "exec_command"}},
        {"item": {"type": "function_call", "name": "mcp__lab__inspect"}},
    ]

    restored = restore_namespace_on_stream_events(
        events,
        [
            {
                "type": "namespace",
                "name": "mcp__lab",
                "tools": [{"name": "inspect", "parameters": {}}],
            }
        ],
    )

    assert restored is events
    assert events[1]["item"] == {
        "type": "function_call",
        "name": "exec_command",
    }
    assert events[2]["item"] == {
        "type": "function_call",
        "name": "inspect",
        "namespace": "mcp__lab",
    }


def test_sanitize_chat_completion_messages_drops_empty_text_only_messages() -> None:
    messages = [
        {"role": "assistant", "content": ""},
        {"role": "assistant", "content": "   "},
        {"role": "assistant", "content": [{"type": "text", "text": ""}]},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "keep"},
                {"type": "text", "text": ""},
                {"type": "input_image", "image_url": {"url": "data:image/png;base64,x"}},
            ],
        },
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "call_1", "type": "function"}],
        },
        {"role": "tool", "content": "", "tool_call_id": "call_1"},
    ]

    sanitized = sanitize_chat_completion_messages(messages)

    assert sanitized == [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "keep"},
                {"type": "input_image", "image_url": {"url": "data:image/png;base64,x"}},
            ],
        },
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "call_1", "type": "function"}],
        },
        {"role": "tool", "content": "", "tool_call_id": "call_1"},
    ]


def test_litellm_responses_sanitizer_wraps_request_and_session_once() -> None:
    class FakeResponsesConfig:
        request_calls = 0
        session_calls = 0

        @staticmethod
        def transform_responses_api_request_to_chat_completion_request(*_args, **_kwargs):
            FakeResponsesConfig.request_calls += 1
            return {
                "messages": [
                    {"role": "assistant", "content": [{"type": "text", "text": ""}]},
                    {"role": "user", "content": "continue"},
                ]
            }

        @staticmethod
        async def async_responses_api_session_handler(*_args, **_kwargs):
            FakeResponsesConfig.session_calls += 1
            return {
                "messages": [
                    {"role": "assistant", "content": ""},
                    {"role": "assistant", "content": "done"},
                ]
            }

    install_litellm_responses_input_sanitizer(FakeResponsesConfig)
    install_litellm_responses_input_sanitizer(FakeResponsesConfig)

    request = FakeResponsesConfig.transform_responses_api_request_to_chat_completion_request()
    session = asyncio.run(FakeResponsesConfig.async_responses_api_session_handler())

    assert FakeResponsesConfig.request_calls == 1
    assert request["messages"] == [{"role": "user", "content": "continue"}]
    assert FakeResponsesConfig.session_calls == 1
    assert session["messages"] == [{"role": "assistant", "content": "done"}]
