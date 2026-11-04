# ds-cc-proxy

Local proxy for Claude Code on DeepSeek V4: sub-agents move to Flash, primary sessions pass through unchanged.

Python ≥ 3.11 · MIT · 164 tests · ~1.5K LOC · 2 dependencies · [中文](./README_CN.md)

```
Claude Code ←→ 127.0.0.1:16889 ←→ api.deepseek.com/anthropic
```

---

## Install

```bash
pip install ds-cc-proxy      # or pipx / uv tool install
ds-cc-proxy                  # start
ds-cc-proxy --stop           # stop
```

Add to `env` in `~/.claude/settings.json`:

```json
"ANTHROPIC_BASE_URL": "http://localhost:16889"
```

---

## Usage

```bash
curl http://localhost:16889/health
# {"status":"ok","version":"0.1.28","upstream":"https://api.deepseek.com/anthropic","circuit":"closed"}

curl http://localhost:16889/usage
# {"requests":247,"input_tokens":1200000,"output_tokens":340000,"cache_hit_pct":98,
#  "estimated_cost_rmb":1.24,"estimated_cost_usd":0.17,
#  "pricing":{"effective":"2026-08-17","is_peak_now":false,...}}
```

Since DeepSeek V4 peak/off-peak pricing (2026-08-17), `/usage` reports cost in ¥ split across
peak windows, with cache hit rate.

---

## How it works

The proxy reads the `thinking` field Claude Code already sends, and routes on it:

| Request type | Primary session | Sub-agent |
|---|---|---|
| `thinking` sent by Claude Code | `enabled` / `adaptive` | `disabled` |
| Intent | Deep reasoning | Quick execution |
| Route | Pro model, original budget | Flash model, `budget_tokens=2048` |
| Reason | Pass-through, no quality impact | DeepSeek requires `enabled`; a minimal budget satisfies it |

Sub-agent cost drop: 40%. Primary-session quality is untouched.

It also fixes 12 compatibility defects in the raw DeepSeek Anthropic API: thinking-mode
isolation, SSE parsing edge cases, connection pool exhaustion, header leakage,
crash-on-invalid-env, and others.

---

## Configuration

| Variable | Default | Description |
|---|---|---|
| `PROXY_UPSTREAM` | `https://api.deepseek.com/anthropic` | Primary session upstream |
| `PROXY_FLASH_UPSTREAM` | Same as `PROXY_UPSTREAM` | Sub-agent Flash upstream (falls back to Pro) |
| `PROXY_FLASH_MODEL` | *(empty)* | Sub-agent model override, e.g. `deepseek-v4-flash` |
| `PROXY_HOST` | `127.0.0.1` | Listen address |
| `PROXY_PORT` | `16889` | Listen port |
| `PROXY_LOG_LEVEL` | `warning` | `debug` / `info` / `warning` / `error` |
| `PROXY_LOG_FILE` | *(empty)* | Log file path (empty = stdout only) |
| `PROXY_LOG_MAX_BYTES` | `10485760` | Max log file size (10MB) |
| `PROXY_LOG_BACKUP_COUNT` | `3` | Log rotation backup count |
| `PROXY_MAX_BODY_BYTES` | `10485760` | Max request body size (10MB) |
| `PROXY_DUMP_DIR` | *(empty)* | Traffic dump (contains secrets, debug only) |
| `PROXY_POOL_MAX_CONNECTIONS` | `50` | Upstream pool max connections |
| `PROXY_POOL_MAX_KEEPALIVE` | `20` | Max keep-alive connections |
| `PROXY_POOL_TIMEOUT` | `120.0` | Pool queue timeout (seconds) |
| `PROXY_UPSTREAM_TIMEOUT` | `600.0` | Per-request upstream timeout (seconds) |
| `PROXY_CONNECT_TIMEOUT` | `10.0` | TCP connect timeout (seconds) |
| `PROXY_CLIENT_MAX_AGE` | `300.0` | Max client connection age (seconds) |
| `PROXY_RETRY_MAX` | `3` | Max retry attempts on transient failures |
| `PROXY_RETRY_BACKOFF` | `1.0` | Base backoff seconds per retry (exponential) |
| `PROXY_CB_THRESHOLD` | `8` | Consecutive failures before circuit opens |
| `PROXY_CB_WINDOW` | `60.0` | Failure-count window (seconds) |
| `PROXY_CB_TIMEOUT_BASE` | `30.0` | Base seconds circuit stays open |
| `PROXY_CB_TIMEOUT_MAX` | `300.0` | Max circuit-open seconds (exponential backoff) |
| `PROXY_CB_BACKOFF_RESET` | `600.0` | Healthy seconds before circuit resets |
| `PROXY_CB_HEALTH_INTERVAL` | `15.0` | Half-open health check interval (seconds) |

Transient failures (network blips, 502/503) are retried with exponential backoff. After
`PROXY_CB_THRESHOLD` consecutive failures within `PROXY_CB_WINDOW`, the circuit opens: every
request returns 503 immediately until a half-open health check (`PROXY_CB_HEALTH_INTERVAL`)
succeeds.

---

## Comparison

| | ds-cc-proxy | LiteLLM | CCR | OpenRouter |
|---|---|---|---|---|
| Focus | DeepSeek specialist | General gateway | Multi-provider | Hosted aggregator |
| Footprint | ~1.5K LOC, 2 deps | ~10K+ LOC, 50+ deps | ~5K+ LOC, 80+ deps | SaaS |
| Auditable | Yes, 10-minute read | No, days | No, hours | No, closed source |
| Thinking adapters | Inject / strip / adaptive | Partial | Via plugin | No |
| Sub-agent cost control | Flash + budget | No | No | No |
| SSE robustness | Multi-layer type guards | Yes | Generic | No |
| Security hardening | 12 fixes | Enterprise | Basic | N/A |

Pick ds-cc-proxy if DeepSeek is your daily driver and you want stability and cost savings with
no configuration. Pick LiteLLM, CCR or OpenRouter if you need multi-provider switching,
enterprise RBAC, or do not use DeepSeek thinking modes.

---

## Notes

`127.0.0.1:16889` handles Claude Code traffic only. Clash Verge, V2Ray and other system-level
proxies work at different network layers; both run at the same time with no configuration.

Forked from [HosheaLi/P14_dsv4ToCC](https://github.com/HosheaLi/P14_dsv4ToCC) v1.8.0. Earlier
versions were published as `dsv4-cc-proxy`; renamed at v0.1.22.

```bash
pip uninstall dsv4-cc-proxy && pip install ds-cc-proxy
```

---

MIT
