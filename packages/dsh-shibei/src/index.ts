import type { Context } from "@deepseek-ai/cordis"
import { ensureSidecar, ShibeiHttpClient } from "./client.ts"
import {
  formatContextPack,
  formatPersona,
  formatRememberHint,
  handleMemoryCommand,
  handleShibeiCommand,
  parseHostLlmTerms,
  shouldSkipInstruction,
  type ContextPack,
  type MemoryRow,
  type PersonaPack,
  type RememberHint,
} from "./inject.ts"

export const name = "dsh-shibei"
export const inject = ["tools", "agents"]

type PluginConfig = {
  configPath?: string
  maxInjectedBytes?: number
  injectLimit?: number
  skipInstruction?: boolean
  rewrite?: "rules" | "host-llm"
}

type AgentHandle = { inject?: (msg: unknown) => void }

function extractUserText(input: unknown): string {
  const rec = input as {
    messages?: Array<{ content?: unknown }>
    claimed?: { content?: unknown }
  }
  const messages = rec?.messages
  if (Array.isArray(messages) && messages.length) {
    const last = messages[messages.length - 1]
    const content = last?.content
    if (typeof content === "string") return content
    if (Array.isArray(content)) {
      return content
        .map((part) =>
          typeof part === "object" && part && "text" in part
            ? String((part as { text?: string }).text || "")
            : "",
        )
        .join(" ")
    }
  }
  return ""
}

function getAgent(input: unknown, ctx: Context): AgentHandle | undefined {
  const rec = input as { agent?: AgentHandle }
  if (rec?.agent?.inject) return rec.agent
  const agents = (ctx as unknown as { agents?: { list?: () => AgentHandle[] } }).agents
  return agents?.list?.()?.[0]
}

function pluginMessage(text: string) {
  return {
    content: [{ type: "text", text }],
    source: { kind: "plugin", plugin: "dsh-shibei" },
  }
}

async function hostLlmQuery(ctx: Context, message: string): Promise<string | undefined> {
  const llm = (ctx as unknown as { get?: (name: string) => unknown }).get?.("llm") as
    | {
        complete?: (prompt: string) => Promise<string>
        chat?: (messages: Array<{ role: string; content: string }>) => Promise<string>
      }
    | undefined
  const prompt =
    "从下面用户消息抽出 5 到 8 个检索关键词，空格分隔，含中英同义，不要句子：\n" + message.slice(0, 2000)
  try {
    if (llm?.complete) return parseHostLlmTerms(await llm.complete(prompt))
    if (llm?.chat) {
      return parseHostLlmTerms(
        await llm.chat([{ role: "user", content: prompt }]),
      )
    }
  } catch {
    return undefined
  }
  return undefined
}

export function apply(ctx: Context) {
  const cfg = ((ctx as unknown as { config?: PluginConfig }).config || {}) as PluginConfig
  const configPath = cfg.configPath || process.env.SHIBEI_CONFIG || "config.yaml"
  const maxBytes = cfg.maxInjectedBytes ?? 4096
  const injectLimit = cfg.injectLimit ?? 5
  const skipInstruction = cfg.skipInstruction !== false
  const rewrite = cfg.rewrite || "rules"

  let client: ShibeiHttpClient | undefined
  try {
    client = new ShibeiHttpClient(ensureSidecar(configPath))
  } catch (err) {
    console.error("[dsh-shibei] sidecar unavailable:", err)
  }

  let lastHint: RememberHint | null = null

  ctx.on("agent/session-start", async (payload: { agent?: AgentHandle }) => {
    if (!client) return
    try {
      const persona = (await client.persona()) as PersonaPack
      const md = formatPersona(persona, maxBytes)
      payload.agent?.inject?.(pluginMessage(md))
    } catch (err) {
      console.error("[dsh-shibei] session-start persona skipped:", err)
    }
  })

  ctx.on("agent/pre-step", async (input: unknown, next: () => Promise<unknown>) => {
    if (client) {
      try {
        const text = extractUserText(input)
        if (text && !shouldSkipInstruction(text, skipInstruction)) {
          let query: string | undefined
          if (rewrite === "host-llm") {
            query = await hostLlmQuery(ctx, text)
          }
          const pack = (await client.context({
            message: text,
            query,
            limit: injectLimit,
          })) as ContextPack
          lastHint = pack.suggest_remember || null
          const md = formatContextPack(pack, maxBytes)
          getAgent(input, ctx)?.inject?.(pluginMessage(md))
        }
      } catch (err) {
        console.error("[dsh-shibei] pre-step inject skipped:", err)
      }
    }
    return next()
  })

  ctx.on("agent/turn-stopping", async (payload: { agent?: AgentHandle }) => {
    const hint = lastHint
    lastHint = null
    const md = hint ? formatRememberHint(hint) : ""
    if (!md) return
    try {
      payload.agent?.inject?.(pluginMessage(md))
    } catch (err) {
      console.error("[dsh-shibei] turn-stopping hint skipped:", err)
    }
  })

  const tools = (ctx as unknown as { tools?: { register: (tool: unknown) => void } }).tools
  if (tools && client) {
    const http = client
    tools.register({
      name: "shibei_remember",
      description: "将重要事实写入拾贝长期记忆（偏好、项目背景、决策）。",
      parameters: {
        type: "object",
        properties: {
          fact: { type: "string" },
          category: { type: "string" },
          tags: { type: "string" },
        },
        required: ["fact"],
      },
      execute: async (args: { fact: string; category?: string; tags?: string }) =>
        http.remember(args),
    })
    tools.register({
      name: "shibei_search",
      description: "在拾贝知识库中按关键词搜索文档与记忆。答前召回由插件自动注入，无需调用。",
      parameters: {
        type: "object",
        properties: {
          query: { type: "string" },
          limit: { type: "integer" },
          tag: { type: "string" },
        },
        required: ["query"],
      },
      execute: async (args: { query: string; limit?: number; tag?: string }) =>
        http.search(args),
    })
  }

  const commands = (
    ctx as unknown as {
      get?: (name: string) => { register: (def: unknown) => void } | undefined
      commands?: { register: (def: unknown) => void }
    }
  ).get?.("commands") || (ctx as unknown as { commands?: { register: (def: unknown) => void } }).commands

  if (commands && client) {
    const http = client
    commands.register({
      name: "shibei",
      description: "检索拾贝知识库；无参数时显示画像",
      input: { hint: "检索词，可空" },
      handler: async ({ rawInput }: { rawInput: string }) => ({
        kind: "success",
        text: await handleShibeiCommand(rawInput || "", {
          search: (query) => http.search({ query, limit: injectLimit }) as Promise<ContextPack>,
          persona: () => http.persona() as Promise<PersonaPack>,
        }),
      }),
    })
    commands.register({
      name: "memory",
      description: "列出/搜索拾贝记忆；remember <事实> 写入",
      input: { hint: "空=列表；remember <事实>；或检索词" },
      handler: async ({ rawInput }: { rawInput: string }) => ({
        kind: "success",
        text: await handleMemoryCommand(rawInput || "", {
          list: async () => (await http.listMemories()) as MemoryRow[],
          remember: async (fact) => (await http.remember({ fact })) as MemoryRow,
          search: async (query) => (await http.searchMemories(query)) as MemoryRow[],
        }),
      }),
    })
  }
}
