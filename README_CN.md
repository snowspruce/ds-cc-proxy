<div align="center">

# ds-cc-proxy

**DeepSeek Anthropic API 代理 · 让 Claude Code 在 DeepSeek V4 上更稳、更省**

[![Version](https://img.shields.io/badge/版本-0.1.22-333?style=flat-square)](https://github.com/snowspruce/ds-cc-proxy)
[![Python](https://img.shields.io/badge/python-≥3.11-3776AB?style=flat-square&logo=python&logoColor=white)](https://pypi.org/project/ds-cc-proxy/)
[![License](https://img.shields.io/badge/license-MIT-333?style=flat-square)](./LICENSE)
[![Tests](https://img.shields.io/badge/tests-59%20passed-333?style=flat-square)](./tests/)
[![Size](https://img.shields.io/badge/代码量-~650%20LOC-333?style=flat-square)](./ds_cc_proxy/)
[![Deps](https://img.shields.io/badge/依赖-3-333?style=flat-square)](./pyproject.toml)

<br>

```
Claude Code ←→ localhost:16889 ←→ api.deepseek.com/anthropic
```

[快速开始](#quick-start) · [对比](#comparison) · [English](./README.md)

</div>

---

## &nbsp;

> 子代理成本降 40%。主会话质量零损失。比直连 DeepSeek API 更少的请求失败。
>
> **怎么做到的？** 读取 Claude Code 请求中的 `thinking` 字段。子代理 (`disabled`) 路由到 Flash + `budget_tokens=2048`。主会话 (`enabled`/`adaptive`) 原封不动透传。

| 请求类型 | 主会话 | 子代理 |
|---|---|---|
| CC 发送的 `thinking` | `enabled` / `adaptive` | `disabled` |
| CC 的意图 | 深度推理 | 快速执行 |
| 代理路由 | Pro 模型 · 原始预算 | Flash 模型 · `budget_tokens=2048` |
| 为什么 | 透传——不损害推理质量 | DeepSeek 要求 `enabled`，最小预算满足兼容即可 |

**质量不是妥协了——是提升了。** ds-cc-proxy 修复了直连 DeepSeek Anthropic API 的 12 个兼容性缺陷：thinking 模式隔离、SSE 解析边界情况、连接池耗尽、请求头泄漏、非法环境变量崩溃等。

---

## &nbsp;

<a id="quick-start"></a>
##  快速开始

<div align="center">

```bash
pip install ds-cc-proxy      # 或 pipx / uv tool install
ds-cc-proxy                   # 启动
ds-cc-proxy --stop            # 停止
```

</div>

在 `~/.claude/settings.json` 的 `env` 中添加：

```json
"ANTHROPIC_BASE_URL": "http://localhost:16889"
```

```bash
curl http://localhost:16889/health
# {"status":"ok","version":"0.1.22","upstream":"https://api.deepseek.com/anthropic"}

curl http://localhost:16889/usage
# {"requests":247,"input_tokens":1200000,"output_tokens":340000,"estimated_cost_usd":0.87,...}
```

---

##  环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `PROXY_UPSTREAM` | `https://api.deepseek.com/anthropic` | 主会话 API 地址 |
| `PROXY_FLASH_UPSTREAM` | 同 `PROXY_UPSTREAM` | 子代理 Flash 上游（不设则用 Pro） |
| `PROXY_FLASH_MODEL` | *(空)* | 子代理模型名，如 `deepseek-v4-flash` |
| `PROXY_HOST` | `127.0.0.1` | 监听地址 |
| `PROXY_PORT` | `16889` | 监听端口 |
| `PROXY_LOG_LEVEL` | `warning` | `debug` / `info` / `warning` / `error` |
| `PROXY_POOL_MAX_CONNECTIONS` | `50` | 上游连接池上限 |
| `PROXY_POOL_MAX_KEEPALIVE` | `20` | 最大保活连接数 |
| `PROXY_POOL_TIMEOUT` | `120.0` | 池满排队超时（秒） |
| `PROXY_UPSTREAM_TIMEOUT` | `600.0` | 单次上游请求超时（秒） |
| `PROXY_CONNECT_TIMEOUT` | `10.0` | TCP 连接超时（秒） |
| `PROXY_DUMP_DIR` | *(空)* | 流量捕获（含敏感数据，仅调试用） |

---

<a id="comparison"></a>
##  同类工具对比

| | **ds-cc-proxy** | LiteLLM | CCR | OpenRouter |
|---|---|---|---|---|
| 定位 | DeepSeek 专项 | 通用企业网关 | 多供应商路由 | 托管聚合平台 |
| 体量 | ~650 LOC · 3 依赖 | ~10K+ LOC · 50+ 依赖 | ~5K+ LOC · 80+ 依赖 | SaaS |
| 可审计 | ✅ 10 分钟通读 | ❌ 数天 | ❌ 半天 | ❌ 闭源 |
| thinking 适配 | ✅ 注入/剥离/adaptive | ⚠️ 部分 | ❌ 需插件 | ❌ |
| 子代理降本 | ✅ Flash 路由 + 预算控制 | ❌ | ❌ | ❌ |
| SSE 解析容错 | ✅ 多重类型守卫 | ✅ | ⚠️ 通用处理 | ❌ |
| 安全加固 | ✅ 12 项修复 | ✅ 企业级 | ⚠️ 基础 | N/A |

> **选 ds-cc-proxy**：DeepSeek 是你的主力模型，要稳定、省成本、不折腾。
>
> **选 LiteLLM / CCR / OpenRouter**：需要多供应商切换、企业级权限管理、或不用 DeepSeek thinking。

---

##  与本地代理共存

ds-cc-proxy (`127.0.0.1:16889`) 只处理 Claude Code 流量。Clash Verge、V2Ray 等系统代理工作在不同网络层——同时运行，无需特殊配置。

---

##  项目由来

Fork 自 [HosheaLi/P14_dsv4ToCC](https://github.com/HosheaLi/P14_dsv4ToCC) v1.8.0。早期版本以 `dsv4-cc-proxy` 发布。v0.1.22 更名为 `ds-cc-proxy`。升级命令：

```bash
pip uninstall dsv4-cc-proxy && pip install ds-cc-proxy
```

---

<div align="center">

## License

MIT · [snowspruce/ds-cc-proxy](https://github.com/snowspruce/ds-cc-proxy) · [English](./README.md)

</div>
