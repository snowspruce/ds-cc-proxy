# ds-cc-proxy 熔断器状态机单元测试
#
# 覆盖 proxy.py 中断路器 (circuit breaker) 的状态机迁移:
#   closed -> open -> half_open -> closed
# 以及 _circuit_prune_window / _circuit_backoff_timeout 的滑动窗口与退避逻辑。
#
# 时间统一通过 proxy._time.monotonic() 注入; 测试用 monkeypatch 控制时钟。

import socket

import httpx

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


class _ClockFixture:
    """自动 monkeypatch proxy._time.monotonic 并暴露可控时钟。"""

    def __init__(self, monkeypatch, start: float = 0.0):
        self._fake = _FakeClock(start)
        monkeypatch.setattr(proxy._time, "monotonic", self._fake.monotonic)

    def advance(self, seconds: float) -> float:
        return self._fake.advance(seconds)

    @property
    def now(self) -> float:
        return self._fake.now


def _reset_circuit():
    """将断路器模块级状态重置为初始 closed 状态。"""
    proxy._circuit_state = "closed"
    proxy._circuit_failure_times.clear()
    proxy._circuit_failure_weight = 0.0
    proxy._circuit_opened_at = 0.0
    proxy._circuit_backoff_level = 0
    proxy._circuit_last_close_at = 0.0


class TestCircuitAllow:
    def test_closed_allows(self, monkeypatch):
        _ClockFixture(monkeypatch)
        _reset_circuit()
        assert proxy._circuit_allow() is True

    def test_open_blocks_before_timeout(self, monkeypatch):
        clock = _ClockFixture(monkeypatch)
        _reset_circuit()
        proxy._circuit_state = "open"
        proxy._circuit_opened_at = clock.now
        proxy._circuit_backoff_level = 0
        clock.advance(proxy.CB_TIMEOUT_BASE - 1.0)  # 仍在退避期内
        assert proxy._circuit_allow() is False

    def test_open_transitions_to_half_open_after_timeout(self, monkeypatch):
        clock = _ClockFixture(monkeypatch)
        _reset_circuit()
        proxy._circuit_state = "open"
        proxy._circuit_opened_at = clock.now
        proxy._circuit_backoff_level = 0
        clock.advance(proxy.CB_TIMEOUT_BASE)  # 恰好到达退避期
        assert proxy._circuit_allow() is True
        assert proxy._circuit_state == "half_open"

    def test_half_open_allows_trial(self, monkeypatch):
        _ClockFixture(monkeypatch)
        _reset_circuit()
        proxy._circuit_state = "half_open"
        assert proxy._circuit_allow() is True


class TestCircuitSuccess:
    def test_in_half_open_resets_to_closed(self, monkeypatch):
        clock = _ClockFixture(monkeypatch)
        _reset_circuit()
        proxy._circuit_state = "half_open"
        proxy._circuit_backoff_level = 3
        proxy._circuit_success()
        assert proxy._circuit_state == "closed"
        assert len(proxy._circuit_failure_times) == 0
        assert proxy._circuit_failure_weight == 0.0
        assert proxy._circuit_last_close_at == clock.now

    def test_in_closed_clears_window(self, monkeypatch):
        clock = _ClockFixture(monkeypatch)
        _reset_circuit()
        proxy._circuit_failure_times.append((clock.now, 1.0))
        proxy._circuit_failure_weight = 1.0
        proxy._circuit_success()
        assert proxy._circuit_state == "closed"
        assert len(proxy._circuit_failure_times) == 0
        assert proxy._circuit_failure_weight == 0.0


class TestCircuitFailure:
    def test_accumulates_weight_stays_closed_below_threshold(self, monkeypatch):
        _ClockFixture(monkeypatch)
        _reset_circuit()
        for _ in range(proxy.CB_THRESHOLD - 1):
            proxy._circuit_failure(httpx.ConnectError("boom"))
        assert proxy._circuit_state == "closed"
        assert proxy._circuit_failure_weight == float(proxy.CB_THRESHOLD - 1)

    def test_opens_when_weight_reaches_threshold(self, monkeypatch):
        clock = _ClockFixture(monkeypatch)
        _reset_circuit()
        for _ in range(proxy.CB_THRESHOLD):
            proxy._circuit_failure(httpx.ConnectError("boom"))
        assert proxy._circuit_state == "open"
        assert proxy._circuit_opened_at == clock.now
        assert proxy._circuit_backoff_level == 1

    def test_failure_in_half_open_reopens(self, monkeypatch):
        _ClockFixture(monkeypatch)
        _reset_circuit()
        proxy._circuit_state = "half_open"
        proxy._circuit_backoff_level = 1
        proxy._circuit_failure(httpx.ConnectError("boom"))
        assert proxy._circuit_state == "open"
        assert proxy._circuit_backoff_level == 2

    def test_backoff_level_capped_at_ten(self, monkeypatch):
        _ClockFixture(monkeypatch)
        _reset_circuit()
        proxy._circuit_backoff_level = 10
        proxy._circuit_failure(httpx.ConnectError("boom"))
        assert proxy._circuit_backoff_level == 10

    def test_backoff_resets_after_reset_period(self, monkeypatch):
        clock = _ClockFixture(monkeypatch)
        _reset_circuit()
        proxy._circuit_backoff_level = 4
        clock.advance(1.0)  # 先推进时钟，使 last_close_at > 0 触发 reset 分支
        proxy._circuit_last_close_at = clock.now
        clock.advance(proxy.CB_BACKOFF_RESET + 1.0)
        proxy._circuit_failure(httpx.ConnectError("boom"))
        # 越过 reset 期后，backoff 先归零；单次失败未达阈值，故保持 closed
        assert proxy._circuit_state == "closed"
        assert proxy._circuit_backoff_level == 0

    def test_non_dns_exception_full_weight(self, monkeypatch):
        _ClockFixture(monkeypatch)
        _reset_circuit()
        proxy._circuit_failure(httpx.ReadTimeout("timed out"))
        assert proxy._circuit_failure_weight == 1.0

    def test_dns_failure_low_weight(self, monkeypatch):
        _ClockFixture(monkeypatch)
        _reset_circuit()
        gaierror = socket.gaierror("name resolution failed")
        exc = httpx.ConnectError("connect failed")
        exc.__cause__ = gaierror  # httpx 将底层错误链式挂到 __cause__
        proxy._circuit_failure(exc)
        assert proxy._circuit_failure_weight == proxy._DNS_ERROR_SEVERITY


class TestCircuitBackoffTimeout:
    def test_base_level_returns_base(self):
        proxy._circuit_backoff_level = 0
        assert proxy._circuit_backoff_timeout() == proxy.CB_TIMEOUT_BASE

    def test_level_one_doubles(self):
        proxy._circuit_backoff_level = 1
        assert proxy._circuit_backoff_timeout() == proxy.CB_TIMEOUT_BASE * 2

    def test_capped_at_max(self):
        proxy._circuit_backoff_level = 20
        assert proxy._circuit_backoff_timeout() == proxy.CB_TIMEOUT_MAX


class TestCircuitPruneWindow:
    def test_prunes_old_entries(self, monkeypatch):
        clock = _ClockFixture(monkeypatch)
        _reset_circuit()
        proxy._circuit_failure_times.append((clock.now, 1.0))
        proxy._circuit_failure_weight = 1.0
        clock.advance(proxy.CB_WINDOW + 1.0)  # 旧条目移出窗口
        proxy._circuit_prune_window(clock.now)
        assert len(proxy._circuit_failure_times) == 0
        assert proxy._circuit_failure_weight == 0.0

    def test_keeps_recent_entries(self, monkeypatch):
        clock = _ClockFixture(monkeypatch)
        _reset_circuit()
        proxy._circuit_failure_times.append((clock.now, 1.0))
        proxy._circuit_failure_weight = 1.0
        clock.advance(proxy.CB_WINDOW - 1.0)  # 仍在窗口内
        proxy._circuit_prune_window(clock.now)
        assert len(proxy._circuit_failure_times) == 1
        assert proxy._circuit_failure_weight == 1.0

    def test_removes_old_prefix_keeps_recent(self, monkeypatch):
        clock = _ClockFixture(monkeypatch)
        _reset_circuit()
        # 两个旧条目 + 一个新条目，仅前两个应被移除
        proxy._circuit_failure_times.append((clock.now, 1.0))
        proxy._circuit_failure_times.append((clock.advance(60.0), 0.5))
        proxy._circuit_failure_times.append((clock.advance(60.0), 0.25))
        proxy._circuit_failure_weight = 1.75
        clock.advance(proxy.CB_WINDOW)
        proxy._circuit_prune_window(clock.now)
        assert len(proxy._circuit_failure_times) == 1
        assert proxy._circuit_failure_weight == 0.25


class TestFullStateTransition:
    def test_closed_open_half_open_closed(self, monkeypatch):
        clock = _ClockFixture(monkeypatch)
        _reset_circuit()

        # closed: 容许请求
        assert proxy._circuit_allow() is True

        # 累积到阈值 -> open
        for _ in range(proxy.CB_THRESHOLD):
            proxy._circuit_failure(httpx.ConnectError("boom"))
        assert proxy._circuit_state == "open"
        assert proxy._circuit_allow() is False  # 退避期内拒绝

        # 退避期结束 -> half_open，放行一次探测
        clock.advance(proxy.CB_TIMEOUT_BASE * 2)
        assert proxy._circuit_allow() is True
        assert proxy._circuit_state == "half_open"

        # 探测成功 -> 回到 closed
        proxy._circuit_success()
        assert proxy._circuit_state == "closed"
        assert proxy._circuit_failure_weight == 0.0
        assert proxy._circuit_backoff_level == 1  # 成功只清窗口，不退避等级

        # 回到 closed 后再次容许请求
        assert proxy._circuit_allow() is True
