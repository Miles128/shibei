export type ContextHit = {
  source?: string
  text?: string
  score?: number
}

export type ContextMemory = {
  category?: string
  text?: string
}

export type ContextPack = {
  query_used?: string
  results?: ContextHit[]
  memories?: ContextMemory[]
}

const SKIP_RE = /我|笔记|记得|之前|偏好/

export function shouldSkipInstruction(message: string, enabled: boolean): boolean {
  if (!enabled) return false
  const text = (message || "").trim()
  if (text.length >= 16) return false
  if (SKIP_RE.test(text)) return false
  return text.length < 8 || /^(改|跑|打开|关闭|重启)/.test(text)
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
  let md = lines.join("\n")
  const encoder = new TextEncoder()
  while (encoder.encode(md).length > maxBytes && md.length > 32) {
    md = md.slice(0, Math.max(32, md.length - 64)) + "…"
  }
  return md
}
