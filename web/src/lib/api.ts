export type Attachment = {
  id: number
  campaign_id: number
  field: string
  filename: string
  content_type: string
  size: number
  created_at: string
}

export type Campaign = {
  id: number
  target: string
  info: string
  instructions: string
  description: string
  scope: string
  model: string
  effort: string
  status: string
  created_at: string
  updated_at: string
  message_count?: number
  running?: boolean
  attachments?: Attachment[]
  usage_in?: number
  usage_out?: number
  last_prompt_tokens?: number
}

export type MessageAttachment = {
  id: number
  filename: string
  content_type: string
  size: number
  path?: string
}

export type LocalFile = {
  filename: string
  preview?: string
}

export type Message = {
  id: number
  role: "user" | "assistant"
  content: string
  reasoning: string
  tool_calls?: string
  parts?: string
  attachments?: MessageAttachment[]
  localFiles?: LocalFile[]
  created_at: string
}

export type ToolCall = {
  id: string
  name: string
  args: Record<string, unknown>
  output?: string | null
  ok?: boolean | null
  ms?: number | null
}

export type Part =
  | { type: "thinking"; text: string }
  | { type: "content"; text: string }
  | { type: "notice"; text: string }
  | ({ type: "tool" } & ToolCall)

export type Skill = {
  name: string
  description: string
  source: "built-in" | "custom"
}

export type SkillDetail = Skill & {
  body: string
  files: string[]
}

export type McpServer = {
  name: string
  command: string[]
  url: string
  env: Record<string, string>
  headers: Record<string, string>
  enabled: string[]
  connected: boolean
  error: string
  available: string[]
}

export type McpStatus = { servers: McpServer[] }

export type AppConfig = {
  models: string[]
  default_model: string
  efforts: string[]
  default_effort: string
  agent_configured: boolean
}

export type StreamEvent =
  | { type: "thinking"; text: string }
  | { type: "content"; text: string }
  | { type: "tool_start"; id: string; name: string }
  | { type: "tool_call"; id: string; name: string; args: Record<string, unknown> }
  | { type: "tool_output"; id: string; chunk: string }
  | { type: "tool_result"; id: string; name: string; output: string; ok: boolean; ms: number }
  | { type: "done"; user: Message; assistant: Message }
  | { type: "error"; detail: string; saved?: boolean }

export const FIELDS = ["target", "info", "instructions", "description", "scope"] as const
export type FieldName = (typeof FIELDS)[number]

async function errorText(res: Response) {
  try {
    const body = await res.json()
    if (Array.isArray(body?.detail)) return body.detail.map((d: { msg?: string }) => d.msg).join(", ")
    if (typeof body?.detail === "string" && body.detail) return body.detail
  } catch {
    // not json
  }
  return `request failed (${res.status})`
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init)
  if (!res.ok) throw new Error(await errorText(res))
  return res.json() as Promise<T>
}

function parseSse(chunk: string): StreamEvent | null {
  let name = ""
  const data: string[] = []
  for (const line of chunk.split("\n")) {
    if (line.startsWith("event: ")) name = line.slice(7).trim()
    else if (line.startsWith("data: ")) data.push(line.slice(6))
  }
  if (!name || data.length === 0) return null
  try {
    return { type: name, ...JSON.parse(data.join("\n")) } as StreamEvent
  } catch {
    return null
  }
}

async function streamMessage(
  id: number,
  content: string,
  files: File[],
  onEvent: (event: StreamEvent) => void,
  signal?: AbortSignal
) {
  const form = new FormData()
  form.set("content", content)
  for (const file of files) form.append("files", file)

  const res = await fetch(`/api/campaigns/${id}/messages`, {
    method: "POST",
    body: form,
    signal,
  })
  if (!res.ok || !res.body) throw new Error(await errorText(res))

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ""

  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    let cut: number
    while ((cut = buffer.indexOf("\n\n")) !== -1) {
      const event = parseSse(buffer.slice(0, cut))
      buffer = buffer.slice(cut + 2)
      if (event) onEvent(event)
    }
  }
}

export const api = {
  config: () => req<AppConfig>("/api/config"),
  listCampaigns: () => req<Campaign[]>("/api/campaigns"),
  getCampaign: (id: number) => req<Campaign>(`/api/campaigns/${id}`),
  createCampaign: (form: FormData) =>
    req<Campaign>("/api/campaigns", { method: "POST", body: form }),
  deleteCampaign: (id: number) =>
    req<{ deleted: number }>(`/api/campaigns/${id}`, { method: "DELETE" }),
  stopCampaign: (id: number) =>
    req<{ stopped: boolean }>(`/api/campaigns/${id}/stop`, { method: "POST" }),
  liveCampaign: (id: number) =>
    req<{ running: boolean; parts: Part[]; pending: string | null; seq: number }>(
      `/api/campaigns/${id}/live`
    ),
  steerCampaign: (id: number, content: string, files: File[]) => {
    const form = new FormData()
    form.set("content", content)
    for (const file of files) form.append("files", file)
    return req<Message>(`/api/campaigns/${id}/steer`, { method: "POST", body: form })
  },
  resumeCampaign: (id: number) =>
    req<{ status: string; campaign_id: number }>(`/api/campaigns/${id}/resume`, {
      method: "POST",
    }),
  listSkills: () => req<Skill[]>("/api/skills"),
  getSkill: (name: string) => req<SkillDetail>(`/api/skills/${encodeURIComponent(name)}`),
  createSkill: (skill: { name: string; description: string; body: string }) =>
    req<{ name: string }>("/api/skills", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(skill),
    }),
  updateSkill: (name: string, skill: { description: string; body: string }) =>
    req<{ name: string }>(`/api/skills/${encodeURIComponent(name)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(skill),
    }),
  mcp: () => req<McpStatus>("/api/mcp"),
  saveMcp: (servers: McpServer[]) =>
    req<McpStatus>("/api/mcp", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(servers),
    }),
  listMessages: (id: number) => req<Message[]>(`/api/campaigns/${id}/messages`),
  listMessagesPage: async (
    id: number,
    options: { limit?: number; after?: number; before?: number } = {}
  ): Promise<{ messages: Message[]; hasMore: boolean }> => {
    const params = new URLSearchParams()
    if (options.limit) params.set("limit", String(options.limit))
    if (options.after) params.set("after", String(options.after))
    if (options.before) params.set("before", String(options.before))
    const query = params.toString()
    const res = await fetch(`/api/campaigns/${id}/messages${query ? `?${query}` : ""}`)
    if (!res.ok) throw new Error(await errorText(res))
    return {
      messages: (await res.json()) as Message[],
      hasMore: res.headers.get("x-has-more") === "1",
    }
  },
  streamMessage,
  attachmentUrl: (id: number) => `/api/attachments/${id}`,
}
