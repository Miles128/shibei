# 拾贝（Shibei）vs TencentDB Agent Memory — 对标分析

> 分析时间：2026-08-16
> 对标对象：腾讯云开源 **TencentDB Agent Memory**（GitHub: `TencentCloud/TencentDB-Agent-Memory`）
> 事实来源：[TencentDB-Agent-Memory-技术事实清单.md](../TencentDB-Agent-Memory-技术事实清单.md)（423 行，含来源 URL）+
> Shibei 源码与三份研究报告（`docs/ANALYSIS_SUMMARY.md` 等）
> 说明：本报告只做事实对比，不评判优劣；"哪边更强"以证据为准。

---

## 0. 一句话结论

两个项目都落到了 **SQLite + FTS5 + BM25 + jieba + RRF + MCP + 本地优先** 的同一套技术底座上（架构收敛，互相验证了选型），但**分工完全不同**：

- **TencentDB Agent Memory** = 「记忆生命周期 + 上下文治理」系统：对话进 → 四层语义金字塔（原子/场景/画像）出，外加 Mermaid 任务画布做短期上下文卸载，解决的是 **Agent 干活时的"失忆"与 Token 爆炸**。
- **拾贝 Shibei** = 「个人知识库 + 答前召回」系统：文档进 → BM25 索引出，解决的是 **Agent 答前的"无据可依"**（把个人笔记/简历/面试资料变成可检索知识）。

一句话：**腾讯管"对话记住什么"，拾贝管"回答依据什么"**。两者不是替代关系，理论上可以叠加使用。

---

## 1. 总体对比

| 维度 | 拾贝 Shibei | TencentDB Agent Memory |
|---|---|---|
| 定位 | 中文知识库 + Agent 长期记忆（「外部大脑」） | Agent 全本地长时记忆 + 短期上下文治理（「第二大脑」） |
| 一句话 | 文档进、知识出，答前强制召回 | 对话进、画像出，上下文卸载省 Token |
| 主语言 | Python ≥3.9 | TypeScript（Node ≥22.16）+ Hermes 适配器 Python |
| 许可证 | MIT | MIT |
| 版本 | 0.1.0 Alpha（个人项目） | v2.0.1-beta.2（2026-08-15） |
| 活跃度 | 单作者、本地开发 | **21,916 stars / 2,007 forks**（2026-08-15），90 天破 2 万 star |
| 开源时间 | — | 2026-05-14（云产品 2026-04-03 发布） |
| 评测数据 | 无基准 | WideSearch Token **−61.38%**、成功率 **+51.52%**、PersonaMem 48%→76% |

## 2. 记忆架构：分层 vs 扁平（最大差异）

| | 拾贝 Shibei | TencentDB Agent Memory |
|---|---|---|
| 长期记忆层级 | **扁平两表**：文档 `chunks` + 记忆 `memories`（fact/category/source/tags/created_at/expires_at/supersedes/superseded_by） | **四层语义金字塔**：L0 对话 → L1 原子（带 `source_message_ids` 溯源）→ L2 场景（Markdown 可读）→ L3 画像 `persona.md` |
| 记忆写入 | **手动**：宿主按契约调 `shibei_remember`（suggest_remember 只建议不落库） | **自动流水线**：L0 全量捕获 → LLM 抽取原子事实 → 聚类场景 → 综合画像 |
| 版本/冲突 | `supersedes` 版本链：新事实与旧记忆 Jaccard ≥0.7 自动替代，历史可追溯 | 向量去重 + 冲突检测（L1） |
| 短期记忆 | 无（context pack 只做 240/400 字截断） | **4 层卸载**：`refs/*.md` 原文 → `offload.jsonl` 摘要 → `*.mmd` 画布节点 → 上下文索引，**100% 可回溯** |
| 可追溯性 | 记忆只有 fact 原文，无溯源链 | 每条原子记忆可回溯到原始对话消息 |
| 过期清理 | `expires_at` + `cleanup_expired`（本次已修复时区比较） | L0 保留期可配 |

**差距本质**：Tencent 把记忆做成了「会进化的资产」（碎片→事实→场景→画像），Shibei 是「事实清单」（一条条 fact + 版本链）。Shibei 没有画像层、没有 LLM 抽取、没有场景聚类。

## 3. 存储技术：同底座的两种演进

| | 拾贝 Shibei | TencentDB Agent Memory |
|---|---|---|
| 主库 | **SQLite FTS5**（`fts5.db`，chunks + memories 共库） | **SQLite + FTS5 + sqlite-vec** |
| 检索 | 纯 BM25（正文+摘要双列 RRF 融合） | 三策略：keyword / embedding / **hybrid（FTS+向量+RRF，默认）** |
| 元数据 | `manifest.json`（文件 hash）、`chunk_hashes.json`（去重）、`dream_result.json` | 画像/场景/画布存**人类可读 Markdown**（data 目录） |
| 可选云 | 无（本地文件，纯离线） | 腾讯云向量数据库 **TCVDB**（官方迁移脚本，可选增强） |
| 向量 | 无（P2 路线图「轻量向量混合」规格锁定未实现） | 已内置（sqlite-vec，本地） |

**共同点**：FTS5+BM25+jieba 中文分词+RRF——两边检索内核几乎一样（Tencent 关键词策略与 Shibei 同构）。**差异**：Tencent 补了向量路（hybrid 召回更强），Shibei 纯词法（靠同义词扩展+查询改写补偿）。

## 4. 能力对照表

| 能力 | 拾贝 Shibei | TencentDB Agent Memory |
|---|---|---|
| 文档知识库导入（md/txt/docx/xlsx/csv） | ✅ 核心能力，多格式+增量+去重 | ⚠️ v2.0 新增 Wiki 资产（MemoryKnowledge），深度不如（无增量/去重/多格式细节） |
| 中文分块（按标题/句子边界/parent-child） | ✅ 完整 | 无（记忆按对话切，非文档） |
| 自动标签（路径规则+正则） | ✅ | ❌ |
| 轻量关联（标签/Markdown 链接/关键词共现） | ✅ | ❌（v2 CodeGraph 是代码图，非文档关联） |
| Dreaming 洞察 + 整理（清理/压缩/合并候选） | ✅ 零 LLM | ❌（靠流水线自动做） |
| 答前上下文召回（`shibei_context`） | ✅ 核心入口（文档+记忆双路） | 无对应（靠宿主自己拼） |
| Mermaid 任务画布 + 上下文卸载 | ❌ | ✅ 独家能力（Flowchart 任务拓扑，node_id 可寻址） |
| 原子记忆抽取（LLM） | ❌（记忆由宿主手动写） | ✅ 自动 |
| 用户画像层 | ❌ | ✅ L3 persona.md |
| 团队记忆（多 Agent 共享） | ❌ | ✅ v2.0 Team Memory |
| 查询改写（规则/LLM）+ 同义词扩展 | ✅ | ❌（检索靠向量兜语义） |
| Token 优化 | 仅结果截断 | ✅ −61%（卸载+画布+prompt cache 命中） |

## 5. 宿主集成：Tencent 覆盖面明显更广

| 集成方式 | 拾贝 Shibei | TencentDB Agent Memory |
|---|---|---|
| MCP | ✅ 自研 stdio JSON-RPC，9 工具 | ✅ 官方 MCP（MemoryKnowledge stdio）+ 第三方 tdai-mcp-server（TRAE） |
| 官方插件 | ❌ | ✅ OpenClaw（npm `@tencentdb-agent-memory/memory-tencentdb`）、Hermes provider + Node Gateway sidecar（:8420） |
| 零代码接入 | ❌ | ✅ **v2.0 MemoryProxy**：Claude Code/Codex/CodeBuddy/WorkBuddy/Hermes/OpenClaw/DeepSeek Harness，`ANTHROPIC_BASE_URL` 指向代理即接入 |
| SDK | ✅ Python 内置 | ✅ 官方 TypeScript/Python SDK（Python 尚未上 PyPI） |
| 多宿主契约 | ✅ 提示词模板（agent.md / .cursor 规则 / Trae skill） | ✅ 网关层自动注入，无需宿主侧改提示词 |

## 6. 隐私与部署

| | 拾贝 Shibei | TencentDB Agent Memory |
|---|---|---|
| 本地优先 | ✅ 纯本地文件，数据不出机 | ✅ 默认本地，零外部 API 依赖 |
| 认证/鉴权 | ❌（stdio 进程内） | ✅ Gateway 可选 apiKey/CORS 鉴权 |
| 共享模型 | 默认私有（本地） | 默认私有、显式授权共享（Team Memory） |
| 可审计 | 部分（版本链） | ✅ 白盒溯源，100% 可回溯 |

## 7. 工程与质量

| | 拾贝 Shibei | TencentDB Agent Memory |
|---|---|---|
| 代码规模 | ~8.5k 行（src+tests） | 大（四服务 + 插件 + SDK） |
| 测试 | 180 用例（166→180，本次修复新增 14 个） | 未核实 |
| 已知问题 | 本次已修 15 项（score 反转/采样有偏/截断丢数据/FTS 连续删除损坏等），剩余中低优先级项见 ANALYSIS_SUMMARY | 14 项不确定项（基准定义、端口不一致、Python SDK 未上 PyPI 等），见事实清单 |

## 8. 可以互相借鉴什么

**拾贝可借鉴 TencentDB：**
1. **记忆分层**：加一层"画像/长期偏好"（L3），把零散 fact 聚合成 persona，答前召回时画像优先注入
2. **上下文卸载**：工具结果写外部文件、上下文只留摘要索引（对标 refs/jsonl/mmd 四层），MCP 场景可大幅省 Token
3. **向量 hybrid**：P2 已规划，可参考 sqlite-vec 本地向量 + RRF 融合（复用现有融合点）
4. **零代码接入**：做一个 MemoryProxy 网关层，让 Claude Code/CodeBuddy 无需改提示词就能挂载
5. **溯源**：记忆加 `source_message_ids` 类字段，Agent 可回溯到原始对话

**TencentDB 可借鉴拾贝：**
1. **多格式文档导入 + 增量 + 去重**（v2 Wiki 资产还很薄）
2. **Dreaming 整理**（过期清理/压缩候选/相似合并的零 LLM housekeeping）
3. **答前 context 契约**（宿主抽词 → 强制召回 → 再回答）

## 9. 结论

| 场景 | 选谁 |
|---|---|
| 个人笔记/简历/面试文档知识库，中文为主，答前召回 | **拾贝**（文档能力、中文优化、轻量） |
| Agent 长任务上下文治理、省 Token、记忆自动沉淀成画像 | **TencentDB**（画布卸载、四层流水线、生态） |
| 两个都要 | 可以叠加：TencentDB 管对话记忆与上下文，拾贝管文档知识召回（MCP 工具可共存） |

**最终判断**：技术底座同源（SQLite FTS5 BM25），差异在**记忆深度**（Tencent 四层金字塔 vs Shibei 扁平版本链）和**上下文治理**（Tencent 画布卸载 vs Shibei 截断）上 Tencent 明显领先；而**文档知识库能力**（多格式/增量/去重/标签/关联/整理）Shibei 反而更扎实。以 Shibei 的定位（个人中文知识库），最值得追赶的是**画像层**和**上下文卸载**这两点。
