# ds-cc-proxy

Claude Code 接 DeepSeek V4 时的本地代理：子代理走 Flash，主会话原样透传。

Python ≥ 3.11 · MIT · 164 测试 · ~1.5K LOC · 2 依赖 · [English](./README.md)

```
Claude Code ←→ localhost:16889 ←→ api.deepseek.com/anthropic
```

---

## 安装

```bash
pip install ds-cc-proxy      # 或 pipx / uv tool install
ds-cc-proxy                  # 启动
ds-cc-proxy --stop           # 停止
```

在 `~/.claude/settings.json` 的 `env` 中添加：

```json
"ANTHROPIC_BASE_URL": "http://localhost:16889"
```

---

## 使用

```bash
curl http://localhost:16889/health
# {"status":"ok","version":"0.1.28","upstream":"https://api.deepseek.com/anthropic","circuit":"closed"}

curl http://localhost:16889/usage
# {"requests":247,"input_tokens":1200000,"output_tokens":340000,"cache_hit_pct":98,
#  "estimated_cost_rmb":1.24,"estimated_cost_usd":0.17,
#  "pricing":{"effective":"2026-08-17","is_peak_now":false,...}}
```

DeepSeek V4 自 2026-08-17 起实行峰谷定价，`/usage` 按高峰窗口以 ¥ 拆分计费并给出缓存命中率。

---

## 工作方式

代理读取 Claude Code 请求里已有的 `thinking` 字段，按它分流：

| 请求类型 | 主会话 | 子代理 |
|---|---|---|
| Claude Code 发送的 `thinking` | `enabled` / `adaptive` | `disabled` |
| 意图 | 深度推理 | 快速执行 |
| 路由 | Pro 模型，原始预算 | Flash 模型，`budget_tokens=2048` |
| 理由 | 透传，不影响质量 | DeepSeek 要求 `enabled`，最小预算即可满足 |

子代理成本下降 40%，主会话质量不变。

同时修复直连 DeepSeek Anthropic API 的 12 个兼容性缺陷：thinking 模式隔离、SSE 解析边界、
连接池耗尽、请求头泄漏、非法环境变量崩溃等。

---

## 配置

| 变量 | 默认值 | 说明 |
|---|---|---|
| `PROXY_UPSTREAM` | `https://api.deepseek.com/anthropic` | 主会话 API 地址 |
| `PROXY_FLASH_UPSTREAM` | 同 `PROXY_UPSTREAM` | 子代理 Flash 上游（不设则用 Pro） |
| `PROXY_FLASH_MODEL` | *(空)* | 子代理模型名，如 `deepseek-v4-flash` |
| `PROXY_HOST` | `127.0.0.1` | 监听地址 |
| `PROXY_PORT` | `16889` | 监听端口 |
| `PROXY_LOG_LEVEL` | `warning` | `debug` / `info` / `warning` / `error` |
| `PROXY_LOG_FILE` | *(空)* | 日志文件路径（空 = 仅输出到 stdout） |
| `PROXY_LOG_MAX_BYTES` | `10485760` | 日志文件最大字节数（10MB） |
| `PROXY_LOG_BACKUP_COUNT` | `3` | 日志滚动备份数量 |
| `PROXY_MAX_BODY_BYTES` | `10485760` | 请求体最大字节数（10MB） |
| `PROXY_DUMP_DIR` | *(空)* | 流量捕获（含敏感数据，仅调试用） |
| `PROXY_POOL_MAX_CONNECTIONS` | `50` | 上游连接池上限 |
| `PROXY_POOL_MAX_KEEPALIVE` | `20` | 最大保活连接数 |
| `PROXY_POOL_TIMEOUT` | `120.0` | 池满排队超时（秒） |
| `PROXY_UPSTREAM_TIMEOUT` | `600.0` | 单次上游请求超时（秒） |
| `PROXY_CONNECT_TIMEOUT` | `10.0` | TCP 连接超时（秒） |
| `PROXY_CLIENT_MAX_AGE` | `300.0` | 客户端连接最大存活时间（秒） |
| `PROXY_RETRY_MAX` | `3` | 瞬时故障最大重试次数 |
| `PROXY_RETRY_BACKOFF` | `1.0` | 每次重试的基础退避秒数（指数增长） |
| `PROXY_CB_THRESHOLD` | `8` | 连续失败多少次后打开熔断 |
| `PROXY_CB_WINDOW` | `60.0` | 失败计数窗口（秒） |
| `PROXY_CB_TIMEOUT_BASE` | `30.0` | 熔断基础保持时间（秒） |
| `PROXY_CB_TIMEOUT_MAX` | `300.0` | 熔断最长保持时间（秒，指数退避） |
| `PROXY_CB_BACKOFF_RESET` | `600.0` | 健康多久后重置熔断（秒） |
| `PROXY_CB_HEALTH_INTERVAL` | `15.0` | 半开状态健康检查间隔（秒） |

瞬时故障（网络抖动、502/503）按指数退避重试。在 `PROXY_CB_WINDOW` 内连续失败
`PROXY_CB_THRESHOLD` 次后熔断打开：所有请求立即返回 503，直到半开状态的健康检查
（`PROXY_CB_HEALTH_INTERVAL`）成功。

---

## 同类工具对比

| | ds-cc-proxy | LiteLLM | CCR | OpenRouter |
|---|---|---|---|---|
| 定位 | DeepSeek 专项 | 通用企业网关 | 多供应商路由 | 托管聚合平台 |
| 体量 | ~1.5K LOC · 2 依赖 | ~10K+ LOC · 50+ 依赖 | ~5K+ LOC · 80+ 依赖 | SaaS |
| 可审计 | 是，10 分钟通读 | 否，数天 | 否，半天 | 否，闭源 |
| thinking 适配 | 注入 / 剥离 / adaptive | 部分 | 需插件 | 否 |
| 子代理降本 | Flash 路由 + 预算控制 | 否 | 否 | 否 |
| SSE 解析容错 | 多重类型守卫 | 是 | 通用处理 | 否 |
| 安全加固 | 12 项修复 | 企业级 | 基础 | N/A |

DeepSeek 是主力模型、要稳定与省成本且不想配置，选 ds-cc-proxy。需要多供应商切换、
企业级权限管理，或不用 DeepSeek thinking 模式，选 LiteLLM、CCR 或 OpenRouter。

---

## 附注

`127.0.0.1:16889` 只处理 Claude Code 流量。Clash Verge、V2Ray 等系统代理工作在不同网络层，
两者同时运行，无需配置。

Fork 自 [HosheaLi/P14_dsv4ToCC](https://github.com/HosheaLi/P14_dsv4ToCC) v1.8.0。早期版本
以 `dsv4-cc-proxy` 发布，v0.1.22 更名。

```bash
pip uninstall dsv4-cc-proxy && pip install ds-cc-proxy
```

---

MIT
