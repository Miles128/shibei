# Feature Specification: Shibei 中文语义知识库

**Feature Branch**: `main`

**Created**: 2025-01-01

**Status**: Active

**Input**: 面向 AI Agent 的中文语义知识库，作为 Agent 的外部大脑

## User Scenarios & Testing

### User Story 1 - 文档导入与搜索 (Priority: P1)

用户将本地 Markdown/TXT/DOCX 文件导入知识库，通过关键词搜索快速定位文档内容。

**Why this priority**: 文档导入和搜索是知识库的核心价值，没有这个功能项目无法使用。

**Independent Test**: 导入一个 Markdown 文件，用关键词搜索验证能返回相关内容。

**Acceptance Scenarios**:

1. **Given** 一个包含 Markdown 文件的目录, **When** 执行 `shibei import`, **Then** 文件被分块并索引，返回 `{"files": N, "chunks": N, "status": "ok"}`
2. **Given** 已导入的文档, **When** 执行 `shibei search "关键词"`, **Then** 返回相关 chunk，包含 source、score、text
3. **Given** 已导入的文档, **When** 再次执行 `shibei import`（无变更）, **Then** 返回 `{"skipped": N, "files": 0}` 跳过未变更文件
4. **Given** 已导入的文档, **When** 修改文件后执行 `shibei import`, **Then** 仅重新导入变更文件，旧 chunk 被删除

---

### User Story 2 - 对话记忆 (Priority: P2)

Agent 在对话中记住用户的关键事实，后续对话可检索这些记忆。

**Why this priority**: 对话记忆让 Agent 具备长期记忆能力，是"外部大脑"定位的核心。

**Independent Test**: 写入一条记忆，搜索验证能返回。

**Acceptance Scenarios**:

1. **Given** Agent 获得一条事实, **When** 执行 `shibei remember "用户喜欢 Python"`, **Then** 记忆被存储并返回 `{"id": "...", "fact": "..."}`
2. **Given** 已存储的记忆, **When** 执行 `shibei search "用户喜欢什么"`, **Then** 返回相关记忆，包含 score 和 fact
3. **Given** 一条新记忆与旧记忆语义重复, **When** 写入新记忆, **Then** 自动建立版本链（supersedes），旧记忆标记为 superseded
4. **Given** LLM 已启用, **When** 执行 `shibei remember` 传入一段话, **Then** LLM 自动提取多条独立事实分别存储

---

### User Story 3 - Dreaming 知识库自主思考 (Priority: P3)

知识库自动扫描所有文档，发现跨文档的隐藏连接和主题关联，生成洞察报告。

**Why this priority**: Dreaming 是差异化功能，让知识库从被动存储变为主动思考。

**Independent Test**: 导入多个文档后执行 `shibei dream`，验证返回包含关键词、主题、洞察的报告。

**Acceptance Scenarios**:

1. **Given** 已导入多个文档, **When** 执行 `shibei dream`, **Then** 返回包含 keywords、topics、insights 的报告
2. **Given** LLM 已启用, **When** 执行 `shibei dream`, **Then** 洞察报告包含 `[LLM]` 标记的增强分析
3. **Given** 已有历史 dream 结果, **When** 再次执行 `shibei dream`, **Then** 支持增量对比，显示新增主题

---

### User Story 4 - MCP 协议集成 (Priority: P4)

通过 MCP 协议将知识库能力暴露给 AI Agent，Agent 可直接调用搜索、记忆、导入等功能。

**Why this priority**: MCP 集成让任何 MCP-compatible Agent 都能使用知识库，是项目生态扩展的关键。

**Independent Test**: 启动 MCP server，发送 JSON-RPC 请求验证响应。

**Acceptance Scenarios**:

1. **Given** MCP server 已启动, **When** 发送 `tools/list` 请求, **Then** 返回 5 个工具定义（shibei_search/import/dream/remember/list_sources）
2. **Given** MCP server 已启动, **When** 发送 `tools/call` 请求调用 `shibei_search`, **Then** 返回搜索结果 JSON
3. **Given** 已导入文档, **When** Agent 调用 `shibei_import` 工具, **Then** 返回导入统计信息

---

### User Story 5 - 多知识库 Profile (Priority: P5)

用户可创建多个独立的知识库（如面试、工作、日记），互不干扰，快速切换。

**Why this priority**: 多 Profile 支持让一个实例服务多个场景，提升实用性。

**Independent Test**: 创建两个 profile，分别导入文档，验证搜索结果隔离。

**Acceptance Scenarios**:

1. **Given** 多个 profile 配置, **When** 执行 `shibei --profile interview search "风控"`, **Then** 仅搜索 interview 知识库
2. **Given** 当前在 interview profile, **When** 执行 `shibei profile switch work`, **Then** 切换到 work 知识库

### Edge Cases

- 空目录导入时返回 `{"files": 0, "chunks": 0, "status": "ok"}`
- 搜索无结果时返回 `{"total": 0, "results": []}`
- jieba 未安装时自动降级到 bigram 分词，搜索质量略降但功能正常
- ChromaDB 未安装时自动降级到纯 BM25 模式
- LLM API 不可达时静默降级到无 LLM 模式，核心功能不受影响
- SQLite 数据库被锁定时等待 30 秒后报错（而非默认 5 秒）
- 文件编码非 UTF-8 时报错并跳过，不影响其他文件导入

## Requirements

### Functional Requirements

- **FR-001**: 系统 MUST 支持导入 md/txt/docx/xlsx/csv 格式文件
- **FR-002**: 系统 MUST 支持 BM25、向量、混合三种搜索引擎
- **FR-003**: 系统 MUST 支持增量导入（通过 manifest 追踪文件 hash）
- **FR-004**: 系统 MUST 支持 chunk 级别去重（通过 content hash）
- **FR-005**: 系统 MUST 支持 parent-child 分块模式（小 chunk 索引 + 大 parent 上下文）
- **FR-006**: 系统 MUST 支持中文分词（jieba 优先，bigram 降级）
- **FR-007**: 系统 MUST 支持对话记忆的写入、搜索、删除
- **FR-008**: 系统 MUST 支持记忆版本链（supersedes/superseded_by）
- **FR-009**: 系统 MUST 支持通过 MCP 协议暴露 5 个工具
- **FR-010**: 系统 MUST 支持多知识库 Profile 切换
- **FR-011**: 系统 MUST 支持插件化文件解析器注册（`register_parser()`）
- **FR-012**: 系统 MUST 支持插件化搜索引擎注册（`register_engine()`）
- **FR-013**: 系统 MUST 支持从环境变量 `SHIBEI_LLM_API_KEY` 读取 API Key
- **FR-014**: 系统 MUST 在 LLM 不可用时静默降级，核心功能不受影响
- **FR-015**: 系统 MUST 使用 `logging` 模块记录核心库日志

### Key Entities

- **Chunk**: 文档分块，包含 source、content、tags、parent_text、parent_id
- **Memory**: 对话记忆，包含 id、fact、category、tags、created_at、supersedes
- **Manifest**: 导入清单，记录文件路径和 hash，用于增量导入
- **Profile**: 知识库配置，包含独立的数据库路径和数据源
- **DreamReport**: Dreaming 结果，包含 keywords、topics、insights、timestamp

## Success Criteria

### Measurable Outcomes

- **SC-001**: 文档导入后搜索能在 100ms 内返回结果（BM25 模式）
- **SC-002**: 1000 个文档的全量导入在 60 秒内完成
- **SC-003**: 增量导入仅处理变更文件，未变更文件 0 延迟
- **SC-004**: 中文搜索准确率 > 80%（jieba 模式下）
- **SC-005**: 93 个测试全部通过，测试执行时间 < 1 秒
- **SC-006**: 核心功能（导入、搜索、记忆）不依赖任何外部 API
- **SC-007**: MCP Server 响应时间 < 200ms（不含 LLM 调用）

## Assumptions

- 用户有稳定的本地文件系统访问权限
- jieba 和 sentence-transformers 为可选依赖，未安装时自动降级
- LLM API 为可选增强，未配置时核心功能正常
- SQLite 和 ChromaDB 数据存储在 `~/.shibei/` 目录下
- 单用户使用场景，不需要多用户并发写入（但需处理 SQLite 锁超时）
- 中文为主要使用语言，英文搜索作为辅助支持
