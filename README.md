# ds-cc-proxy

DeepSeek Anthropic API 兼容性代理 — 让 Claude Code 在 DeepSeek V4 上 **更稳定、更省成本**。
> DeepSeek Anthropic API compatibility proxy — making Claude Code on DeepSeek V4 **more stable and cheaper**.

```
Claude Code ←→ localhost:16889 (ds-cc-proxy) ←→ api.deepseek.com/anthropic
```

## 更低成本，更高质量 · Lower Cost, Higher Quality

ds-cc-proxy 做到了看起来矛盾的事：比直连 DeepSeek API **更稳定**，同时比直连 **更省钱**。
> ds-cc-proxy achieves the seemingly contradictory: **more stable** than calling DeepSeek directly, while also **cheaper**.

**原理很简单——识别 Claude Code 的请求意图，区别对待：**
> **The principle is simple — recognize Claude Code's intent from the request and route accordingly:**

| 请求类型 · Request Type | 主会话 · Primary Session | 子代理 · Sub-agent |
|---|---|---|
| CC 发送的 thinking | `enabled` / `adaptive` | `disabled` |
| CC 的意图 · CC's intent | 我要深度思考 · Deep reasoning needed | 我不需要思考 · No reasoning needed |
| ds-cc-proxy 路由 | Pro 模型 + 原始预算 | Flash 模型 + budget_tokens=2048 |
| 理由 · Rationale | 透传原始配置，不损害推理质量 · Passthrough, no quality loss | DeepSeek 要求 thinking=enabled，给最小预算满足兼容性即可 · Minimum budget to satisfy API requirement |

**成本节省 ~40%**：子代理占 API 调用量的 30-50%，每条节省 ~50% thinking token + Flash 模型单价更低。主会话质量完全不受影响。
> **~40% cost reduction**: sub-agents account for 30-50% of API calls; each saves ~50% thinking tokens + lower Flash model pricing. Primary session quality is untouched.

**质量更高**：ds-cc-proxy 修复了直连 DeepSeek Anthropic API 的兼容性问题（thinking 模式隔离、SSE 解析容错、连接池管理），请求失败率更低、响应更完整。
> **Higher quality**: ds-cc-proxy fixes compatibility bugs in the raw DeepSeek Anthropic API (thinking mode isolation, SSE parsing robustness, connection pool management) — fewer request failures, more complete responses.

## 快速开始 · Quick Start

```bash
pip install ds-cc-proxy      # 或 pipx / uv tool install
ds-cc-proxy                   # 启动 · start
ds-cc-proxy --stop            # 停止 · stop
```

在 `~/.claude/settings.json` 的 `env` 中添加 · Add to `~/.claude/settings.json` under `env`:

```json
"ANTHROPIC_BASE_URL": "http://localhost:16889"
```

## 环境变量 · Environment Variables

| 变量 · Variable | 默认值 · Default | 说明 · Description |
|------|--------|------|
| `PROXY_UPSTREAM` | `https://api.deepseek.com/anthropic` | 主会话 API 地址 · Primary session upstream |
| `PROXY_FLASH_UPSTREAM` | 同 `PROXY_UPSTREAM` · same as upstream | 子代理 Flash 上游 · Sub-agent Flash upstream |
| `PROXY_FLASH_MODEL` | *(空 · empty)* | 子代理模型名，如 `deepseek-v4-flash` · Sub-agent model override |
| `PROXY_HOST` | `127.0.0.1` | 监听地址 · Listen address |
| `PROXY_PORT` | `16889` | 监听端口 · Listen port |
| `PROXY_LOG_LEVEL` | `warning` | `debug` / `info` / `warning` / `error` |
| `PROXY_LOG_FILE` | *(空 · empty)* | 日志文件路径 · Log file path |
| `PROXY_POOL_MAX_CONNECTIONS` | `50` | 上游连接池上限 · Upstream pool max connections |
| `PROXY_POOL_MAX_KEEPALIVE` | `20` | 最大保活连接数 · Max keep-alive connections |
| `PROXY_POOL_TIMEOUT` | `120.0` | 池满排队超时（秒）· Pool queue timeout (seconds) |
| `PROXY_UPSTREAM_TIMEOUT` | `600.0` | 单次上游请求超时（秒）· Per-request upstream timeout (seconds) |
| `PROXY_CONNECT_TIMEOUT` | `10.0` | TCP 连接超时（秒）· TCP connect timeout (seconds) |
| `PROXY_DUMP_DIR` | *(空 · empty)* | 流量捕获目录（含敏感数据，仅调试用）· Traffic dump dir (contains secrets, debug only) |

## 健康检查 · Health Check

```bash
curl http://localhost:16889/health
# {"status":"ok","version":"0.1.22","upstream":"https://api.deepseek.com/anthropic"}
```

## 与同类工具的对比 · Comparison

| | **ds-cc-proxy** | LiteLLM | Claude Code Router | OpenRouter |
|---|---|---|---|---|
| 定位 · Focus | DeepSeek 专项 · DeepSeek specialist | 通用企业网关 · General gateway | 多供应商路由 · Multi-provider router | 托管聚合平台 · Hosted aggregator |
| 体量 · Size | Python ~650 LOC / 3 deps | ~10K+ LOC / 50+ deps | ~5K+ LOC / 80+ deps | SaaS |
| 可审计性 · Auditable | ✅ 10 分钟通读 · 10 min read | ❌ 需数天 · days | ❌ 需半天 · hours | ❌ 闭源 · closed source |
| thinking 适配 | ✅ 注入/剥离/adaptive | ⚠️ 部分 · partial | ❌ 需插件 · via plugin | ❌ |
| 子代理成本优化 · Sub-agent cost opt. | ✅ Flash 路由 + budget | ❌ | ❌ | ❌ |
| SSE 解析容错 · SSE robustness | ✅ 多重类型守卫 · type guards | ✅ | ⚠️ 通用 · generic | ❌ |
| 安全加固 · Security hardening | ✅ 12 项 · 12 items | ✅ 企业级 · enterprise | ⚠️ 基础 · basic | N/A |

**选 ds-cc-proxy**：主模型是 DeepSeek，需要稳定 + 省成本，不想折腾。
> **Choose ds-cc-proxy** if DeepSeek is your primary model and you want stability + cost savings without complexity.

**选 LiteLLM / CCR / OpenRouter**：需要多供应商切换、企业级 RBAC、或不需要 DeepSeek thinking 专项适配。
> **Choose LiteLLM / CCR / OpenRouter** if you need multi-provider switching, enterprise RBAC, or don't need DeepSeek-specific thinking optimizations.

## 与本地代理的关系 · Coexistence with Local Proxies

ds-cc-proxy 监听 `127.0.0.1:16889`，仅处理 Claude Code 发送的请求。Clash Verge、V2Ray 等系统代理工作在不同网络层，两者可同时运行，无需特殊配置。
> ds-cc-proxy listens on `127.0.0.1:16889` and only handles Claude Code requests. System-level proxies like Clash Verge and V2Ray operate at different network layers — both can run simultaneously with no special configuration.

## 项目由来 · Project Origin

本项目的早期版本以 `dsv4-cc-proxy` 发布（DeepSeek V4 → Claude Code Proxy），v0.1.22 社区版更名为 `ds-cc-proxy`，去掉了版本号限定。两者功能兼容，`ds-cc-proxy` 是后继版本：
> Earlier versions were released as `dsv4-cc-proxy`. The community edition v0.1.22 was renamed to `ds-cc-proxy`, dropping the version number qualifier. Both are functionally compatible:

```bash
pip uninstall dsv4-cc-proxy && pip install ds-cc-proxy
```

Fork 自 [HosheaLi/P14_dsv4ToCC](https://github.com/HosheaLi/P14_dsv4ToCC) v1.8.0，在原有双向代理修复基础上增加了安全加固、thinking 协议适配和成本优化。
> Forked from [HosheaLi/P14_dsv4ToCC](https://github.com/HosheaLi/P14_dsv4ToCC) v1.8.0, adding security hardening, thinking protocol adaptation, and cost optimization on top of the original bidirectional proxy fixes.

## 许可证 · License

MIT
