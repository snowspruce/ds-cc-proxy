# ds-cc-proxy / proxy — 核心代理逻辑
#
# 环境变量:
#   PROXY_UPSTREAM    DeepSeek API 地址 (默认 https://api.deepseek.com/anthropic)
#   PROXY_HOST        监听地址 (默认 127.0.0.1)
#   PROXY_PORT        监听端口 (默认 16889)
#   PROXY_LOG_LEVEL   日志级别 (默认 warning)
#   PROXY_LOG_FILE    日志文件路径 (默认空=仅 stdout)
#   PROXY_LOG_MAX_BYTES  日志文件最大字节数 (默认 10MB)
#   PROXY_LOG_BACKUP_COUNT 轮转备份数量 (默认 3)
#   PROXY_DUMP_DIR    流量捕获目录 (默认空=关闭, ⚠ 含敏感数据)
#
# 参考: https://api-docs.deepseek.com/guides/thinking_mode

from __future__ import annotations

import asyncio
import json
import logging
import logging.handlers
import os
import sys
from contextlib import asynccontextmanager

import httpx
from starlette.applications import Starlette
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from ds_cc_proxy._version import VERSION

# ---- 配置 ----

DEEPSEEK_BASE = os.getenv("PROXY_UPSTREAM", "https://api.deepseek.com/anthropic")
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

# 需要从客户端请求头中剥离的危险头
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

# SSE 流处理参数上限
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


# ---- httpx 客户端 ----


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


# ---- 健康检查 ----


async def health(request):
    return JSONResponse(
        {
            "status": "ok",
            "version": VERSION,
            "upstream": DEEPSEEK_BASE,
        }
    )


# ---- 修复 1: 请求端 thinking 注入 ----


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


# ---- 修复 2: thinking 模式标准化 ----


def _normalize_thinking(data: dict) -> bool:
    if "thinking" not in data:
        return False
    thinking_cfg = data["thinking"]
    if not isinstance(thinking_cfg, dict):
        return False

    thinking_type = thinking_cfg.get("type", "")

    # adaptive + effort=high → 主会话最优路径，不做任何修改
    if thinking_type == "adaptive":
        # 保留 output_config (effort=high)，DeepSeek v4 原生支持 adaptive
        return False

    # enabled → 合法，透传
    if thinking_type == "enabled":
        return False

    # disabled → 子代理硬编码值，DeepSeek v4 在某些请求下拒绝
    # 转为 enabled 并移除 reasoning_effort/output_config
    if thinking_type == "disabled":
        data["thinking"] = {"type": "enabled"}

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

    # 未知类型 → 不处理
    return False


# ---- 修复 3: 响应端 thinking 剥离 ----


def _thinking_requested(data: dict) -> bool:
    thinking_cfg = data.get("thinking", {})
    return isinstance(thinking_cfg, dict) and thinking_cfg.get("type") in ("enabled", "adaptive")


def _filter_sse_line(line: str, thinking_indices: set) -> tuple:
    if not line.startswith("data: "):
        return line, thinking_indices

    try:
        data = json.loads(line[6:])
    except json.JSONDecodeError:
        return line, thinking_indices

    if not isinstance(data, dict):
        return line, thinking_indices

    t = data.get("type", "")

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


# ---- 流量捕获 ----


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


# ---- 请求处理 ----


def _build_response_headers(upstream_resp, is_sse: bool) -> dict:
    strip_keys = {"transfer-encoding"}
    if is_sse:
        strip_keys.add("content-length")
    return {k: v for k, v in upstream_resp.headers.items() if k.lower() not in strip_keys}


async def proxy(request):
    # S0: shutdown 期间拒绝新请求
    if _shutting_down:
        return JSONResponse(
            {"error": {"message": "server shutting down", "type": "shutting_down"}},
            status_code=503,
            headers={"Retry-After": "10"},
        )

    method = request.method
    raw_path = request.url.path

    # S1: 防止路径穿越
    if ".." in raw_path:
        logger.warning("[SEC] path traversal attempt: %s", raw_path)
        return JSONResponse(
            {"error": {"message": "bad request", "type": "invalid_path"}},
            status_code=400,
        )

    path = "/" + raw_path.lstrip("/")
    upstream_url = f"{DEEPSEEK_BASE}{path}"

    # S2: 剥离危险请求头
    headers = {k: v for k, v in request.headers.items() if k.lower() not in _REQUEST_STRIP_HEADERS}

    is_messages = method == "POST" and path.rstrip("/").endswith("/messages")

    # S9: 限制请求体大小
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

    # S9: 双重检查实际 body 大小
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
        try:
            data = json.loads(body)
            logger.info("[REQ] %s", json.dumps(_summarize_request(data), ensure_ascii=False))
            _dump_json("last_request.json", data)

            original_thinking_enabled = _thinking_requested(data)

            thinking_normalized = _normalize_thinking(data)

            if _inject_thinking_blocks(data):
                logger.info("[INJECT] added empty thinking block")
                thinking_normalized = True

            if original_thinking_enabled:
                strip_thinking = False
            else:
                logger.info("[STRIP] response filter enabled")

            if thinking_normalized:
                modified_body = json.dumps(data, ensure_ascii=False).encode("utf-8")
                headers["content-length"] = str(len(modified_body))
                _dump_json("last_request_modified.json", data)

        except json.JSONDecodeError:
            logger.warning("[REQ] invalid JSON body, forwarding as-is")
        except (KeyError, TypeError):
            logger.exception("[REQ] unexpected body structure, forwarding as-is")

    client = _get_client()

    try:
        req = client.build_request(
            method=method,
            url=upstream_url,
            headers=headers,
            content=modified_body,
        )
        upstream_resp = await client.send(req, stream=True)
    except httpx.PoolTimeout:
        logger.warning("upstream pool exhausted, returning 503")
        return JSONResponse(
            {"error": {"message": "upstream busy, retry later", "type": "pool_exhausted"}},
            status_code=503,
            headers={"Retry-After": "10"},
        )
    except Exception:
        logger.exception("upstream request failed: %s %s", method, upstream_url)
        return JSONResponse(
            {"error": {"message": "upstream unavailable", "type": "proxy_error"}},
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

        try:
            async for chunk in upstream_resp.aiter_bytes():
                text = chunk.decode("utf-8", errors="replace")
                buffer += text
                if len(buffer) > max_buffer_bytes:
                    logger.warning("[FILTER] SSE buffer overflow, truncating")
                    # Flush buffer as-is and reset
                    yield buffer.encode("utf-8")
                    buffer = ""
                    continue

                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    # C1: 兼容 SSE 规范的 \r\n 行分隔符
                    line = line.rstrip("\r")

                    if line.startswith("data: ") and len(event_types) < MAX_EVENT_TYPES:
                        try:
                            d = json.loads(line[6:])
                            if isinstance(d, dict):
                                event_types.append(d.get("type", "?"))
                        except json.JSONDecodeError:
                            pass

                    filtered, thinking_indices = _filter_sse_line(line, thinking_indices)
                    if filtered is not None:
                        if len(all_filtered) < MAX_FILTERED_LINES:
                            all_filtered.append(filtered)
                        yield (filtered + "\n").encode("utf-8")

            # C1: 处理尾部缓冲区（同样 rstrip \r）
            buffer = buffer.rstrip("\r")
            if buffer.strip():
                if buffer.startswith("data: ") and len(event_types) < MAX_EVENT_TYPES:
                    try:
                        d = json.loads(buffer[6:])
                        if isinstance(d, dict):
                            event_types.append(d.get("type", "?"))
                    except json.JSONDecodeError:
                        pass
                filtered, thinking_indices = _filter_sse_line(buffer, thinking_indices)
                if filtered is not None:
                    yield (filtered + "\n").encode("utf-8")

        except Exception:
            logger.exception("upstream stream read error")
        finally:
            logger.info("[RESP-EVENTS] raw=%s", event_types[:LOG_EVENT_PREVIEW])
            logger.info("[RESP-FILTERED] lines=%d", len(all_filtered))
            _dump_json(
                "last_response_events.json",
                {
                    "raw_events": event_types,
                    "filtered_count": len(all_filtered),
                    "first_filtered": all_filtered[:DUMP_PREVIEW_LINES],
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


# ---- 应用工厂 ----

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
            Route(
                "/{path:path}",
                proxy,
                methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
            ),
        ],
    )
