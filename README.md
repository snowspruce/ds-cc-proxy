# ds-cc-proxy

DeepSeek Anthropic API 兼容性代理 — 让 Claude Code 在 DeepSeek V4 上 **更稳定、更省成本**。

```
Claude Code ←→ localhost:16889 (ds-cc-proxy) ←→ api.deepseek.com/anthropic
```

## 更低成本，更高质量

ds-cc-proxy 做到了看起来矛盾的事：比直连 DeepSeek API **更稳定**，同时比直连 **更省钱**。

**原理很简单——识别 Claude Code 的请求意图，区别对待：**

| | 主会话 | 子代理 |
|---|---|---|
| CC 发送的 thinking | `enabled` / `adaptive` | `disabled` |
| CC 的意图 | 我要深度思考 | 我不需要思考 |
| ds-cc-proxy 路由 | Pro 模型 + 原始 thinking 预算 | Flash 模型 + budget_tokens=2048 |
| 为什么这样 | 透传原始配置，不损害推理质量 | DeepSeek 要求 thinking=enabled，给最小预算满足兼容性即可 |

**成本节省 ~40%**：子代理占 API 调用量的 30-50%，每条节省 ~50% thinking token + Flash 模型单价更低。主会话质量完全不受影响。

**质量更高**：ds-cc-proxy 修复了直连 DeepSeek Anthropic API 的兼容性问题（thinking 模式隔离、SSE 解析容错、连接池管理），请求失败率更低、响应更完整。

## 快速开始

```bash
pip install ds-cc-proxy      # 或 pipx / uv tool install
ds-cc-proxy                   # 启动
ds-cc-proxy --stop            # 停止
```

在 `~/.claude/settings.json` 的 `env` 中添加：

```json
"ANTHROPIC_BASE_URL": "http://localhost:16889"
```

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `PROXY_UPSTREAM` | `https://api.deepseek.com/anthropic` | 主会话 API 地址 |
| `PROXY_FLASH_UPSTREAM` | 同 `PROXY_UPSTREAM` | 子代理 Flash 上游（不设置则子代理也用 Pro） |
| `PROXY_FLASH_MODEL` | *(空)* | 子代理模型名，如 `deepseek-v4-flash` |
| `PROXY_HOST` | `127.0.0.1` | 监听地址 |
| `PROXY_PORT` | `16889` | 监听端口 |
| `PROXY_LOG_LEVEL` | `warning` | `debug` / `info` / `warning` / `error` |
| `PROXY_LOG_FILE` | *(空)* | 日志文件路径 |
| `PROXY_POOL_MAX_CONNECTIONS` | `50` | 上游连接池上限 |
| `PROXY_POOL_MAX_KEEPALIVE` | `20` | 最大保活连接数 |
| `PROXY_POOL_TIMEOUT` | `120.0` | 池满排队超时（秒） |
| `PROXY_UPSTREAM_TIMEOUT` | `600.0` | 单次上游请求超时（秒） |
| `PROXY_CONNECT_TIMEOUT` | `10.0` | TCP 连接超时（秒） |
| `PROXY_DUMP_DIR` | *(空)* | 调试用流量捕获目录（含敏感数据） |

## 健康检查

```bash
curl http://localhost:16889/health
# {"status":"ok","version":"0.1.22","upstream":"https://api.deepseek.com/anthropic"}
```

## 与同类工具的对比

| | **ds-cc-proxy** | LiteLLM | Claude Code Router | OpenRouter |
|---|---|---|---|---|
| 定位 | DeepSeek 专项 | 通用企业网关 | 多供应商路由 | 托管聚合平台 |
| 体量 | Python ~650 LOC / 3 依赖 | ~10K+ LOC / 50+ 依赖 | ~5K+ LOC / 80+ 依赖 | SaaS |
| 可审计性 | ✅ 10 分钟通读 | ❌ 需数天 | ❌ 需半天 | ❌ 闭源 |
| thinking 适配 | ✅ 注入/剥离/adaptive 透传 | ⚠️ 部分 | ❌ 需插件 | ❌ |
| 子代理成本优化 | ✅ Flash 路由 + budget 控制 | ❌ | ❌ | ❌ |
| SSE 解析容错 | ✅ 多重类型守卫 | ✅ | ⚠️ 通用处理 | ❌ |
| 安全加固 | ✅ 12 项（路径防护/流控/日志隔离等） | ✅ 企业级 | ⚠️ 基础 | N/A |

**选 ds-cc-proxy**：主模型是 DeepSeek，需要稳定 + 省成本，不想折腾。

**选 LiteLLM / CCR / OpenRouter**：需要多供应商切换、企业级 RBAC、或不需要 DeepSeek thinking 专项适配。

## 与本地代理的关系

ds-cc-proxy 监听 `127.0.0.1:16889`，仅处理 Claude Code 发送的请求。Clash Verge、V2Ray 等系统代理工作在不同网络层，两者可同时运行，无需特殊配置。

## 项目由来

本项目的早期版本以 `dsv4-cc-proxy` 发布（DeepSeek V4 → Claude Code Proxy），v0.1.22 社区版更名为 `ds-cc-proxy`，去掉了版本号限定。两者功能兼容，`ds-cc-proxy` 是后继版本：

```bash
pip uninstall dsv4-cc-proxy && pip install ds-cc-proxy
```

Fork 自 [HosheaLi/P14_dsv4ToCC](https://github.com/HosheaLi/P14_dsv4ToCC) v1.8.0，在原有双向代理修复基础上增加了安全加固、thinking 协议适配和成本优化。

## 许可证

MIT
