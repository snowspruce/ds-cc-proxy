# ds-cc-proxy app 工厂 / 生命周期 / 客户端 / 健康检查 / 流量抓取 单元测试
#
# 覆盖 proxy.py 中此前的未覆盖区域:
#   - _get_client           客户端创建 / 复用 / 超龄重建 / 已关闭重建
#   - _circuit_health_check / _start_health_check / _stop_health_check  后台健康检查
#   - lifespan / create_app  app 工厂与生命周期
#   - usage_endpoint        /usage 返回结构（含 primary / subagent / 成本字段）
#   - _dump_json            流量抓取（DUMP_DIR 启用 / 未启用）
#
# 约定与 test_circuit.py 一致: 时间通过 proxy._time.monotonic() 注入,
# 测试用 monkeypatch 控制。

import asyncio
import json

import httpx
import pytest

import ds_cc_proxy.proxy as proxy


class _FakeClock:
    """可手动推进的单调时钟，取代 proxy._time.monotonic。"""

    def __init__(self, start: float = 0.0):
        self.now = start

    def advance(self, seconds: float) -> float:
        self.now += seconds
        return self.now

    def monotonic(self) -> float:
        return self.now

    @classmethod
    def patch(cls, monkeypatch, start: float = 0.0):
        fake = cls(start)
        monkeypatch.setattr(proxy._time, "monotonic", fake.monotonic)
        return fake


def _reset_client_and_circuit():
    """重置客户端与熔断器相关的模块级状态。"""
    proxy._shared_client = None
    proxy._client_created_at = 0.0
    proxy._circuit_state = "closed"
    proxy._circuit_failure_times.clear()
    proxy._circuit_failure_weight = 0.0
    proxy._circuit_opened_at = 0.0
    proxy._circuit_backoff_level = 0
    proxy._circuit_last_close_at = 0.0
    proxy._circuit_health_task = None
    proxy._shutting_down = False


def _run(coro):
    """在独立事件循环中运行协程，返回结果。"""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# _get_client
# ---------------------------------------------------------------------------


class TestGetClient:
    def test_creates_new_client_when_none(self, monkeypatch):
        _FakeClock.patch(monkeypatch, start=1000.0)
        _reset_client_and_circuit()
        client = proxy._get_client()
        assert isinstance(client, httpx.AsyncClient)
        assert not client.is_closed
        assert proxy._shared_client is client
        assert proxy._client_created_at == 1000.0

    def test_reuses_existing_client(self, monkeypatch):
        clock = _FakeClock.patch(monkeypatch, start=1000.0)
        _reset_client_and_circuit()
        first = proxy._get_client()
        clock.advance(1.0)  # 未超过 CLIENT_MAX_AGE → 复用
        second = proxy._get_client()
        assert second is first
        assert proxy._shared_client is first

    def test_recreates_after_max_age(self, monkeypatch):
        clock = _FakeClock.patch(monkeypatch, start=1000.0)
        _reset_client_and_circuit()
        first = proxy._get_client()
        clock.advance(proxy.CLIENT_MAX_AGE + 1.0)
        second = proxy._get_client()
        assert second is not first
        assert proxy._shared_client is second

    def test_recreates_when_closed(self, monkeypatch):
        _FakeClock.patch(monkeypatch, start=1000.0)
        _reset_client_and_circuit()
        client = proxy._get_client()

        async def _close():
            await client.aclose()

        _run(_close())
        assert client.is_closed

        second = proxy._get_client()
        assert second is not client
        assert not second.is_closed


class TestCircuitDeprecatedStaleClient:
    """熔断触发后 _circuit_failure 丢弃连接池，_get_client 会重建。"""

    def test_circuit_discards_pool_then_recreates(self, monkeypatch):
        _FakeClock.patch(monkeypatch, start=1000.0)
        _reset_client_and_circuit()
        original = proxy._get_client()
        assert proxy._shared_client is original

        for _ in range(proxy.CB_THRESHOLD):
            proxy._circuit_failure(httpx.ConnectError("boom"))

        assert proxy._circuit_state == "open"  # 熔断已触发
        assert proxy._shared_client is None
        assert proxy._client_created_at == 0.0

        fresh = proxy._get_client()
        assert fresh is not original
        assert proxy._shared_client is fresh


# ---------------------------------------------------------------------------
# _start_health_check / _stop_health_check
# ---------------------------------------------------------------------------


class TestHealthCheckLifecycle:
    def test_start_creates_task_not_done(self, monkeypatch):
        _FakeClock.patch(monkeypatch)
        _reset_client_and_circuit()

        def _body():
            proxy._start_health_check()
            task = proxy._circuit_health_task
            assert task is not None
            assert not task.done()
            proxy._stop_health_check()

        _run(_run_sync(_body))
        assert proxy._shutting_down is False

    def test_start_is_idempotent(self, monkeypatch):
        _FakeClock.patch(monkeypatch)
        _reset_client_and_circuit()

        def _body():
            proxy._start_health_check()
            first = proxy._circuit_health_task
            proxy._start_health_check()  # 第二次不应新建任务
            assert proxy._circuit_health_task is first
            proxy._stop_health_check()

        _run(_run_sync(_body))

    def test_start_restarts_after_done(self, monkeypatch):
        _FakeClock.patch(monkeypatch)
        _reset_client_and_circuit()

        async def _body():
            proxy._start_health_check()
            first = proxy._circuit_health_task
            first.cancel()
            # 让取消真正完成, 任务进入 done 状态
            await asyncio.gather(first, return_exceptions=True)
            assert first.done()
            proxy._start_health_check()  # done -> 重新创建
            assert proxy._circuit_health_task is not first
            proxy._stop_health_check()

        _run(_body())


def _run_sync(fn):
    """包装同步函数为协程：在运行中的事件循环上执行，让 create_task 可用。"""

    async def _inner():
        fn()

    return _inner()


class TestHealthCheckCancellation:
    def test_stop_cancels_running_task(self, monkeypatch):
        _FakeClock.patch(monkeypatch)
        _reset_client_and_circuit()

        def _body():
            proxy._start_health_check()
            task = proxy._circuit_health_task
            proxy._stop_health_check()
            assert task.cancelling() or task.cancelled() or task.done()

        _run(_run_sync(_body))

    def test_stop_noop_when_no_task(self, monkeypatch):
        _FakeClock.patch(monkeypatch)
        _reset_client_and_circuit()
        proxy._stop_health_check()  # 无任务, 不应抛异常
        assert proxy._circuit_health_task is None

    def test_stop_noop_when_task_done(self, monkeypatch):
        _FakeClock.patch(monkeypatch)
        _reset_client_and_circuit()

        async def _body():
            proxy._start_health_check()
            task = proxy._circuit_health_task
            # 直接取消并等待完成, 任务进入 done
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            assert task.done()
            proxy._stop_health_check()  # done -> 不应抛异常

        _run(_body())


# ---------------------------------------------------------------------------
# _circuit_health_check — 后台探测逻辑
# ---------------------------------------------------------------------------


class TestCircuitHealthCheck:
    def test_closed_circuit_skips_probe_and_sleeps(self, monkeypatch):
        """非 open 状态下, 健康检查只 sleep 一轮, 不发起探测请求。"""
        _reset_client_and_circuit()
        proxy._circuit_state = "closed"

        sleeps = []

        async def _fake_sleep(seconds):
            sleeps.append(seconds)
            # 第一轮 sleep 后停止, 避免死循环
            proxy._shutting_down = True

        monkeypatch.setattr(proxy.asyncio, "sleep", _fake_sleep)

        _run(proxy._circuit_health_check())

        assert sleeps == [proxy.CB_HEALTH_INTERVAL]

    def test_open_circuit_successful_probe_resets(self, monkeypatch):
        """open 状态 + 上游返回 <500 时, 熔断器应被重置为 closed。"""
        clock = _FakeClock.patch(monkeypatch)
        _reset_client_and_circuit()
        proxy._circuit_state = "open"
        proxy._circuit_failure_times.append((clock.now, 1.0))
        proxy._circuit_failure_weight = 999.0
        proxy._circuit_backoff_level = 3
        proxy._circuit_last_close_at = 0.0

        client = proxy._get_client()

        async def _fake_get(url, timeout=None):
            return _MockResp(200)

        client.get = _fake_get  # type: ignore[method-assign]

        async def _fake_sleep(seconds):
            proxy._shutting_down = True  # 探测一轮后退出

        monkeypatch.setattr(proxy.asyncio, "sleep", _fake_sleep)

        _run(proxy._circuit_health_check())

        assert proxy._circuit_state == "closed"
        assert len(proxy._circuit_failure_times) == 0
        assert proxy._circuit_failure_weight == 0.0
        assert proxy._circuit_backoff_level == 0
        assert proxy._circuit_last_close_at == clock.now

    def test_open_circuit_server_error_keeps_open(self, monkeypatch):
        """上游返回 >=500 时不重置, 熔断器保持 open。"""
        _FakeClock.patch(monkeypatch)
        _reset_client_and_circuit()
        proxy._circuit_state = "open"
        proxy._circuit_backoff_level = 2

        client = proxy._get_client()

        async def _fake_get(url, timeout=None):
            return _MockResp(500)

        client.get = _fake_get  # type: ignore[method-assign]

        async def _fake_sleep(seconds):
            proxy._shutting_down = True

        monkeypatch.setattr(proxy.asyncio, "sleep", _fake_sleep)

        _run(proxy._circuit_health_check())

        assert proxy._circuit_state == "open"
        assert proxy._circuit_backoff_level == 2

    def test_open_circuit_probe_exception_is_swallowed(self, monkeypatch):
        """探测抛异常时应被吞掉, 熔断器保持 open, 循环继续 sleep。"""
        _FakeClock.patch(monkeypatch)
        _reset_client_and_circuit()
        proxy._circuit_state = "open"

        client = proxy._get_client()

        async def _fake_get(url, timeout=None):
            raise httpx.ConnectError("down")

        client.get = _fake_get  # type: ignore[method-assign]

        sleeps = []

        async def _fake_sleep(seconds):
            sleeps.append(seconds)
            proxy._shutting_down = True

        monkeypatch.setattr(proxy.asyncio, "sleep", _fake_sleep)

        _run(proxy._circuit_health_check())

        assert proxy._circuit_state == "open"
        assert sleeps == [proxy.CB_HEALTH_INTERVAL]


class _MockResp:
    def __init__(self, status_code):
        self.status_code = status_code


# ---------------------------------------------------------------------------
# lifespan / create_app
# ---------------------------------------------------------------------------


class TestLifespan:
    def test_lifespan_flips_shutting_down(self, monkeypatch):
        _reset_client_and_circuit()

        async def _fake_sleep(seconds):
            return None

        monkeypatch.setattr(proxy.asyncio, "sleep", _fake_sleep)

        state = {}

        async def _run_lifespan():
            async with proxy.lifespan(None):
                state["entered"] = proxy._shutting_down
            state["exited"] = proxy._shutting_down

        _run(_run_lifespan())

        assert state["entered"] is False  # 进入时未 shutting down
        assert state["exited"] is True  # 退出后已 shutting down


class TestCreateApp:
    def test_create_app_routes(self):
        app = proxy.create_app()

        async def _drive():
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
                assert (await c.get("/health")).status_code == 200
                assert (await c.get("/usage")).status_code == 200
                assert (await c.post("/admin/circuit/reset")).status_code == 200
                assert (await c.get("/no-such-path")).status_code == 404

        _run(_drive())

    def test_create_app_lifespan_attached(self, monkeypatch):
        app = proxy.create_app()
        assert callable(app)

        # 避免 lifespan shutdown 的 5s 真实 sleep，并让后台健康检查不启动
        # （防止其自旋占用事件循环）。保存真实 sleep 供驱动循环 yield 用。
        orig_sleep = asyncio.sleep

        async def _fake_sleep(seconds):
            return None

        monkeypatch.setattr(proxy.asyncio, "sleep", _fake_sleep)
        monkeypatch.setattr(proxy, "_start_health_check", lambda: None)
        monkeypatch.setattr(proxy, "_stop_health_check", lambda: None)

        _reset_client_and_circuit()
        result = {}

        async def _drive():
            inbox = asyncio.Queue()
            sent = result["sent"] = []

            async def _receive():
                return await inbox.get()

            async def _send(message):
                sent.append(message)

            await inbox.put({"type": "lifespan.startup"})
            task = asyncio.ensure_future(app({"type": "lifespan"}, _receive, _send))
            while not any(m["type"] == "lifespan.startup.complete" for m in sent):
                await orig_sleep(0)
            assert proxy._shutting_down is False
            await inbox.put({"type": "lifespan.shutdown"})
            await task

        _run(_drive())
        sent = result["sent"]
        assert any(m["type"] == "lifespan.startup.complete" for m in sent)
        assert any(m["type"] == "lifespan.shutdown.complete" for m in sent)
        assert proxy._shutting_down is True


# ---------------------------------------------------------------------------
# usage_endpoint — /usage 返回结构补全
# ---------------------------------------------------------------------------


def _reset_usage():
    proxy._usage.update(requests=0, input_tokens=0, output_tokens=0, cache_read=0)
    proxy._usage_primary.update(requests=0, input_tokens=0, output_tokens=0)
    proxy._usage_subagent.update(requests=0, input_tokens=0, output_tokens=0)


class TestUsageEndpoint:
    def test_zero_state(self):
        _reset_usage()
        resp = _run(proxy.usage_endpoint(None))
        data = json.loads(resp.body)
        assert data["requests"] == 0
        assert data["input_tokens"] == 0
        assert data["output_tokens"] == 0
        assert data["cache_hit_pct"] == 0
        assert data["estimated_cost_usd"] == 0.0
        assert data["primary"] is None  # 无 primary 请求时返回 None
        assert isinstance(data["subagent"], dict)
        assert data["subagent_saved_thinking_tokens"] == 0
        assert data["estimated_saved_usd"] == 0.0

    def test_with_primary_usage(self):
        _reset_usage()
        proxy._track_usage(
            "primary",
            {
                "input_tokens": 1_000_000,
                "output_tokens": 1_000_000,
                "cache_read_input_tokens": 500_000,
            },
        )
        resp = _run(proxy.usage_endpoint(None))
        data = json.loads(resp.body)
        assert data["requests"] == 1
        assert data["input_tokens"] == 1_000_000
        assert data["output_tokens"] == 1_000_000
        # cache_read=500k, input=1M -> cacheable=1.5M -> 500k*100//1.5M = 33
        assert data["cache_hit_pct"] == 33
        # cost = 1M/1M*0.42 + 1M/1M*0.83 = 1.25
        assert data["estimated_cost_usd"] == pytest.approx(1.25)
        assert data["primary"] == {
            "requests": 1,
            "input_tokens": 1_000_000,
            "output_tokens": 1_000_000,
        }

    def test_with_subagent_and_savings(self):
        _reset_usage()
        proxy._track_usage(
            "subagent",
            {"input_tokens": 1_000_000, "output_tokens": 1_000_000},
        )
        resp = _run(proxy.usage_endpoint(None))
        data = json.loads(resp.body)
        assert data["subagent"]["requests"] == 1
        assert data["subagent_saved_thinking_tokens"] == 1_000_000
        # 子代理无缓存 -> cache_hit_pct = 0
        assert data["cache_hit_pct"] == 0
        # 输入节省 1M*(0.42-0.14)=0.28, 输出节省 1M*0.83=0.83 -> 1.11
        assert data["estimated_saved_usd"] == pytest.approx(1.11)


# ---------------------------------------------------------------------------
# _dump_json
# ---------------------------------------------------------------------------


class TestDumpJson:
    def test_noop_when_dump_disabled(self, monkeypatch):
        monkeypatch.setattr(proxy, "DUMP_DIR", "")
        # 不应写任何文件, 也不应抛异常
        proxy._dump_json("test.json", {"a": 1})

    def test_writes_file_when_enabled(self, monkeypatch, tmp_path):
        monkeypatch.setattr(proxy, "DUMP_DIR", str(tmp_path))
        proxy._dump_json("capture.json", {"hello": "世界"})
        written = tmp_path / "capture.json"
        assert written.exists()
        data = json.loads(written.read_text())
        assert data["hello"] == "世界"

    def test_writes_non_json_serializable(self, monkeypatch, tmp_path):
        monkeypatch.setattr(proxy, "DUMP_DIR", str(tmp_path))
        proxy._dump_json("obj.json", {"fn": object()})
        assert (tmp_path / "obj.json").exists()

    def test_truncates_large_payload(self, monkeypatch, tmp_path):
        monkeypatch.setattr(proxy, "DUMP_DIR", str(tmp_path))
        big = "x" * (proxy.DUMP_MAX_BYTES + 1000)
        proxy._dump_json("big.json", {"data": big})
        written = tmp_path / "big.json"
        assert written.exists()
        content = written.read_text()
        assert "TRUNCATED" in content
        assert len(content) <= proxy.DUMP_MAX_BYTES + 100
