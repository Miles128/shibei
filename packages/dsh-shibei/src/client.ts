import { execFileSync } from "node:child_process"
import { request as httpRequest } from "node:http"

export type SidecarInfo = {
  base_url: string
  token: string
}

import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"

function pythonBin(): string {
  return process.env.SHIBEI_PYTHON || "python3"
}

function repoSrc(): string {
  const here = dirname(fileURLToPath(import.meta.url))
  return join(here, "..", "..", "..", "src")
}

export function ensureSidecar(configPath: string): SidecarInfo {
  const src = repoSrc()
  const env = {
    ...process.env,
    PYTHONPATH: `${src}${process.env.PYTHONPATH ? `:${process.env.PYTHONPATH}` : ""}`,
  }
  const out = execFileSync(
    pythonBin(),
    [
      "-c",
      "from shibei.client import ensure_sidecar; import json,sys; print(json.dumps(ensure_sidecar(sys.argv[1])))",
      configPath,
    ],
    { env, encoding: "utf8" },
  )
  return JSON.parse(out.trim()) as SidecarInfo
}

function postJson(info: SidecarInfo, path: string, body: unknown): Promise<unknown> {
  const url = new URL(info.base_url + path)
  const payload = Buffer.from(JSON.stringify(body), "utf8")
  return new Promise((resolve, reject) => {
    const req = httpRequest(
      {
        hostname: url.hostname,
        port: url.port,
        path: url.pathname,
        method: "POST",
        headers: {
          Authorization: `Bearer ${info.token}`,
          "Content-Type": "application/json; charset=utf-8",
          "Content-Length": payload.length,
        },
      },
      (res) => {
        const chunks: Buffer[] = []
        res.on("data", (c) => chunks.push(c))
        res.on("end", () => {
          const raw = Buffer.concat(chunks).toString("utf8")
          if ((res.statusCode || 500) >= 400) {
            reject(new Error(raw || `HTTP ${res.statusCode}`))
            return
          }
          resolve(raw ? JSON.parse(raw) : null)
        })
      },
    )
    req.on("error", reject)
    req.write(payload)
    req.end()
  })
}

export class ShibeiHttpClient {
  constructor(private readonly info: SidecarInfo) {}

  context(body: { message: string; query?: string; limit?: number }): Promise<unknown> {
    return postJson(this.info, "/v1/context", body)
  }

  remember(body: { fact: string; category?: string; tags?: string }): Promise<unknown> {
    return postJson(this.info, "/v1/remember", body)
  }

  search(body: { query: string; limit?: number; tag?: string }): Promise<unknown> {
    return postJson(this.info, "/v1/search", body)
  }
}
