# 从 MemoryCore 提取什么

> 调研对象：`TencentCloud/TencentDB-Agent-Memory` 的 `MemoryCore`（MIT，v2.0.3，2026-10-26）
> 目的：不安装完整栈，只把它的**记忆工程质量**借到 Shibei + dsh 上。
> 结论：值得抄的是「抽取 / 去重 / 注入 / 画像触发」四件事，不是它的团队与部署。

## 1. 值得抄的长处

| # | 长处 | MemoryCore 的做法 | 我们现状 | 落点 |
|---|---|---|---|---|
| 1 | **L1 结构化抽取** | 一次 LLM 调用同时做「情境切分 + 记忆提取」；三类 `persona / episodic / instruction`；每条带 `priority`、`source_message_ids`、`metadata`；要求独立完整、合并强关联事实 | Shibei `auto_remember` 只做「一行一条事实」，不分类型、不打分、不带来源 | Shibei |
| 2 | **在线冲突检测** | 新记忆先召回 top-5 候选（hybrid/FTS/vector），再一次 LLM 批量判断 `store / update / skip`，写 `supersedes` | Shibei 有 `supersedes` 字段但没在线使用；只有离线 `consolidate` | Shibei |
| 3 | **自动召回注入** | 每轮把相关 L1 前置到 user prompt；L3 persona 追加到 system prompt（可缓存）；L2 scene 导航按需；附工具使用指南 + 每轮最多 3 次搜索 | dsh 里只有 prompt 规则，模型可以不听 | dsh 插件 |
| 4 | **Persona 触发条件** | 显式请求 / 冷启动 / 恢复 / 首个 scene / 达到阈值，五种条件才重算 | Shibei `persona` 是即时聚合，没有检查点与触发 | Shibei |
| 5 | **Scene 分段（L2）** | 按话题切换切 scene，场景名 + 消息范围；召回时给导航 | 无 | Shibei（后期） |
| 6 | **生成溯源** | 记录 prompt id/version/hash、输入输出引用，可按 memory id 追踪 | 无 | Shibei（后期） |
| 7 | **上下文卸载** | 大结果落文件，只回摘要 + 引用 | Shibei 已有 `OffloadConfig` | 保留 |
| 8 | **混合检索** | BM25 + 向量 + RRF，FTS 不可用自动降级 | 代码已有，配置默认关闭 | 开启 |

## 2. 不抄的部分

团队 / User / ACL / Hub / Proxy / Panel、MongoDB、OpenTelemetry、quota、Skill 资产库。
这些是「团队级 Memory Hub」的成本，单人本地用不上，而且会把架构拖重。

## 3. 落到 Shibei 的数据模型

Shibei 现有 `memories` 表已经有 `category`、`source`、`tags`、`expires_at`、`supersedes`、`superseded_by`、`source_message_ids`，底子够，只差语义字段。

建议增量：

```sql
ALTER TABLE memories ADD COLUMN memory_type TEXT;   -- persona | episodic | instruction
ALTER TABLE memories ADD COLUMN priority INTEGER;   -- 0-100, instruction 可为 -1
ALTER TABLE memories ADD COLUMN scene_name TEXT;    -- L2 场景
ALTER TABLE memories ADD COLUMN confidence REAL;    -- 0-1
```

L0 不复制：dsh 的 session log 就是 L0，用 `session_id + turn + message_id` 关联即可。
L2 用 `~/.shibei/db/scenes/<scene>.md`，L3 用 `persona.md`。

## 4. Shibei 的 L1 抽取

把 `auto_remember` 从「一行一条」换成 MemoryCore 风格的单次 JSON 调用：

```json
[
  {
    "scene_name": "我在和四海做 Lumina 的 dsh 迁移",
    "memories": [
      {
        "content": "用户决定用 dsh 原生 workflow 脚本替代自研 Skill DAG",
        "type": "instruction",
        "priority": 90,
        "confidence": 0.9,
        "source_message_ids": ["session-...:turn-3"]
      }
    ]
  }
]
```

规则（直接沿用其思路，中文改写）：

- 只提 `persona`（稳定偏好/身份）、`episodic`（发生过的决策/事件）、`instruction`（长期行为规则）
- 宁缺毋滥：寒暄、一次性操作、AI 自己的输出、纯情绪不记
- 必须独立完整，脱离上下文也能看懂
- 强关联事实合并成一条
- 输出语言跟用户一致；JSON 字段名保持英文

触发时机：`turn/end`，且本轮有用户消息 + 至少一条工具调用或 > N 字；避免每轮都烧 token。

## 5. 在线去重

```
新 L1 → 召回 top-5（BM25 / 向量 / RRF）
      → 一次 LLM 批量判断：
          store   新事实
          update  替换候选（写 supersedes/superseded_by）
          skip    已有等价事实
      → 无 LLM 时降级：hash + 子串 + 高相似度阈值
```

关键点：**候选召回和判断分开**，判断只做一次批量调用，避免每条记忆一次 LLM。

## 6. 自动召回注入（dsh）

在 `dsh-lumina` 里加一个 `agent/pre-step`：

```
pre-step(userText)
  → 调 shibei_context（或新增 /recall）
  → L1 top-5 相关记忆 → 前置到本轮 user 上下文
  → L3 persona（缓存，稳定）→ 追加到 system prompt
  → L2 scene 导航（可选）
  → 附「记忆工具指南」+ 每轮最多 3 次搜索
```

注意：

- persona 和工具指南是稳定内容，放 system prompt，别每轮变，保 KV cache
- L1 是动态内容，放 user prompt 前置
- 注入要有边界：总字符上限（如 1.5K），超出截断并注明可搜索
- 每轮记录注入了哪些 memory id，便于回溯

## 7. Persona 触发

照抄五条件，落到 Shibei 的 checkpoint：

```json
{
  "total_processed": 120,
  "memories_since_last_persona": 8,
  "scenes_processed": 12,
  "last_persona_at": 1792866310000,
  "request_persona_update": false
}
```

触发：显式请求 / 冷启动 / persona 丢失恢复 / 首个 scene / `memories_since_last_persona >= N`。
默认 N=20，避免频繁重算。

## 8. 分期

| 阶段 | 内容 | 工作量 | 收益 |
|---|---|---|---|
| P0 | 开向量混合 + parent-child（纯配置） | 0.5h | 召回立刻变好 |
| P1 | L1 抽取（schema + JSON prompt） | 1d | 记忆从「流水账」变结构化 |
| P2 | 在线去重 | 0.5–1d | 不重复、可更新 |
| P3 | dsh 自动召回注入 | 0.5d | 不靠模型自觉 |
| P4 | persona 触发 + scene | 1–2d | 长期画像稳定 |
| P5 | 生成溯源 | 0.5d | 审计/排障 |

建议顺序：**P0 → P1 → P3 → P2 → P4 → P5**。P1 和 P3 是体验提升最大的两步。

## 9. 风险

- **LLM 成本**：抽取和去重都调模型；用 turn 门禁 + 批量判断 + 小模型控制
- **隐私**：本地模型或本地 API；不要把笔记发给不可信端点
- **记忆污染**：宁缺毋滥，置信度低的直接丢
- **注入膨胀**：硬上限 + 只注入相关项 + persona 缓存
- **与 MEMORY.md 重复**：MEMORY.md 只放「每轮都必须知道」的短事实，其余进 L1

## 10. 参考文件（MemoryCore 源码）

- `MemoryCore/src/core/prompts/l1-extraction.ts` — 抽取 prompt
- `MemoryCore/src/core/prompts/l1-dedup.ts` — 冲突判断 prompt
- `MemoryCore/src/core/record/l1-dedup.ts` — 批量去重流程
- `MemoryCore/src/core/hooks/auto-recall.ts` — 召回与注入
- `MemoryCore/src/core/persona/persona-trigger.ts` — 画像触发
- `MemoryCore/src/core/conversation/l0-recorder.ts` — L0 记录
