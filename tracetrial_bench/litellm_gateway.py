from __future__ import annotations

import ctypes
import json
import os
import secrets
import signal
import stat
import subprocess
import sys
import time
from dataclasses import dataclass, field
from functools import wraps
from pathlib import Path
from typing import Callable, Mapping
from urllib.error import URLError
from urllib.request import ProxyHandler, Request, build_opener

from tracetrial_bench.container_environment import add_loopback_no_proxy
from tracetrial_bench.model_registry import INTERNAL_API_KEY_ENV, ModelRuntimeSpec


GATEWAY_HOST = "127.0.0.1"
GATEWAY_PORT = 4000
GATEWAY_TOKEN_ENV = "HARNESSBENCH_GATEWAY_TOKEN"
LITELLM_PYTHON = "/opt/litellm/bin/python"
_NORMALIZER_MARKER = "_harnessbench_tool_call_normalizer_installed"
_NAMESPACE_COMPAT_MARKER = "_harnessbench_namespace_tool_compat_installed"
_NAMESPACE_STREAM_COMPAT_MARKER = (
    "_harnessbench_namespace_stream_tool_compat_installed"
)
_ANTHROPIC_REASONING_STREAM_COMPAT_MARKER = (
    "_harnessbench_anthropic_reasoning_stream_compat_installed"
)
_RESPONSES_SANITIZER_MARKER = "_harnessbench_responses_input_sanitizer_installed"
_PR_GET_DUMPABLE = 3
_PR_SET_DUMPABLE = 4


class OutputSecurityError(RuntimeError):
    pass


_REDACTABLE_OUTPUT_SUFFIXES = frozenset(
    {".json", ".jsonl", ".log", ".md", ".txt"}
)


@dataclass(frozen=True)
class GatewayInfo:
    base_url: str
    openai_base_url: str
    token: str = field(repr=False)

    def public_summary(self) -> dict[str, str | bool | int]:
        return {
            "implementation": "litellm",
            "ready": True,
            "listen_host": GATEWAY_HOST,
            "listen_port": GATEWAY_PORT,
        }


def build_gateway_config(model: ModelRuntimeSpec) -> dict[str, object]:
    if model.api_mode != "chat_completions":
        raise ValueError("LiteLLM gateway requires api_mode=chat_completions")
    litellm_params: dict[str, object] = {
        "model": f"openai/{model.model}",
        "api_base": model.base_url,
        "api_key": f"os.environ/{INTERNAL_API_KEY_ENV}",
        "use_chat_completions_api": True,
    }
    if model.litellm_disable_web_search:
        # Codex declares a built-in Responses web-search tool even when the
        # selected OpenAI-compatible route cannot execute it. LiteLLM derives
        # that declaration into web_search_options; drop only that derived
        # parameter for explicitly opted-out models.
        litellm_params["additional_drop_params"] = ["web_search_options"]
    return {
        "model_list": [
            {
                "model_name": model.model,
                "litellm_params": litellm_params,
            }
        ],
        "general_settings": {
            "master_key": f"os.environ/{GATEWAY_TOKEN_ENV}",
        },
        "litellm_settings": {
            "drop_params": True,
        },
    }


def normalize_tool_call_messages(
    messages: list[dict[str, object]],
) -> list[dict[str, object]]:
    normalized: list[dict[str, object]] = []
    index = 0
    while index < len(messages):
        message = _normalize_tool_call_content(messages[index])
        if _is_empty_assistant_placeholder(message):
            index += 1
            continue
        call_ids = _tool_call_ids(message)
        if message.get("role") != "assistant" or not call_ids:
            normalized.append(message)
            index += 1
            continue

        following = index + 1
        assistant_messages: list[dict[str, object]] = []
        while following < len(messages):
            candidate = messages[following]
            if (
                candidate.get("role") != "assistant"
                or candidate.get("tool_calls")
                or not set(candidate).issubset({"role", "content"})
            ):
                break
            assistant_messages.append(candidate)
            following += 1

        if (
            not assistant_messages
            or following >= len(messages)
            or messages[following].get("role") != "tool"
            or messages[following].get("tool_call_id") not in call_ids
        ):
            normalized.append(message)
            index += 1
            continue

        merged_content = message.get("content")
        merge_succeeded = True
        for assistant_message in assistant_messages:
            merge_succeeded, merged_content = _merge_assistant_content(
                merged_content,
                assistant_message.get("content"),
            )
            if not merge_succeeded:
                break
        if not merge_succeeded:
            normalized.append(message)
            index += 1
            continue

        normalized.append(
            _normalize_tool_call_content({**message, "content": merged_content})
        )
        index = following
    return normalized


def _normalize_tool_call_content(
    message: dict[str, object],
) -> dict[str, object]:
    if message.get("role") != "assistant" or not _tool_call_ids(message):
        return message
    content = message.get("content")
    if content in ("", []):
        # Tool-call-only assistant messages do not require textual content.
        # Some OpenAI-compatible providers reject an empty string/list while
        # accepting the canonical null form.
        return {**message, "content": None}
    flattened, text = _flatten_text_content_parts(content)
    if flattened:
        # LLM Gateway's Bedrock route rejects OpenAI text content-part arrays
        # when they accompany tool calls, even though the same request with a
        # plain string is accepted. Preserve mixed/non-text content verbatim.
        return {**message, "content": text}
    return message


def _flatten_text_content_parts(content: object) -> tuple[bool, str]:
    if not isinstance(content, list) or not content:
        return False, ""
    text_parts: list[str] = []
    for part in content:
        if isinstance(part, str):
            text_parts.append(part)
            continue
        if (
            isinstance(part, Mapping)
            and part.get("type") == "text"
            and isinstance(part.get("text"), str)
        ):
            text_parts.append(part["text"])
            continue
        return False, ""
    return True, "".join(text_parts)


def _is_empty_assistant_placeholder(message: Mapping[str, object]) -> bool:
    return bool(
        message.get("role") == "assistant"
        and not _tool_call_ids(message)
        and set(message).issubset({"role", "content"})
        and message.get("content") in (None, "", [])
    )


def _tool_call_ids(message: Mapping[str, object]) -> set[str]:
    tool_calls = message.get("tool_calls")
    if not isinstance(tool_calls, list):
        return set()
    return {
        call_id
        for tool_call in tool_calls
        if isinstance(tool_call, Mapping)
        and isinstance((call_id := tool_call.get("id")), str)
        and call_id
    }


def _merge_assistant_content(
    current: object,
    additional: object,
) -> tuple[bool, object]:
    supported_types = (type(None), str, list)
    if not isinstance(current, supported_types) or not isinstance(
        additional,
        supported_types,
    ):
        return False, current
    if current is None or current == "" or current == []:
        return True, list(additional) if isinstance(additional, list) else additional
    if additional is None or additional == "" or additional == []:
        return True, list(current) if isinstance(current, list) else current
    if isinstance(current, str) and isinstance(additional, str):
        return True, current + additional
    current_parts = (
        list(current)
        if isinstance(current, list)
        else [{"type": "text", "text": current}]
    )
    additional_parts = (
        list(additional)
        if isinstance(additional, list)
        else [{"type": "text", "text": additional}]
    )
    return True, current_parts + additional_parts


def install_litellm_message_normalizer(config_class: type | None = None) -> None:
    # LiteLLM 1.93.0 can split one tool turn into adjacent assistant messages.
    if config_class is None:
        from litellm.llms.openai.chat.gpt_transformation import OpenAIGPTConfig

        config_class = OpenAIGPTConfig
    if config_class.__dict__.get(_NORMALIZER_MARKER):
        return
    original = config_class._transform_messages

    @wraps(original)
    def normalized_transform(
        self: object,
        messages: list[dict[str, object]],
        model: str,
        is_async: bool = False,
    ) -> object:
        return original(
            self,
            normalize_tool_call_messages(messages),
            model,
            is_async,
        )

    config_class._transform_messages = normalized_transform
    setattr(config_class, _NORMALIZER_MARKER, True)


def install_litellm_anthropic_reasoning_stream_compat(
    streaming_class: type | None = None,
) -> None:
    """Backport LiteLLM's Anthropic reasoning-stream block fix.

    LiteLLM 1.93.0 eagerly opens a text block for a Responses ``message``
    item, then writes later ``reasoning_summary_text.delta`` events into that
    text block as ``thinking_delta`` chunks.  Anthropic clients discard that
    invalid block/delta combination, so Claude Code can receive an otherwise
    successful ``end_turn`` with no content.  Keep the text block lazy and
    switch between thinking and text blocks explicitly, matching upstream
    LiteLLM PR #32364.  Stop patching automatically once the installed class
    exposes the upstream helper.
    """

    if streaming_class is None:
        from litellm.llms.anthropic.experimental_pass_through.responses_adapters.streaming_iterator import (
            AnthropicResponsesStreamWrapper,
        )

        streaming_class = AnthropicResponsesStreamWrapper
    if streaming_class.__dict__.get(_ANTHROPIC_REASONING_STREAM_COMPAT_MARKER):
        return
    if callable(streaming_class.__dict__.get("_ensure_block_of_type")):
        setattr(streaming_class, _ANTHROPIC_REASONING_STREAM_COMPAT_MARKER, True)
        return

    original_init = streaming_class.__init__
    original_process_event = streaming_class._process_event

    @wraps(original_init)
    def reasoning_compatible_init(
        self: object,
        *args: object,
        **kwargs: object,
    ) -> None:
        original_init(self, *args, **kwargs)
        setattr(self, "_open_block_type", None)

    def close_open_block(self: object) -> None:
        if getattr(self, "_open_block_type", None) is None:
            return
        getattr(self, "_chunk_queue").append(
            {
                "type": "content_block_stop",
                "index": getattr(self, "_current_block_index"),
            }
        )
        setattr(self, "_open_block_type", None)

    def open_content_block(
        self: object,
        content_block: dict[str, object],
        item_id: str | None = None,
    ) -> int:
        block_index = getattr(self, "_next_block_index")()
        if item_id:
            getattr(self, "_item_id_to_block_index")[item_id] = block_index
        setattr(self, "_open_block_type", content_block["type"])
        getattr(self, "_chunk_queue").append(
            {
                "type": "content_block_start",
                "index": block_index,
                "content_block": content_block,
            }
        )
        return block_index

    def ensure_block_of_type(
        self: object,
        block_type: str,
        content_block: dict[str, object],
        item_id: str | None = None,
    ) -> int:
        if getattr(self, "_open_block_type", None) != block_type:
            close_open_block(self)
            return open_content_block(self, content_block, item_id)
        return int(getattr(self, "_current_block_index"))

    @wraps(original_process_event)
    def reasoning_compatible_process_event(self: object, event: object) -> object:
        event_type = _message_get(event, "type")

        if event_type == "response.created" and getattr(
            self,
            "_sent_message_start",
            False,
        ):
            return None

        if event_type == "response.output_item.added":
            item = _message_get(event, "item")
            item_type = _message_get(item, "type")
            if item_type == "message":
                # A role-only message event carries no text yet.  Opening its
                # block here is what causes later reasoning deltas to be
                # attached to a text block in LiteLLM 1.93.0.
                return None
            if item_type in {"function_call", "reasoning"}:
                close_open_block(self)
                result = original_process_event(self, event)
                setattr(
                    self,
                    "_open_block_type",
                    "tool_use" if item_type == "function_call" else "thinking",
                )
                return result

        if event_type == "response.output_text.delta":
            item_id = _optional_string(_message_get(event, "item_id"))
            ensure_block_of_type(
                self,
                "text",
                {"type": "text", "text": ""},
                item_id,
            )
        elif event_type == "response.reasoning_summary_text.delta":
            item_id = _optional_string(_message_get(event, "item_id"))
            ensure_block_of_type(
                self,
                "thinking",
                {"type": "thinking", "thinking": ""},
                item_id,
            )
        elif event_type == "response.output_item.done":
            close_open_block(self)
            return None
        elif event_type in {
            "response.completed",
            "response.failed",
            "response.incomplete",
        }:
            close_open_block(self)

        return original_process_event(self, event)

    streaming_class.__init__ = reasoning_compatible_init
    streaming_class._close_open_block = close_open_block
    streaming_class._open_content_block = open_content_block
    streaming_class._ensure_block_of_type = ensure_block_of_type
    streaming_class._process_event = reasoning_compatible_process_event
    setattr(streaming_class, _ANTHROPIC_REASONING_STREAM_COMPAT_MARKER, True)


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def flatten_responses_namespace_tools(
    tools: list[dict[str, object]] | None,
) -> list[dict[str, object]] | None:
    """Convert Codex Responses ``namespace`` groups into callable functions.

    LiteLLM's Responses-to-Chat-Completions bridge drops namespace entries,
    which silently removes Codex MCP and collaboration tools before the model
    request reaches providers such as DeepSeek.  Codex' legacy flat naming is
    reversible: ``namespace`` plus ``__`` plus the nested name.  Preserve all
    ordinary tools and fail closed on malformed groups or name collisions.
    """

    if tools is None:
        return None
    flattened: list[dict[str, object]] = []
    names: set[str] = set()
    for tool in tools:
        if not isinstance(tool, dict):
            raise ValueError("Responses tool declaration is not an object")
        if tool.get("type") != "namespace":
            name = tool.get("name")
            if isinstance(name, str) and name:
                if name in names:
                    raise ValueError(f"duplicate Responses tool name: {name}")
                names.add(name)
            flattened.append(dict(tool))
            continue

        namespace = tool.get("name")
        nested_tools = tool.get("tools")
        if not isinstance(namespace, str) or not namespace:
            raise ValueError("Responses namespace has no name")
        if not isinstance(nested_tools, list) or not nested_tools:
            raise ValueError(f"Responses namespace {namespace!r} has no tools")
        for nested in nested_tools:
            if not isinstance(nested, dict):
                raise ValueError(
                    f"Responses namespace {namespace!r} contains a non-object tool"
                )
            nested_name = nested.get("name")
            parameters = nested.get("parameters")
            if not isinstance(nested_name, str) or not nested_name:
                raise ValueError(
                    f"Responses namespace {namespace!r} contains an unnamed tool"
                )
            if parameters is not None and not isinstance(parameters, dict):
                raise ValueError(
                    f"Responses namespace tool {nested_name!r} has an invalid schema"
                )
            flat_name = f"{namespace}__{nested_name}"
            if flat_name in names:
                raise ValueError(f"duplicate flattened Responses tool name: {flat_name}")
            names.add(flat_name)
            flattened.append(
                {
                    "type": "function",
                    "name": flat_name,
                    "description": str(nested.get("description") or ""),
                    "parameters": dict(parameters or {"type": "object"}),
                    "strict": bool(nested.get("strict", False)),
                }
            )
    return flattened


def _namespace_tool_aliases(
    tools: object,
) -> dict[str, tuple[str, str]]:
    aliases: dict[str, tuple[str, str]] = {}
    if not isinstance(tools, list):
        return aliases
    for tool in tools:
        if not isinstance(tool, dict) or tool.get("type") != "namespace":
            continue
        namespace = tool.get("name")
        nested_tools = tool.get("tools")
        if not isinstance(namespace, str) or not isinstance(nested_tools, list):
            continue
        for nested in nested_tools:
            if not isinstance(nested, dict):
                continue
            nested_name = nested.get("name")
            if isinstance(nested_name, str) and nested_name:
                aliases[f"{namespace}__{nested_name}"] = (namespace, nested_name)
    return aliases


def qualify_namespaced_function_call(function_call: dict[str, object]) -> dict[str, object]:
    """Restore the flat provider name while replaying a prior Codex call."""

    namespace = function_call.get("namespace")
    name = function_call.get("name")
    if not isinstance(namespace, str) or not namespace:
        return function_call
    if not isinstance(name, str) or not name:
        return function_call
    return {**function_call, "name": f"{namespace}__{name}"}


def restore_namespace_on_response_calls(
    output_calls: object,
    original_tools: object,
) -> object:
    """Turn flat provider calls back into Codex namespace calls for dispatch."""

    aliases = _namespace_tool_aliases(original_tools)
    if not aliases or not isinstance(output_calls, list):
        return output_calls
    for item in output_calls:
        name = item.get("name") if isinstance(item, dict) else getattr(item, "name", None)
        binding = aliases.get(name) if isinstance(name, str) else None
        if binding is None:
            continue
        namespace, nested_name = binding
        if isinstance(item, dict):
            item["name"] = nested_name
            item["namespace"] = namespace
        else:
            setattr(item, "name", nested_name)
            setattr(item, "namespace", namespace)
    return output_calls


def restore_namespace_on_stream_events(
    events: object,
    original_tools: object,
) -> object:
    """Restore namespaces on streaming output-item events in place."""

    if not isinstance(events, list):
        return events
    output_items = []
    for event in events:
        item = (
            event.get("item")
            if isinstance(event, dict)
            else getattr(event, "item", None)
        )
        if item is not None:
            output_items.append(item)
    restore_namespace_on_response_calls(output_items, original_tools)
    return events


def sanitize_chat_completion_messages(
    messages: object,
) -> object:
    """Drop empty text-only chat messages before provider submission.

    Codex can emit empty Responses ``agent_message`` items.  LiteLLM 1.93.0
    preserves those as chat-completion text content blocks, but Anthropic-
    compatible providers reject empty text blocks.  Keep tool calls/results and
    non-text content intact; only remove content that carries no provider-
    visible information.
    """

    if not isinstance(messages, list):
        return messages
    sanitized: list[object] = []
    for message in messages:
        cleaned = _sanitize_chat_completion_message(message)
        if cleaned is not None:
            sanitized.append(cleaned)
    return sanitized


def _sanitize_chat_completion_message(message: object) -> object | None:
    role = _message_get(message, "role")
    content = _message_get(message, "content")
    tool_calls = _message_get(message, "tool_calls")
    has_tool_calls = isinstance(tool_calls, list) and len(tool_calls) > 0

    if isinstance(content, str):
        if content.strip() or has_tool_calls or role == "tool":
            return message
        return None

    if isinstance(content, list):
        cleaned_content = _sanitize_content_parts(content)
        if cleaned_content or has_tool_calls or role == "tool":
            return _message_with_content(message, cleaned_content)
        return None

    if content is None:
        if has_tool_calls or role == "tool":
            return message
        return None

    return message


def _sanitize_content_parts(content: list[object]) -> list[object]:
    cleaned: list[object] = []
    for part in content:
        if isinstance(part, str):
            if part.strip():
                cleaned.append(part)
            continue
        if isinstance(part, dict):
            text = part.get("text")
            if isinstance(text, str) and not text.strip():
                continue
        cleaned.append(part)
    return cleaned


def _message_get(message: object, key: str) -> object:
    if isinstance(message, Mapping):
        return message.get(key)
    return getattr(message, key, None)


def _message_with_content(message: object, content: list[object]) -> object:
    if isinstance(message, dict):
        return {**message, "content": content}
    try:
        clone = message.copy(deep=True)
    except Exception:
        clone = message
    try:
        setattr(clone, "content", content)
    except Exception:
        return message
    return clone


def install_litellm_responses_input_sanitizer(
    config_class: type | None = None,
) -> None:
    """Remove empty text blocks produced by Codex before LiteLLM calls providers."""

    if config_class is None:
        from litellm.responses.litellm_completion_transformation.transformation import (
            LiteLLMCompletionResponsesConfig,
        )

        config_class = LiteLLMCompletionResponsesConfig
    if config_class.__dict__.get(_RESPONSES_SANITIZER_MARKER):
        return
    original_request = (
        config_class.transform_responses_api_request_to_chat_completion_request
    )
    original_session = config_class.async_responses_api_session_handler

    @wraps(original_request)
    def sanitized_request(*args: object, **kwargs: object) -> dict[str, object]:
        request = original_request(*args, **kwargs)
        if isinstance(request, dict) and "messages" in request:
            request = {**request, "messages": sanitize_chat_completion_messages(request["messages"])}
        return request

    @wraps(original_session)
    async def sanitized_session(*args: object, **kwargs: object) -> dict[str, object]:
        request = await original_session(*args, **kwargs)
        if isinstance(request, dict) and "messages" in request:
            request = {**request, "messages": sanitize_chat_completion_messages(request["messages"])}
        return request

    config_class.transform_responses_api_request_to_chat_completion_request = staticmethod(
        sanitized_request
    )
    config_class.async_responses_api_session_handler = staticmethod(sanitized_session)
    setattr(config_class, _RESPONSES_SANITIZER_MARKER, True)


def install_litellm_namespace_tool_compat(
    config_class: type | None = None,
    streaming_class: type | None = None,
) -> None:
    """Keep Codex namespace tools intact across LiteLLM's chat bridge."""

    if config_class is None:
        from litellm.responses.litellm_completion_transformation.transformation import (
            LiteLLMCompletionResponsesConfig,
        )

        config_class = LiteLLMCompletionResponsesConfig
        if streaming_class is None:
            from litellm.responses.litellm_completion_transformation.streaming_iterator import (
                LiteLLMCompletionStreamingIterator,
            )

            streaming_class = LiteLLMCompletionStreamingIterator

    if not config_class.__dict__.get(_NAMESPACE_COMPAT_MARKER):
        original_tools = (
            config_class.transform_responses_api_tools_to_chat_completion_tools
        )
        original_input_call = (
            config_class._transform_responses_api_function_call_to_chat_completion_message
        )
        original_input_messages = (
            config_class.transform_responses_api_input_to_messages
        )
        original_output_calls = (
            config_class.transform_chat_completion_tools_to_responses_tools
        )

        @wraps(original_tools)
        def namespace_compatible_transform(
            tools: list[dict[str, object]] | None,
        ) -> object:
            return original_tools(flatten_responses_namespace_tools(tools))

        @wraps(original_input_call)
        def namespace_compatible_input_call(
            function_call: dict[str, object],
        ) -> object:
            return original_input_call(qualify_namespaced_function_call(function_call))

        @wraps(original_input_messages)
        def normalized_responses_input_messages(
            *args: object,
            **kwargs: object,
        ) -> object:
            messages = original_input_messages(*args, **kwargs)
            if not isinstance(messages, list) or any(
                not isinstance(message, dict) for message in messages
            ):
                return messages
            return normalize_tool_call_messages(messages)

        @wraps(original_output_calls)
        def namespace_compatible_output_calls(
            chat_completion_response: object,
            responses_api_request: dict[str, object] | None = None,
        ) -> object:
            calls = original_output_calls(
                chat_completion_response,
                responses_api_request,
            )
            original_request_tools = (
                responses_api_request.get("tools")
                if isinstance(responses_api_request, dict)
                else None
            )
            return restore_namespace_on_response_calls(calls, original_request_tools)

        config_class.transform_responses_api_tools_to_chat_completion_tools = staticmethod(
            namespace_compatible_transform
        )
        config_class._transform_responses_api_function_call_to_chat_completion_message = (
            staticmethod(namespace_compatible_input_call)
        )
        config_class.transform_responses_api_input_to_messages = staticmethod(
            normalized_responses_input_messages
        )
        config_class.transform_chat_completion_tools_to_responses_tools = staticmethod(
            namespace_compatible_output_calls
        )
        setattr(config_class, _NAMESPACE_COMPAT_MARKER, True)

    if streaming_class is None or streaming_class.__dict__.get(
        _NAMESPACE_STREAM_COMPAT_MARKER
    ):
        return
    original_stream_delta = streaming_class._queue_tool_call_delta_events
    original_stream_final = streaming_class._queue_final_tool_call_done_events

    def original_request_tools(iterator: object) -> object:
        request = getattr(iterator, "responses_api_request", None)
        return request.get("tools") if isinstance(request, Mapping) else None

    @wraps(original_stream_delta)
    def namespace_compatible_stream_delta(
        self: object,
        tool_calls: object,
    ) -> None:
        original_stream_delta(self, tool_calls)
        restore_namespace_on_stream_events(
            getattr(self, "_pending_tool_events", None),
            original_request_tools(self),
        )

    @wraps(original_stream_final)
    def namespace_compatible_stream_final(
        self: object,
        response: object,
    ) -> None:
        original_stream_final(self, response)
        restore_namespace_on_stream_events(
            getattr(self, "_pending_tool_events", None),
            original_request_tools(self),
        )

    streaming_class._queue_tool_call_delta_events = namespace_compatible_stream_delta
    streaming_class._queue_final_tool_call_done_events = namespace_compatible_stream_final
    setattr(streaming_class, _NAMESPACE_STREAM_COMPAT_MARKER, True)


def protect_process_credentials(
    *,
    prctl: Callable[[int, int], int] | None = None,
    platform: str | None = None,
) -> None:
    if (platform or sys.platform) != "linux":
        raise RuntimeError("process credential isolation requires Linux")
    controller = prctl or _system_prctl
    try:
        set_result = controller(_PR_SET_DUMPABLE, 0)
        observed = controller(_PR_GET_DUMPABLE, 0)
    except (OSError, TypeError, ValueError) as exc:
        raise RuntimeError("process credential isolation failed") from exc
    if set_result != 0 or observed != 0:
        raise RuntimeError("process credential isolation could not be verified")


def _system_prctl(option: int, value: int) -> int:
    libc = ctypes.CDLL(None, use_errno=True)
    prctl = libc.prctl
    prctl.argtypes = [
        ctypes.c_int,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
    ]
    prctl.restype = ctypes.c_int
    result = prctl(option, value, 0, 0, 0)
    if result < 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number))
    return int(result)


def direct_urlopen(request: object, *, timeout: float):
    return build_opener(ProxyHandler({})).open(request, timeout=timeout)


def gateway_process_environment(
    *,
    model_key: str,
    gateway_token: str,
    state_root: Path,
    source_environment: Mapping[str, str] | None = None,
) -> dict[str, str]:
    environment = dict(os.environ if source_environment is None else source_environment)
    environment.update(
        {
            INTERNAL_API_KEY_ENV: model_key,
            GATEWAY_TOKEN_ENV: gateway_token,
            "HOME": str(state_root / "home"),
            "XDG_CACHE_HOME": str(state_root / "cache"),
            "LITELLM_TELEMETRY": "false",
            "LITELLM_LOG": "ERROR",
        }
    )
    return add_loopback_no_proxy(environment)


class LiteLLMGateway:
    def __init__(
        self,
        *,
        model: ModelRuntimeSpec,
        state_root: Path,
        output_root: Path,
        startup_timeout: float = 30.0,
        shutdown_timeout: float = 5.0,
        health_poll_interval: float = 0.1,
        token_factory: Callable[[], str] | None = None,
        popen_factory: Callable[..., object] = subprocess.Popen,
        urlopen: Callable[..., object] = direct_urlopen,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        killpg: Callable[[int, int], None] = os.killpg,
    ) -> None:
        if startup_timeout <= 0 or shutdown_timeout <= 0:
            raise ValueError("LiteLLM gateway timeouts must be positive")
        if health_poll_interval < 0:
            raise ValueError("LiteLLM gateway poll interval must not be negative")
        self.model = model
        self.state_root = Path(state_root)
        self.output_root = Path(output_root)
        self.startup_timeout = startup_timeout
        self.shutdown_timeout = shutdown_timeout
        self.health_poll_interval = health_poll_interval
        self.token_factory = token_factory or (lambda: secrets.token_urlsafe(32))
        self.popen_factory = popen_factory
        self.urlopen = urlopen
        self.monotonic = monotonic
        self.sleep = sleep
        self.killpg = killpg
        self.runtime_root = self.state_root / "litellm"
        self.config_path = self.runtime_root / "config.yaml"
        self.raw_log_path = self.runtime_root / "litellm.raw.log"
        self.public_log_path = self.output_root / "litellm.log"
        self._process: object | None = None
        self._raw_log = None
        self._token: str | None = None

    def start(self) -> GatewayInfo:
        if self._process is not None or self._raw_log is not None:
            raise RuntimeError("LiteLLM gateway is already started")
        token = self.token_factory()
        if not isinstance(token, str) or not token:
            raise ValueError("LiteLLM gateway token factory returned an empty value")
        self._token = token
        _ensure_private_directory(self.runtime_root)
        _ensure_private_directory(self.runtime_root / "home")
        _ensure_private_directory(self.runtime_root / "cache")
        _ensure_private_directory(self.output_root)
        config_text = json.dumps(
            build_gateway_config(self.model),
            indent=2,
            sort_keys=True,
        ) + "\n"
        _write_private_text(self.config_path, config_text)
        self._raw_log = _open_private_log(self.raw_log_path)
        command = [
            LITELLM_PYTHON,
            "-m",
            "tracetrial_bench.litellm_gateway",
            "--config",
            str(self.config_path),
            "--host",
            GATEWAY_HOST,
            "--port",
            str(GATEWAY_PORT),
        ]
        environment = gateway_process_environment(
            model_key=self.model.api_key,
            gateway_token=token,
            state_root=self.runtime_root,
        )
        try:
            self._process = self.popen_factory(
                command,
                stdout=self._raw_log,
                stderr=subprocess.STDOUT,
                env=environment,
                cwd=str(self.runtime_root),
                text=True,
                start_new_session=True,
            )
            self._wait_until_ready(token)
        except Exception:
            self.stop()
            raise
        base_url = f"http://{GATEWAY_HOST}:{GATEWAY_PORT}"
        return GatewayInfo(
            base_url=base_url,
            openai_base_url=f"{base_url}/v1",
            token=token,
        )

    def stop(self) -> None:
        process = self._process
        try:
            if process is not None and process.poll() is None:
                try:
                    self.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=self.shutdown_timeout)
                except subprocess.TimeoutExpired:
                    try:
                        self.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait(timeout=self.shutdown_timeout)
        finally:
            self._process = None
            self._publish_sanitized_log()

    def _wait_until_ready(self, token: str) -> None:
        if self._process is None:
            raise RuntimeError("LiteLLM gateway process was not created")
        deadline = self.monotonic() + self.startup_timeout
        health_url = (
            f"http://{GATEWAY_HOST}:{GATEWAY_PORT}/health/liveliness"
        )
        while True:
            returncode = self._process.poll()
            if returncode is not None:
                raise RuntimeError(f"LiteLLM gateway exited with {returncode}")
            request = Request(
                health_url,
                headers={"Authorization": f"Bearer {token}"},
            )
            try:
                with self.urlopen(request, timeout=1.0) as response:
                    status_code = getattr(response, "status", None)
                    if isinstance(status_code, int) and 200 <= status_code < 300:
                        return
            except (OSError, TimeoutError, URLError):
                pass
            if self.monotonic() >= deadline:
                raise RuntimeError("LiteLLM gateway did not become ready")
            self.sleep(self.health_poll_interval)

    def _publish_sanitized_log(self) -> None:
        if self._raw_log is not None:
            self._raw_log.close()
            self._raw_log = None
        content = _read_private_regular_bytes(self.raw_log_path)
        if content is None:
            return
        try:
            for secret_value in (self.model.api_key, self._token):
                if secret_value:
                    content = content.replace(
                        secret_value.encode("utf-8"), b"<redacted>"
                    )
            _write_private_bytes(self.public_log_path, content)
        finally:
            self.raw_log_path.unlink(missing_ok=True)


def assert_output_has_no_secrets(
    root: Path,
    secret_values: tuple[str, ...],
) -> dict[str, int]:
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise OutputSecurityError("output root is missing or symlinked")
    secrets_to_find = tuple(
        dict.fromkeys(
            value.encode("utf-8")
            for value in secret_values
            if isinstance(value, str) and value
        )
    )
    files = 0
    total_bytes = 0

    def walk_error(_error: OSError) -> None:
        raise OutputSecurityError("cannot inspect output tree")

    for directory, directory_names, file_names in os.walk(
        root,
        topdown=True,
        followlinks=False,
        onerror=walk_error,
    ):
        for name in directory_names:
            relative = (Path(directory) / name).relative_to(root)
            if _path_contains_secret(relative, secrets_to_find):
                raise OutputSecurityError("output tree path contains a credential")
            try:
                info = (Path(directory) / name).lstat()
            except OSError as exc:
                raise OutputSecurityError("cannot inspect output tree") from exc
            if stat.S_ISLNK(info.st_mode):
                raise OutputSecurityError("output tree contains a symlink")
            if not stat.S_ISDIR(info.st_mode):
                raise OutputSecurityError("output tree contains a special file")
        for name in file_names:
            path = Path(directory) / name
            if _path_contains_secret(path.relative_to(root), secrets_to_find):
                raise OutputSecurityError("output tree path contains a credential")
            try:
                info = path.lstat()
            except OSError as exc:
                raise OutputSecurityError("cannot inspect output tree") from exc
            if stat.S_ISLNK(info.st_mode):
                raise OutputSecurityError("output tree contains a symlink")
            if not stat.S_ISREG(info.st_mode):
                raise OutputSecurityError("output tree contains a special file")
            if _regular_file_contains_secret(path, info, secrets_to_find):
                raise OutputSecurityError(
                    "output tree contains a credential: "
                    + path.relative_to(root).as_posix()
                )
            files += 1
            total_bytes += info.st_size
    return {"files": files, "bytes": total_bytes}


def redact_output_secrets(
    root: Path,
    secret_values: tuple[str, ...],
) -> dict[str, int]:
    """Remove exact runtime credentials from text artifacts before archiving.

    Trace adapters can surface provider-owned raw events, so writer-level
    redaction is backed by this final filesystem pass. Unknown binary formats,
    links, special files, and credential-bearing paths remain hard failures.
    """

    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise OutputSecurityError("output root is missing or symlinked")
    secrets_to_replace = tuple(
        dict.fromkeys(
            value.encode("utf-8")
            for value in secret_values
            if isinstance(value, str) and value
        )
    )
    if not secrets_to_replace:
        return {"files_redacted": 0, "matches_redacted": 0}
    files_redacted = 0
    matches_redacted = 0

    def walk_error(_error: OSError) -> None:
        raise OutputSecurityError("cannot inspect output tree")

    for directory, directory_names, file_names in os.walk(
        root,
        topdown=True,
        followlinks=False,
        onerror=walk_error,
    ):
        for name in directory_names:
            path = Path(directory) / name
            relative = path.relative_to(root)
            if _path_contains_secret(relative, secrets_to_replace):
                raise OutputSecurityError("output tree path contains a credential")
            if path.is_symlink() or not path.is_dir():
                raise OutputSecurityError("output tree contains an unsafe directory")
        for name in file_names:
            path = Path(directory) / name
            relative = path.relative_to(root)
            if _path_contains_secret(relative, secrets_to_replace):
                raise OutputSecurityError("output tree path contains a credential")
            try:
                info = path.lstat()
            except OSError as exc:
                raise OutputSecurityError("cannot inspect output tree") from exc
            if not stat.S_ISREG(info.st_mode) or path.is_symlink():
                raise OutputSecurityError("output tree contains an unsafe file")
            payload = path.read_bytes()
            match_count = sum(payload.count(secret) for secret in secrets_to_replace)
            if not match_count:
                continue
            if path.suffix.lower() not in _REDACTABLE_OUTPUT_SUFFIXES:
                raise OutputSecurityError(
                    "credential found in non-text output artifact: "
                    + relative.as_posix()
                )
            for secret in secrets_to_replace:
                payload = payload.replace(secret, b"<redacted>")
            _write_private_bytes(path, payload)
            files_redacted += 1
            matches_redacted += match_count
    return {
        "files_redacted": files_redacted,
        "matches_redacted": matches_redacted,
    }


def _path_contains_secret(path: Path, secret_values: tuple[bytes, ...]) -> bool:
    encoded_path = path.as_posix().encode("utf-8", errors="surrogateescape")
    return any(value in encoded_path for value in secret_values)


def _run_litellm_proxy() -> object:
    protect_process_credentials()
    install_litellm_message_normalizer()
    install_litellm_anthropic_reasoning_stream_compat()
    install_litellm_responses_input_sanitizer()
    install_litellm_namespace_tool_compat()
    from litellm import run_server

    sys.argv[0] = "litellm"
    return run_server()


def _regular_file_contains_secret(
    path: Path,
    expected_info: os.stat_result,
    secret_values: tuple[bytes, ...],
) -> bool:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise OutputSecurityError("cannot inspect output tree") from exc
    try:
        opened_info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened_info.st_mode)
            or opened_info.st_dev != expected_info.st_dev
            or opened_info.st_ino != expected_info.st_ino
        ):
            raise OutputSecurityError("output tree changed during inspection")
        if not secret_values:
            return False
        largest_secret = max(len(value) for value in secret_values)
        overlap = b""
        while True:
            chunk = os.read(descriptor, 64 * 1024)
            if not chunk:
                return False
            candidate = overlap + chunk
            if any(value in candidate for value in secret_values):
                return True
            overlap = candidate[-(largest_secret - 1) :] if largest_secret > 1 else b""
    finally:
        os.close(descriptor)


def _read_private_regular_bytes(path: Path) -> bytes | None:
    try:
        expected_info = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise RuntimeError("cannot inspect private LiteLLM log") from exc
    if not stat.S_ISREG(expected_info.st_mode):
        raise RuntimeError("private LiteLLM log is not a regular file")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise RuntimeError("cannot open private LiteLLM log") from exc
    try:
        opened_info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened_info.st_mode)
            or opened_info.st_dev != expected_info.st_dev
            or opened_info.st_ino != expected_info.st_ino
        ):
            raise RuntimeError("private LiteLLM log changed during inspection")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 64 * 1024)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
    finally:
        os.close(descriptor)


def _ensure_private_directory(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink() or not path.is_dir():
        raise RuntimeError("private runtime path is not a real directory")
    path.chmod(0o700)


def _open_private_log(path: Path):
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise RuntimeError("cannot open private LiteLLM log") from exc
    os.fchmod(descriptor, 0o600)
    return os.fdopen(descriptor, "w", encoding="utf-8")


def _write_private_text(path: Path, content: str) -> None:
    _write_private_bytes(path, content.encode("utf-8"))


def _write_private_bytes(path: Path, content: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise RuntimeError("cannot write private LiteLLM artifact") from exc
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)


if __name__ == "__main__":
    raise SystemExit(_run_litellm_proxy())
