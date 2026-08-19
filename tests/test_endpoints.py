# Endpoint 集成测试 (httpx ASGI transport)
#
# 通过 ASGI transport 直接驱动 ds_cc_proxy.proxy.create_app() 创建的 Starlette app,
# 覆盖以下三个只读/管理端点:
#   - GET  /health
#   - POST /admin/circuit/reset
#   - GET  /usage

import httpx
import pytest
import pytest_asyncio

from ds_cc_proxy import proxy as proxy_module
from ds_cc_proxy.proxy import create_app


@pytest_asyncio.fixture
async def client():
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_health_endpoint(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "version" in data
    assert "upstream" in data
    assert "circuit" in data


@pytest.mark.asyncio
async def test_admin_reset_circuit(client):
    resp = await client.post("/admin/circuit/reset")
    assert resp.status_code == 200
    data = resp.json()
    assert data["circuit"] == "reset"
    assert data["state"] == "closed"
    assert "previous" in data


@pytest.mark.asyncio
async def test_usage_endpoint_structure(client):
    resp = await client.get("/usage")
    assert resp.status_code == 200
    data = resp.json()
    # 顶层字段
    for key in (
        "requests",
        "input_tokens",
        "output_tokens",
        "cache_hit_pct",
        "estimated_cost_usd",
        "primary",
        "subagent",
        "subagent_saved_thinking_tokens",
        "estimated_saved_usd",
    ):
        assert key in data
    # 计数与成本类型
    assert isinstance(data["requests"], int)
    assert isinstance(data["estimated_cost_usd"], float)
    # subagent 桶始终存在
    assert isinstance(data["subagent"], dict)
    for key in ("requests", "input_tokens", "output_tokens"):
        assert key in data["subagent"]
    # 按模型拆分的 token 计数 (pro/flash × input/cache_read/output)
    assert set(data["models"]) == {"pro", "flash"}
    for family in ("pro", "flash"):
        for key in ("input", "cache_read", "output"):
            assert isinstance(data["models"][family][key], int)


class _FakeUpstreamResp:
    """Fake httpx streaming response exposing aiter_bytes/headers/status/aclose."""

    def __init__(self, chunks, content_type="text/event-stream", status_code=200):
        self._chunks = chunks
        self.status_code = status_code
        self.headers = httpx.Headers({"content-type": content_type, "x-custom": "yes"})

    async def aiter_bytes(self):
        for chunk in self._chunks:
            yield chunk

    async def aclose(self):
        pass


class _FakeClient:
    def __init__(self, resp):
        self._resp = resp
        self.built = None

    def build_request(self, method, url, headers=None, content=None):
        self.built = (method, url, headers, content)
        return httpx.Request(method, url)

    async def send(self, request, stream=False):
        return self._resp


@pytest.mark.asyncio
async def test_proxy_streaming_response_and_body(monkeypatch):
    """Drive POST /messages through ASGI transport, exercising StreamingResponse
    send path and Request.body()."""
    proxy_module._shutting_down = False
    proxy_module._circuit_state = "closed"
    proxy_module._circuit_failure_times.clear()
    proxy_module._circuit_failure_weight = 0.0

    sse_chunk = b'data: {"type":"message_delta","usage":{"output_tokens":1}}\n\n'
    fake_resp = _FakeUpstreamResp([sse_chunk])
    fake_client = _FakeClient(fake_resp)
    monkeypatch.setattr(proxy_module, "_get_client", lambda: fake_client)

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        payload = {"model": "deepseek-v4-pro", "stream": True, "thinking": {"type": "enabled"}}
        resp = await c.post("/v1/messages", json=payload)
        assert resp.status_code == 200
        body = resp.content
        assert sse_chunk in body
        # request body was read and forwarded as content
        assert fake_client.built is not None
        assert fake_client.built[3] is not None


@pytest.mark.asyncio
async def test_proxy_json_error_passthrough(monkeypatch):
    """POST returning a JSON error upstream exercises the error JSONResponse path."""
    proxy_module._shutting_down = False
    proxy_module._circuit_state = "closed"
    proxy_module._circuit_failure_times.clear()
    proxy_module._circuit_failure_weight = 0.0

    fake_resp = _FakeUpstreamResp([b'{"error": "boom"}'], "application/json", 500)
    monkeypatch.setattr(proxy_module, "_get_client", lambda: _FakeClient(fake_resp))

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post("/v1/messages", json={"model": "x", "stream": False})
        assert resp.status_code == 500
        assert b"boom" in resp.content


@pytest.mark.asyncio
async def test_flash_model_routes_as_subagent(monkeypatch):
    """flash 模型名的请求应按子代理路由到 Flash 上游（不依赖 thinking=disabled）。"""
    proxy_module._shutting_down = False
    proxy_module._circuit_state = "closed"
    proxy_module._circuit_failure_times.clear()
    proxy_module._circuit_failure_weight = 0.0

    monkeypatch.setattr(proxy_module, "DEEPSEEK_BASE", "https://pro.example.com")
    monkeypatch.setattr(proxy_module, "DEEPSEEK_FLASH", "https://flash.example.com")

    sse_chunk = b'data: {"type":"message_delta","usage":{"output_tokens":1}}\n\n'
    fake_resp = _FakeUpstreamResp([sse_chunk])
    fake_client = _FakeClient(fake_resp)
    monkeypatch.setattr(proxy_module, "_get_client", lambda: fake_client)

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        payload = {"model": "deepseek-v4-flash", "stream": True}
        resp = await c.post("/v1/messages", json=payload)
        assert resp.status_code == 200

    assert fake_client.built is not None
    method, url, _headers, _content = fake_client.built
    assert url.startswith("https://flash.example.com")
