# dsh-shibei

拾贝在 DeepSeek Harness 上的外挂记忆插件。召回走 `agent/pre-step` + `agent.inject()`，读写走与 CLI/MCP 相同的 sidecar `/v1` API。

## 安装

在 Harness profile 的 `cordis.patch.yml` 插入：

```yaml
- insert:
    - id: shibei
      name: /absolute/path/to/shibei/packages/dsh-shibei/src/index.ts
      config:
        configPath: /Users/you/.shibei/config.yaml
        maxInjectedBytes: 4096
        injectLimit: 5
        skipInstruction: true
        rewrite: rules                 # 或 host-llm
        autoRemember: true             # turn 结束后自动抽取写入（默认 true）
        autoNotify: true               # 自动写入后注入简短通知（默认 true）
```

需要本机已安装拾贝（`PYTHONPATH` 能 `import shibei`），插件启动时会 `ensure` sidecar。

## 行为

- 会话开始：`agent/session-start` 注入短画像
- 每个 step：`agent/pre-step` 自动 `POST /v1/context`，inject compact pack
- 回合结束：**自动记忆闭环**——调用 `POST /v1/auto-remember` 用 LLM 从本轮用户消息抽取事实写入（去重、上限由拾贝 `config.auto_memory` 控制，未启用时静默跳过）；写入成功且 `autoNotify` 时注入简短通知防止下轮重复写；自动路径未产出时回退为 `suggest_remember` 写记忆提示
- 工具：`shibei_remember`、`shibei_search`、`shibei_memories`（检索/列出记忆，返回 id）、`shibei_forget`（删除单条）
- 人机命令：`/shibei [检索词]`、`/memory`、`/memory remember <事实>`、`/memory forget #<id>`
- 长期记忆仍在 `~/.shibei` SQLite，不进 DSH session log

`rewrite: host-llm` 时，pre-step 用宿主 LLM 抽 5–8 个词再检索（仍在同一步）。默认 `rules`。
