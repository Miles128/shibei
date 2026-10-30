# Sidecar Shared RPC Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** CLI、MCP、DSH 插件全部经同一条 loopback HTTP sidecar 访问拾贝内核，禁止适配器直连 SQLite。

**Architecture:** `Shibei` 只活在 sidecar 进程里。`ShibeiClient`（Python）与 `dsh-shibei`（TypeScript）打同一套 `/v1` JSON。CLI/MCP 改走客户端；DSH 在 `agent/pre-step` 注入 compact pack。

**Tech Stack:** Python 3.9 stdlib `http.server`；pytest；TypeScript Cordis 插件（peer `@deepseek-ai/cordis`）。

## Global Constraints

- 不新增 PyPI 依赖
- 只绑定 `127.0.0.1`，`Authorization: Bearer`
- 内核表结构不变
- `from shibei import Shibei` 仅 sidecar 内部与内核单测使用
- 验证：`PYTHONPATH=src pytest tests/ -v`

---

### Task 1: Sidecar HTTP + 内核往返

**Files:**
- Create: `src/shibei/sidecar.py`
- Create: `tests/test_sidecar_protocol.py`
- Modify: `src/shibei/services.py` (`search` 增加每请求 rewriter/expander 覆盖，不写回 YAML)

**Interfaces:**
- Produces: `start_server(config_path) -> (ThreadingHTTPServer, run_info)`；`/v1/health|context|search|remember|...`

- [ ] Write failing protocol tests (health 401/200, remember then context hits, search flags)
- [ ] Run `PYTHONPATH=src pytest tests/test_sidecar_protocol.py -v` — FAIL import
- [ ] Implement sidecar router + auth + write lock
- [ ] Re-run tests — PASS
- [ ] Commit: `feat(sidecar): add loopback HTTP kernel server`

### Task 2: Python client + ensure daemon

**Files:**
- Create: `src/shibei/client.py`
- Create: `tests/test_client_ensure.py`

**Interfaces:**
- Produces: `ShibeiClient(config_path)` 方法与 `Shibei` 同名；`ensure_sidecar()` flock + spawn `python -m shibei sidecar --daemon`

- [ ] Failing tests: client remember/context; ensure spawn/reuse/restart-on-dead-pid
- [ ] Implement client + CLI `sidecar` subcommand (`--daemon`, `--stop`)
- [ ] Tests PASS
- [ ] Commit: `feat(sidecar): add ShibeiClient and daemon ensure`

### Task 3: CLI + MCP 切到 client

**Files:**
- Modify: `src/shibei/cli.py` — 所有 `Shibei(` → `ShibeiClient(`；search 覆盖走请求字段
- Modify: `src/shibei/server.py` — `_init_brain` 返回 `ShibeiClient`
- Modify: `tests/test_cli.py` — patch `ShibeiClient`

- [ ] CLI/MCP 现有测试仍绿（FakeBrain 改 patch 目标）
- [ ] 一条真 sidecar 冒烟：CLI remember → MCP context 同源
- [ ] Commit: `feat: route CLI and MCP through sidecar client`

### Task 4: dsh-shibei 插件

**Files:**
- Create: `packages/dsh-shibei/src/client.ts`, `inject.ts`, `index.ts`, `package.json`

**Interfaces:**
- Consumes: 同一 `/v1` 契约
- Produces: `agent/pre-step` inject + tools `shibei_remember` / `shibei_search`

- [ ] Pack 渲染截断单测
- [ ] Plugin `apply` 注册 hook/tools
- [ ] Commit: `feat(dsh): add dsh-shibei pre-step inject plugin`

### Task 5: 文档

- Modify: `README.md`, `docs/MCP_INTEGRATION.md` — 三入口经 sidecar
- [ ] Commit: `docs: sidecar as the single RPC path`
