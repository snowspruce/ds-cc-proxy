# ds-cc-proxy 单元测试
#
# 覆盖 proxy.py 中所有纯函数逻辑:
#   - env var 解析
#   - thinking 注入 / 标准化 / 检测
#   - SSE 行过滤
#   - 请求摘要 & 响应头构建

import socket
import time
from unittest.mock import MagicMock

import httpx
import pytest

import ds_cc_proxy.proxy as proxy_module
from ds_cc_proxy.proxy import (
    _billing_cost_rmb,
    _build_response_headers,
    _dns_error_severity,
    _extract_usage,
    _has_thinking,
    _has_tool_use,
    _inject_thinking_blocks,
    _is_peak,
    _log_and_track_usage,
    _normalize_thinking,
    _parse_env_float,
    _parse_env_int,
    _process_sse_data_line,
    _scan_sse_usage,
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


# ---------------------------------------------------------------------------
# _dns_error_severity / DNS 熔断分级
# ---------------------------------------------------------------------------


def _make_gaierror():
    return socket.gaierror(8, "nodename nor servname provided, or not known")


def _reset_circuit(proxy):
    proxy._circuit_failure_times.clear()
    proxy._circuit_failure_weight = 0.0
    proxy._circuit_state = "closed"
    proxy._circuit_backoff_level = 0
    proxy._circuit_opened_at = 0.0
    proxy._circuit_last_close_at = 0.0


class TestDnsErrorSeverity:
    def test_raw_gaierror(self):
        assert _dns_error_severity(_make_gaierror()) is not None

    def test_returns_low_severity_for_gaierror(self):
        import ds_cc_proxy.proxy as proxy

        assert _dns_error_severity(_make_gaierror()) == proxy._DNS_ERROR_SEVERITY

    def test_httpx_wrapped_gaierror(self):
        gai = _make_gaierror()
        wrapped = httpx.ConnectError(str(gai))
        wrapped.__cause__ = gai  # 模拟 httpx `raise ... from exc`
        assert _dns_error_severity(wrapped) is not None

    def test_deep_chain_httpcore_gaierror(self):
        # 真实链路: httpx.ConnectError -> httpcore.ConnectError -> socket.gaierror
        gai = _make_gaierror()
        httpcore_err = Exception(str(gai))
        httpcore_err.__cause__ = gai
        httpx_err = httpx.ConnectError(str(httpcore_err))
        httpx_err.__cause__ = httpcore_err
        assert _dns_error_severity(httpx_err) is not None

    def test_context_only_gaierror(self):
        # `raise ConnectError(...) from None` 会把 gaierror 挂到 __context__ 而非 __cause__
        gai = _make_gaierror()
        wrapped = httpx.ConnectError(str(gai))
        wrapped.__context__ = gai
        assert _dns_error_severity(wrapped) is not None

    def test_connect_error_without_dns(self):
        assert _dns_error_severity(httpx.ConnectError("Connection refused")) is None

    def test_timeout_not_dns(self):
        assert _dns_error_severity(httpx.TimeoutException("timed out")) is None

    def test_remote_protocol_error_not_dns(self):
        assert _dns_error_severity(httpx.RemoteProtocolError("server disconnected")) is None


class TestCircuitFailureDnsGrading:
    def test_transient_dns_blip_does_not_trip_circuit(self):
        import ds_cc_proxy.proxy as proxy

        _reset_circuit(proxy)

        for _ in range(proxy.CB_THRESHOLD):
            proxy._circuit_failure(_make_gaierror())

        # CB_THRESHOLD 次 DNS 失败 = CB_THRESHOLD * 0.25 权重, 未达到阈值
        assert proxy._circuit_failure_weight == pytest.approx(
            proxy.CB_THRESHOLD * proxy._DNS_ERROR_SEVERITY
        )
        assert proxy._circuit_state == "closed"
        assert len(proxy._circuit_failure_times) == proxy.CB_THRESHOLD

    def test_sustained_dns_outage_trips_circuit(self):
        import ds_cc_proxy.proxy as proxy

        _reset_circuit(proxy)

        # 持续 DNS 故障: 需要 int(CB_THRESHOLD / severity) + 1 次才能越过阈值
        n = int(proxy.CB_THRESHOLD / proxy._DNS_ERROR_SEVERITY) + 1
        for _ in range(n):
            proxy._circuit_failure(_make_gaierror())

        assert proxy._circuit_state == "open"

    def test_non_dns_failure_still_counts_at_full_weight(self):
        import ds_cc_proxy.proxy as proxy

        _reset_circuit(proxy)

        proxy._circuit_failure(httpx.ConnectError("Connection refused"))

        assert proxy._circuit_failure_weight == 1.0
        assert len(proxy._circuit_failure_times) == 1


# ---------------------------------------------------------------------------
# _scan_sse_usage / _extract_usage (passthrough 路径的 usage 采集)
# ---------------------------------------------------------------------------


class TestScanSseUsage:
    def test_extracts_message_delta_usage(self):
        usage = {}
        line = (
            'data: {"type":"message_delta","usage":'
            '{"input_tokens":120,"output_tokens":30,"cache_read_input_tokens":100}}'
        )
        _scan_sse_usage(line, usage)
        assert usage == {
            "input_tokens": 120,
            "output_tokens": 30,
            "cache_read_input_tokens": 100,
        }

    def test_extracts_message_stop_usage(self):
        usage = {}
        _scan_sse_usage('data: {"type":"message_stop","usage":{"output_tokens":7}}', usage)
        assert usage == {"output_tokens": 7}

    def test_ignores_non_usage_events(self):
        usage = {}
        _scan_sse_usage('data: {"type":"content_block_delta","index":0}', usage)
        assert usage == {}

    def test_ignores_non_data_lines(self):
        usage = {}
        _scan_sse_usage("event: message_stop", usage)
        assert usage == {}

    def test_ignores_invalid_json(self):
        usage = {}
        _scan_sse_usage("data: not valid json", usage)
        assert usage == {}

    def test_ignores_non_dict_usage(self):
        usage = {}
        _scan_sse_usage('data: {"type":"message_delta","usage":"nope"}', usage)
        assert usage == {}

    def test_merges_multiple_events(self):
        usage = {}
        _scan_sse_usage('data: {"type":"message_delta","usage":{"output_tokens":5}}', usage)
        _scan_sse_usage('data: {"type":"message_stop","usage":{"input_tokens":10}}', usage)
        assert usage == {"output_tokens": 5, "input_tokens": 10}

    def test_handles_crlf_line(self):
        usage = {}
        _scan_sse_usage(
            'data: {"type":"message_delta","usage":{"output_tokens":3}}\r', usage
        )
        assert usage == {"output_tokens": 3}


class TestExtractUsage:
    def test_copies_from_message_stop(self):
        usage = {}
        _extract_usage(
            {"type": "message_stop", "usage": {"input_tokens": 1, "output_tokens": 2}},
            usage,
        )
        assert usage == {"input_tokens": 1, "output_tokens": 2}

    def test_ignores_other_types(self):
        usage = {}
        _extract_usage({"type": "message_start"}, usage)
        assert usage == {}

    def test_ignores_missing_usage(self):
        usage = {}
        _extract_usage({"type": "message_delta"}, usage)
        assert usage == {}


# ---------------------------------------------------------------------------
# _log_and_track_usage (/usage 计数器累积)
# ---------------------------------------------------------------------------


def _reset_usage_counters():
    proxy_module._usage.update(requests=0, input_tokens=0, output_tokens=0, cache_read=0)
    proxy_module._usage_primary.update(requests=0, input_tokens=0, output_tokens=0, cache_read=0)
    proxy_module._usage_subagent.update(requests=0, input_tokens=0, output_tokens=0, cache_read=0)
    proxy_module._billing = proxy_module._empty_billing()
    proxy_module._billing_subagent = proxy_module._empty_billing()


class TestLogAndTrackUsage:
    def test_tracks_primary_usage(self):
        _reset_usage_counters()
        _log_and_track_usage(
            "primary",
            "deepseek-v4-pro",
            {
                "input_tokens": 1000,
                "output_tokens": 50,
                "cache_read_input_tokens": 600,
            },
        )
        assert proxy_module._usage == {
            "requests": 1,
            "input_tokens": 1000,
            "output_tokens": 50,
            "cache_read": 600,
        }
        assert proxy_module._usage_primary["requests"] == 1
        assert proxy_module._usage_primary["input_tokens"] == 1000
        assert proxy_module._usage_subagent["requests"] == 0

    def test_tracks_subagent_usage(self):
        _reset_usage_counters()
        _log_and_track_usage(
            "subagent",
            "deepseek-v4-flash",
            {"input_tokens": 87, "output_tokens": 16},
        )
        assert proxy_module._usage_subagent["requests"] == 1
        assert proxy_module._usage_subagent["output_tokens"] == 16
        assert proxy_module._usage_primary["requests"] == 0

    def test_empty_usage_not_tracked(self):
        _reset_usage_counters()
        _log_and_track_usage("primary", "deepseek-v4-pro", {})
        assert proxy_module._usage["requests"] == 0
        assert proxy_module._usage_primary["requests"] == 0

    def test_cache_hit_line_logged(self, caplog):
        _reset_usage_counters()
        with caplog.at_level("INFO", logger="deepseek-proxy"):
            _log_and_track_usage(
                "primary",
                "deepseek-v4-pro",
                {
                    "input_tokens": 500,
                    "output_tokens": 20,
                    "cache_read_input_tokens": 500,
                },
            )
        assert any("cache_hit=50%" in rec.message for rec in caplog.records)

    def test_tracks_usage_with_pristine_buckets(self):
        """回归: v0.1.27 事故 — 生产初始化无 cache_read 键时 _track_usage 抛 KeyError,
        异常从流式 finally 传播导致响应被掐断 (compaction: Connection closed mid-response)。"""
        proxy_module._usage = {"requests": 0, "input_tokens": 0, "output_tokens": 0, "cache_read": 0}
        proxy_module._usage_primary = {"requests": 0, "input_tokens": 0, "output_tokens": 0}
        proxy_module._usage_subagent = {"requests": 0, "input_tokens": 0, "output_tokens": 0}
        proxy_module._billing = proxy_module._empty_billing()
        proxy_module._billing_subagent = proxy_module._empty_billing()
        _log_and_track_usage(
            "primary",
            "deepseek-v4-pro",
            {"input_tokens": 1, "output_tokens": 1, "cache_read_input_tokens": 1},
        )
        assert proxy_module._usage_primary["cache_read"] == 1
        assert proxy_module._usage["requests"] == 1

    def test_tracking_failure_never_raises(self, monkeypatch):
        """指标统计的任何异常都必须被吞掉, 绝不允许破坏响应流。"""
        _reset_usage_counters()

        def boom(*args, **kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(proxy_module, "_track_usage", boom)
        _log_and_track_usage("primary", "deepseek-v4-pro", {"input_tokens": 1, "output_tokens": 1})
        # 不抛异常即通过


# ---------------------------------------------------------------------------
# 2026-08-17 峰谷定价计费
# ---------------------------------------------------------------------------


class TestIsPeak:
    def _at(self, hour):
        return time.struct_time((2026, 8, 17, hour, 0, 0, 0, 229, 0))

    def test_peak_morning(self):
        assert _is_peak(self._at(9)) is True
        assert _is_peak(self._at(11)) is True

    def test_peak_afternoon(self):
        assert _is_peak(self._at(14)) is True
        assert _is_peak(self._at(17)) is True

    def test_off_peak(self):
        assert _is_peak(self._at(8)) is False
        assert _is_peak(self._at(12)) is False
        assert _is_peak(self._at(13)) is False
        assert _is_peak(self._at(18)) is False
        assert _is_peak(self._at(0)) is False


class TestBillingBuckets:
    def test_pro_peak_bucket(self, monkeypatch):
        _reset_usage_counters()
        monkeypatch.setattr(proxy_module, "_is_peak", lambda now=None: True)
        _log_and_track_usage(
            "primary",
            "deepseek-v4-pro",
            {"input_tokens": 1000, "output_tokens": 100, "cache_read_input_tokens": 9000},
        )
        assert proxy_module._billing["pro"]["peak"] == {
            "input": 1000,
            "cache_read": 9000,
            "output": 100,
        }
        assert proxy_module._billing["pro"]["off"] == {"input": 0, "cache_read": 0, "output": 0}
        assert proxy_module._billing_subagent["pro"]["peak"] == {
            "input": 0,
            "cache_read": 0,
            "output": 0,
        }

    def test_flash_off_bucket_and_subagent_split(self, monkeypatch):
        _reset_usage_counters()
        monkeypatch.setattr(proxy_module, "_is_peak", lambda now=None: False)
        _log_and_track_usage(
            "subagent",
            "deepseek-v4-flash",
            {"input_tokens": 500, "output_tokens": 50, "cache_read_input_tokens": 4000},
        )
        assert proxy_module._billing["flash"]["off"] == {
            "input": 500,
            "cache_read": 4000,
            "output": 50,
        }
        assert proxy_module._billing_subagent["flash"]["off"] == {
            "input": 500,
            "cache_read": 4000,
            "output": 50,
        }

    def test_unknown_model_treated_as_pro(self, monkeypatch):
        _reset_usage_counters()
        monkeypatch.setattr(proxy_module, "_is_peak", lambda now=None: True)
        _log_and_track_usage("primary", "?", {"input_tokens": 10, "output_tokens": 1})
        assert proxy_module._billing["pro"]["peak"]["input"] == 10


class TestBillingCost:
    def test_cost_rmb_peak_pro(self, monkeypatch):
        _reset_usage_counters()
        monkeypatch.setattr(proxy_module, "_is_peak", lambda now=None: True)
        _log_and_track_usage(
            "primary",
            "deepseek-v4-pro",
            {
                "input_tokens": 1_000_000,
                "output_tokens": 1_000_000,
                "cache_read_input_tokens": 1_000_000,
            },
        )
        # peak pro: miss 9.0 + hit 0.30 + out 27.0 = 36.3
        assert _billing_cost_rmb(proxy_module._billing) == pytest.approx(36.3)

    def test_cost_rmb_off_flash(self, monkeypatch):
        _reset_usage_counters()
        monkeypatch.setattr(proxy_module, "_is_peak", lambda now=None: False)
        _log_and_track_usage(
            "primary",
            "deepseek-v4-flash",
            {
                "input_tokens": 1_000_000,
                "output_tokens": 2_000_000,
                "cache_read_input_tokens": 3_000_000,
            },
        )
        # off flash: miss 1.5 + out 2*4.5=9.0 + hit 3*0.05=0.15 = 10.65
        assert _billing_cost_rmb(proxy_module._billing) == pytest.approx(10.65)

    def test_subagent_savings_peak(self, monkeypatch):
        _reset_usage_counters()
        monkeypatch.setattr(proxy_module, "_is_peak", lambda now=None: True)
        _log_and_track_usage(
            "subagent",
            "deepseek-v4-flash",
            {
                "input_tokens": 1_000_000,
                "output_tokens": 1_000_000,
                "cache_read_input_tokens": 1_000_000,
            },
        )
        # peak delta (pro - flash): miss 9-3 + hit 0.30-0.10 + out 27-9 = 24.2
        sb = proxy_module._billing_subagent["flash"]
        saved = sum(
            sb[w]["input"] / 1e6 * (proxy_module.PRICES_RMB["pro"][w]["miss"] - proxy_module.PRICES_RMB["flash"][w]["miss"])
            + sb[w]["cache_read"] / 1e6 * (proxy_module.PRICES_RMB["pro"][w]["hit"] - proxy_module.PRICES_RMB["flash"][w]["hit"])
            + sb[w]["output"] / 1e6 * (proxy_module.PRICES_RMB["pro"][w]["out"] - proxy_module.PRICES_RMB["flash"][w]["out"])
            for w in sb
        )
        assert saved == pytest.approx(24.2)
