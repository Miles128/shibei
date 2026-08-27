import type { Context } from "@deepseek-ai/cordis"
import { ensureSidecar, ShibeiHttpClient } from "./client.ts"
import { formatContextPack, shouldSkipInstruction, type ContextPack } from "./inject.ts"

export const name = "dsh-shibei"
export const inject = ["tools", "agents"]

type PluginConfig = {
  configPath?: string
  maxInjectedBytes?: number
  injectLimit?: number
  skipInstruction?: boolean
}

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
        .map((part) => (typeof part === "object" && part && "text" in part ? String((part as { text?: string }).text || "") : ""))
        .join(" ")
    }
  }
  return ""
}

function getAgent(input: unknown, ctx: Context): { inject?: (msg: unknown) => void } | undefined {
  const rec = input as { agent?: { inject?: (msg: unknown) => void } }
  if (rec?.agent?.inject) return rec.agent
  const agents = (ctx as unknown as { agents?: { list?: () => Array<{ inject?: (msg: unknown) => void }> } }).agents
  const list = agents?.list?.()
  return list?.[0]
}

export function apply(ctx: Context) {
  const cfg = ((ctx as unknown as { config?: PluginConfig }).config || {}) as PluginConfig
  const configPath = cfg.configPath || process.env.SHIBEI_CONFIG || "config.yaml"
  const maxBytes = cfg.maxInjectedBytes ?? 4096
  const injectLimit = cfg.injectLimit ?? 5
  const skipInstruction = cfg.skipInstruction !== false

  let client: ShibeiHttpClient | undefined
  try {
    client = new ShibeiHttpClient(ensureSidecar(configPath))
  } catch (err) {
    console.error("[dsh-shibei] sidecar unavailable:", err)
  }

  ctx.on("agent/pre-step", async (input: unknown, next: () => Promise<unknown>) => {
    if (client) {
      try {
        const text = extractUserText(input)
        if (text && !shouldSkipInstruction(text, skipInstruction)) {
          const pack = (await client.context({
            message: text,
            limit: injectLimit,
          })) as ContextPack
          const md = formatContextPack(pack, maxBytes)
          const agent = getAgent(input, ctx)
          agent?.inject?.({
            content: [{ type: "text", text: md }],
            source: { kind: "plugin", plugin: "dsh-shibei" },
          })
        }
      } catch (err) {
        console.error("[dsh-shibei] pre-step inject skipped:", err)
      }
    }
    return next()
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
}
