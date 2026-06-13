**创建日期**: 2026-06-13 | **最后更新**: 2026-06-13 (v1.0)
**状态**: [IN_PROGRESS]

# ds-cc-proxy TODOS

---

## P2: 去 Starlette，手写原始 ASGI

**What:** 移除 Starlette 依赖，用 ~40 LOC 手工 ASGI callable 替代 Starlette 的路由和 Response 封装。依赖从 3 → 2（httpx + uvicorn）。

**Why:**
- 降低认知负担：贡献者不需要学 Starlette 的 API
- ASGI 是 Python 官方标准（PEP 后继），比 Starlette 更稳定
- 「2 个依赖」的对比表数字更强
- HTTP 边界处理风险真实存在，不适合独立 PR

**Pros:**
- 依赖从 3 → 2，净减 ~100 LOC（手工 ASGI 比 Starlette 更短）
- 安装体积从 ~2MB → ~1.5MB
- 代码从「框架使用」变成「协议实现」，自文档化更强

**Cons:**
- 手工 ASGI 需要自行处理 HTTP 边界（chunked body、trailer headers、pipeline rejection）
- 需要增加 ASGI 层的集成测试
- Starlette 的 736KB 是数百万用户验证过的——手工版本可能引入回归

**Context:**
- 当前 proxy.py 使用 Starlette 仅做三件事：路由（2 条）、Response 封装（JSONResponse/StreamingResponse）、lifespan
- ASGI 接口直接替代方案：`async def app(scope, receive, send)` + `if scope["path"] == "/health"` + `send({...})`
- 实施前应在本地跑通 + 与运行中的 dsv4-cc-proxy 并存验证至少一周
- 改动范围：proxy.py（create_app 函数）+ pyproject.toml（移除 starlette）

**Effort:** M → CC+gstack: S（~30min）
**Priority:** P2 — 锦上添花，不阻塞任何核心功能
**Depends on:** 无
