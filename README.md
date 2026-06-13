# ds-cc-proxy

[中文](./README_CN.md) | English

DeepSeek Anthropic API compatibility proxy — making Claude Code on DeepSeek V4 **more stable and cheaper**.

```
Claude Code ←→ localhost:16889 (ds-cc-proxy) ←→ api.deepseek.com/anthropic
```

## Lower Cost, Higher Quality

ds-cc-proxy achieves the seemingly contradictory: **more stable** than calling DeepSeek directly, while also **cheaper**.

**The principle is simple — recognize Claude Code's intent from the request and route accordingly:**

| Request Type | Primary Session | Sub-agent |
|---|---|---|
| CC sends `thinking` | `enabled` / `adaptive` | `disabled` |
| CC's intent | Deep reasoning needed | No reasoning needed |
| ds-cc-proxy routes to | Pro model + original budget | Flash model + `budget_tokens=2048` |
| Rationale | Passthrough — no quality loss | DeepSeek requires `enabled`; minimum budget satisfies API without wasting tokens |

**~40% cost reduction**: sub-agents account for 30-50% of API calls; each saves ~50% thinking tokens + lower Flash model pricing. Primary session quality is untouched.

**Higher quality**: ds-cc-proxy fixes compatibility bugs in the raw DeepSeek Anthropic API (thinking mode isolation, SSE parsing robustness, connection pool management) — fewer request failures, more complete responses.

## Quick Start

```bash
pip install ds-cc-proxy      # or pipx / uv tool install
ds-cc-proxy                   # start
ds-cc-proxy --stop            # stop
```

Add to `~/.claude/settings.json` under `env`:

```json
"ANTHROPIC_BASE_URL": "http://localhost:16889"
```

## Environment Variables

| Variable | Default | Description |
|------|--------|------|
| `PROXY_UPSTREAM` | `https://api.deepseek.com/anthropic` | Primary session upstream URL |
| `PROXY_FLASH_UPSTREAM` | same as `PROXY_UPSTREAM` | Sub-agent Flash upstream (defaults to Pro if unset) |
| `PROXY_FLASH_MODEL` | *(empty)* | Sub-agent model override, e.g. `deepseek-v4-flash` |
| `PROXY_HOST` | `127.0.0.1` | Listen address |
| `PROXY_PORT` | `16889` | Listen port |
| `PROXY_LOG_LEVEL` | `warning` | `debug` / `info` / `warning` / `error` |
| `PROXY_LOG_FILE` | *(empty)* | Log file path |
| `PROXY_POOL_MAX_CONNECTIONS` | `50` | Upstream pool max connections |
| `PROXY_POOL_MAX_KEEPALIVE` | `20` | Max keep-alive connections |
| `PROXY_POOL_TIMEOUT` | `120.0` | Pool queue timeout (seconds) |
| `PROXY_UPSTREAM_TIMEOUT` | `600.0` | Per-request upstream timeout (seconds) |
| `PROXY_CONNECT_TIMEOUT` | `10.0` | TCP connect timeout (seconds) |
| `PROXY_DUMP_DIR` | *(empty)* | Traffic dump dir (contains secrets, debug only) |

## Health Check

```bash
curl http://localhost:16889/health
# {"status":"ok","version":"0.1.22","upstream":"https://api.deepseek.com/anthropic"}
```

## Comparison

| | **ds-cc-proxy** | LiteLLM | Claude Code Router | OpenRouter |
|---|---|---|---|---|
| Focus | DeepSeek specialist | General gateway | Multi-provider router | Hosted aggregator |
| Size | Python ~650 LOC / 3 deps | ~10K+ LOC / 50+ deps | ~5K+ LOC / 80+ deps | SaaS |
| Auditable | ✅ 10 min read | ❌ Days | ❌ Hours | ❌ Closed source |
| Thinking adapters | ✅ Inject/strip/adaptive passthrough | ⚠️ Partial | ❌ Via plugin | ❌ |
| Sub-agent cost optimization | ✅ Flash routing + budget control | ❌ | ❌ | ❌ |
| SSE robustness | ✅ Multi-layer type guards | ✅ | ⚠️ Generic | ❌ |
| Security hardening | ✅ 12 fixes | ✅ Enterprise | ⚠️ Basic | N/A |

**Choose ds-cc-proxy** if DeepSeek is your primary model and you want stability + cost savings without complexity.

**Choose LiteLLM / CCR / OpenRouter** if you need multi-provider switching, enterprise RBAC, or don't need DeepSeek-specific thinking optimizations.

## Coexistence with Local Proxies

ds-cc-proxy listens on `127.0.0.1:16889` and only handles Claude Code requests. System-level proxies like Clash Verge and V2Ray operate at different network layers — both can run simultaneously with no special configuration.

## Project Origin

Earlier versions were released as `dsv4-cc-proxy`. The community edition v0.1.22 was renamed to `ds-cc-proxy`, dropping the version number qualifier. Both are functionally compatible:

```bash
pip uninstall dsv4-cc-proxy && pip install ds-cc-proxy
```

Forked from [HosheaLi/P14_dsv4ToCC](https://github.com/HosheaLi/P14_dsv4ToCC) v1.8.0, adding security hardening, thinking protocol adaptation, and cost optimization on top of the original bidirectional proxy fixes.

## License

MIT
