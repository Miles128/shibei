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
```

需要本机已安装拾贝（`PYTHONPATH` 能 `import shibei`），插件启动时会 `ensure` sidecar。

## 行为

- 每个 step 自动 `POST /v1/context`，把 compact pack inject 进模型可见上下文
- 工具：`shibei_remember`、`shibei_search`
- 长期记忆仍在 `~/.shibei` SQLite，不进 DSH session log
