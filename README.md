# ds-cc-proxy

DeepSeek Anthropic API 兼容性代理 — 让 Claude Code 在 DeepSeek V4 模型上稳定运行。

**只做 DeepSeek，做到极致。**

> 与 [LiteLLM](https://github.com/BerriAI/litellm)、[Claude Code Router](https://github.com/musistudio/claude-code-router)、[OpenRouter](https://openrouter.ai/) 不同，ds-cc-proxy 不做通用多供应商路由。我们**专注 DeepSeek V4 + Claude Code 这一条链路**，在 thinking mode 协议适配、SSE 流解析容错、连接池管理、安全加固上深耕——这些是通用网关永远做不到位的细节。

```
Claude Code ←→ localhost:16889 (ds-cc-proxy) ←→ api.deepseek.com/anthropic
```

## 项目由来

### 命名说明

本项目的早期版本以 `dsv4-cc-proxy`（DeepSeek V4 → Claude Code Proxy）发布，对应 PyPI 包名 `dsv4-cc-proxy`。随着项目从单纯的 "V4 适配" 演变为通用 DeepSeek Anthropic 兼容代理，v1.9.0 社区版更名为 `ds-cc-proxy`（DeepSeek → Claude Code Proxy），去掉了版本号限定。

**如果你正在运行 `dsv4-cc-proxy`**：两者功能兼容，`ds-cc-proxy` 是其后继版本，增加了大量安全加固和鲁棒性修复（见下表）。升级时只需：
```bash
pip uninstall dsv4-cc-proxy
pip install ds-cc-proxy
# CLI 命令变为 ds-cc-proxy（原 dsv4-cc-proxy 不再可用）
```

### 与上游的关系

本项目 fork 自 [HosheaLi/P14_dsv4ToCC](https://github.com/HosheaLi/P14_dsv4ToCC) v1.8.0，在原版双向代理修复基础上，针对 Claude Code 2.1.167+ 的 thinking mode 协议变更和连接池稳定性做了大幅优化。

### 修复的问题

**功能性**

| 问题 | 原版 | 修复后 |
|------|------|--------|
| adaptive thinking 被错误剥离 | Claude Code 2.1.167 `adaptive` 模式触发 thinking 剥离，功能异常 | 识别 `adaptive`，透传 thinking |

**稳定性**

| 问题 | 原版 | 修复后 |
|------|------|--------|
| 连接池耗尽 | 固定 20 连接，长连接占满后全部 502 | 50 连接 + 120s 排队 + 503 Retry-After |
| 上游 4xx/5xx 被误解析 | 当作 SSE 流解析，客户端收到乱码 | 状态码 ≥400 直接透传 |

**鲁棒性**

| 问题 | 原版 | 修复后 |
|------|------|--------|
| 环境变量解析崩溃 | `int(os.getenv(...))` 遇非法值进程崩溃 | try/except + 范围校验 + 安全降级 |
| dump 目录不存在崩溃 | `open()` 失败无处理 | `os.makedirs(exist_ok=True)` + OSError 捕获 |
| `data: null` 致 SSE 解析崩溃 | `data["index"]` KeyError、`.get()` on None | `.get()` + `isinstance(dict)` 守卫 |
| SSE buffer 无界增长 | 无上限，长连接可能 OOM | 1MB 上限 |
| tools/content 非 list | 假设 list 致 TypeError/KeyError | isinstance 类型守卫 |

**生命周期**

| 问题 | 原版 | 修复后 |
|------|------|--------|
| 关闭时暴力切断活跃流 | `aclose()` 立即断开 | 5s 排空期 + shutdown 信号 |
| aclose 异常掩盖原始错误 | 异常覆盖，丢失根因 | try/except 独立记录 |

**可运维**

| 问题 | 原版 | 修复后 |
|------|------|--------|
| root logger 污染 uvicorn 日志 | 清除全局 handler，日志混乱 | 专用 app logger 隔离 |
| content-encoding 错误剥离 | 剥离压缩头，压缩数据当明文返回 | 保留 content-encoding |

## 快速开始

```bash
pip install ds-cc-proxy

# 或隔离安装
pipx install ds-cc-proxy
uv tool install ds-cc-proxy

# 启动代理
ds-cc-proxy

# 停止代理
ds-cc-proxy --stop
```

### 配置 Claude Code

在 `~/.claude/settings.json` 的 `env` 中添加：

```json
"ANTHROPIC_BASE_URL": "http://localhost:16889"
```

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `PROXY_UPSTREAM` | `https://api.deepseek.com/anthropic` | DeepSeek API 地址 |
| `PROXY_HOST` | `127.0.0.1` | 监听地址 |
| `PROXY_PORT` | `16889` | 监听端口 |
| `PROXY_LOG_LEVEL` | `warning` | 日志级别 (`debug`/`info`/`warning`/`error`) |
| `PROXY_LOG_FILE` | *(空)* | 日志文件路径 |
| `PROXY_POOL_MAX_CONNECTIONS` | `50` | 上游连接池最大连接数 |
| `PROXY_POOL_MAX_KEEPALIVE` | `20` | 最大保活连接数 |
| `PROXY_POOL_TIMEOUT` | `120.0` | 池满时等待空闲连接的超时（秒） |
| `PROXY_UPSTREAM_TIMEOUT` | `600.0` | 单次上游请求总超时（秒） |
| `PROXY_CONNECT_TIMEOUT` | `10.0` | 上游 TCP 连接超时（秒） |
| `PROXY_DUMP_DIR` | *(空)* | 流量捕获目录（含敏感数据，仅调试用） |

## 健康检查

```bash
curl http://localhost:16889/health
# {"status":"ok","version":"0.1.22","upstream":"https://api.deepseek.com/anthropic"}
```

## 与本地代理的关系

ds-cc-proxy 监听 `127.0.0.1:16889`，仅处理 Claude Code 通过 `ANTHROPIC_BASE_URL` 显式发来的请求，与 Clash Verge、V2Ray 等系统级代理互不冲突：

- **Clash Verge / V2Ray**：作为系统 HTTP/SOCKS5 代理（通常 `127.0.0.1:7890`），接管浏览器和大部分应用的出站流量
- **ds-cc-proxy**：应用层代理，Claude Code 直连 `localhost:16889`，不经过系统代理

两者工作在不同网络层，可同时运行，无需任何特殊配置。

## 与同类工具的对比

| | **ds-cc-proxy** | LiteLLM | Claude Code Router | OpenRouter |
|---|---|---|---|---|
| 定位 | DeepSeek 专项 | 通用企业网关 | 多供应商路由 | 托管聚合平台 |
| 语言 / 体量 | Python ~650 LOC | Python ~10K+ LOC | Node.js ~5K+ LOC | SaaS，无需部署 |
| 依赖数 | 3 | 50+ | 80+（npm 树） | N/A |
| 可审计性 | ✅ 10 分钟通读 | ❌ 需数天 | ❌ 需半天 | ❌ 闭源 |
| DeepSeek thinking 注入 | ✅ 专项适配 | ⚠️ 部分 | ❌ 需 transformer 插件 | ❌ |
| adaptive 透传 | ✅ 原生 | ❌ | ❌ | ❌ |
| SSE 流解析容错 | ✅ 多重守卫 | ✅ | ⚠️ 通用处理 | ❌ |
| 安全加固 | ✅ 12 项 | ✅ 企业级 | ⚠️ 基础 | N/A（托管） |
| 部署 | `pip install` 一条命令 | YAML 配置 + 启动 | `npm install -g` + JSON | 注册 + API Key |

**什么时候选 ds-cc-proxy**：你的主模型是 DeepSeek，需要 Claude Code 稳定运行，不想折腾复杂配置。

**什么时候选 LiteLLM / CCR / OpenRouter**：需要同时在多个供应商之间切换、需要企业级 RBAC/审计、或者不需要 DeepSeek thinking 专项优化。

## 许可证

MIT
