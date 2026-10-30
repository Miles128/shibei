export type ContextHit = {
  source?: string
  text?: string
  score?: number
}

export type ContextMemory = {
  category?: string
  text?: string
}

export type RememberHint = {
  should?: boolean
  fact?: string
  reason?: string
}

export type ContextPack = {
  query_used?: string
  results?: ContextHit[]
  memories?: ContextMemory[]
  suggest_remember?: RememberHint
}

export type PersonaFact = { fact?: string }

export type PersonaPack = {
  total?: number
  categories?: Record<string, PersonaFact[] | string[]>
}

export type MemoryRow = {
  fact?: string
  text?: string
  category?: string
  id?: string
}

const SKIP_RE = /我|笔记|记得|之前|偏好/

export function shouldSkipInstruction(message: string, enabled: boolean): boolean {
  if (!enabled) return false
  const text = (message || "").trim()
  if (text.length >= 16) return false
  if (SKIP_RE.test(text)) return false
  return text.length < 8 || /^(改|跑|打开|关闭|重启)/.test(text)
}

function clipUtf8(md: string, maxBytes: number): string {
  const encoder = new TextEncoder()
  while (encoder.encode(md).length > maxBytes && md.length > 32) {
    md = md.slice(0, Math.max(32, md.length - 64)) + "…"
  }
  return md
}

export function formatContextPack(pack: ContextPack, maxBytes: number): string {
  const lines: string[] = ["[拾贝上下文]"]
  if (pack.query_used) {
    lines.push(`检索: ${pack.query_used}`)
  }
  const memories = pack.memories || []
  if (memories.length) {
    lines.push("记忆:")
    for (const mem of memories.slice(0, 5)) {
      const cat = mem.category ? `[${mem.category}] ` : ""
      lines.push(`- ${cat}${(mem.text || "").trim()}`)
    }
  }
  const results = pack.results || []
  if (results.length) {
    lines.push("文档:")
    for (const hit of results.slice(0, 5)) {
      const src = hit.source || ""
      const text = (hit.text || "").replace(/\s+/g, " ").trim().slice(0, 240)
      lines.push(`- ${src}: ${text}`)
    }
  }
  return clipUtf8(lines.join("\n"), maxBytes)
}

export function formatPersona(pack: PersonaPack, maxBytes = 2048): string {
  const total = pack.total || 0
  if (!total) return "[拾贝画像]\n暂无长期记忆。"
  const lines = ["[拾贝画像]"]
  for (const [cat, facts] of Object.entries(pack.categories || {})) {
    lines.push(`${cat}:`)
    for (const item of facts.slice(0, 8)) {
      const fact = typeof item === "string" ? item : item.fact || ""
      if (fact) lines.push(`- ${fact}`)
    }
  }
  return clipUtf8(lines.join("\n"), maxBytes)
}

export function formatRememberHint(hint: RememberHint): string {
  if (!hint?.should) return ""
  const fact = (hint.fact || "").trim()
  return [
    "[拾贝写记忆提示]",
    "本轮披露了可长期保存的事实。请调用 shibei_remember，不要只口头答应。",
    fact ? `建议写入: ${fact}` : "",
    hint.reason ? `原因: ${hint.reason}` : "",
  ]
    .filter(Boolean)
    .join("\n")
}

export type AutoMemoryRow = {
  fact?: string
}

/** 通知 agent 自动记忆已写入哪些事实，避免下一轮重复调用 shibei_remember。 */
export function formatAutoMemoryNotice(rows: AutoMemoryRow[]): string {
  const facts: string[] = []
  for (const row of rows || []) {
    const fact = (row?.fact || "").trim()
    if (fact) facts.push(fact)
  }
  if (!facts.length) return ""
  const lines = [
    "[拾贝自动记忆]",
    "本轮已自动写入以下长期记忆，无需再调用 shibei_remember 重复写入:",
    ...facts.slice(0, 5).map((f) => `- ${f}`),
  ]
  return clipUtf8(lines.join("\n"), 1024)
}

export function parseHostLlmTerms(raw: string, maxTerms = 8): string {  const terms = (raw || "")
    .split(/[\s,，、]+/)
    .map((t) => t.replace(/^[0-9]+[.)、．]\s*/, "").replace(/^[-*•]\s*/, "").trim())
    .filter((t) => t && t.length < 40)
  const seen = new Set<string>()
  const out: string[] = []
  for (const t of terms) {
    const key = t.toLowerCase()
    if (seen.has(key)) continue
    seen.add(key)
    out.push(t)
    if (out.length >= maxTerms) break
  }
  return out.join(" ")
}

export async function handleShibeiCommand(
  rawInput: string,
  deps: {
    search: (query: string) => Promise<ContextPack>
    persona: () => Promise<PersonaPack>
  },
): Promise<string> {
  const q = rawInput.trim()
  if (!q) {
    const persona = await deps.persona()
    return `${formatPersona(persona)}\n\n用法: /shibei <检索词>`
  }
  const pack = await deps.search(q)
  return formatContextPack(pack, 4096)
}

export async function handleMemoryCommand(
  rawInput: string,
  deps: {
    list: () => Promise<MemoryRow[]>
    remember: (fact: string) => Promise<MemoryRow>
    search: (query: string) => Promise<MemoryRow[]>
    forget?: (memoryId: string) => Promise<boolean>
  },
): Promise<string> {
  const q = rawInput.trim()
  if (!q) {
    const rows = await deps.list()
    if (!rows.length) return "[拾贝记忆]\n暂无记忆。"
    const lines = ["[拾贝记忆]"]
    for (const row of rows.slice(0, 20)) {
      const text = row.fact || row.text || ""
      const cat = row.category ? `[${row.category}] ` : ""
      const id = row.id ? `#${row.id} ` : ""
      lines.push(`- ${id}${cat}${text}`)
    }
    lines.push("forget #<id> 删除对应记忆")
    return lines.join("\n")
  }
  const rememberMatch = q.match(/^(remember|add|记)\s+(.+)$/i)
  if (rememberMatch) {
    const row = await deps.remember(rememberMatch[2].trim())
    return `已写入记忆: ${row.fact || rememberMatch[2].trim()}`
  }
  const forgetMatch = q.match(/^(forget|delete|删|忘)\s*#?\s*(\S+)$/i)
  if (forgetMatch) {
    if (!deps.forget) return "[拾贝记忆]\n当前不支持删除。"
    const target = forgetMatch[2]
    const ok = await deps.forget(target)
    return ok ? `已删除记忆 #${target}` : `未找到记忆 #${target}`
  }
  const rows = await deps.search(q)
  if (!rows.length) return `[拾贝记忆]\n未找到与「${q}」相关的记忆。`
  const lines = ["[拾贝记忆]"]
  for (const row of rows.slice(0, 10)) {
    const id = row.id ? `#${row.id} ` : ""
    lines.push(`- ${id}${row.fact || row.text || ""}`)
  }
  return lines.join("\n")
}
