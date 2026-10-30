# 拾贝 Agent 外挂记忆 — 设计规格

**Date:** 2026-08-27
**Status:** Draft, pending review
**Decision lock:** CLI、MCP、DeepSeek Harness 从 P0 起走**同一条** sidecar RPC，禁止任何适配器直连 `Shibei` 内核。

---

## 1. Problem

拾贝今天是「中文知识库 + 长期记忆」，对外三层接口（CLI / MCP / Python）都在进程内调用 `Shibei`。Agent 侧的「强制召回」写在宿主 Rule 里：模型必须先想起来再调 `shibei_context`。结果是：

- 答前多一轮工具调用（慢、易漏调；MCP 因此被关掉）
- 三套入口各自持有内核实例，没有单一进程边界（SQLite 多写、配置漂移）
- DeepSeek Harness 等新宿主有 `agent/pre-step` + `agent.inject()` 原语，MCP 用不上

要解决的不是「再做一个 MCP」，而是把拾贝变成 **Agent 外挂记忆**：检索内核常驻，宿主只做薄适配；召回发生在模型开口之前。

## 2. Goals

1. **一条 RPC**：所有外部入口（CLI、MCP、DSH 插件、以及未来宿主）只通过 sidecar 的 `/v1/*` JSON API 访问知识库与记忆。
2. **强制注入，不靠自觉**：DSH 在 `agent/pre-step` 自动 `inject` compact pack；MCP 仍是工具模型（宿主限制），但协议与数据与 CLI/DSH 完全一致。
3. **内核不动**：BM25 / 分块 / 导入 / 版本链记忆 / Dreaming 仍由 Python `Shibei` 实现；sidecar 是它的唯一对外进程。
4. **长期记忆不进 session log**：SQLite 仍在 `~/.shibei`。DSH 只把每步 compact pack 写入 session（满足「模型可见 = 已落日志」）。

## 3. Non-goals

- 把长期记忆写入 DSH session log 或 Markdown 文件当主存储
- 用 TypeScript 重写 BM25 / jieba / 分块
- 绑死 DSH-only（MCP / CLI 继续存在，只是改成 sidecar 客户端）
- 在 pre-step 里跑服务端 LLM 摘要或 `auto_memory` 抽取
- P0 做 Dreaming UI、settings card、Conversation Node
- 改现有 chunk / memory 表结构

## 4. Architecture

```
CLI (shibei CLI)
MCP adapter (stdio JSON-RPC)     ──►  ShibeiClient  ──►  sidecar : loopback HTTP
DSH plugin (Cordis TypeScript)

sidecar process
  HTTP /v1/*  ──►  Shibei (services.py)  ──►  SQLite FTS5 (~/.shibei)
```

| 单元 | 职责 | 依赖 |
|---|---|---|
| `Shibei` | 内核：检索、记忆、导入、dream | config + SQLite |
| sidecar | 唯一对外进程；把 HTTP 映射到 `Shibei` 方法 | `Shibei` |
| `ShibeiClient` | Python 客户端：ensure daemon、鉴权、超时 | 运行文件 |
| MCP adapter | MCP JSON-RPC ↔ `ShibeiClient` | 客户端 |
| CLI | 参数解析、打印；业务调用只走客户端 | 客户端 |
| `dsh-shibei` | Cordis 插件：pre-step inject + tools/commands | 同一 `/v1` 契约 |

`from shibei import Shibei` 保留给 sidecar 内部与单元测试。进程外代码（CLI、MCP、第三方脚本）使用 `from shibei.client import ShibeiClient`。

## 5. Sidecar process

### 5.1 Identity

每个 **profile** 一个 sidecar（独立 DB、独立端口）。默认 profile 名为 `default`。

运行文件（权限 `0600`）：

```
~/.shibei/run/<profile>/sidecar.json
```

```json
{
  "pid": 12345,
  "host": "127.0.0.1",
  "port": 49152,
  "token": "<32-byte hex>",
  "config_path": "/Users/me/.shibei/profiles/default.yaml",
  "started_at": "2026-08-27T15:00:00+08:00",
  "version": "0.1.0"
}
```

- 只绑定 `127.0.0.1`，`port=0` 由内核分配，写入运行文件。
- 每个请求带 `Authorization: Bearer <token>`。无 token 或 token 错 → `401`。
- 不使用 Unix socket（Node `fetch` 与 curl 调试成本更高）；loopback HTTP 是三条适配器的唯一传输。

### 5.2 Lifecycle

命令：

```
shibei sidecar                 # 前台（开发）
shibei sidecar --daemon        # 后台；写 pid；stdin 断开
shibei sidecar --stop          # 对运行文件里的 pid 发 SIGTERM，删运行文件
```

**Ensure（所有客户端共用）**：

1. 读 `sidecar.json`。没有 → spawn。
2. `GET /v1/health`，超时 200ms。成功且 `config_path` 与本次请求一致 → 复用。
3. 失败：若 pid 仍活则 `SIGTERM` 后等 300ms；删除运行文件；spawn。
4. Spawn：`sys.executable -m shibei sidecar --daemon --config <path>`，工作目录不重要。等 health，最多 2s，间隔 50ms。
5. 仍失败 → 抛 `SidecarError`，CLI 退出码 2。

DSH 插件同样执行 ensure：用 `child_process.spawn` 调 `shibei sidecar --daemon`（`SHIBEI_CONFIG` 来自插件 config）。unload 时 **不** 杀 sidecar——CLI 与 MCP 可能还在用。只有 `shibei sidecar --stop` 或用户杀进程才停。

### 5.3 Concurrency

一个 sidecar 一个 `Shibei` 实例。`ThreadingHTTPServer` 接受并发 HTTP；写路径（remember / import / update / consolidate）在 sidecar 内用一把 `threading.Lock` 串行化。SQLite 仍是权威锁。

读路径（context / search / persona / health）不加这把锁，避免 pre-step 被 import 堵住超过 80ms 预算。若写正在持有 SQLite 写锁，读等待 SQLite，不另造队列。

## 6. HTTP contract (`/v1`)

统一信封：

- 成功：`200` + JSON body（与今天 `Shibei.*` 返回的 dict/list/bool 相同，字段名不改）。
- 校验失败：`400` + `{"error": {"code": "invalid_request", "message": "..."}}`
- 未找到：`404` + `{"error": {"code": "not_found", "message": "..."}}`（如 update 未知 memory_id）
- 未授权：`401`
- 内核异常：`500` + `{"error": {"code": "internal", "message": "..."}}`（message 不带 traceback）

`Content-Type: application/json; charset=utf-8`。

| Method | Path | Body / Query | Kernel |
|---|---|---|---|
| GET | `/v1/health` | — | `{ok, version, profile, db_path, pid, config_path}` |
| POST | `/v1/context` | `{message, query?, limit?, tag?, write_hints?}` | `Shibei.context` |
| POST | `/v1/search` | `{query, limit?, tag?, include_memories?, rewrite?, llm_rewrite?, expand?, fuzzy?, synonyms?}` | `Shibei.search` + 每请求覆盖 rewriter/expander（**不写回** config 文件） |
| POST | `/v1/remember` | `{fact, category?, source?, tags?, expires_at?, source_message_ids?}` | `Shibei.remember` |
| POST | `/v1/memories/update` | `{memory_id, fact, tags?}` | `Shibei.update_memory` |
| POST | `/v1/memories/forget` | `{memory_id}` | `Shibei.forget_memory` |
| GET | `/v1/memories` | `?limit&category` | `Shibei.list_memories` |
| GET | `/v1/persona` | `?per_category&max_facts` | `Shibei.persona` |
| POST | `/v1/import` | `{dirs?, full?, file_pattern?}` | `Shibei.import_all` |
| GET | `/v1/sources` | `?limit&offset` | `Shibei.list_sources` |
| POST | `/v1/dream` | `{housekeeping?, format?, persist?}` | `Shibei.dream` |
| POST | `/v1/consolidate` | `{threshold?, min_cluster_size?, max_chunks?, apply?}` | `Shibei.consolidate` |
| POST | `/v1/consolidate/apply` | `{clusters}` | `Shibei.apply_consolidation` |

超时（客户端）：

| 类 | 超时 |
|---|---|
| health | 200ms |
| context / search / persona / memories | 2s |
| remember / update / forget | 2s |
| import / dream / consolidate | 120s |

Offload 行为保持现状：kernel 在结果超过 `offload.max_bytes` 时落盘，HTTP 仍返回摘要 + 路径。适配器不得把完整 JSON 再展开进模型上下文，除非用户显式 `search`。

## 7. Adapters

### 7.1 Python client

新模块 `src/shibei/client.py`：

- `ShibeiClient(config_path: str)`：ensure sidecar，暴露与上表一一对应的方法。
- 方法签名与 `Shibei` 对齐（同名、同默认值），返回同结构 dict。CLI/MCP 从 `Shibei(...)` 换成 `ShibeiClient(...)` 即可。
- `search` 的 CLI 覆盖项（`--expand` 等）作为请求字段传入，不在客户端改 YAML。

测试：对 sidecar 用 `http.client` 打真实端口（pytest fixture 起前台 sidecar，临时 `SHIBEI_HOME` / 临时 db）。内核单测继续直接实例化 `Shibei`。

### 7.2 CLI

`cmd_*` 全部改走 `ShibeiClient`。新增子命令 `sidecar`（见 5.2）。`shibei serve` 仍是 MCP 入口，但内部不再 `Shibei(config)`，改为 ensure + MCP 循环。

用户体感：第一次 `shibei search` 可能多 ~200ms spawn；之后复用 daemon。`shibei sidecar --stop` 写进 `--help`。

### 7.3 MCP

`server.py` 只做 JSON-RPC 分发。每个 handler 调 `ShibeiClient`。工具名、inputSchema、返回 JSON 文本与现在兼容，避免已有 Cursor Rule 失效。

不再在 MCP 进程里打开 SQLite。MCP 崩溃不影响 sidecar。

### 7.4 DeepSeek Harness plugin (`dsh-shibei`)

独立 npm 包，仓库路径 `packages/dsh-shibei/`（TypeScript）。不 vendoring DeepSeek Harness。

**inject（硬依赖）**

```
tools, agents
```

可选探测：`ctx.get('commands')`、`ctx.get('jobs')`、`ctx.get('llm')`。

**Hooks（P0 必须）**

| 扩展点 | 行为 |
|---|---|
| `agent/pre-step` | 取本步将进入的 user 文本；`POST /v1/context`；`agent.inject({ content, source: { kind: 'plugin', plugin: 'dsh-shibei' } })`。pack 渲染为短 Markdown（命中标题 + 截断正文 + 记忆），受 `maxInjectedBytes`（默认 4096）截断。context 失败则跳过注入，不 reject 该步。 |
| `ctx.tools.register` | `shibei_remember`、`shibei_search`（显式）。P0 不注册 import/dream（CLI 已有；下一阶段再挂）。 |

**Hooks（P1）**

| 扩展点 | 行为 |
|---|---|
| `agent/session-start` | `GET /v1/persona`，inject 一次短画像 |
| `ctx.commands` | `/shibei`、`/memory` |
| `agent/turn-stopping` | 若最近一次 context 的 `suggest_remember.should` 为真，inject 一条写侧提示（默认不自动 `remember`） |

**禁止**

- 用 `ctx.systemPrompt.section()` 塞召回包（`complete: true` preset 会丢掉）。
- 把知识库全文 inject。
- 在 pre-step 里调宿主 LLM 做改写（P0 用 sidecar 规则抽词；`query` 不传）。P1 可加插件 config `rewrite: host-llm`，同步 `ctx.llm` 抽 5–8 词后再 context，仍在同一步。

**插件 config（cordis patch）**

```yaml
configPath: ~/.shibei/config.yaml   # 或 SHIBEI_CONFIG
maxInjectedBytes: 4096
injectLimit: 5
skipInstruction: true               # 纯指令类消息不召回：长度 < 8 且无「我/笔记/记得」等
```

加载：`dsh plugin add` 或 profile `cordis.patch.yml` insert 一行指向包入口。`package.json` 带 `dsh` bundle 字段以便发现。

## 8. Query rewrite policy

| 入口 | P0 改写 |
|---|---|
| DSH pre-step | 不传 `query`，sidecar 规则抽词 |
| MCP `shibei_context` | 保持现状：宿主可传 `query` |
| CLI `shibei context --query` | 原样传到 `/v1/context` |
| CLI `--llm-rewrite` | 请求字段 `llm_rewrite: true`；需要 `SHIBEI_LLM_API_KEY` 在 **sidecar 进程**环境里 |

不在 DSH pre-step 默认打宿主 LLM：延迟预算是 80ms 量级；规则抽词 + BM25 已能工作。

## 9. Error handling

| 情况 | 行为 |
|---|---|
| sidecar 未起、ensure 失败 | CLI 退出 2；MCP 工具返回 `isError`；DSH pre-step 跳过 inject |
| context 超时 | 客户端当失败；DSH 跳过 inject |
| 写冲突 / SQLite busy | sidecar 重试 3 次、间隔 20ms；仍失败 500 |
| 配置路径与运行中 sidecar 不一致 | ensure 停旧进程再拉新的（同一 profile 不允许两份 config） |
| token 文件被他人可读 | 创建时 `0600`；启动时若 mode 过宽则拒绝启动 |

## 10. Testing

内核现有 pytest 保持 in-process（`Shibei`）。新增：

1. `tests/test_sidecar_protocol.py`：临时目录起 sidecar，覆盖 health / context / remember / search 往返，断言与 `Shibei` 直连返回字段一致。
2. `tests/test_client_ensure.py`：无 daemon 时 ensure 会 spawn；第二次复用同一 pid；pid 死后会重启。
3. `tests/test_cli_via_client.py`：现有 CLI 测试改为打 sidecar（或 mock `ShibeiClient` 的合同测试 + 一条真 sidecar 冒烟）。
4. `tests/test_mcp_via_client.py`：MCP `tools/call` 在 sidecar 之上返回与现在相同的 JSON 文本结构。
5. `packages/dsh-shibei`：用最小 mock `ctx` 测 pre-step 渲染与 byte 截断（不引入完整 dsh）。

验证命令（与仓库惯例一致）：

```
PYTHONPATH=src pytest tests/ -v
```

DSH 包另加 `pnpm test`（P0 至少渲染/截断单测）。

## 11. File layout

```
src/shibei/services.py          # 内核，仅 sidecar 与单测实例化
src/shibei/sidecar.py           # 新建：HTTP 服务、运行文件、daemon
src/shibei/client.py            # 新建：ShibeiClient + ensure
src/shibei/server.py            # MCP：改走 client
src/shibei/cli.py               # 改走 client；新增 sidecar 子命令
packages/dsh-shibei/            # 新建：Cordis 插件
  src/index.ts
  src/client.ts                 # 同一 /v1 契约的 TS 客户端
  src/inject.ts                 # pack → Markdown + 截断
  package.json
tests/test_sidecar_protocol.py
tests/test_client_ensure.py
docs/MCP_INTEGRATION.md         # 注明 MCP 经 sidecar；契约工具名不变
```

不新增 PyPI 依赖（stdlib `http.server` + `json`）。DSH 包依赖 `@deepseek-ai/cordis` 为 peerDependency。

## 12. Phases

**P0 — 同一条 RPC + DSH 强制注入（本规格的实现范围）**

- sidecar、运行文件、token、ensure
- Python `ShibeiClient`；CLI 与 MCP 全部切过去
- `dsh-shibei`：pre-step inject + remember/search 工具
- 验收：
  1. `shibei remember "..."` 后，不重启，MCP `shibei_context` 与 DSH 下一 turn 都能召回同一条。
  2. DSH 新会话不问背景，pre-step 注入的 pack 含已导入笔记的命中。
  3. 停掉 sidecar 后，下一条 CLI 命令会拉起并成功。
  4. `PYTHONPATH=src pytest tests/ -v` 通过。

**P1** session-start persona、`/shibei` `/memory`、turn-stopping 写提示、可选 `rewrite: host-llm`。

**P2** MCP 手写循环的协议缺口（ping、parse error）按需修；不改工具契约。

**P3** dream → `ctx.jobs`；settings card；命中 Conversation Node。

## 13. Risks

| 风险 | 缓解 |
|---|---|
| DSH API 仍在 developer preview，breaking | 插件只依赖 `agents`/`tools` 与 `agent/pre-step`、`agent.inject`；版本写进 README |
| 本机防火墙/端口 | 只绑 127.0.0.1；port 0 |
| CLI 首次 spawn 感觉慢 | ensure 等待上限 2s；daemon 常驻 |
| 多 profile 串库 | 运行目录按 profile 隔离；ensure 校验 `config_path` |
| 测试里残留 daemon | fixture `finally` 调 `--stop` 并删临时 `SHIBEI_HOME` |
