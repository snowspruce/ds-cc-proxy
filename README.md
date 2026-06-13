# dsv4-cc-proxy

DeepSeek Anthropic API 兼容性代理 — 让 Claude Code 在 DeepSeek V4 模型上稳定运行。

```
Claude Code ←→ localhost:16889 (dsv4-cc-proxy) ←→ api.deepseek.com/anthropic
```

## 与上游的区别

本项目 fork 自 [HosheaLi/P14_dsv4ToCC](https://github.com/HosheaLi/P14_dsv4ToCC) v1.8.0，在原版双向代理修复基础上，针对 Claude Code 2.1.167+ 的 thinking mode 协议变更和连接池稳定性做了大幅优化。

### 修复的问题

| 类别 | 问题 | 原版行为 | 修复后 |
|------|------|----------|--------|
| **功能性** | adaptive thinking 被错误剥离 | Claude Code 2.1.167 默认 `adaptive` 模式触发 thinking 剥离，导致功能异常 | 识别 `adaptive` 类型，透传 thinking |
| **稳定性** | 连接池耗尽 | 固定 20 连接，thinking mode 长连接占满后所有请求返回 502 | 50 连接 + 120s 排队超时 + 503 Retry-After |
| **稳定性** | 上游错误透传 | 4xx/5xx 被当作 SSE 解析，客户端收到乱码 | 4xx/5xx 先于 SSE 解析检查，直接透传 |
| **鲁棒性** | 配置解析崩溃 | `int(os.getenv(...))` 遇非法值直接进程崩溃 | try/except + 合法性校验 + 安全降级 |
| **鲁棒性** | dump 目录缺失 | `_dump_json` 遇目录不存在/不可写时崩溃 | 启动时 `os.makedirs` + OSError 捕获 |
| **鲁棒性** | SSE 解析崩溃 | `data["index"]` KeyError、`data: null` 致 `.get()` AttributeError | `.get()` + isinstance 守卫 |
| **鲁棒性** | SSE buffer OOM | buffer 无上限 | 1MB 上限防溢出 |
| **鲁棒性** | tools/messages 格式异常 | 非 list 类型的 tools/content 致 TypeError/KeyError | isinstance 类型守卫 |
| **生命周期** | 优雅关闭 | 暴力 `aclose()` 切断活跃流 | 5s 排空期 + shutdown 标志 |
| **生命周期** | 流异常覆盖 | `aclose()` 抛异常覆盖原始错误 | try/except 包装 |
| **可运维** | 日志冲突 | 清除 root logger 与 uvicorn 冲突 | 专用 app logger 隔离 |
| **可运维** | content-encoding 错误剥离 | 剥离后压缩字节当成未压缩数据返回 | 保留 content-encoding 头 |

## 快速开始

```bash
pip install git+https://github.com/snowspruce/ds-cc-proxy.git

# 启动代理
dsv4-cc-proxy

# 停止代理
dsv4-cc-proxy --stop
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
# {"status":"ok","version":"1.9.0","upstream":"https://api.deepseek.com/anthropic"}
```

## 许可证

MIT
