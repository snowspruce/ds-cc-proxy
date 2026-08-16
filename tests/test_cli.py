# ds-cc-proxy CLI 单元测试
#
# 覆盖 __main__.py 中的 CLI 流程:
#   - _stop 的 SIGTERM / SIGKILL / PID 文件缺失 / 进程不存在 分支
#   - main 的 --stop / --usage 分支
#   - PID 文件原子创建 (O_EXCL) 与残留清理

import json
import os
import signal
import stat
import sys
from unittest import mock

import pytest

import ds_cc_proxy.__main__ as main_module
from ds_cc_proxy.__main__ import _stop, main

# ---------------------------------------------------------------------------
# _stop
# ---------------------------------------------------------------------------


class TestStop:
    def test_pidfile_not_found_exits_1(self, monkeypatch, capsys):
        monkeypatch.setattr(main_module.os.path, "exists", lambda p: False)
        with pytest.raises(SystemExit) as exc:
            _stop("/tmp/nonexistent.pid")
        assert exc.value.code == 1
        assert "not running" in capsys.readouterr().out

    def test_process_not_found_cleans_up_pidfile(self, monkeypatch, capsys):
        # PID 文件存在，但进程已死 (SIGTERM 抛 ProcessLookupError)
        monkeypatch.setattr(main_module.os.path, "exists", lambda p: True)
        monkeypatch.setattr(
            "builtins.open", mock.mock_open(read_data="4242\n"), raising=False
        )
        kills = []

        def fake_kill(pid, sig):
            kills.append((pid, sig))
            if sig == signal.SIGTERM:
                raise ProcessLookupError

        monkeypatch.setattr(main_module.os, "kill", fake_kill)
        unlinked = []
        monkeypatch.setattr(main_module.os, "unlink", unlinked.append)

        _stop("/tmp/run.pid")

        assert kills == [(4242, signal.SIGTERM)]
        assert unlinked == ["/tmp/run.pid"]
        assert "cleaning up" in capsys.readouterr().out

    def test_graceful_shutdown_unlinks_pidfile(self, monkeypatch, capsys):
        # SIGTERM 后进程退出 → "stopped gracefully" → unlink
        monkeypatch.setattr(main_module.os.path, "exists", lambda p: True)
        monkeypatch.setattr(
            "builtins.open", mock.mock_open(read_data="7\n"), raising=False
        )
        kills = []

        def fake_kill(pid, sig):
            kills.append((pid, sig))
            if sig == 0:
                raise ProcessLookupError

        monkeypatch.setattr(main_module.os, "kill", fake_kill)
        monkeypatch.setattr(main_module.time, "sleep", lambda _: None)
        unlinked = []
        monkeypatch.setattr(main_module.os, "unlink", unlinked.append)

        _stop("/tmp/run.pid")

        assert kills == [(7, signal.SIGTERM), (7, 0)]
        assert unlinked == ["/tmp/run.pid"]
        assert "stopped gracefully" in capsys.readouterr().out

    def test_forced_kill_after_timeout(self, monkeypatch, capsys):
        # 进程不退出 → 循环 10 次后 SIGKILL
        monkeypatch.setattr(main_module.os.path, "exists", lambda p: True)
        monkeypatch.setattr(
            "builtins.open", mock.mock_open(read_data="99\n"), raising=False
        )
        kills = []

        def fake_kill(pid, sig):
            kills.append((pid, sig))
            # 对信号 0 (存活探测) 不抛异常 → 进程一直存活

        monkeypatch.setattr(main_module.os, "kill", fake_kill)
        monkeypatch.setattr(main_module.time, "sleep", lambda _: None)
        unlinked = []
        monkeypatch.setattr(main_module.os, "unlink", unlinked.append)

        _stop("/tmp/run.pid")

        # SIGTERM + 10 次存活探测 + SIGKILL
        assert kills[0] == (99, signal.SIGTERM)
        assert kills[1:11] == [(99, 0)] * 10
        assert kills[11] == (99, signal.SIGKILL)
        assert unlinked == ["/tmp/run.pid"]
        assert "forced" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# main --stop
# ---------------------------------------------------------------------------


class TestMainStop:
    def test_stop_flag_calls_stop(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["ds-cc-proxy", "--stop", "--pidfile", "/tmp/x.pid"])
        calls = []

        def fake_stop(pidfile):
            calls.append(pidfile)

        monkeypatch.setattr(main_module, "_stop", fake_stop)

        main()

        assert calls == ["/tmp/x.pid"]


# ---------------------------------------------------------------------------
# main --usage
# ---------------------------------------------------------------------------


class TestMainUsage:
    def _usage_payload(self):
        return {
            "requests": 10,
            "input_tokens": 1000,
            "output_tokens": 500,
            "cache_hit_pct": 12.5,
            "estimated_cost_usd": 0.01,
            "estimated_saved_usd": 0.02,
            "primary": {"requests": 6, "input_tokens": 700, "output_tokens": 300},
            "subagent": {"requests": 4, "input_tokens": 300, "output_tokens": 200},
            "subagent_saved_thinking_tokens": 1111,
        }

    def test_usage_prints_stats(self, monkeypatch, capsys):
        payload = self._usage_payload()
        fake_resp = mock.MagicMock()
        fake_resp.read.return_value = json.dumps(payload).encode()
        monkeypatch.setattr(main_module.urllib.request, "urlopen", lambda *a, **k: fake_resp)
        monkeypatch.setattr(sys, "argv", ["ds-cc-proxy", "--usage"])

        main()

        out = capsys.readouterr().out
        assert "Requests:" in out
        assert "1,000" in out  # input tokens with comma separator
        assert "12.5%" in out

    def test_usage_print_uses_urlopen_timeout(self, monkeypatch, capsys):
        payload = self._usage_payload()
        fake_resp = mock.MagicMock()
        fake_resp.read.return_value = json.dumps(payload).encode()
        captured = {}

        def fake_urlopen(url, timeout=None):
            captured["url"] = url
            captured["timeout"] = timeout
            return fake_resp

        monkeypatch.setattr(main_module.urllib.request, "urlopen", fake_urlopen)
        monkeypatch.setattr(sys, "argv", ["ds-cc-proxy", "--usage"])

        main()

        assert captured["url"] == f"http://{main_module.HOST}:{main_module.PORT}/usage"
        assert captured["timeout"] == 5

    def test_usage_unreachable_exits_1(self, monkeypatch, capsys):
        def boom(*a, **k):
            raise ConnectionError("refused")

        monkeypatch.setattr(main_module.urllib.request, "urlopen", boom)
        monkeypatch.setattr(sys, "argv", ["ds-cc-proxy", "--usage"])

        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 1
        assert "not reachable" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# main — PID 文件原子创建与残留清理
# ---------------------------------------------------------------------------

_PIDFILE_FLAGS = os.O_CREAT | os.O_EXCL | os.O_WRONLY
_PIDFILE_MODE = stat.S_IRUSR | stat.S_IWUSR


def _record_fd_ops(monkeypatch):
    """Mock os.open / os.fdopen to capture flags, mode, and written content."""
    state = {"opens": [], "writes": [], "fd_counter": [100]}

    real_open = os.open

    def fake_open(path, flags, mode=0o777):
        state["opens"].append((path, flags, mode))
        state["fd_counter"][0] += 1
        return state["fd_counter"][0]

    class _FakeFile:
        def __init__(self, writes):
            self._writes = writes

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def write(self, s):
            self._writes.append(s)

    def fake_fdopen(fd, mode):
        return _FakeFile(state["writes"])

    monkeypatch.setattr(main_module.os, "open", fake_open)
    monkeypatch.setattr(main_module.os, "fdopen", fake_fdopen)
    return state, real_open


class TestMainPidfile:
    def test_atomic_pidfile_creation(self, monkeypatch, tmp_path):
        # 冷启动：PID 文件不存在 → O_EXCL 原子创建成功
        pidfile = str(tmp_path / "proxy.pid")
        monkeypatch.setattr(sys, "argv", ["ds-cc-proxy", "--pidfile", pidfile])
        monkeypatch.setattr(main_module.uvicorn, "run", lambda *a, **k: None)
        monkeypatch.setattr(main_module.os, "getpid", lambda: 12345)
        state, _ = _record_fd_ops(monkeypatch)

        main()

        assert state["opens"] == [(pidfile, _PIDFILE_FLAGS, _PIDFILE_MODE)]
        assert state["writes"] == ["12345"]

    def test_existing_live_pid_exits_1(self, monkeypatch, tmp_path, capsys):
        # 已存在活跃进程 → 提示 already running 并退出
        pidfile = str(tmp_path / "proxy.pid")
        real_open = os.open
        fd = real_open(pidfile, os.O_CREAT | os.O_WRONLY, 0o644)
        with os.fdopen(fd, "w") as f:
            f.write("555\n")
        monkeypatch.setattr(sys, "argv", ["ds-cc-proxy", "--pidfile", pidfile])
        monkeypatch.setattr(main_module.os, "kill", lambda pid, sig: None)

        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 1
        assert "already running" in capsys.readouterr().out

    def test_stale_pidfile_cleaned_and_retried(self, monkeypatch, tmp_path):
        # 残留 PID 文件 (非数字 → ValueError) → 清理后重试，最终写入新 PID
        pidfile = str(tmp_path / "stale.pid")
        real_open = os.open
        fd = real_open(pidfile, os.O_CREAT | os.O_WRONLY, 0o644)
        with os.fdopen(fd, "w") as f:
            f.write("dead\n")
        monkeypatch.setattr(sys, "argv", ["ds-cc-proxy", "--pidfile", pidfile])
        monkeypatch.setattr(main_module.uvicorn, "run", lambda *a, **k: None)
        monkeypatch.setattr(main_module.os, "getpid", lambda: 777)
        state, _ = _record_fd_ops(monkeypatch)

        main()

        # 第一次 open 抛 FileExistsError (真实文件已存在) → 清理 → 第二次 open 成功
        assert len(state["opens"]) == 1
        assert state["opens"][0] == (pidfile, _PIDFILE_FLAGS, _PIDFILE_MODE)
        assert state["writes"] == ["777"]

    def test_stale_pidfile_dead_process(self, monkeypatch, tmp_path):
        # 残留 PID 文件 + 进程死 (os.kill(pid,0) 抛 ProcessLookupError) → 清理后重试
        pidfile = str(tmp_path / "stale2.pid")
        real_open = os.open
        fd = real_open(pidfile, os.O_CREAT | os.O_WRONLY, 0o644)
        with os.fdopen(fd, "w") as f:
            f.write("999\n")

        def fake_kill(pid, sig):
            raise ProcessLookupError

        monkeypatch.setattr(sys, "argv", ["ds-cc-proxy", "--pidfile", pidfile])
        monkeypatch.setattr(main_module.os, "kill", fake_kill)
        monkeypatch.setattr(main_module.uvicorn, "run", lambda *a, **k: None)
        monkeypatch.setattr(main_module.os, "getpid", lambda: 888)
        state, _ = _record_fd_ops(monkeypatch)

        main()

        assert len(state["opens"]) == 1
        assert state["opens"][0] == (pidfile, _PIDFILE_FLAGS, _PIDFILE_MODE)
        assert state["writes"] == ["888"]
