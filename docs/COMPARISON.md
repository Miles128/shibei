# 开源 AI Agent 外部记忆方案对比（聚焦版）

> 调研时间：2026 年 6 月  
> 对比对象：**Hy-Memory、TencentDB Agent Memory、agentmemory、Shibei**  
> 场景定位：个人/小团队 Agent 长期记忆，中文环境，本地优先

---

## 一、一句话定位

| 方案 | 一句话 |
|------|--------|
| **Hy-Memory** | 腾讯混元推出的闭源 Agent 记忆压缩插件，用"版本演化链"减少记忆冗余 |
| **TencentDB Agent Memory** | 腾讯开源的四层渐进式记忆架构，SQLite 本地运行，中文优化 |
| **agentmemory** | Shopify/OpenClaw 生态的 Coding Agent 零干预记忆工具，三流融合检索 |
| **Shibei** | 面向中文个人用户的本地文档知识库 + MCP Server，BM25+向量双引擎 |

---

## 二、总览对比表

| 维度 | **Hy-Memory** | **TencentDB Agent Memory** | **agentmemory** | **Shibei** |
|------|---------------|---------------------------|-----------------|------------|
| **开源协议** | 闭源（插件） | Apache 2.0 | MIT | 个人项目 |
| **核心目标** | Agent 行为记忆压缩 | Agent 长期记忆管理 | Coding Agent 操作记忆 | 个人文档知识库 |
| **存储层** | 未公开（推测本地 SQLite/云端混合） | SQLite | SQLite | SQLite FTS5 + ChromaDB |
| **检索方式** | 记忆版本链检索 | 四层渐进式检索 | BM25 + 向量 + 图谱，RRF 融合 | BM25 + 向量，RRF 融合 |
| **中文支持** | **原生中文优化** | **针对中文优化** | 默认英文嵌入，中文需换模型 | **原生中文语义搜索+分词** |
| **LLM 依赖** | 需要（压缩/演化） | 未明确，推测需要 | **不需要** | 可选（仅 dreaming 增强） |
| **MCP 支持** | 否（OpenClaw/Hermes 插件） | 否 | 否（通过 OpenClaw 集成） | **原生 MCP Server** |
| **数据隐私** | 数据本地，但闭源 | 纯本地 | 纯本地 | 纯本地 |
| **主要输入** | Agent 对话/行为 | Agent 对话/行为 | Coding Agent 工具调用 | 本地文档（md/txt/docx/xlsx/csv） |
| **是否支持对话记忆** | 是 | 是 | 是 | **否（文档库，待扩展）** |
| **是否支持文档库** | 否 | 否 | 否 | **是** |
| **部署复杂度** | 极低（装插件） | 低（pip install） | 低（pip install） | 低（pip install） |

---

## 三、各方案详细分析

### 1. Hy-Memory

- **来源**: 腾讯混元，2026-06-01 发布
- **核心思想**: 记忆不是存得越多越好，而是需要"版本演化"。新记忆明确标注替代了哪条旧记忆（supersedes 指针），旧记忆归档但不删除。
- **技术架构**:
  - 6 层记忆框架（L1-L6）：原始对话 → 事实 → 偏好 → 行为模式 → 心智模型
  - System1 / System2 双系统：快速回忆 vs 深度反思
  - 减量 70%+，信息密度提升 45%+
- **接入方式**: OpenClaw / Hermes Agent / Opencode 插件
- **优势**:
  - 记忆压缩和版本演化思路先进
  - 中文原生优化
  - 安装即用
- **劣势**:
  - **闭源**，无法二次开发
  - 仅支持特定 Agent IDE
  - 偏向**行为/对话记忆**，不支持个人文档库
- **对 Shibei 的启示**:
  - Dreaming 的历史归档压缩可借鉴 supersedes 思想
  - 未来对话记忆功能需要版本链管理

### 2. TencentDB Agent Memory

- **来源**: 腾讯，2026-04 开源，`Tencent/TencentDB-Agent-Memory`
- **核心思想**: 四层渐进式记忆架构，完全 SQLite 本地运行，中文优化
- **技术架构**:
  - 四层记忆（具体分层需进一步调研）
  - 基于 TencentDB VectorDB（但开源版 SQLite 本地）
  - 准确率 76.10%（公开基准）
- **接入方式**: Python SDK / 自托管
- **优势**:
  - 开源（Apache 2.0）
  - 完全本地 SQLite
  - 针对中文内容和国内部署优化
- **劣势**:
  - 知名度低，生态待观察
  - 文档和社区活跃度不足
  - 技术细节公开较少
- **对 Shibei 的启示**:
  - 验证 SQLite 本地路线可行
  - 中文优化是差异化关键
  - 需要加强文档和社区建设

### 3. agentmemory

- **来源**: Shopify CEO Tobi Lütke 发起，OpenClaw 生态核心工具，`rohitg00/agentmemory`
- **核心思想**: 零干预捕获 Coding Agent 的工具调用，自动压缩、持久化、跨会话召回
- **技术架构**:
  - Hook 机制自动捕获 Observation
  - iii-engine 压缩
  - 三流融合检索：BM25 + 向量语义 + 知识图谱，RRF 融合
  - 纯 SQLite，零外部依赖，零 LLM
- **性能**: LongMemEval-S R@5 = 95.2%
- **优势**:
  - 无需 LLM API，纯本地
  - 检索精度高
  - 多 Agent 共享记忆
- **劣势**:
  - 仅适合 Coding Agent 场景
  - 默认英文嵌入，中文项目需换 Qwen3-Embedding
  - 不支持通用文档库
- **对 Shibei 的启示**:
  - 三流融合（BM25+向量+图谱）值得参考
  - "零 LLM 依赖"是纯本地方案的核心卖点
  - 可考虑增加 Coding Agent 专用模式

### 4. Shibei

- **来源**: 自建项目
- **核心思想**: 用最轻量的技术栈（SQLite FTS5 + ChromaDB）打造中文个人文档知识库，通过 MCP 暴露给任何 Agent
- **技术架构**:
  - BM25（FTS5）+ 向量（ChromaDB）双引擎，RRF 融合
  - 文档分块：标题切分 → 句子边界 → parent-child
  - 增量导入 + 精确去重
  - Dreaming：跨文档关联挖掘
  - Profile：多场景知识库切换
- **优势**:
  - **原生 MCP Server**：Claude/Cursor/Trae 直接接入
  - **纯本地零依赖**：无需 LLM API，无需 Docker
  - **中文原生**：BGE-M3/M3E + jieba 可选分词
  - **文档库场景最优**：个人 Markdown/Word/Excel 知识库
- **劣势**:
  - **无对话记忆**：目前只能读取文档，不能记住对话中的事实
  - 生态小，仅个人维护
  - 检索策略少于 agentmemory/Hindsight
- **对自身的启示**:
  - 必须补上**对话记忆**能力，才能从"文档库"升级为"Agent 外部大脑"
  - 可借鉴 Hy-Memory 的版本链和 agentmemory 的三流融合

---

## 四、优劣势矩阵

| 对比维度 | Hy-Memory | TencentDB Agent Memory | agentmemory | Shibei |
|---------|-----------|----------------------|-------------|--------|
| **记忆类型** | 对话/行为记忆 | 对话/行为记忆 | 工具操作记忆 | 文档知识 |
| **中文优化** | ★★★★★ | ★★★★☆ | ★★☆☆☆ | ★★★★★ |
| **开源可控** | ★☆☆☆☆ | ★★★★★ | ★★★★★ | ★★★★★ |
| **MCP 接入** | ★☆☆☆☆ | ★★☆☆☆ | ★★☆☆☆ | ★★★★★ |
| **本地隐私** | ★★★★☆ | ★★★★★ | ★★★★★ | ★★★★★ |
| **LLM 依赖** | 需要 | 未明确 | 不需要 | 可选 |
| **部署易用** | ★★★★★ | ★★★★☆ | ★★★★☆ | ★★★★☆ |
| **文档库支持** | ★☆☆☆☆ | ★☆☆☆☆ | ★☆☆☆☆ | ★★★★★ |
| **对话记忆支持** | ★★★★★ | ★★★★☆ | ★★★★☆ | ★☆☆☆☆ |

---

## 五、场景推荐

| 你的需求 | 推荐方案 | 理由 |
|---------|---------|------|
| 想让 Agent 记住我的编码习惯和项目决策 | **agentmemory** | 专为 Coding Agent 设计，零 LLM 依赖 |
| 想要一个中文原生、会进化的 Agent 行为记忆 | **Hy-Memory** | 版本演化链先进，中文优化，但闭源 |
| 想要开源、本地、中文的 Agent 记忆基座 | **TencentDB Agent Memory** | Apache 2.0 + SQLite + 中文优化 |
| 想让 Agent 读取我的本地文档/笔记/资料 | **Shibei** | 唯一同时支持文档库 + MCP + 纯本地 + 中文的方案 |
| 想要文档库 + 对话记忆统一 | **Shibei（未来）** | 当前缺失对话记忆，但架构最适合补全 |

---

## 六、对 Shibei 的战略启示

### 6.1 必须补齐的短板

1. **对话记忆**：当前只有文档库，没有 Agent 行为/对话记忆。需新增 `shibei_remember` MCP 工具。
2. **记忆版本链**：借鉴 Hy-Memory 的 supersedes，让事实可以演化而非堆积。
3. **三流融合检索**：在 BM25+向量基础上，引入轻量级图谱/实体链接，向 agentmemory 看齐。

### 6.2 应坚持的差异化

1. **MCP-first**：Hy-Memory/TencentDB/agentmemory 均不原生支持 MCP，这是 Shibei 最大优势。
2. **纯本地 + 零 LLM 依赖**：大厂的方案要么闭源要么需要 LLM API。
3. **中文文档库**：这是 Shibei 独一无二的市场空白。

### 6.3 竞争结论

| 赛道 | 领先者 | Shibei 的机会 |
|------|--------|--------------|
| Agent 行为记忆压缩 | Hy-Memory | 无法竞争闭源大厂，但可学习其版本链 |
| 开源本地 Agent 记忆 | TencentDB Agent Memory | 比其更早 MCP 化、更聚焦文档库 |
| Coding Agent 记忆 | agentmemory | 可借鉴三流融合，但场景不同 |
| 中文个人文档知识库 | **Shibei** | 唯一专注者，需尽快补齐对话记忆 |

---

## Sources

- [腾讯混元 Hy-Memory：Agent记忆越少反而越聪明](https://www.geekpark.net/news/365698)
- [腾讯 ima 正式解锁 Agent 形态](http://tech.cnr.cn/techgd/20260429/t20260429_527605152.shtml)
- [腾讯网盘：为 Agent 而生](https://36kr.com/p/3858282630822537)
- [行业 Agent 记忆：国内 Agent 记忆方案概览](https://blog.csdn.net/weixin_49657774/article/details/160903064)
- [Agent 六款开源记忆工具大横评](http://m.toutiao.com/group/7652659795845071402/)
- [Coze 3.0 正式发布](https://www.aitop100.cn/coze3.0)
