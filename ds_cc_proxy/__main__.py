# ds-cc-proxy CLI entry point


import argparse
import json
import os
import signal
import stat
import sys
import time
import urllib.request

import uvicorn

from ds_cc_proxy._version import VERSION
from ds_cc_proxy.proxy import DUMP_DIR, HOST, LOG_LEVEL, PORT, create_app

PIDFILE_DEFAULT = "/tmp/ds-cc-proxy.pid"


def _stop(pidfile: str):
    """Stop the proxy: read PID → SIGTERM → wait → SIGKILL (force-kill on timeout)."""
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
    parser.add_argument("--stop", action="store_true", help="Stop running proxy")
    parser.add_argument("--usage", action="store_true", help="Show usage stats of running proxy")
    parser.add_argument(
        "--pidfile",
        default=PIDFILE_DEFAULT,
        help=f"PID file path (default: {PIDFILE_DEFAULT})",
    )
    args = parser.parse_args()

    if args.stop:
        _stop(args.pidfile)
        return

    if args.usage:
        try:
            url = f"http://{HOST}:{PORT}/usage"
            resp = urllib.request.urlopen(url, timeout=5)
            data = json.loads(resp.read())
            print(f"Requests:     {data['requests']}")
            print(f"Input:        {data['input_tokens']:,} tokens")
            print(f"Output:       {data['output_tokens']:,} tokens")
            print(f"Cache hit:    {data['cache_hit_pct']}%")
            print(f"Est. cost:    ${data['estimated_cost_usd']}")
            saved = data["estimated_saved_usd"]
            green = "\033[32m"
            reset = "\033[0m"
            print(f"Est. saved:   {green}${saved}{reset}")
            if data.get("primary"):
                p = data["primary"]
                pi, po = p["input_tokens"], p["output_tokens"]
                print(f"  Primary:    {p['requests']} reqs, {pi:,}+{po:,} tok")
            s = data["subagent"]
            si, so = s["input_tokens"], s["output_tokens"]
            print(f"  Sub-agent:  {s['requests']} reqs, {si:,}+{so:,} tok")
            print(f"    ├ saved thinking: {data['subagent_saved_thinking_tokens']:,} tokens")
            return
        except Exception as e:
            print(f"Proxy not reachable at {HOST}:{PORT}: {e}")
            sys.exit(1)

    pidfile = args.pidfile
    pidfile_dir = os.path.dirname(pidfile) or "."

    # Ensure PID file directory exists
    os.makedirs(pidfile_dir, exist_ok=True)

    # S5+S6: Atomic PID file creation — eliminates TOCTOU race, restricts permissions to owner-only
    try:
        pidfd = os.open(
            pidfile,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            stat.S_IRUSR | stat.S_IWUSR,
        )
    except FileExistsError:
        # PID file exists — check if process is still alive
        try:
            with open(pidfile) as f:
                pid = int(f.read().strip())
            os.kill(pid, 0)
            print(f"Proxy already running (PID {pid}), use --stop first")
            sys.exit(1)
        except (OSError, ValueError):
            # Process is dead or PID file is corrupt — clean up and retry
            os.unlink(pidfile)
            pidfd = os.open(
                pidfile,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                stat.S_IRUSR | stat.S_IWUSR,
            )

    with os.fdopen(pidfd, "w") as f:
        f.write(str(os.getpid()))

    print(f"DeepSeek Thinking Proxy v{VERSION} → {HOST}:{PORT} (PID {os.getpid()})")
    if DUMP_DIR:
        print(f"⚠ DUMP mode: {DUMP_DIR}")
    try:
        uvicorn.run(
            create_app,
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
