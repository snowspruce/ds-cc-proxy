# ds-cc-proxy / proxy — core proxy logic
#
# Environment variables:
#   PROXY_UPSTREAM         DeepSeek API base URL (default https://api.deepseek.com/anthropic)
#   PROXY_FLASH_UPSTREAM   Sub-agent Flash upstream URL (default same as PROXY_UPSTREAM)
#   PROXY_FLASH_MODEL      Sub-agent model override, e.g. deepseek-v4-flash (default empty)
#   PROXY_HOST             Listen address (default 127.0.0.1)
#   PROXY_PORT             Listen port (default 16889)
#   PROXY_LOG_LEVEL        Log level (default warning)
#   PROXY_LOG_FILE         Log file path (default empty = stdout only)
#   PROXY_LOG_MAX_BYTES    Max log file size (default 10MB)
#   PROXY_LOG_BACKUP_COUNT Rotation backup count (default 3)
#   PROXY_MAX_BODY_BYTES   Max request body size (default 10MB)
#   PROXY_DUMP_DIR         Traffic capture dir (default empty = off, ⚠ contains secrets)
#
# Reference: https://api-docs.deepseek.com/guides/thinking_mode

from __future__ import annotations

import asyncio
import json
import logging
import logging.handlers
import os
import random as _random
import sys
import time as _time
from contextlib import asynccontextmanager

import httpx
from starlette.applications import Starlette
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from ds_cc_proxy._version import VERSION

# ---- Configuration ----

DEEPSEEK_BASE = os.getenv("PROXY_UPSTREAM", "https://api.deepseek.com/anthropic")
DEEPSEEK_FLASH = os.getenv("PROXY_FLASH_UPSTREAM", DEEPSEEK_BASE)
FLASH_MODEL = os.getenv("PROXY_FLASH_MODEL", "")  # sub-agent model override, e.g. deepseek-v4-flash
HOST = os.getenv("PROXY_HOST", "127.0.0.1")
try:
    PORT = int(os.getenv("PROXY_PORT", "16889"))
except (TypeError, ValueError):
    print("Error: PROXY_PORT must be an integer", file=sys.stderr)
    sys.exit(1)
LOG_LEVEL = os.getenv("PROXY_LOG_LEVEL", "warning")
DUMP_DIR = os.getenv("PROXY_DUMP_DIR", "")


def _parse_env_int(name: str, default: int, min_val: int | None = None) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        val = int(raw)
    except (TypeError, ValueError):
        print(f"Error: {name}={raw!r} must be an integer, using default {default}", file=sys.stderr)
        return default
    if min_val is not None and val < min_val:
        print(f"Error: {name}={val} must be >= {min_val}, using default {default}", file=sys.stderr)
        return default
    return val


def _parse_env_float(name: str, default: float, min_val: float | None = None) -> float:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        val = float(raw)
    except (TypeError, ValueError):
        print(f"Error: {name}={raw!r} must be a number, using default {default}", file=sys.stderr)
        return default
    if min_val is not None and val < min_val:
        print(f"Error: {name}={val} must be >= {min_val}, using default {default}", file=sys.stderr)
        return default
    return val


PROXY_POOL_MAX_CONNECTIONS = _parse_env_int("PROXY_POOL_MAX_CONNECTIONS", 50, min_val=1)
PROXY_POOL_MAX_KEEPALIVE = _parse_env_int("PROXY_POOL_MAX_KEEPALIVE", 20, min_val=0)
if PROXY_POOL_MAX_KEEPALIVE > PROXY_POOL_MAX_CONNECTIONS:
    PROXY_POOL_MAX_KEEPALIVE = PROXY_POOL_MAX_CONNECTIONS
PROXY_POOL_TIMEOUT = _parse_env_float("PROXY_POOL_TIMEOUT", 120.0, min_val=1.0)
PROXY_UPSTREAM_TIMEOUT = _parse_env_float("PROXY_UPSTREAM_TIMEOUT", 600.0, min_val=1.0)
PROXY_CONNECT_TIMEOUT = _parse_env_float("PROXY_CONNECT_TIMEOUT", 10.0, min_val=1.0)
MAX_BODY_BYTES = _parse_env_int("PROXY_MAX_BODY_BYTES", 10 * 1024 * 1024, min_val=1024)
RETRY_MAX = _parse_env_int("PROXY_RETRY_MAX", 3, min_val=0)
RETRY_BACKOFF = _parse_env_float("PROXY_RETRY_BACKOFF", 1.0, min_val=0.1)
CB_THRESHOLD = _parse_env_int("PROXY_CIRCUIT_BREAKER_THRESHOLD", 5, min_val=1)
CB_TIMEOUT = _parse_env_float("PROXY_CIRCUIT_BREAKER_TIMEOUT", 30.0, min_val=5.0)

# Dangerous hop-by-hop headers to strip from inbound requests
_REQUEST_STRIP_HEADERS = {
    "host",
    "transfer-encoding",
    "connection",
    "upgrade",
    "proxy-authorization",
    "proxy-connection",
    "proxy-authenticate",
    "te",
    "trailer",
    "keep-alive",
}

# SSE stream processing limits
MAX_EVENT_TYPES = 50
MAX_FILTERED_LINES = 200
DUMP_PREVIEW_LINES = 30
DUMP_MAX_BYTES = 500000
LOG_EVENT_PREVIEW = 15
LOG_FILE = os.getenv("PROXY_LOG_FILE", "")
LOG_MAX_BYTES = _parse_env_int("PROXY_LOG_MAX_BYTES", 10 * 1024 * 1024, min_val=1024)
LOG_BACKUP_COUNT = _parse_env_int("PROXY_LOG_BACKUP_COUNT", 3, min_val=0)

log_format = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
log_level = getattr(logging, LOG_LEVEL.upper(), logging.WARNING)

_stream_handler = logging.StreamHandler(sys.stdout)
_stream_handler.setFormatter(log_format)

_app_logger = logging.getLogger("deepseek-proxy")
_app_logger.setLevel(log_level)
_app_logger.handlers.clear()
_app_logger.addHandler(_stream_handler)

if LOG_FILE:
    _file_handler = logging.handlers.RotatingFileHandler(
        LOG_FILE, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT, encoding="utf-8"
    )
    _file_handler.setFormatter(log_format)
    _app_logger.addHandler(_file_handler)

logger = _app_logger

_shared_client: httpx.AsyncClient | None = None

# ---- Usage tracking ----

_usage = {"requests": 0, "input_tokens": 0, "output_tokens": 0, "cache_read": 0}
_usage_primary = {"requests": 0, "input_tokens": 0, "output_tokens": 0}
_usage_subagent = {"requests": 0, "input_tokens": 0, "output_tokens": 0}


def _track_usage(role: str, usage: dict):
    global _usage, _usage_primary, _usage_subagent
    inp = usage.get("input_tokens", 0)
    out = usage.get("output_tokens", 0)
    cr = usage.get("cache_read_input_tokens", 0)

    _usage["requests"] += 1
    _usage["input_tokens"] += inp
    _usage["output_tokens"] += out
    _usage["cache_read"] += cr

    bucket = _usage_subagent if role == "subagent" else _usage_primary
    bucket["requests"] += 1
    bucket["input_tokens"] += inp
    bucket["output_tokens"] += out


async def usage_endpoint(request):
    n = _usage["requests"]
    inp = _usage["input_tokens"]
    out = _usage["output_tokens"]
    cache = _usage["cache_read"]
    cacheable = inp + cache
    hit_pct = (cache * 100 // cacheable) if cacheable > 0 else 0
    # Cost model: DeepSeek V4 Pro ~$0.50/MTok input, ~$2.00/MTok output
    est_cost = round(inp / 1_000_000 * 0.50 + out / 1_000_000 * 2.00, 3)

    # Savings estimate for sub-agents
    sub = _usage_subagent
    sub_reqs = sub["requests"]
    sub_out = sub["output_tokens"]
    # Without budget_tokens=2048, deepseek would use default ~4000 thinking tok/req
    # Budget caps it at ~2000 — saving ~2000 output tok/req (≈50% of actual out)
    saved_thinking_tokens = sub_out  # actual output ≈ 50% of what it would be
    saved_thinking_cost = round(saved_thinking_tokens / 1_000_000 * 2.00, 3)

    return JSONResponse(
        {
            "requests": n,
            "input_tokens": inp,
            "output_tokens": out,
            "cache_hit_pct": hit_pct,
            "estimated_cost_usd": est_cost,
            "subagent_requests": sub_reqs,
            "subagent_saved_thinking_tokens": saved_thinking_tokens,
            "estimated_saved_usd": saved_thinking_cost,
            "primary": None if not _usage_primary["requests"] else dict(_usage_primary),
            "subagent": dict(sub),
        }
    )


# ---- Circuit breaker ----

_circuit_state = "closed"  # closed | open | half_open
_circuit_failures = 0
_circuit_opened_at = 0.0

_RETRYABLE_STATUS = {429, 502, 503}
_RETRYABLE_EXCEPTIONS = (
    httpx.TimeoutException,
    httpx.ConnectError,
    httpx.RemoteProtocolError,
)


def _circuit_allow() -> bool:
    """Return True if the circuit allows a request through."""
    global _circuit_state, _circuit_failures, _circuit_opened_at
    if _circuit_state == "closed":
        return True
    if _circuit_state == "open":
        if _time.monotonic() - _circuit_opened_at >= CB_TIMEOUT:
            _circuit_state = "half_open"
            logger.warning("[CB] circuit half-open — allowing trial request")
            return True
        return False
    # half_open — allow one trial
    return True


def _circuit_success():
    global _circuit_state, _circuit_failures
    if _circuit_state == "half_open":
        _circuit_state = "closed"
        _circuit_failures = 0
        logger.info("[CB] circuit closed — upstream recovered")
    elif _circuit_state == "closed":
        _circuit_failures = 0


def _circuit_failure():
    global _circuit_state, _circuit_failures, _circuit_opened_at
    _circuit_failures += 1
    if _circuit_state == "half_open" or (
        _circuit_state == "closed" and _circuit_failures >= CB_THRESHOLD
    ):
        _circuit_state = "open"
        _circuit_opened_at = _time.monotonic()
        logger.warning(
            "[CB] circuit open — %d consecutive failures, blocking for %.0fs",
            _circuit_failures,
            CB_TIMEOUT,
        )


# ---- httpx client ----


def _get_client() -> httpx.AsyncClient:
    global _shared_client
    if _shared_client is None or _shared_client.is_closed:
        _shared_client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                PROXY_UPSTREAM_TIMEOUT,
                connect=PROXY_CONNECT_TIMEOUT,
                pool=PROXY_POOL_TIMEOUT,
            ),
            limits=httpx.Limits(
                max_keepalive_connections=PROXY_POOL_MAX_KEEPALIVE,
                max_connections=PROXY_POOL_MAX_CONNECTIONS,
            ),
        )
    return _shared_client


# ---- Health check ----


async def health(request):
    return JSONResponse(
        {
            "status": "ok",
            "version": VERSION,
            "upstream": DEEPSEEK_BASE,
        }
    )


# ---- Fix 1: request-side thinking injection ----


def _has_tool_use(content: list) -> bool:
    return any(isinstance(b, dict) and b.get("type") == "tool_use" for b in content)


def _has_thinking(content: list) -> bool:
    return any(
        isinstance(b, dict) and b.get("type") in ("thinking", "redacted_thinking") for b in content
    )


def _inject_thinking_blocks(data: dict) -> bool:
    thinking_cfg = data.get("thinking", {})
    if not isinstance(thinking_cfg, dict):
        return False
    if thinking_cfg.get("type") != "enabled":
        return False

    model = data.get("model", "")
    if not isinstance(model, str) or not model.startswith("deepseek-v4"):
        return False

    modified = False
    for msg in data.get("messages", []):
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content", [])
        if not isinstance(content, list):
            continue
        if _has_tool_use(content) and not _has_thinking(content):
            for i, block in enumerate(content):
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    content.insert(i, {"type": "thinking", "thinking": ""})
                    modified = True
                    break
    return modified


# ---- Fix 2: thinking mode normalization ----


def _normalize_thinking(data: dict) -> bool:
    if "thinking" not in data:
        return False
    thinking_cfg = data["thinking"]
    if not isinstance(thinking_cfg, dict):
        return False

    thinking_type = thinking_cfg.get("type", "")

    # adaptive — primary session optimal path, passthrough unchanged
    if thinking_type == "adaptive":
        # Keep output_config (effort=high); DeepSeek V4 natively supports adaptive
        return False

    # enabled — valid, passthrough
    if thinking_type == "enabled":
        return False

    # disabled — sub-agent hardcoded value, rejected by DeepSeek V4 on some requests
    # Convert to enabled + minimal budget (sub-agents don't need deep thinking,
    # but a small budget ensures sufficient reasoning quality)
    if thinking_type == "disabled":
        data["thinking"] = {"type": "enabled", "budget_tokens": 2048}

        for key in ("reasoning_effort", "output_config"):
            val = data.pop(key, None)
            if val is not None:
                logger.info("[THINKING] removed %s=%s", key, val)

        stripped = 0
        for msg in data.get("messages", []):
            if msg.get("role") != "assistant":
                continue
            content = msg.get("content", [])
            if not isinstance(content, list):
                continue
            new_content = [
                b
                for b in content
                if not (isinstance(b, dict) and b.get("type") in ("thinking", "redacted_thinking"))
            ]
            if len(new_content) != len(content):
                stripped += len(content) - len(new_content)
                msg["content"] = new_content

        logger.info(
            "[THINKING] converted disabled → enabled, stripped %d thinking blocks",
            stripped,
        )
        return True

    # unknown type — leave unchanged
    return False


# ---- Fix 3: response-side thinking stripping ----


def _thinking_requested(data: dict) -> bool:
    thinking_cfg = data.get("thinking", {})
    return isinstance(thinking_cfg, dict) and thinking_cfg.get("type") in ("enabled", "adaptive")


def _process_sse_data_line(
    line: str, thinking_indices: set, event_types: list, response_usage: dict
) -> tuple:
    """Parse and process a ``data:`` SSE line — track event types and filter thinking blocks.

    Parses JSON once, then performs both event-type tracking and thinking-index management.
    Returns ``(filtered_line_or_None, thinking_indices)``.
    """
    if not line.startswith("data: "):
        return line, thinking_indices

    try:
        data = json.loads(line[6:])
    except json.JSONDecodeError:
        return line, thinking_indices

    if not isinstance(data, dict):
        return line, thinking_indices

    t = data.get("type", "")

    # Event type tracking
    if len(event_types) < MAX_EVENT_TYPES:
        event_types.append(t if t else "?")
    if t in ("message_stop", "message_delta"):
        u = data.get("usage")
        if isinstance(u, dict):
            response_usage.update(u)

    # Thinking block filtering
    if t == "content_block_start":
        cb = data.get("content_block", {})
        if cb.get("type") == "thinking":
            idx = data.get("index")
            if idx is not None:
                thinking_indices.add(idx)
            return None, thinking_indices

    elif t in ("content_block_delta", "content_block_stop"):
        idx = data.get("index")
        if idx in thinking_indices:
            if t == "content_block_stop":
                thinking_indices.discard(idx)
            return None, thinking_indices

    return line, thinking_indices


# ---- Traffic capture ----


if DUMP_DIR:
    os.makedirs(DUMP_DIR, exist_ok=True)
    logger.warning(
        "⚠ PROXY_DUMP_DIR enabled — data saved to %s. "
        "Request/response bodies may contain API keys, tokens, and other secrets. "
        "Use only for debugging and delete contents when done.",
        DUMP_DIR,
    )


def _dump_json(filename: str, data):
    if not DUMP_DIR:
        return
    try:
        path = os.path.join(DUMP_DIR, filename)
        s = json.dumps(data, ensure_ascii=False, indent=2, default=str)
        if len(s) > DUMP_MAX_BYTES:
            s = s[:DUMP_MAX_BYTES] + f"\n\n... [TRUNCATED at {DUMP_MAX_BYTES // 1000}KB]"
        with open(path, "w") as f:
            f.write(s)
        logger.info("[DUMP] %s (%d bytes)", filename, len(s))
    except OSError as e:
        logger.warning("[DUMP] failed to write %s: %s", filename, e)


def _summarize_request(data: dict) -> dict:
    msgs = data.get("messages", [])
    tools = data.get("tools", [])
    system = data.get("system", "")
    if isinstance(system, list):
        system = " ".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in system[:2])
    return {
        "model": data.get("model", "?"),
        "stream": data.get("stream", False),
        "max_tokens": data.get("max_tokens", "?"),
        "thinking": data.get("thinking", "not set"),
        "messages": len(msgs),
        "tools": len(tools) if isinstance(tools, list) else 0,
        "tool_names": [
            t.get("name", "?") if isinstance(t, dict) else "?"
            for t in (tools[:10] if isinstance(tools, list) else [])
        ],
        "system_len": len(system),
    }


# ---- Request handling ----


def _build_response_headers(upstream_resp, is_sse: bool) -> dict:
    strip_keys = {"transfer-encoding"}
    if is_sse:
        strip_keys.add("content-length")
    return {k: v for k, v in upstream_resp.headers.items() if k.lower() not in strip_keys}


async def proxy(request):
    # S0: reject new requests during shutdown
    if _shutting_down:
        return JSONResponse(
            {"error": {"message": "server shutting down", "type": "shutting_down"}},
            status_code=503,
            headers={"Retry-After": "10"},
        )

    method = request.method
    raw_path = request.url.path

    # S1: prevent path traversal
    if ".." in raw_path:
        logger.warning("[SEC] path traversal attempt: %s", raw_path)
        return JSONResponse(
            {"error": {"message": "bad request", "type": "invalid_path"}},
            status_code=400,
        )

    path = "/" + raw_path.lstrip("/")
    upstream_url = f"{DEEPSEEK_BASE}{path}"

    # S2: strip dangerous request headers
    headers = {k: v for k, v in request.headers.items() if k.lower() not in _REQUEST_STRIP_HEADERS}

    is_messages = method == "POST" and path.rstrip("/").endswith("/messages")

    # S9: limit request body size
    if is_messages:
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > MAX_BODY_BYTES:
                    logger.warning("[SEC] request body too large: %s bytes", content_length)
                    return JSONResponse(
                        {
                            "error": {
                                "message": "request body too large",
                                "type": "payload_too_large",
                            }
                        },
                        status_code=413,
                    )
            except (TypeError, ValueError):
                pass

    body = await request.body() if is_messages else b""

    # S9: double-check actual body size after read
    if is_messages and len(body) > MAX_BODY_BYTES:
        logger.warning("[SEC] request body exceeds limit after read: %d bytes", len(body))
        return JSONResponse(
            {
                "error": {
                    "message": "request body too large",
                    "type": "payload_too_large",
                }
            },
            status_code=413,
        )

    modified_body = body
    strip_thinking = True

    if is_messages:
        is_subagent = False
        model_name = "?"
        try:
            data = json.loads(body)
            model_name = data.get("model", "?")
            logger.info("[REQ] %s", json.dumps(_summarize_request(data), ensure_ascii=False))
            _dump_json("last_request.json", data)

            # Capture original thinking type before _normalize_thinking mutates in-place
            thinking_cfg = data.get("thinking", {})
            is_subagent = isinstance(thinking_cfg, dict) and thinking_cfg.get("type") == "disabled"

            original_thinking_enabled = _thinking_requested(data)

            thinking_normalized = _normalize_thinking(data)

            if _inject_thinking_blocks(data):
                logger.info("[INJECT] added empty thinking block")
                thinking_normalized = True

            if original_thinking_enabled:
                strip_thinking = False
            else:
                logger.info("[STRIP] response filter enabled")

            if is_subagent:
                # Sub-agent routing: switch to Flash upstream + override model name
                upstream_url = f"{DEEPSEEK_FLASH}{path}"
                if FLASH_MODEL:
                    data["model"] = FLASH_MODEL
                    logger.info("[FLASH] routing to %s model=%s", DEEPSEEK_FLASH, FLASH_MODEL)
                else:
                    logger.info("[FLASH] routing to %s (model unchanged)", DEEPSEEK_FLASH)
                thinking_normalized = True

            if thinking_normalized:
                modified_body = json.dumps(data, ensure_ascii=False).encode("utf-8")
                headers["content-length"] = str(len(modified_body))
                _dump_json("last_request_modified.json", data)

        except json.JSONDecodeError:
            logger.warning("[REQ] invalid JSON body, forwarding as-is")
        except (KeyError, TypeError):
            logger.exception("[REQ] unexpected body structure, forwarding as-is")

    client = _get_client()

    # Retry loop with circuit breaker
    last_exc = None
    upstream_resp = None
    for attempt in range(RETRY_MAX + 1):
        if not _circuit_allow():
            logger.warning("[CB] circuit open — rejecting request")
            return JSONResponse(
                {"error": {"message": "upstream temporarily unavailable", "type": "circuit_open"}},
                status_code=503,
                headers={"Retry-After": str(int(CB_TIMEOUT))},
            )

        try:
            req = client.build_request(
                method=method,
                url=upstream_url,
                headers=headers,
                content=modified_body,
            )
            upstream_resp = await client.send(req, stream=True)
            _circuit_success()
            break
        except _RETRYABLE_EXCEPTIONS as exc:
            last_exc = exc
            _circuit_failure()
            if attempt < RETRY_MAX:
                delay = RETRY_BACKOFF * (2**attempt) + _random.uniform(0, 0.5)
                logger.warning(
                    "[RETRY] attempt %d/%d, retrying in %.1fs: %s",
                    attempt + 1,
                    RETRY_MAX,
                    delay,
                    exc,
                )
                await asyncio.sleep(delay)
        except httpx.PoolTimeout:
            logger.warning("upstream pool exhausted, returning 503")
            return JSONResponse(
                {"error": {"message": "upstream busy, retry later", "type": "pool_exhausted"}},
                status_code=503,
                headers={"Retry-After": "10"},
            )
        except Exception:
            _circuit_failure()
            logger.exception("upstream request failed: %s %s", method, upstream_url)
            return JSONResponse(
                {"error": {"message": "upstream unavailable", "type": "proxy_error"}},
                status_code=502,
            )
    else:
        logger.error("[RETRY] all %d attempts exhausted: %s", RETRY_MAX, last_exc)
        return JSONResponse(
            {
                "error": {
                    "message": "upstream unavailable after retries",
                    "type": "upstream_timeout",
                }
            },
            status_code=502,
        )

    content_type = upstream_resp.headers.get("content-type", "")
    is_sse = "text/event-stream" in content_type
    logger.info("[RESP] status=%s sse=%s", upstream_resp.status_code, is_sse)

    # If upstream returned an error, passthrough raw response regardless of content-type
    if upstream_resp.status_code >= 400:
        logger.warning("[RESP] upstream error %s, passthrough", upstream_resp.status_code)

        async def error_passthrough():
            try:
                async for chunk in upstream_resp.aiter_bytes():
                    yield chunk
            except Exception:
                logger.exception("upstream error stream read error")
            finally:
                try:
                    await upstream_resp.aclose()
                except Exception:
                    logger.debug("upstream_resp aclose error")

        return StreamingResponse(
            error_passthrough(),
            status_code=upstream_resp.status_code,
            headers=_build_response_headers(upstream_resp, False),
        )

    if not strip_thinking or not is_sse:

        async def passthrough():
            try:
                async for chunk in upstream_resp.aiter_bytes():
                    yield chunk
            except Exception:
                logger.exception("upstream stream read error")
            finally:
                try:
                    await upstream_resp.aclose()
                except Exception:
                    logger.debug("upstream_resp aclose error (may already be closed)")

        return StreamingResponse(
            passthrough(),
            status_code=upstream_resp.status_code,
            headers=_build_response_headers(upstream_resp, is_sse),
        )

    logger.info("[FILTER] stripping thinking from SSE stream")

    async def filtered_stream():
        thinking_indices = set()
        event_types = []
        all_filtered = []
        buffer = ""
        max_buffer_bytes = 1024 * 1024  # 1MB
        response_usage = {}

        try:
            async for chunk in upstream_resp.aiter_bytes():
                text = chunk.decode("utf-8", errors="replace")
                buffer += text
                if len(buffer) > max_buffer_bytes:
                    logger.warning("[FILTER] SSE buffer overflow, truncating")
                    yield buffer.encode("utf-8")
                    buffer = ""
                    continue

                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.rstrip("\r")  # C1: handle SSE \r\n per spec

                    filtered, thinking_indices = _process_sse_data_line(
                        line, thinking_indices, event_types, response_usage
                    )
                    if filtered is not None:
                        if len(all_filtered) < MAX_FILTERED_LINES:
                            all_filtered.append(filtered)
                        yield (filtered + "\n").encode("utf-8")

            # C1: handle trailing buffer (rstrip \r as above)
            buffer = buffer.rstrip("\r")
            if buffer.strip():
                filtered, thinking_indices = _process_sse_data_line(
                    buffer, thinking_indices, event_types, response_usage
                )
                if filtered is not None:
                    yield (filtered + "\n").encode("utf-8")

        except Exception:
            logger.exception("upstream stream read error")
        finally:
            logger.info("[RESP-EVENTS] raw=%s", event_types[:LOG_EVENT_PREVIEW])
            logger.info("[RESP-FILTERED] lines=%d", len(all_filtered))
            if response_usage:
                role = "subagent" if is_subagent else "primary"
                cache_read = response_usage.get("cache_read_input_tokens", 0)
                cache_create = response_usage.get("cache_creation_input_tokens", 0)
                total_input = response_usage.get("input_tokens", 0)
                cacheable = total_input + cache_read + cache_create
                hit_pct = (cache_read * 100 // cacheable) if cacheable > 0 else 0
                logger.info(
                    "[COST] role=%s model=%s input=%s output=%s cache_read=%s cache_hit=%s%%",
                    role,
                    model_name,
                    total_input,
                    response_usage.get("output_tokens", 0),
                    cache_read,
                    hit_pct,
                )
                _track_usage(role, response_usage)
            _dump_json(
                "last_response_events.json",
                {
                    "raw_events": event_types,
                    "filtered_count": len(all_filtered),
                    "first_filtered": all_filtered[:DUMP_PREVIEW_LINES],
                    "usage": response_usage,
                },
            )
            try:
                await upstream_resp.aclose()
            except Exception:
                logger.debug("upstream_resp aclose error")

    return StreamingResponse(
        filtered_stream(),
        status_code=upstream_resp.status_code,
        headers=_build_response_headers(upstream_resp, is_sse=True),
    )


# ---- Application factory ----

_shutting_down = False


@asynccontextmanager
async def lifespan(app):
    global _shutting_down
    logger.info("started v%s (upstream=%s)", VERSION, DEEPSEEK_BASE)
    yield
    logger.info("shutting down — draining active connections")
    _shutting_down = True
    # Allow a grace period for in-flight requests to complete
    try:
        await asyncio.sleep(5)
    except asyncio.CancelledError:
        pass
    if _shared_client and not _shared_client.is_closed:
        await _shared_client.aclose()
    logger.info("shutdown complete")


def create_app() -> Starlette:
    return Starlette(
        lifespan=lifespan,
        routes=[
            Route("/health", health, methods=["GET"]),
            Route("/usage", usage_endpoint, methods=["GET"]),
            Route(
                "/{path:path}",
                proxy,
                methods=["POST"],
            ),
        ],
    )
