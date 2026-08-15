# TencentDB Agent Memory 技术事实清单

> 调研对象：腾讯云开源项目 **TencentDB Agent Memory**（GitHub: `TencentCloud/TencentDB-Agent-Memory`，2026 年 5 月开源）
> 调研方式：多轮 web_search + 直接抓取 GitHub README/LICENSE/源码目录、DeepWiki 架构页、腾讯云官方文档与开发者社区文章
> 调研时间：2026-08-15（文中量化数据为抓取时点数据）
> 结论原则：每个事实尽量标注来源 URL；查不到的单独列入「不确定/缺失的信息」，不编造。

---

## 0. 项目速览

| 项目 | 事实 |
| --- | --- |
| 官方标语 | "Agents remember. Humans innovate." / 「让 Agent 沉淀经验，让人专注创造」 |
| 开源时间 | 2026 年 5 月（仓库创建 2026-04-07；v0.3.4 发布 2026-05-13；官方宣发文章 2026-05-14） |
| 定位 | 面向 AI Agent 的**全本地长时记忆** + 短期上下文治理系统，核心卖点"4 层渐进式流水线 + 零外部 API 依赖" |
| 最新版本 | v2.0.1-beta.2（2026-08-15）；v2.0.0（2026-08-03）发布 Team Memory |
| 许可证 | MIT（LICENSE 文件明确；GitHub API 的 license 字段误判为 Other/NOASSERTION） |
| 主要语言 | TypeScript（Node ≥ 22.16）；Hermes 端适配器为 Python |
| Star/活跃度 | 21,916 stars、2,007 forks（2026-08-15 抓取）；官方 PR 称"开源 90 天破 20,000 star、多次 GitHub Trending 第一" |
| 一句话技术 | 符号化短期记忆（上下文卸载 + Mermaid 任务画布）+ 分层式长期记忆（L0→L3 语义金字塔），混合检索（关键词 + 向量 + RRF） |

主要来源：
- GitHub 仓库主页：https://github.com/TencentCloud/TencentDB-Agent-Memory
- README（main）：https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/main/README.md
- README_CN（main）：https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/main/README_CN.md
- v2 分支 README：https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/feat/server_team/README.md

---

## 1. 四层渐进式流水线（4-tier progressive pipeline）

### 1.1 仓库描述与"四层"的官方出处

- 仓库原始描述（开源时）："TencentDB Agent Memory delivers fully local long-term memory for AI Agents via a **4-tier progressive pipeline**, with zero external API dependencies"（用户提供线索；社区复述见 [awesome-harness-engineering](https://github.com/ai-boost/awesome-harness-engineering/blob/main/README.md)，其中明确写为 "4-tier progressive pipeline (Conversation → Atom → Scenario → Persona)"）。
- 腾讯云 2026-04-03 产品发布文章直接定义"**四层渐进式记忆架构**"：L0 原始对话 → L1 原子记忆 → L2 场景分块 → L3 用户画像（[cloud.tencent.cn/developer/article/2650211](https://cloud.tencent.cn/developer/article/2650211)）。
- 结论：**"4 层"指长期记忆的 L0→L3 语义金字塔**（会话→原子事实→场景→画像），信息沿"碎片化对话 → 结构化事实 → 场景化认知 → 个性化画像"逐级提炼。

### 1.2 L0–L3 各层职责（长期记忆）

| 层 | 名称 | 职责 | 存储实体 |
| --- | --- | --- | --- |
| L0 | Conversation（原始对话） | 记录原始 user/assistant 消息与工具证据，经 sanitize 后持久化，作为证据底座；必要时可回到 L0 核对 | JSONL / SQLite / 可选 VectorDB（DeepWiki 称经 StorageAdapter 写本地文件系统或对象存储 COS） |
| L1 | Atom（原子记忆） | LLM 抽取的结构化事实（偏好、指令、事件），带 `source_message_ids` 溯源链接；向量去重/冲突检测 | SQLite `l1_records` + `l1_vec` 向量表 |
| L2 | Scenario（场景块） | 同类 L1 事实聚类成"场景块"（如某项目开发习惯、某类排障流程），人类/Agent 可读的 Markdown | Markdown 文件（data 目录下） |
| L3 | Persona（用户画像） | 综合用户长期偏好、工作方式的稳定画像 `persona.md` | Markdown 文件 |

来源：
- 云文档（四层中文命名略不同，见 1.3）：https://cloud.tencent.cn/document/product/1813/132100
- DeepWiki 架构总览（L0–L3 实体与数据流）：https://deepwiki.com/TencentCloud/TencentDB-Agent-Memory/1.2-architecture-overview
- DeepWiki Memory 流水线：https://deepwiki.com/TencentCloud/TencentDB-Agent-Memory/2-memorycore-memory-pipeline
- 中文拆解（每层职责 + L1 结构化示例 JSON）：https://www.cnblogs.com/ai-old-six/p/20067315
- Hermes 插件 README（四层定义：L0 capture → L1 episodic extraction → L2 scene blocks → L3 persona synthesis）：https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/main/hermes-plugin/memory/memory_tencentdb/README.md

### 1.3 云产品版的四层命名（略有差异）

腾讯云官方文档（[Memory 介绍](https://cloud.tencent.cn/document/product/1813/132100)）将长期记忆称为"**四层分层金字塔**"，命名与开源版措辞不同：

1. **原始对话**——最底层事实依据（对话记录、工具调用日志、文档片段）
2. **原子记忆**——可独立检索与引用的原子事实，每条带来源链接
3. **场景记忆**——同类原子事实归纳出的场景模式与高频路径（任务级经验）
4. **核心记忆**——跨场景沉淀的核心洞察、稳定结论与团队规范（Agent 平时直接调用的高密度知识）

> 注意：开源版 L3 叫 Persona（用户画像），云产品版 L3 叫"核心记忆"。二者同构（都是顶层高密度画像/结论层），措辞不同。

### 1.4 短期记忆（上下文卸载）也有 4 层结构

开源文章（[cloud.tencent.cn/developer/article/2668960](https://cloud.tencent.cn/developer/article/2668960)）明确：工具结果按**四层递进结构**存储在外部：

| 层级 | 内容 | 位置 |
| --- | --- | --- |
| Level 0 | 完整工具返回原文 | `refs/*.md` |
| Level 1 | 工具调用级摘要 | `offload.jsonl` |
| Level 2 | 任务画布节点 | `*.mmd` |
| Level 3 | 任务级索引（目标 + 状态） | 上下文 |

"底层保留证据，高层保留结构……任何一层压缩都不是不可逆的黑盒——系统内每一条信息都可以沿索引链路 **100% 找回**。"

### 1.5 v2.0 演进：四服务架构 + 四类记忆资产

DeepWiki 架构总览（索引日期 2026-08-05）显示项目已演进为**四服务架构**：

| 服务 | 角色 | 职责 |
| --- | --- | --- |
| MemoryCore | 记忆引擎/Gateway | L0–L3 流水线、上下文卸载、召回 |
| MemoryProxy | LLM 请求代理 | 拦截 Agent 请求、注入记忆/Skill、捕获新对话 |
| MemoryPanel | 管理 UI | Web 控制台（团队、Agent、资产管理） |
| MemoryKnowledge | 知识引擎 | Wiki 与 CodeGraph 索引与检索 |

同时管理**四类记忆资产（Memory Asset）**：
- **Chat Memory**：L0–L3 金字塔
- **Skill**：从成功任务抽取的可复用 SOP（含执行步骤与验证规则）
- **Wiki**：LLM 优化的结构化文档页与链接图谱
- **CodeGraph**：源码符号/调用图索引，支持影响面分析

来源：https://deepwiki.com/TencentCloud/TencentDB-Agent-Memory/1.2-architecture-overview
仓库目录（四服务 + sdk + deploy 确认）：https://github.com/TencentCloud/TencentDB-Agent-Memory/tree/feat/server_team

### 1.6 分层设计哲学：渐进式披露（Progressive Disclosure）

- README 核心论点："拒绝平铺，走向分层与符号化"；传统方案（全量历史回灌/强摘要/扁平向量库/只维护 Persona）各有短板（[cnblogs](https://www.cnblogs.com/ai-old-six/p/20067315) 有对比表）。
- 核心原则："压缩可以发生，但压缩结果必须可逆；抽象可以存在，但抽象必须带着索引。"
- 召回采用**渐进式披露**：默认只给模型最高层、最少噪音的信息（Persona/画布），必要时沿 "L3/L2/L1 → L0" 下钻回查原文（[README](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/main/README.md)、[cnblogs](https://www.cnblogs.com/ai-old-six/p/20067315)）。
- 分层也应用于"技能生成"（Roadmap 层）：底层执行轨迹（Conversation）→ 中层通用方案（Scenario）→ 顶层可复用 Skill/SOP（Persona）（[README](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/main/README.md)）。

---

## 2. 存储技术栈与 TencentDB / TDSQL 的关系

### 2.1 本地默认存储：SQLite 组合拳

- **默认后端 = 本地 SQLite + sqlite-vec 扩展**（向量）**+ 内置 FTS5 模块**（关键词全文检索）（[README](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/main/README.md)、[DeepWiki 存储页](https://deepwiki.com/TencentCloud/TencentDB-Agent-Memory/3.2-search-strategies-and-storage-backends)）。
- 表结构：`l1_records`（结构化记忆）+ `l1_vec`（vec0 虚拟表，存 embedding）；`upsertL1`/`upsertL0` 保证记录与向量一致性。
- 异构存储 + 渐进式披露：**底层事实/日志/轨迹存数据库**（便于全文检索）；**上层画像/场景/画布存人类可读的 Markdown 文件**（高信息密度 + 白盒可检视）（[README](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/main/README.md)）。
- L0 存储：本地文件系统或对象存储（COS），经 StorageAdapter 抽象（[DeepWiki 流水线页](https://deepwiki.com/TencentCloud/TencentDB-Agent-Memory/2-memorycore-memory-pipeline)）。
- 中文拆解确认："SQLite、FTS5 与向量检索组成可降级的本地优先记忆检索路径；需要时也可以接 Tencent Cloud VectorDB"（[cnblogs](https://www.cnblogs.com/ai-old-six/p/20067315)）。

### 2.2 可选云后端：腾讯云向量数据库 TCVDB

- 进阶支持：接入**腾讯云向量数据库（TCVDB）**实现混合检索（BM25 + Vector）（[2668960](https://cloud.tencent.cn/developer/article/2668960)）。
- DeepWiki：TCVDB 后端（`TcvdbMemoryStore`）面向高并发/大规模，按层建 collection：`<db>_l1_memories`、`<db>_l0_conversations`、`<db>_profiles`；支持批量 upsert（`upsertL1Batch`/`upsertL0Batch`）；连接需 URL/用户名/API Key/库名，可选 CA 证书。
- 提供官方迁移脚本：`migrate-sqlite-to-tcvdb`（本地 SQLite → 云 TCVDB）、`export-tencent-vdb`（[DeepWiki](https://deepwiki.com/TencentCloud/TencentDB-Agent-Memory/3.2-search-strategies-and-storage-backends)）。
- 云产品版的数据底座：腾讯云向量数据库 + 文件存储（承载向量索引与原始日志/卸载大对象）（[132100 文档](https://cloud.tencent.cn/document/product/1813/132100)）。

### 2.3 "TencentDB" 品牌与 TDSQL 的关系

- "TencentDB" 是腾讯云数据库产品线品牌；该产品在云上归类为「**腾讯云数据库 AI 服务**」下的「云数据库 Agent Memory（TencentDB Agent Memory）」，由**腾讯云数据库团队**完全自研（[132100 文档](https://cloud.tencent.cn/document/product/1813/132100)、[2668960](https://cloud.tencent.cn/developer/article/2668960)）。
- **本地开源版默认完全不依赖云**：官方原文"零外部依赖：默认使用本地 SQLite 存储，所有中间产物（画布、摘要）均为人类可读的 Markdown/Mermaid 文件"（[2668960](https://cloud.tencent.cn/developer/article/2668960)）；仓库描述亦为 "zero external API dependencies"。
- TCVDB 是**可选**增强项，不是本地运行的依赖。
- **与 TDSQL 的关系：未找到任何官方说明表明本地版使用 TDSQL 或依赖 TDSQL。** 二者同属 TencentDB 品牌家族，但 Agent Memory 是独立的记忆引擎产品，不是构建在 TDSQL 之上的。详见第 11 节。

---

## 3. 检索机制：三种检索策略

### 3.1 三种策略（官方配置 + 拆解文一致）

| 检索策略 | 依赖能力 | 适用场景 |
| --- | --- | --- |
| `keyword` | FTS5 + BM25（分词支持 `zh`/jieba、`en`） | 关键词明确，或未配置 embedding |
| `embedding` | 向量相似度（余弦） | 表达不完全一致但语义接近 |
| `hybrid` | FTS + 向量 + **RRF 融合排序** | 默认推荐策略，兼顾精确匹配与语义召回 |

来源：
- README 配置表 `recall.strategy`：https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/main/README.md
- 中文拆解（三策略表 + 降级设计）：https://www.cnblogs.com/ai-old-six/p/20067315
- 云文档"混合检索（关键词 + 向量语义 + RRF 融合排序）"：https://cloud.tencent.cn/document/product/1813/132100

### 3.2 混合检索的实现细节（DeepWiki）

- 存储抽象：一切读写走 `IMemoryStore` 接口；`StoreCapabilities` 声明能力，缺能力时**优雅降级**（如 embedding 服务不可用 → 退回 keyword 检索）。
  - 能力位：`vectorSearch`（稠密余弦）、`ftsSearch`、`nativeHybridSearch`（服务端融合）、`sparseVectors`（BM25 稀疏向量）。
- Hybrid RRF：分别取向量 Top-K 与关键词 Top-K，用 **Reciprocal Rank Fusion（RRF）** 融合——在语义相似或关键词精确匹配任一维度排名靠前的记录都会优先。
- TCVDB 混合检索生成 BM25 稀疏向量：本地 `createBM25Encoder` 做分词与权重计算，支持 en/zh。

来源：https://deepwiki.com/TencentCloud/TencentDB-Agent-Memory/3.2-search-strategies-and-storage-backends

### 3.3 Agent 可直接调用的检索工具

- `tdai_memory_search`：按记忆类型/数量过滤检索 L1 结构化记忆。
- `tdai_conversation_search`：检索原始 L0 对话历史，可限定 `session_key`。

来源：https://deepwiki.com/TencentCloud/TencentDB-Agent-Memory/3.2-search-strategies-and-storage-backends

### 3.4 降级设计（工程细节，来自拆解文）

- 未配置 embedding → 直接走 FTS；
- embedding 调用失败不阻塞主流程；
- SQLite L0 写入支持"先写元数据 + FTS，再后台补向量"，避免一次 `agent_end` 被远程 embedding 服务卡住。
- 原文："没有降级能力的 Memory，很难成为 Agent 平台的基础设施。"（[cnblogs](https://www.cnblogs.com/ai-old-six/p/20067315)）

### 3.5 召回注入位置（上下文治理的一部分）

- 召回到的内容分**动态上下文**（每轮变化的 L1 相关记忆，靠近用户 prompt）与**稳定上下文**（Persona、Scene Navigation、工具指南，放 system prompt 尾部）——后者更易命中模型厂商的 **prompt cache**，降低重复开销（[cnblogs](https://www.cnblogs.com/ai-old-six/p/20067315)）。
- Auto-Recall Hook 负责在每轮注入 L1 记忆、L3 Persona、L2 场景导航图（[DeepWiki 3.1](https://deepwiki.com/TencentCloud/TencentDB-Agent-Memory/3.1-auto-recall-hook)）。
- 上下文预算控制：`recall.maxCharsPerMemory` / `recall.maxTotalRecallChars`（默认 0 关闭；设置后按分数裁剪超长条目）；`recall.timeoutMs`（默认 5000，超时跳过注入不阻塞对话）（[README](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/main/README.md)、[CHANGELOG 0.3.6](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/main/CHANGELOG.md)）。

---

## 4. Mermaid 任务画布：符号化记忆与上下文卸载

### 4.1 是什么

- 核心论文式思路：用 **Mermaid Flowchart**（纯文本图语言）把长任务中几十次工具调用组织成一张**任务拓扑画布**：节点 = 里程碑/动作，边 = 转移/依赖，节点带 `node_id`、`summary`、`result_ref`（[2673955](https://cloud.tencent.cn/developer/article/2673955)、[DeepWiki 5.2](https://deepwiki.com/TencentCloud/TencentDB-Agent-Memory/5.2-mermaid-task-canvas-symbolic-memory)）。
- 选型理由：Mermaid 满足符号化三原则——①通用知识（所有主流 LLM 预训练见过）；②生成简单（节点+箭头+标签，认知负担低）；③表达自由（任意形状/方向/分支/合并/循环）（[2673955](https://cloud.tencent.cn/developer/article/2673955)）。
- 图类型选择：**Flowchart 优于 StateDiagram，实验效果约高 15%**——因为 Agent 真实执行是"开放空间探索"（并行分支、失败回退、交叉引用），不是严格状态机（[2673955](https://cloud.tencent.cn/developer/article/2673955)）。

### 4.2 上下文卸载（Context Offloading）流程

```
完整工具结果 ──> refs/*.md         （外部文件，原文保底）
工具调用摘要 ──> offload.jsonl     （步骤级摘要）
任务状态结构 ──> *.mmd             （Mermaid 任务地图）
上下文注入   ──> 只放 Mermaid 任务地图（数百 token）
需要细节     ──> 按 node_id / ref 回查原文
```

- README 配图：Logs（数十万 token）→ ①卸载全文到 `refs/*.md` → ②抽关系生成 Mermaid Canvas（带 node_id）→ ③轻量注入 Agent 上下文（数百 token）→ ④按 node_id 回查（[README](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/main/README.md)）。
- Hook 流程（after_tool_call）：收集 ToolPair → 完整结果写 refs → L1 总结工具调用 → **L1.5 判断任务边界** → L2 更新 Mermaid 图 → L3 按 token 压力压缩历史上下文（[cnblogs](https://www.cnblogs.com/ai-old-six/p/20067315)）。
- **L1.5 Boundary Detector**：判断当前工具调用应复用 active MMD 还是开新任务图，防止任务地图混乱（[cnblogs](https://www.cnblogs.com/ai-old-six/p/20067315)）。

### 4.3 触发与参数

- 卸载开关：`offload.enabled`（默认 false，OpenClaw 插件 ≥0.3.4 可用）。
- 压缩触发：`offload.mildOffloadRatio`（默认 0.5，上下文窗口占比）、`offload.aggressiveCompressRatio`（默认 0.85）、`offload.mmdMaxTokenRatio`（默认 0.2，Mermaid 注入 token 预算占比）。
- 补丁脚本 `scripts/openclaw-after-tool-call-messages.patch.sh`：hook `after-tool-call` 消息，使其可被卸载与恢复（README）。

来源：[README](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/main/README.md)、[2673955](https://cloud.tencent.cn/developer/article/2673955)

### 4.4 与长期记忆的关系

- 短期画布是**长期记忆的"入口"**：任务完成后，短期记忆中有价值的信息自动进入长期记忆的分层提炼流程（[132100 文档](https://cloud.tencent.cn/document/product/1813/132100)）。
- 两者共享"分层 + 可追溯"哲学：短期（refs→jsonl→mmd→上下文）与长期（L0→L1→L2→L3）都保证"顶层符号 → 中层索引 → 底层原文"的确定性回查路径（[README](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/main/README.md)）。

---

## 5. 上下文治理：Token 省 61%、成功率 +52% 的机制

### 5.1 机制组成

官方将收益归因于（[2668960](https://cloud.tencent.cn/developer/article/2668960)、[2673955](https://cloud.tencent.cn/developer/article/2673955)）：

1. **上下文卸载（Context Offloading）**：完整工具结果（搜索原文、代码、报错日志——"长任务真正的 Token 黑洞"）移出上下文到 `refs/*.md`，只留摘要 + 索引路径。
2. **Mermaid 任务画布**：把离散摘要组织成结构图，让 Agent 知道"任务走到哪、下一步是什么、证据存在哪"，保持轻量上下文且不丢证据。
3. **分层记忆 + 渐进式披露**：默认注入高层结构（Persona/画布），按需下钻。
4. **召回注入位置优化**：稳定上下文放 system prompt 尾部，命中 prompt cache（[cnblogs](https://www.cnblogs.com/ai-old-six/p/20067315)）。
5. **上下文预算控制**（0.3.6 起）：裁剪超长召回、总字符预算（[CHANGELOG](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/main/CHANGELOG.md)）。

### 5.2 官方实验数据（README 基准表）

> 评测口径：连续长会话（非单题清空上下文）；如 SWE-bench 每个 Session 连续执行 50 个任务，模拟长程 Agent 的上下文累积压力。

| 记忆能力 | Benchmark | OpenClaw 基线成功率 | 加插件后 | 相对提升 | 基线 Token | 加插件 Token | Token 相对变化 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 短期 | WideSearch | 33% | 50% | **+51.52%** | 221.31M | 85.64M | **−61.38%** |
| 短期 | SWE-bench | 58.4% | 64.2% | +9.93% | 3474.1M | 2375.4M | −33.09% |
| 短期 | AA-LCR | 44.0% | 47.5% | +7.95% | 112.0M | 77.3M | −30.98% |
| 长期 | PersonaMem | 48% | 76% | **+59%**（相对） | — | — | — |

来源：https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/main/README.md

### 5.3 消融实验（画布的独立贡献）

- 仅卸载：Token 节省约 **15%**；
- 叠加 Mermaid 画布：提升至 **31%–33%**；
- 完整方案（卸载 + 画布 + 多任务连续 Session）：最高 **61.38%** Token 下降，同时成功率上升——"上下文中的噪声减少后，模型注意力更集中在当前任务目标上"（[2668960](https://cloud.tencent.cn/developer/article/2668960)、[2673955](https://cloud.tencent.cn/developer/article/2673955)）。

> 注意：61.38% 是 WideSearch 场景（多任务连续 Session）的最优值；SWE-bench/AA-LCR 场景分别为 −33.09% / −30.98%。媒体标题"Token 降低 61%"取的是最优值。

---

## 6. 集成方式

### 6.1 v1.x 时代（OpenClaw 插件 + Hermes 插件）

**OpenClaw（推荐，零配置）：**

```bash
openclaw plugins install @tencentdb-agent-memory/memory-tencentdb
openclaw gateway restart
```

- 启用只需 `"memory-tencentdb": {"enabled": true}`；默认本地 SQLite + sqlite-vec 后端；自动完成会话捕获、记忆抽取、场景聚合、画像生成、下轮召回（[README](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/main/README.md)）。
- npm 包：`@tencentdb-agent-memory/memory-tencentdb`（latest 1.0.1；https://www.npmjs.com/package/@tencentdb-agent-memory/memory-tencentdb）。
- 要求 OpenClaw ≥ 2026.3.13、Node ≥ 22.16。

**Hermes（NousResearch 的 hermes-agent）：**

- 提供 Python `MemoryProvider`（`memory_tencentdb`，目录名必须下划线），本质是**薄 HTTP 客户端 + 进程守护**，连接同包内置的 Node.js Gateway sidecar（默认 `127.0.0.1:8420`）。
- 生命周期映射：`prefetch(query)` → `POST /recall`（同步注入）；`sync_turn` → `POST /capture`（后台线程，最多 4 个并发 + 背压）；`shutdown` → `POST /session/end`。
- 可靠性：熔断器（连续 5 次失败暂停 60s）、受监管启动（30s 健康轮询）、零配置自动发现 Gateway。
- 部署方式：Docker 一体化镜像（`docker/opensource/Dockerfile.hermes`，默认 Tencent Cloud LKE 的 DeepSeek-V3.2，端口 8420，数据卷 `/opt/data`）；现有 Hermes 附加安装（npm 安装插件 + symlink + 环境变量）；Windows 原生 `.bat` 脚本。
- 来源：https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/main/hermes-plugin/memory/memory_tencentdb/README.md

### 6.2 v2.0 时代（MemoryProxy：零代码接入）

- 三件套部署（memory-core + memory-hub + proxy，`deploy/global-images/start-all.sh`）后，**把 Agent 的 base URL 指向 Proxy 即完成接入**："One Proxy, unchanged protocol, zero-code integration. No plugin, hook, or MCP server is required."
- Claude Code 接入示例：
  ```bash
  export ANTHROPIC_BASE_URL=http://127.0.0.1:8096/claude-code/default
  export ANTHROPIC_AUTH_TOKEN='sk-mem-<32 chars>'
  claude --model <上游模型>
  ```
- 官方支持的宿主矩阵：**Claude Code、Codex、CodeBuddy、WorkBuddy、Hermes、OpenClaw、DeepSeek Harness**（INSTALL.md 各有独立章节）。
- 端口参考：Memory Core 8420、Panel UI 8125、Knowledge 8424、Proxy 8096（Anthropic/OpenAI 双协议）。
- 来源：[feat/server_team README](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/feat/server_team/README.md)、[INSTALL.md](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/feat/server_team/INSTALL.md)

### 6.3 MCP

- **官方 MCP**：MemoryKnowledge 内置 `src/mcp/`（MCP stdio，转发到本机 HTTP API；入口 `bin/mcp.mjs`）——面向 Wiki/Code-Graph 服务（[MemoryKnowledge/README](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/feat/server_team/MemoryKnowledge/README.md)）。
- **第三方 MCP**：`iceyychain/tdai-mcp-server`——面向 TRAE 等 Agent 的 MCP 记忆工具（记忆对话、总结偏好），仓库自称基于该项目能力（https://github.com/iceyychain/tdai-mcp-server）。
- **第三方 Claude Code 插件**：ClaudeAtlas 上 `baodq97/tencentdb-agent-memory`（https://claudeatlas.com/plugins/baodq97/tencentdb-agent-memory/）。
- 注：v2.0 官方推荐路径是 Proxy 而非 MCP；官方未把 MCP 作为主推集成方式。

### 6.4 SDK（官方）

- **TypeScript SDK**：`sdk/memory-core/typescript`（MemoryClient，v2/v3 API）。
- **Python SDK**：`sdk/memory-core/python`——包名 `tencentdb-agent-memory-sdk-python`、导入路径 `tencentdb_agent_memory`，同步 `MemoryClient` + 异步 `AsyncMemoryClient`，支持 v2/v3 API；提供 `add_conversation`（L0）、`search_atomic`/`update_atomic`（L1）、`list/read/write_scenario`（L2）、`read/write_core`（L3）、offload 上报等（[sdk README_CN](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/feat/server_team/sdk/memory-core/python/README_CN.md)）。
- ⚠️ 该 Python 包 README 标注"发布后"可从 PyPI 安装；调研时 PyPI 上**未查到**该包（详见第 11 节）。

### 6.5 云产品接入

- 云上版作为**内置插件**集成进腾讯云 Lighthouse、ClawPro 等：控制台 → OpenClaw 实例 →「应用管理-记忆管理」→ 打开 Agent Memory 开关 → 重启 Gateway 即生效；**免费一键开启**；与 OpenClaw 原生 Memory Core 共存互补，数据存在本机（[2650211](https://cloud.tencent.cn/developer/article/2650211)）。
- 企业版"Agent Memory Pro"（基于腾讯云向量数据库，支持备份、回档、权限控制）当时预告即将推出（[2650211](https://cloud.tencent.cn/developer/article/2650211)）。

---

## 7. 开源情况

| 维度 | 事实 | 来源 |
| --- | --- | --- |
| 许可证 | **MIT**（LICENSE：Copyright (C) 2026 Tencent）；注意 GitHub API 的 license 字段显示 "Other/NOASSERTION"（应为检测问题） | [LICENSE](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/main/LICENSE) |
| 语言 | TypeScript 为主（Node ≥ 22.16）；Hermes 适配器 Python | [README](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/main/README.md) |
| Star | **21,916**（2026-08-15 API 抓取）；官方 PR：开源 90 天超 **20,000 star**（截至 2026-08-12），多次 GitHub Trending 第一 | [PR（Manila Times 转载）](https://www.manilatimes.net/2026/08/13/tmt-newswire/pr-newswire/tencentdb-agent-memory-tops-20000-github-stars-in-90-days-launches-team-memory-for-multi-agent-collaboration/2404570) |
| Forks / Issues | 2,007 forks、591 open issues（2026-08-15） | GitHub API |
| 仓库创建 | 2026-04-07（私有开发期）；2026-05-13 v0.3.4 发布；2026-05-14 官方宣布开源 | GitHub API / [2668960](https://cloud.tencent.cn/developer/article/2668960) |
| 版本线 | v0.1.4 → v0.3.6（2026-05-28）→ v1.0.0（2026-06-11）→ v1.0.1（2026-07-14）→ v2.0.0（2026-08-03，Team Memory）→ v2.0.1-beta.2（2026-08-15） | [Releases](https://github.com/TencentCloud/TencentDB-Agent-Memory/releases) |
| 默认分支 | `feat/server_team`（v2 团队记忆开发线） | GitHub API |
| 社区 | Discord、微信社群（README 内置二维码）、Trendshift 徽章、npm 包 | [README](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/main/README.md) |
| 镜像 | Gitee 镜像（非官方同步）：`devai/TencentDB-Agent-Memory` | https://gitee.com/devai/TencentDB-Agent-Memory |
| 生态引用 | AI 原生全景图（jimmysong landscape）、AI-Compass 周报、awesome-harness-engineering 收录 | [landscape](https://landscape.jimmysong.io/zh/projects/tencentdb-agent-memory/) |

- 主要事件：2026-04-03 云产品"龙虾记忆服务"发布（免费一键开启）→ 2026-05-14 开源（短期记忆压缩为开源重点，长期记忆已先行免费上线）→ 2026-08-13 Team Memory 发布（PR Newswire，见 [tmcnet](https://www.tmcnet.com/usubmit/2026/08/13/10429826.htm)）。

---

## 8. 量化数据汇总

**基准评测（README 官方）：**

| 指标 | 数值 |
| --- | --- |
| WideSearch 成功率 | 33% → 50%（相对 +51.52%） |
| WideSearch Token | 221.31M → 85.64M（−61.38%，即"省 61%"出处） |
| SWE-bench 成功率 | 58.4% → 64.2%（+9.93%） |
| SWE-bench Token | 3474.1M → 2375.4M（−33.09%） |
| AA-LCR 成功率 | 44.0% → 47.5%（+7.95%） |
| AA-LCR Token | 112.0M → 77.3M（−30.98%） |
| PersonaMem 准确率 | 48% → 76%（相对 +59%） |

**消融与设计实验：**

| 指标 | 数值 | 来源 |
| --- | --- | --- |
| 仅卸载 Token 节省 | 约 15% | [2668960](https://cloud.tencent.cn/developer/article/2668960) |
| 卸载 + Mermaid 画布 | 31%–33% | [2668960](https://cloud.tencent.cn/developer/article/2668960) |
| Flowchart vs StateDiagram | Flowchart 效果好约 15% | [2673955](https://cloud.tencent.cn/developer/article/2673955) |
| PersonaMem 评测集规模 | 20 个模拟用户画像、6000+ 条消息、589 道测评题 | [2650211](https://cloud.tencent.cn/developer/article/2650211) |
| 云产品版总回答准确率 | 76.10%（较原生记忆提升近 59%） | [2650211](https://cloud.tencent.cn/developer/article/2650211) |

**运营数据：**

| 指标 | 数值 |
| --- | --- |
| Star（2026-08-15） | 21,916 |
| 开源 90 天 Star（2026-08-12） | >20,000 |
| Forks | 2,007 |
| 评测口径 | 连续长会话（SWE-bench 每会话 50 任务） |

---

## 9. 隐私与安全：本地优先设计

1. **本地优先（Local-First）**：默认后端为本地 SQLite，所有中间产物（Mermaid 画布、摘要、画像、场景）为人类可读 Markdown/Mermaid 文件，**零外部 API 依赖**；仓库 topics 含 `local-first`（[README](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/main/README.md)、[2668960](https://cloud.tencent.cn/developer/article/2668960)、GitHub API）。
2. **数据不出本机（云产品场景）**：云上版启用后"数据存在本机，安全可控"，与 OpenClaw 原生 Memory Core 共存（[2650211](https://cloud.tencent.cn/developer/article/2650211)）。
3. **白盒可溯源**：每条结论带指向下层的来源链接，沿"核心记忆→场景记忆→原子记忆→原始对话"四层逐级下钻，满足审计、纠错与企业合规（[132100 文档](https://cloud.tencent.cn/document/product/1813/132100)）。
4. **默认私有 + 显式授权共享（团队记忆）**：Agent 创建时自动获得独立记忆；新产生的 Chat Memory/Skill 默认仅归 Owner；共享给团队是明确操作而非默认可见；多租户层级 Team/Agent/Task，可见性 private/team/restricted（[132100 文档](https://cloud.tencent.cn/document/product/1813/132100)、[DeepWiki](https://deepwiki.com/TencentCloud/TencentDB-Agent-Memory/1.2-architecture-overview)）。
5. **Gateway 安全开关（可选）**：`server.apiKey`（Bearer 鉴权、常量时间比较，除 `/health` 外全部路由 401）与 `server.corsOrigins`（CORS 白名单，默认空=不发 CORS 头）；两者默认关闭以保持向后兼容；未设 key 监听非回环地址会输出更响亮的 WARN（[README](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/main/README.md)）。
6. **进程隔离与默认回环**：Hermes Gateway 默认监听 127.0.0.1:8420，仅本地 sidecar（[hermes README](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/main/hermes-plugin/memory/memory_tencentdb/README.md)）。
7. **数据卫生**：L0 消息 sanitize 后落库（[DeepWiki](https://deepwiki.com/TencentCloud/TencentDB-Agent-Memory/2-memorycore-memory-pipeline)）；本地保留期 `capture.l0l1RetentionDays`（0=永不清理）；`capture.excludeAgents` 可排除特定 Agent 的捕获（[README](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/main/README.md)）；工程上有 workspace 限制、备份、软删除清理机制（[cnblogs](https://www.cnblogs.com/ai-old-six/p/20067315)）。
8. **企业治理**：Agent Memory Pro 提供备份、回档、权限控制（[2650211](https://cloud.tencent.cn/developer/article/2650211)）。
9. **远程 embedding 可选**：embedding 服务默认不强制（可走纯 FTS）；配置远程 embedding 时支持 OpenAI 兼容 API，且发送方可通过 `sendDimensions` 兼容 BGE-M3 等开源模型（[README](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/main/README.md)）——即"完全离线"取决于是否自带 embedding/LLM，基础设施本身不强制连云。

> 提醒：记忆抽取（L1/L2/L3）与画布生成需要 LLM（默认复用宿主 OpenClaw 的模型或配置的 LLM；Hermes Docker 镜像默认指向腾讯云 LKE 的 DeepSeek-V3.2，可改任意 OpenAI 兼容端点）。"零外部 API 依赖"指的是**记忆基础设施不依赖外部记忆/向量 API**，不是完全无 LLM 调用。

---

## 10. 版本演进时间线（简）

| 日期 | 事件 |
| --- | --- |
| 2026-04-03 | 云产品"龙虾记忆服务"TencentDB Agent Memory 发布，免费一键开启（Lighthouse/ClawPro 内置） |
| 2026-04-07 | GitHub 仓库创建 |
| 2026-05-13 | v0.3.4 发布（含上下文卸载能力，offload ≥ 0.3.4） |
| 2026-05-14 | 官方宣布开源（短期记忆压缩为重点） |
| 2026-05-18 | 社区架构拆解文（cnblogs）发布 |
| 2026-05-26 | 腾讯云开发者发文详解"Mermaid 无限画布 × 上下文卸载" |
| 2026-06-11 | v1.0.0 发布 |
| 2026-07-14 | v1.0.1 发布 |
| 2026-08-03 | v2.0.0 发布（Team Memory / Memory Hub / MemoryProxy） |
| 2026-08-13 | PR Newswire：90 天超 2 万 star + Team Memory 发布 |
| 2026-08-15 | v2.0.1-beta.2（调研时点，Star 21,916） |

---

## 11. 不确定 / 缺失的信息

1. **"4-tier progressive pipeline"的权威定义**：官方仓库当前描述已更新为 "team-level memory hub"，早期描述（用户提供的原文）未能在仓库历史中直接定位到官方章节标题；"4 层 = L0 Conversation→L1 Atom→L2 Scenario→L3 Persona" 由云产品发布文、Hermes README、第三方收录（awesome-harness-engineering）三方交叉印证，可信度高但非"官方文档单页"出处。
2. **WideSearch 与 AA-LCR 基准**：README 引用但未给出定义/公开数据集/论文链接，推测为项目内部长程评测（WideSearch ≈ 连续多任务网络搜索场景；AA-LCR 未查到解释）。公开性存疑。
3. **TDSQL 的直接关系**：未找到任何官方资料说明 Agent Memory 使用/依赖 TDSQL（或与之有技术关联）；二者仅共享"TencentDB"品牌。若需确证，建议查云产品架构白皮书或向官方提问。
4. **云产品计费**：本文只确认"免费一键开启/免费试用"；具体计费（免费额度、Pro 定价）未核实（文档有"计费概述"页但未抓取）。
5. **Python SDK 发布状态**：`tencentdb-agent-memory-sdk-python` 在 PyPI 未查到（README 写"发布后"）；TypeScript SDK 以源码形式存在于仓库，npm 包主要是 OpenClaw 插件（`@tencentdb-agent-memory/memory-tencentdb`）。
6. **端口不一致**：INSTALL.md 写 Knowledge 服务 8424，而 MemoryKnowledge/README 写默认 8421——疑似分支/版本差异，未逐版本核实。
7. **License 字段矛盾**：GitHub API license 字段返回 "Other/NOASSERTION"，但 LICENSE 文件明确为 MIT；以 LICENSE 文件为准，原因未深究（可能是自定义文件头导致检测失败）。
8. **GitHub 仓库组织**：README 内链接大量使用 `github.com/Tencent/TencentDB-Agent-Memory`（重定向至 TencentCloud 组织），历史上可能发生过组织迁移；未深究。
9. **"龙虾"品牌**：官方发布文中"龙虾"指 OpenClaw 生态形象（"给 OpenClaw 补上记忆"），未见正式品牌解释。
10. **Star 增长曲线细节**：未抓取 star-history 数据；"90 天 2 万 star" 为官方 PR 口径，调研时点 21,916 与之吻合。
11. **默认 embedding 提供方**：README 未写明默认 embedding 模型；仅说明未配置时可走 FTS、远程 embedding 可选（OpenAI 兼容）。"零配置开箱"下 hybrid 检索实际退化为关键词检索，这一点是拆解文推断，官方未明说。
12. **Hermes 的归属**：Hermes 是 NousResearch 的开源 Agent（hermes-agent.nousresearch.com），不是腾讯产品；腾讯只是适配它。
13. **开源版与云产品版的功能差异边界**：开源版 v1.x 只覆盖短期压缩 + 个人长期记忆；团队记忆（Team Memory/Memory Hub）、Wiki/CodeGraph、MemoryProxy 是 v2.0（2026-08 起）新增，且云产品版部分能力（备份回档、企业权限）未全部开源——具体哪些能力云上独有未逐项核实。
14. **评测可复现性**：README 未公开评测脚本/数据集链接（PersonaMem 规模见于 4 月发布文）；复现路径存疑。

---

### 附：主要来源清单

- 官方仓库与文档：https://github.com/TencentCloud/TencentDB-Agent-Memory（README/README_CN/LICENSE/CHANGELOG/Releases/INSTALL.md/hermes-plugin README/sdk）
- DeepWiki（架构、流水线、检索存储、Mermaid 画布、Auto-Recall、本地 LLM Runner）：https://deepwiki.com/TencentCloud/TencentDB-Agent-Memory/
- 腾讯云产品文档：Memory 介绍 https://cloud.tencent.cn/document/product/1813/132100 ；Memory Hub 简介 https://cloud.tencent.cn/document/product/1813/134572
- 腾讯云开发者社区：开源宣发 https://cloud.tencent.cn/developer/article/2668960 ；Mermaid×卸载详解 https://cloud.tencent.cn/developer/article/2673955 ；云产品发布 https://cloud.tencent.cn/developer/article/2650211
- 媒体：量子位 https://www.qbitai.com/2026/05/417753.html ；腾讯新闻 https://news.qq.com/rain/a/20260514A05LJS00 ；PR Newswire（Manila Times 转载）https://www.manilatimes.net/2026/08/13/tmt-newswire/pr-newswire/tencentdb-agent-memory-tops-20000-github-stars-in-90-days-launches-team-memory-for-multi-agent-collaboration/2404570
- 社区分析：博客园架构拆解 https://www.cnblogs.com/ai-old-six/p/20067315 ；AgentUpdate https://agentupdate.ai/zh/news/tencentdb-agent-memory-open-source-4-tier-local-pipeline
- 第三方集成：tdai-mcp-server https://github.com/iceyychain/tdai-mcp-server ；ClaudeAtlas 插件 https://claudeatlas.com/plugins/baodq97/tencentdb-agent-memory/
