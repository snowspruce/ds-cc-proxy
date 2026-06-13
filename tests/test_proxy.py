# ds-cc-proxy 单元测试
#
# 覆盖 proxy.py 中所有纯函数逻辑:
#   - env var 解析
#   - thinking 注入 / 标准化 / 检测
#   - SSE 行过滤
#   - 请求摘要 & 响应头构建

from unittest.mock import MagicMock

from ds_cc_proxy.proxy import (
    _build_response_headers,
    _has_thinking,
    _has_tool_use,
    _inject_thinking_blocks,
    _normalize_thinking,
    _parse_env_float,
    _parse_env_int,
    _process_sse_data_line,
    _summarize_request,
    _thinking_requested,
)

# ---------------------------------------------------------------------------
# _parse_env_int / _parse_env_float
# ---------------------------------------------------------------------------


class TestParseEnvInt:
    def test_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("TEST_INT_VAR", raising=False)
        assert _parse_env_int("TEST_INT_VAR", 42) == 42

    def test_default_when_empty_string(self, monkeypatch):
        monkeypatch.setenv("TEST_INT_VAR", "")
        assert _parse_env_int("TEST_INT_VAR", 42) == 42

    def test_valid_value(self, monkeypatch):
        monkeypatch.setenv("TEST_INT_VAR", "100")
        assert _parse_env_int("TEST_INT_VAR", 42) == 100

    def test_invalid_value_falls_back(self, monkeypatch):
        monkeypatch.setenv("TEST_INT_VAR", "not_a_number")
        assert _parse_env_int("TEST_INT_VAR", 42) == 42

    def test_below_min_falls_back(self, monkeypatch):
        monkeypatch.setenv("TEST_INT_VAR", "5")
        assert _parse_env_int("TEST_INT_VAR", 42, min_val=10) == 42

    def test_at_min_passes(self, monkeypatch):
        monkeypatch.setenv("TEST_INT_VAR", "10")
        assert _parse_env_int("TEST_INT_VAR", 42, min_val=10) == 10


class TestParseEnvFloat:
    def test_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("TEST_FLOAT_VAR", raising=False)
        assert _parse_env_float("TEST_FLOAT_VAR", 3.14) == 3.14

    def test_valid_float(self, monkeypatch):
        monkeypatch.setenv("TEST_FLOAT_VAR", "2.5")
        assert _parse_env_float("TEST_FLOAT_VAR", 1.0) == 2.5

    def test_invalid_float_falls_back(self, monkeypatch):
        monkeypatch.setenv("TEST_FLOAT_VAR", "abc")
        assert _parse_env_float("TEST_FLOAT_VAR", 1.0) == 1.0

    def test_below_min_falls_back(self, monkeypatch):
        monkeypatch.setenv("TEST_FLOAT_VAR", "0.5")
        assert _parse_env_float("TEST_FLOAT_VAR", 1.0, min_val=1.0) == 1.0


# ---------------------------------------------------------------------------
# _has_tool_use / _has_thinking
# ---------------------------------------------------------------------------


class TestHasToolUse:
    def test_finds_tool_use(self):
        content = [
            {"type": "text", "text": "hello"},
            {"type": "tool_use", "id": "1", "name": "read"},
        ]
        assert _has_tool_use(content) is True

    def test_no_tool_use(self):
        content = [{"type": "text", "text": "hello"}]
        assert _has_tool_use(content) is False

    def test_empty_list(self):
        assert _has_tool_use([]) is False

    def test_ignores_non_dict(self):
        assert _has_tool_use(["string_item", 123]) is False


class TestHasThinking:
    def test_finds_thinking(self):
        content = [{"type": "thinking", "thinking": "..."}]
        assert _has_thinking(content) is True

    def test_finds_redacted_thinking(self):
        content = [{"type": "redacted_thinking", "data": "..."}]
        assert _has_thinking(content) is True

    def test_no_thinking(self):
        content = [{"type": "text", "text": "hello"}]
        assert _has_thinking(content) is False

    def test_empty_list(self):
        assert _has_thinking([]) is False


# ---------------------------------------------------------------------------
# _inject_thinking_blocks
# ---------------------------------------------------------------------------


class TestInjectThinkingBlocks:
    def test_injects_before_tool_use(self):
        data = {
            "model": "deepseek-v4-2-0528",
            "thinking": {"type": "enabled"},
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "id": "1", "name": "read"},
                    ],
                },
            ],
        }
        assert _inject_thinking_blocks(data) is True
        blocks = data["messages"][0]["content"]
        assert blocks[0]["type"] == "thinking"
        assert blocks[0]["thinking"] == ""
        assert blocks[1]["type"] == "tool_use"

    def test_no_inject_when_thinking_exists(self):
        data = {
            "model": "deepseek-v4-2-0528",
            "thinking": {"type": "enabled"},
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": "x"},
                        {"type": "tool_use", "id": "1", "name": "read"},
                    ],
                },
            ],
        }
        assert _inject_thinking_blocks(data) is False

    def test_no_inject_for_non_deepseek_model(self):
        data = {
            "model": "claude-sonnet-4-6",
            "thinking": {"type": "enabled"},
            "messages": [
                {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "id": "1"}],
                },
            ],
        }
        assert _inject_thinking_blocks(data) is False

    def test_no_inject_when_thinking_not_enabled(self):
        data = {
            "model": "deepseek-v4-2-0528",
            "thinking": {"type": "disabled"},
            "messages": [
                {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "id": "1"}],
                },
            ],
        }
        assert _inject_thinking_blocks(data) is False

    def test_no_inject_for_user_role(self):
        data = {
            "model": "deepseek-v4-2-0528",
            "thinking": {"type": "enabled"},
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "tool_use", "id": "1"}],
                },
            ],
        }
        assert _inject_thinking_blocks(data) is False

    def test_content_not_list_skipped(self):
        data = {
            "model": "deepseek-v4-2-0528",
            "thinking": {"type": "enabled"},
            "messages": [
                {
                    "role": "assistant",
                    "content": "plain string",
                },
            ],
        }
        assert _inject_thinking_blocks(data) is False

    def test_thinking_config_not_dict(self):
        data = {
            "model": "deepseek-v4-2-0528",
            "thinking": "enabled",
            "messages": [],
        }
        assert _inject_thinking_blocks(data) is False


# ---------------------------------------------------------------------------
# _normalize_thinking
# ---------------------------------------------------------------------------


class TestNormalizeThinking:
    def test_no_thinking_key(self):
        data = {"model": "deepseek-v4-2-0528"}
        assert _normalize_thinking(data) is False

    def test_adaptive_passthrough(self):
        data = {
            "thinking": {"type": "adaptive", "output_config": {"effort": "high"}},
        }
        assert _normalize_thinking(data) is False
        assert data["thinking"]["type"] == "adaptive"
        assert "output_config" in data["thinking"]

    def test_enabled_passthrough(self):
        data = {"thinking": {"type": "enabled"}}
        assert _normalize_thinking(data) is False

    def test_disabled_converted(self):
        data = {
            "thinking": {"type": "disabled"},
            "reasoning_effort": "high",
            "output_config": {"effort": "high"},
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": "old"},
                        {"type": "text", "text": "hi"},
                    ],
                },
            ],
        }
        assert _normalize_thinking(data) is True
        assert data["thinking"]["type"] == "enabled"
        assert data["thinking"]["budget_tokens"] == 2048
        assert "reasoning_effort" not in data
        assert "output_config" not in data
        # thinking block stripped from messages
        msg_content = data["messages"][0]["content"]
        assert len(msg_content) == 1
        assert msg_content[0]["type"] == "text"

    def test_disabled_no_messages(self):
        data = {"thinking": {"type": "disabled"}}
        assert _normalize_thinking(data) is True
        assert data["thinking"]["type"] == "enabled"
        assert data["thinking"]["budget_tokens"] == 2048

    def test_unknown_type_noop(self):
        data = {"thinking": {"type": "unknown_mode"}}
        assert _normalize_thinking(data) is False

    def test_thinking_config_not_dict(self):
        data = {"thinking": "not_a_dict"}
        assert _normalize_thinking(data) is False

    def test_messages_with_non_list_content(self):
        data = {
            "thinking": {"type": "disabled"},
            "messages": [
                {"role": "assistant", "content": "string not list"},
            ],
        }
        assert _normalize_thinking(data) is True

    def test_disabled_budget_matches_subagent_expectation(self):
        """子代理 disabled→enabled 的 budget_tokens=2048 是设计值，勿随意改。"""
        data = {
            "thinking": {"type": "disabled"},
            "messages": [],
        }
        _normalize_thinking(data)
        assert data["thinking"]["budget_tokens"] == 2048

    def test_enabled_preserves_original_budget(self):
        """主会话 enabled + budget_tokens 应完整保留，不被篡改。"""
        data = {
            "thinking": {"type": "enabled", "budget_tokens": 4096},
            "messages": [],
        }
        assert _normalize_thinking(data) is False
        assert data["thinking"]["budget_tokens"] == 4096


# ---------------------------------------------------------------------------
# _thinking_requested
# ---------------------------------------------------------------------------


class TestThinkingRequested:
    def test_enabled(self):
        assert _thinking_requested({"thinking": {"type": "enabled"}}) is True

    def test_adaptive(self):
        assert _thinking_requested({"thinking": {"type": "adaptive"}}) is True

    def test_disabled(self):
        assert _thinking_requested({"thinking": {"type": "disabled"}}) is False

    def test_missing(self):
        assert _thinking_requested({}) is False

    def test_not_dict(self):
        assert _thinking_requested({"thinking": "enabled"}) is False


# ---------------------------------------------------------------------------
# _process_sse_data_line
# ---------------------------------------------------------------------------


class TestProcessSseDataLine:
    # --- thinking filtering (legacy _filter_sse_line behavior) ---

    def test_passthrough_non_data_line(self):
        line = "event: message"
        result, indices = _process_sse_data_line(line, set(), [], {})
        assert result == line
        assert indices == set()

    def test_passthrough_non_thinking_event(self):
        line = 'data: {"type":"content_block_start","index":0,"content_block":{"type":"text"}}'
        result, indices = _process_sse_data_line(line, set(), [], {})
        assert result == line

    def test_filters_thinking_start(self):
        line = 'data: {"type":"content_block_start","index":1,"content_block":{"type":"thinking"}}'
        result, indices = _process_sse_data_line(line, set(), [], {})
        assert result is None
        assert 1 in indices

    def test_filters_thinking_delta(self):
        indices = {1}
        line = (
            'data: {"type":"content_block_delta","index":1,'
            '"delta":{"type":"thinking_delta","thinking":"x"}}'
        )
        result, indices = _process_sse_data_line(line, indices, [], {})
        assert result is None
        assert 1 in indices

    def test_clears_on_stop(self):
        indices = {1}
        line = 'data: {"type":"content_block_stop","index":1}'
        result, indices = _process_sse_data_line(line, indices, [], {})
        assert result is None
        assert 1 not in indices

    def test_passthrough_non_thinking_index(self):
        indices = {1}
        line = (
            'data: {"type":"content_block_delta","index":2,'
            '"delta":{"type":"text_delta","text":"hi"}}'
        )
        result, indices = _process_sse_data_line(line, indices, [], {})
        assert result == line

    def test_handles_invalid_json(self):
        line = "data: not valid json"
        result, indices = _process_sse_data_line(line, set(), [], {})
        assert result == line

    def test_handles_non_dict_data(self):
        line = "data: [1, 2, 3]"
        result, indices = _process_sse_data_line(line, set(), [], {})
        assert result == line

    def test_rstripped_line(self):
        """C1: verify trailing \r is handled before _process_sse_data_line."""
        line = 'data: {"type":"content_block_start","index":0,"content_block":{"type":"text"}}'
        clean = line.rstrip("\r")
        result, _ = _process_sse_data_line(clean, set(), [], {})
        assert result == clean

    # --- event type tracking (legacy _track_event_type behavior) ---

    def test_tracks_event_type(self):
        event_types = []
        line = 'data: {"type":"content_block_start","index":0,"content_block":{"type":"text"}}'
        _process_sse_data_line(line, set(), event_types, {})
        assert event_types == ["content_block_start"]

    def test_tracks_unknown_type_as_question(self):
        event_types = []
        line = 'data: {"index":0}'
        _process_sse_data_line(line, set(), event_types, {})
        assert event_types == ["?"]

    def test_tracks_usage_from_message_stop(self):
        response_usage = {}
        line = 'data: {"type":"message_stop","usage":{"input_tokens":10,"output_tokens":20}}'
        _process_sse_data_line(line, set(), [], response_usage)
        assert response_usage == {"input_tokens": 10, "output_tokens": 20}

    def test_tracks_usage_from_message_delta(self):
        response_usage = {}
        line = 'data: {"type":"message_delta","usage":{"output_tokens":5}}'
        _process_sse_data_line(line, set(), [], response_usage)
        assert response_usage == {"output_tokens": 5}

    def test_event_types_respects_max(self):
        """MAX_EVENT_TYPES is a module-level limit; test that we don't exceed it."""
        event_types = []
        for i in range(100):
            line = f'data: {{"type":"event_{i}","index":0}}'
            _process_sse_data_line(line, set(), event_types, {})
        # MAX_EVENT_TYPES is 50, so we should have at most 50
        assert len(event_types) <= 50

    def test_json_parsed_once(self):
        """Verify that a single line is parsed only once (merged function)."""
        event_types = []
        line = 'data: {"type":"content_block_start","index":0,"content_block":{"type":"text"}}'
        _process_sse_data_line(line, set(), event_types, {})
        # If JSON were parsed twice, this line would be added twice
        assert event_types == ["content_block_start"]


# ---------------------------------------------------------------------------
# _build_response_headers
# ---------------------------------------------------------------------------


class TestBuildResponseHeaders:
    def test_strips_transfer_encoding(self):
        resp = MagicMock()
        resp.headers = MagicMock()
        resp.headers.items.return_value = [
            ("content-type", "application/json"),
            ("transfer-encoding", "chunked"),
        ]
        headers = _build_response_headers(resp, is_sse=False)
        assert "content-type" in headers
        assert "transfer-encoding" not in headers

    def test_strips_content_length_for_sse(self):
        resp = MagicMock()
        resp.headers = MagicMock()
        resp.headers.items.return_value = [
            ("content-type", "text/event-stream"),
            ("content-length", "9999"),
        ]
        headers = _build_response_headers(resp, is_sse=True)
        assert "content-type" in headers
        assert "content-length" not in headers

    def test_keeps_content_length_for_non_sse(self):
        resp = MagicMock()
        resp.headers = MagicMock()
        resp.headers.items.return_value = [
            ("content-type", "application/json"),
            ("content-length", "123"),
        ]
        headers = _build_response_headers(resp, is_sse=False)
        assert "content-length" in headers

    def test_case_insensitive_strip(self):
        resp = MagicMock()
        resp.headers = MagicMock()
        resp.headers.items.return_value = [
            ("Transfer-Encoding", "chunked"),
        ]
        headers = _build_response_headers(resp, is_sse=False)
        assert "Transfer-Encoding" not in headers


# ---------------------------------------------------------------------------
# _summarize_request
# ---------------------------------------------------------------------------


class TestSummarizeRequest:
    def test_basic(self):
        summary = _summarize_request(
            {
                "model": "deepseek-v4-2-0528",
                "stream": True,
                "max_tokens": 4096,
                "messages": [{"role": "user", "content": "hi"}],
            }
        )
        assert summary["model"] == "deepseek-v4-2-0528"
        assert summary["stream"] is True
        assert summary["max_tokens"] == 4096
        assert summary["messages"] == 1

    def test_string_system(self):
        summary = _summarize_request({"system": "You are helpful", "messages": []})
        assert summary["system_len"] == len("You are helpful")

    def test_list_system(self):
        summary = _summarize_request(
            {
                "system": [
                    {"type": "text", "text": "System prompt A"},
                    {"type": "text", "text": "System prompt B"},
                    {"type": "text", "text": "System prompt C"},  # beyond slice [:2]
                ],
                "messages": [],
            }
        )
        # system_len 是拼接后字符串的长度
        assert summary["system_len"] > 0

    def test_tools_basic(self):
        summary = _summarize_request(
            {
                "tools": [{"name": "read"}, {"name": "write"}],
                "messages": [],
            }
        )
        assert summary["tools"] == 2
        assert summary["tool_names"] == ["read", "write"]

    def test_tools_not_list(self):
        summary = _summarize_request({"tools": "not_a_list", "messages": []})
        assert summary["tools"] == 0

    def test_tool_names_truncated_at_10(self):
        tools = [{"name": f"tool_{i}"} for i in range(15)]
        summary = _summarize_request({"tools": tools, "messages": []})
        assert len(summary["tool_names"]) == 10


