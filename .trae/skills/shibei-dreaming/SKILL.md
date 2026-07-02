---
name: "shibei-dreaming"
description: "Executes the Shibei knowledge-base dreaming process via MCP to discover cross-document connections, compress old memories, and merge similar chunks. Invoke when the user asks to run dreaming, organize notes, clean up old memories, consolidate overlapping knowledge, or explore hidden relationships in their personal knowledge base."
---

# Shibei Dreaming Skill

This skill guides an external AI system through the Shibei "dreaming" workflow using the Model Context Protocol (MCP).

`shibei_dream` now **automatically performs housekeeping** by default:

- Cleans expired memories (always).
- Compresses old short-term memories when an LLM is available.
- Merges similar chunks when an LLM is available.

If Shibei already has its own LLM configured, `shibei_dream` uses it to apply compression and consolidation directly.
If `llm_config` is provided, it overrides Shibei's own LLM.
Only when **no LLM is available** does `shibei_dream` fall back to **advisory / skill mode** and return candidates for manual review.

## 1. Activation Parameters

Trigger this skill when the user expresses any of the following intents:

- "Run dreaming" / "Start dreaming" / "整理一下我的笔记" / "做个 dreaming"
- "Clean up old memories" / "压缩旧记忆" / "清理过期记忆"
- "Merge duplicate notes" / "合并重复内容" / "去重知识库"
- "Find connections across my notes" / "发现笔记之间的关联"
- "Organize my knowledge base" / "整理知识库"

Do **not** use this skill for simple search queries or one-off memory writes; use `shibei_search` or `shibei_remember` directly instead.

## 2. Input Requirements

All dreaming calls go through the `shibei_dream` MCP tool.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `format` | `"brief" \| "full"` | `"brief"` | `brief` returns stats, insights, and housekeeping summaries (recommended for MCP). `full` adds `cross_references`, `patterns`, and detailed cluster chunks. |
| `persist` | `boolean` | `true` | Whether Shibei persists the result for future diff comparisons. |
| `housekeeping` | `boolean \| object` | `true` | `false` disables housekeeping. `true` uses defaults. Pass an object to override thresholds, e.g. `{"compress_max_age_days": 30, "consolidate_threshold": 0.6}`. |
| `llm_config` | `object` | omitted | Optional LLM config forwarded by the external AI to override Shibei's own LLM. When omitted, `shibei_dream` falls back to Shibei's configured LLM; if none is configured, it returns candidates only. |

### Example MCP Call (Auto Mode)

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/call",
  "params": {
    "name": "shibei_dream",
    "arguments": {
      "format": "brief",
      "persist": true,
      "housekeeping": {
        "compress_max_age_days": 30,
        "compress_keep_recent": 10,
        "consolidate_threshold": 0.6,
        "consolidate_min_cluster_size": 2,
        "consolidate_max_chunks": 500
      },
      "llm_config": {
        "api_key": "<external-ai-api-key>",
        "model": "deepseek-chat",
        "base_url": "https://api.deepseek.com/v1",
        "temperature": 0.3,
        "max_tokens": 1024,
        "timeout": 30
      }
    }
  }
}
```

## 3. Execution Steps

Follow this sequence exactly:

### Step 0 — Memory Access Check

Before any reasoning about "my memory" or the user's personal knowledge base, you **MUST** invoke `shibei_search` or `shibei_dream` through MCP. Do not hallucinate contents from training data.

### Step 1 — Run Dreaming

Call `shibei_dream(format="brief", persist=true, housekeeping=true)`.

If Shibei does not have its own LLM configured and you want to force auto-apply, include `llm_config` with the host AI's API credentials. Otherwise, Shibei will use its own LLM automatically.

Validate the response:
- If `status` is `"empty"`, stop and inform the user the knowledge base is empty.
- If the MCP call fails, follow the error-handling protocol in Section 6.

### Step 2 — Process Insights

Read the returned fields:
- `stats.total_chunks`, `stats.unique_docs`, `stats.cross_references`
- `insights[]` — high-level observations
- `housekeeping.memory.expired_deleted` — automatically cleaned memories

When an LLM is available (Shibei's own or via `llm_config`):
- `housekeeping.memory.compress` — compression result (may include `compressed_count`).
- `housekeeping.consolidation.applied` — whether similar chunks were merged.
- `housekeeping.consolidation.removed` / `inserted` — how many chunks were removed/inserted.

When no LLM is available:
- `housekeeping.memory.compressible_candidates[]` — old memory candidates for compression.
- `housekeeping.consolidation.cluster_count` and `clusters[]` — overlapping chunk candidates.

### Step 3 — Act on Memory Compression Candidates (Only When No LLM Available)

If an LLM is available (Shibei's own or via `llm_config`), Shibei has already compressed old memories; skip this step.

Otherwise, for each `compressible_candidate`:
1. Read its `fact`, `tags`, `category`, and `created_at`.
2. Use your own LLM to classify lifespan: `short_term`, `long_term`, or `personal`.
3. If `short_term`, generate a concise summary.
4. Call `shibei_update_memory` with `memory_id` and the new `fact` (and optionally updated `tags`).
5. Skip `long_term` and `personal` memories.

### Step 4 — Act on Similar Chunk Clusters (Only When No LLM Available)

If an LLM is available (Shibei's own or via `llm_config`), Shibei has already merged similar chunks; skip this step.

Otherwise, for each cluster with `size >= 2`:
1. Read the `chunks[]` array to understand the overlapping content. Each chunk exposes `_rowid`.
2. Use your own LLM to generate a merged summary that removes duplication.
3. Build a payload: `{"clusters": [{"rowids": [...], "summary": "...", "sources": [...]}]}`.
4. Call `shibei_apply_consolidation` to write the merged summary back and delete the original overlapping chunks.

### Step 5 — Format Final Output

Present the results using the output template in Section 5.

## 4. Output Formatting Specifications

Use this structure when reporting results to the user.

### Auto Mode (`llm_config` provided)

```markdown
## Dreaming 结果

### 概览
- 总 chunk 数：{stats.total_chunks}
- 文档数：{stats.unique_docs}
- 跨文档连接：{stats.cross_references}
- 过期记忆已清理：{housekeeping.memory.expired_deleted}
- 旧短期记忆已压缩：{housekeeping.memory.compress.compressed_count} 条
- 相似 chunk 已合并：移除 {housekeeping.consolidation.removed} 条，新增 {housekeeping.consolidation.inserted} 条

### 洞察
{for each insight}
- {insight}
{/for}
```

### Skill Mode (`llm_config` omitted)

```markdown
## Dreaming 结果

### 概览
- 总 chunk 数：{stats.total_chunks}
- 文档数：{stats.unique_docs}
- 跨文档连接：{stats.cross_references}
- 过期记忆已清理：{housekeeping.memory.expired_deleted}

### 洞察
{for each insight}
- {insight}
{/for}

### 可压缩记忆候选（{housekeeping.memory.compressible_count} 条）
{for each candidate}
- **ID**: `{id}`
  - **原文**: {fact}
  - **创建时间**: {created_at}
  - **建议**: 由 LLM 判断生命周期后，若为短期记忆则压缩
{/for}

### 相似 Chunk 候选（{housekeeping.consolidation.cluster_count} 个 cluster）
{for each cluster}
- **Cluster {index}**（{size} 个 chunk，来源：{sources}）
  - **原文摘要**:
    {for each chunk}
    - {text[:120]}...
    {/for}
  - **建议**: 用 LLM 生成合并摘要后调用 shibei_apply_consolidation
{/for}
```

For `format="full"`, additionally include:
- `cross_references[]` as a markdown table
- `patterns{}` per tag

## 5. Memory Access Rule (Mandatory)

Whenever this skill (or any external AI using it) needs to reference "my memory," "my notes," "my knowledge base," or any fact about the user that is not in the current conversation context, you **MUST** perform an MCP call to Shibei first.

### Allowed Memory Operations

| Intent | Required MCP Tool | Why |
|--------|-------------------|-----|
| Search user's stored facts | `shibei_search` | Recall existing memory |
| Discover connections / patterns | `shibei_dream` | Insight generation |
| List sources | `shibei_list_sources` | Validate what is imported |
| Write new fact | `shibei_remember` | Persist new information |
| Update / compress old fact | `shibei_update_memory` | Modify existing memory |
| Merge duplicate chunks | `shibei_apply_consolidation` | Consolidate knowledge base with externally generated summary |

### Prohibited Behavior

- Do **not** answer "based on your memory" without an MCP result.
- Do **not** infer the contents of the knowledge base from the conversation alone.
- Do **not** call `shibei_update_memory` or `shibei_apply_consolidation` without first running `shibei_dream` (or `shibei_search`) to ground the action in actual data.

## 6. Error Handling

### MCP Call Fails

1. **Retry once** after 1 second.
2. If the second call also fails, capture the error message.
3. Report to the user:
   ```markdown
   ⚠️ MCP call to Shibei failed: `{error_message}`
   
   Possible causes:
   - Shibei server is not running (`shibei serve` not started)
   - `SHIBEI_CONFIG` environment variable points to a missing config file
   - Network / IPC issue between the AI host and the MCP server
   
   Please check that Shibei is running and try again.
   ```
4. Do not proceed with memory modifications until the connection is restored.

### Empty Knowledge Base

If `shibei_dream` returns `{"status": "empty"}`:

```markdown
Your knowledge base is currently empty. Run `shibei import` first to import documents, or use `shibei_remember` to add memories.
```

### Invalid Tool Response

If the response is missing expected fields (e.g., `housekeeping` is absent):
1. Log the raw response for debugging.
2. Fall back to reporting whatever fields are available.
3. Do not fabricate missing values.

## 7. Memory Access Permission Validation

Before acting on memory candidates, verify:

1. **Identity of caller**: The MCP server runs under the user's local environment. Assume the caller has the same permissions as the user.
2. **Scope of modification**: Only update or delete memory entries that were explicitly returned by `shibei_dream` or `shibei_search`.
3. **No cross-user access**: Never use memory IDs from one user for another user.
4. **Confirmation for destructive operations**: When `shibei_consolidate(apply=true)` would delete original chunks, summarize the impact (number of chunks to be removed and inserted) before applying.

## 8. Interaction Flow Diagram

```
┌─────────────────┐     1. tools/call       ┌─────────────────┐
│   External AI   │ ───────────────────────>│  MCP Client/    │
│  (this skill)   │                         │  Host (Trae/    │
└─────────────────┘                         │  Claude/Cursor) │
        │                                   └─────────────────┘
        │                                            │
        │  2. MCP JSON-RPC over stdio                │
        │                                            ▼
        │                                   ┌─────────────────┐
        │                                   │  shibei.server  │
        │                                   │  (MCP server)   │
        │                                   └─────────────────┘
        │                                            │
        │  6. formatted result                3. read/write
        │<───────────────────────────────────────────┤
        │                                            ▼
        │                                   ┌─────────────────┐
        │                                   │  SQLite + FTS5  │
        │                                   │  (BM25 index &  │
        │                                   │   memories)     │
        │                                   └─────────────────┘
        │
        ▼
┌─────────────────┐
│  User-facing    │
│  report         │
└─────────────────┘
```

### Flow Description

1. **External AI** decides to run dreaming based on user intent.
2. External AI sends an MCP `tools/call` request to the host.
3. Host forwards the request to `shibei.server` via stdio JSON-RPC.
4. `shibei.server` reads/writes to the local SQLite database (BM25 index and memory store).
5. `shibei.server` returns structured JSON results.
6. External AI formats the results and presents them to the user.
7. If modifications are needed, External AI issues additional MCP calls (`shibei_update_memory`, `shibei_consolidate`) and repeats steps 2–6.

## 9. Example Full Session (Auto Mode)

**User**: "整理一下我的笔记，去掉重复内容，把旧短期记忆压缩一下。"

**AI** (uses this skill):

1. Call `shibei_dream(format="brief", housekeeping=true, llm_config={...})` using the host AI's own LLM credentials.
2. Receive:
   ```json
   {
     "housekeeping": {
       "memory": {
         "expired_deleted": 2,
         "compress": {
           "compressed_count": 2,
           "skipped_personal": 1
         }
       },
       "consolidation": {
         "status": "ok",
         "applied": true,
         "removed": 2,
         "inserted": 1,
         "cluster_count": 1
       }
     }
   }
   ```
3. Report final results to the user.

> If you do not provide `llm_config`, `shibei_dream` falls back to skill mode and returns `compressible_candidates` and `clusters` for manual review, as described in Steps 3 and 4.
