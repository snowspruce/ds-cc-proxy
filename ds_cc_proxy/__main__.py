# ds-cc-proxy CLI 入口


import argparse
import os
import signal
import stat
import sys
import time

import uvicorn

from ds_cc_proxy._version import VERSION
from ds_cc_proxy.proxy import DUMP_DIR, HOST, LOG_LEVEL, PORT

PIDFILE_DEFAULT = "/tmp/ds-cc-proxy.pid"


def _stop(pidfile: str):
    """停止代理：读取 PID 文件 → SIGTERM → 等待 → SIGKILL（超时则强制杀）。"""
    if not os.path.exists(pidfile):
        print(f"Proxy not running (PID file not found: {pidfile})")
        sys.exit(1)

    with open(pidfile) as f:
        pid = int(f.read().strip())

    print(f"Stopping ds-cc-proxy (PID {pid})...")

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        print("Process not found, cleaning up PID file")
        os.unlink(pidfile)
        return

    for _ in range(10):
        time.sleep(0.5)
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            print("Proxy stopped gracefully")
            try:
                os.unlink(pidfile)
            except FileNotFoundError:
                pass
            return

    print("Graceful shutdown timed out, sending SIGKILL...")
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        os.unlink(pidfile)
    except FileNotFoundError:
        pass
    print("Proxy stopped (forced)")


def main():
    parser = argparse.ArgumentParser(description="DeepSeek Thinking Proxy")
    parser.add_argument(
        "--stop", action="store_true", help="Stop running proxy"
    )
    parser.add_argument(
        "--pidfile", default=PIDFILE_DEFAULT,
        help=f"PID file path (default: {PIDFILE_DEFAULT})",
    )
    args = parser.parse_args()

    if args.stop:
        _stop(args.pidfile)
        return

    pidfile = args.pidfile
    pidfile_dir = os.path.dirname(pidfile) or "."

    # 确保 PID 文件目录存在
    os.makedirs(pidfile_dir, exist_ok=True)

    # S5+S6: 原子创建 PID 文件，消除 TOCTOU 竞态，限制权限为 owner-only
    try:
        pidfd = os.open(pidfile, os.O_CREAT | os.O_EXCL | os.O_WRONLY, stat.S_IRUSR | stat.S_IWUSR)
    except FileExistsError:
        # 文件已存在，检查进程是否存活
        try:
            with open(pidfile) as f:
                pid = int(f.read().strip())
            os.kill(pid, 0)
            print(f"Proxy already running (PID {pid}), use --stop first")
            sys.exit(1)
        except (OSError, ValueError):
            # 进程已死或 PID 文件损坏，清理后重试
            os.unlink(pidfile)
            pidfd = os.open(pidfile, os.O_CREAT | os.O_EXCL | os.O_WRONLY, stat.S_IRUSR | stat.S_IWUSR)

    with os.fdopen(pidfd, "w") as f:
        f.write(str(os.getpid()))

    print(f"DeepSeek Thinking Proxy v{VERSION} → {HOST}:{PORT} (PID {os.getpid()})")
    if DUMP_DIR:
        print(f"⚠ DUMP mode: {DUMP_DIR}")
    try:
        uvicorn.run(
            "ds_cc_proxy.proxy:create_app",
            host=HOST,
            port=PORT,
            log_level=LOG_LEVEL,
            factory=True,
        )
    finally:
        try:
            os.unlink(pidfile)
        except FileNotFoundError:
            pass


if __name__ == "__main__":
    main()
