<div align="center">

# ds-cc-proxy

**DeepSeek Anthropic API Proxy · Make Claude Code on DeepSeek V4 stabler and cheaper**

[![Version](https://img.shields.io/badge/version-0.1.22-333?style=flat-square)](https://github.com/snowspruce/ds-cc-proxy)
[![Python](https://img.shields.io/badge/python-≥3.11-3776AB?style=flat-square&logo=python&logoColor=white)](https://pypi.org/project/ds-cc-proxy/)
[![License](https://img.shields.io/badge/license-MIT-333?style=flat-square)](./LICENSE)
[![Tests](https://img.shields.io/badge/tests-59%20passed-333?style=flat-square)](./tests/)
[![Size](https://img.shields.io/badge/size-~650%20LOC-333?style=flat-square)](./ds_cc_proxy/)
[![Deps](https://img.shields.io/badge/deps-3-333?style=flat-square)](./pyproject.toml)

<br>

```
Claude Code ←→ localhost:16889 ←→ api.deepseek.com/anthropic
```

[Quick Start](#quick-start) · [Comparison](#comparison) · [中文](./README_CN.md)

</div>

---

## &nbsp;

> 40% cheaper sub-agents. Zero quality loss on primary sessions. Fewer failures than calling DeepSeek directly.
>
> **How?** Read the `thinking` field from Claude Code. Route sub-agents (`disabled`) to Flash with `budget_tokens=2048`. Passthrough primary sessions (`enabled`/`adaptive`) untouched.

| Request Type | Primary Session | Sub-agent |
|---|---|---|
| CC sends `thinking` | `enabled` / `adaptive` | `disabled` |
| CC's intent | Deep reasoning | Quick execution |
| Proxy routes to | Pro model · original budget | Flash model · `budget_tokens=2048` |
| Why | Passthrough — no quality impact | DeepSeek requires `enabled`; minimal budget satisfies API |

**Quality is not compromised — it's improved.** ds-cc-proxy fixes 12 compatibility bugs in the raw DeepSeek Anthropic API: thinking mode isolation, SSE parsing edge cases, connection pool exhaustion, header leakage, crash-on-invalid-env, and more.

---

## &nbsp;

<a id="quick-start"></a>
##  Quick Start

<div align="center">

```bash
pip install ds-cc-proxy      # or pipx / uv tool install
ds-cc-proxy                   # start
ds-cc-proxy --stop            # stop
```

</div>

Add to `~/.claude/settings.json` under `env`:

```json
"ANTHROPIC_BASE_URL": "http://localhost:16889"
```

```bash
curl http://localhost:16889/health
# {"status":"ok","version":"0.1.22","upstream":"https://api.deepseek.com/anthropic"}
```

---

##  Environment Variables

| Variable | Default | Description |
|---|---|---|
| `PROXY_UPSTREAM` | `https://api.deepseek.com/anthropic` | Primary session upstream |
| `PROXY_FLASH_UPSTREAM` | Same as `PROXY_UPSTREAM` | Sub-agent Flash upstream (falls back to Pro) |
| `PROXY_FLASH_MODEL` | *(empty)* | Sub-agent model override, e.g. `deepseek-v4-flash` |
| `PROXY_HOST` | `127.0.0.1` | Listen address |
| `PROXY_PORT` | `16889` | Listen port |
| `PROXY_LOG_LEVEL` | `warning` | `debug` / `info` / `warning` / `error` |
| `PROXY_POOL_MAX_CONNECTIONS` | `50` | Upstream pool max connections |
| `PROXY_POOL_MAX_KEEPALIVE` | `20` | Max keep-alive connections |
| `PROXY_POOL_TIMEOUT` | `120.0` | Pool queue timeout (seconds) |
| `PROXY_UPSTREAM_TIMEOUT` | `600.0` | Per-request upstream timeout (seconds) |
| `PROXY_CONNECT_TIMEOUT` | `10.0` | TCP connect timeout (seconds) |
| `PROXY_MAX_BODY_BYTES` | `10485760` | Max request body size (10MB) |
| `PROXY_RETRY_MAX` | `3` | Max retry attempts on transient failures |
| `PROXY_RETRY_BACKOFF` | `1.0` | Base backoff seconds per retry (exponential) |
| `PROXY_CIRCUIT_BREAKER_THRESHOLD` | `5` | Consecutive failures before circuit opens |
| `PROXY_CIRCUIT_BREAKER_TIMEOUT` | `30.0` | Seconds circuit stays open before half-open trial |
| `PROXY_DUMP_DIR` | *(empty)* | Traffic dump (contains secrets, debug only) |

### Reliability · Retry + Circuit Breaker

Transient failures (network blips, 502/503) are retried automatically with exponential backoff. After `PROXY_CIRCUIT_BREAKER_THRESHOLD` consecutive failures, the circuit opens — all requests immediately return 503 until a half-open trial succeeds. No more cascading timeouts.

### Caching · Prompt Cache Hints

`cache_control: ephemeral` breakpoints are injected on system prompts and the last message of each request. DeepSeek caches the matching prefix — subsequent requests with the same prefix pay only 10% for cached input tokens. Transparent to Claude Code; check `[COST]` logs for `cache_hit%`.

---

<a id="comparison"></a>
##  Comparison

| | **ds-cc-proxy** | LiteLLM | CCR | OpenRouter |
|---|---|---|---|---|
| Focus | DeepSeek specialist | General gateway | Multi-provider | Hosted aggregator |
| Footprint | ~650 LOC · 3 deps | ~10K+ LOC · 50+ deps | ~5K+ LOC · 80+ deps | SaaS |
| Auditable | ✅ 10-minute read | ❌ Days | ❌ Hours | ❌ Closed source |
| Thinking adapters | ✅ Inject / strip / adaptive | ⚠️ Partial | ❌ Via plugin | ❌ |
| Sub-agent cost opt. | ✅ Flash + budget control | ❌ | ❌ | ❌ |
| SSE robustness | ✅ Multi-layer type guards | ✅ | ⚠️ Generic | ❌ |
| Security hardening | ✅ 12 fixes | ✅ Enterprise | ⚠️ Basic | N/A |

> **Pick ds-cc-proxy** if DeepSeek is your daily driver and you want stability, cost savings, and zero config.
>
> **Pick LiteLLM / CCR / OpenRouter** if you need multi-provider switching, enterprise RBAC, or don't use DeepSeek thinking modes.

---

##  Coexistence with Local Proxies

ds-cc-proxy (`127.0.0.1:16889`) handles only Claude Code traffic. Clash Verge, V2Ray, and other system-level proxies work at different network layers — run both simultaneously, no configuration needed.

---

##  Project Origin

Forked from [HosheaLi/P14_dsv4ToCC](https://github.com/HosheaLi/P14_dsv4ToCC) v1.8.0. Earlier versions were released as `dsv4-cc-proxy`. v0.1.22 was renamed to `ds-cc-proxy`. Upgrading:

```bash
pip uninstall dsv4-cc-proxy && pip install ds-cc-proxy
```

---

<div align="center">

## License

MIT · [snowspruce/ds-cc-proxy](https://github.com/snowspruce/ds-cc-proxy) · [中文](./README_CN.md)

</div>
