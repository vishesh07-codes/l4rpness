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
  mode?: "continuous" | "goal"
  status: string
  created_at: string
  updated_at: string
  message_count?: number
  running?: boolean
  attachments?: Attachment[]
  usage_in?: number
  usage_out?: number
  last_prompt_tokens?: number
  context_window?: number
  context_window_source?: "api" | "table" | "fallback"
  kernel_enabled?: number
  kernel_root_access?: number
  kernel_root_flag?: number
}

export type CampaignVm = {
  enabled: boolean
  state?: string
  pid?: number
  uptime_s?: number
  restarts?: number
  root_access?: boolean
  root_flag?: boolean
  accel?: string
  clone_bytes?: number
  summary?: string
  console?: string
  error?: string
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

export type GeneratorInfo = {
  id: number
  campaign_id: number
  focus: string
  status: string
  hypothesis_count: number
  last_snippet?: string
  created_at: string
  updated_at: string
}

export type GeneratorMessage = {
  id: number
  generator_id: number
  content: string
  parts: Part[]
  created_at: string
}

export type WorkerInfo = {
  id: number
  campaign_id: number
  title: string
  note: string
  status: string
  last_prompt_tokens: number
  hypothesis_count: number
  result_count: number
  last_snippet?: string
  created_at: string
  updated_at: string
}

export type WorkerMessage = {
  id: number
  worker_id: number
  role: string
  content: string
  parts: Part[]
  created_at: string
}

export type Hunt = {
  id: number
  campaign_id: number
  worker_id: number
  hypothesis_id?: number | null
  verdict: string
  severity: string
  impact: string
  poc_path: string
  repro: string
  evidence: string
  created_at: string
}

export type Hypothesis = {
  id: number
  campaign_id: number
  generator_id?: number | null
  worker_id?: number | null
  title: string
  angle: string
  vuln_class: string
  rationale: string
  result: string
  status: string
  attempts: number
  created_at: string
  updated_at: string
}

export type Skill = {
  name: string
  description: string
  source: "built-in" | "custom"
}

export type SkillDetail = Skill & {
  body: string
  files: string[]
}

export type WorkFile = {
  path: string
  name: string
  type: "file" | "dir"
  size: number
}

export type WorkFileContent = {
  path: string
  content: string
  truncated: boolean
  size: number
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

export type ProviderKeyStatus = {
  provider: string
  env_var: string
  configured: boolean
  source: "settings" | "environment" | "missing"
  length: number
  suffix: string | null
}

export type RuntimeSection = { id: string; title: string }

export type RuntimeField = {
  key: string
  section: string
  label: string
  hint?: string
  kind: "text" | "select" | "list" | "int" | "number"
  min?: number
  max?: number
  step?: number
  options?: string[]
}

export type RuntimeConfig = {
  sections: RuntimeSection[]
  fields: RuntimeField[]
  values: Record<string, unknown>
  sources: Record<string, "settings" | "file">
}

export type HostEnvVar = { present: boolean; length: number; suffix: string | null }

export type HostEnvState = {
  mounted: boolean
  readable: boolean
  writable: boolean
  changed: boolean
  vars: Record<string, HostEnvVar>
  last_applied: { vars: string[]; at: number | null }
}

export type HostPushResult = {
  written: string[]
  providers: ProviderKeyStatus[]
  host: HostEnvState
}

export type HostReloadResult = {
  changed: string[]
  providers: ProviderKeyStatus[]
  host: HostEnvState
}

export type HostConfigState = {
  mounted: boolean
  readable: boolean
  writable: boolean
  changed: boolean
  keys: Record<string, unknown>
  last_applied: { keys: string[]; at: number | null }
}

export type HostConfigPushResult = {
  written: string[]
  runtime: RuntimeConfig
  host: HostConfigState
}

export type HostConfigReloadResult = {
  changed: string[]
  runtime: RuntimeConfig
  host: HostConfigState
}

export type Endpoint = {
  name: string
  base_url: string
  api_key_env: string
  thinking: boolean
  default: boolean
  key_configured: boolean
}

export type EndpointsState = {
  endpoints: Endpoint[]
  mounted: boolean
  writable: boolean
}

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
  // some endpoints (e.g. DELETE skill) reply 204 with an empty body; parsing
  // that as JSON throws "unexpected end of data" even though the call succeeded
  if (res.status === 204) return undefined as T
  const text = await res.text()
  return (text ? JSON.parse(text) : undefined) as T
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
  updateCampaignRuntime: (id: number, values: { model?: string; effort?: string }) =>
    req<Campaign>(`/api/campaigns/${id}/runtime`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(values),
    }),
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
  deleteSkill: (name: string) =>
    req<void>(`/api/skills/${encodeURIComponent(name)}`, { method: "DELETE" }),
  settingsProviders: () => req<ProviderKeyStatus[]>("/api/settings/providers"),
  saveProviderKeys: (values: Record<string, string>) =>
    req<ProviderKeyStatus[]>("/api/settings/providers", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ values }),
    }),
  settingsConfig: () => req<RuntimeConfig>("/api/settings/config"),
  saveRuntimeConfig: (values: Record<string, string>) =>
    req<RuntimeConfig>("/api/settings/config", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ values }),
    }),
  resetRuntimeConfig: () =>
    req<RuntimeConfig>("/api/settings/config", { method: "DELETE" }),
  hostEnv: () => req<HostEnvState>("/api/settings/host"),
  pushHostEnv: (values: Record<string, string>) =>
    req<HostPushResult>("/api/settings/host/push", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ values }),
    }),
  reloadHostEnv: () =>
    req<HostReloadResult>("/api/settings/host/reload", { method: "POST" }),
  hostConfig: () => req<HostConfigState>("/api/settings/config/host"),
  pushHostConfig: (values: Record<string, string>) =>
    req<HostConfigPushResult>("/api/settings/config/host/push", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ values }),
    }),
  reloadHostConfig: () =>
    req<HostConfigReloadResult>("/api/settings/config/host/reload", { method: "POST" }),
  settingsEndpoints: () => req<EndpointsState>("/api/settings/endpoints"),
  saveEndpoint: (endpoint: {
    name: string
    base_url: string
    api_key_env: string
    thinking: boolean
  }) =>
    req<EndpointsState>("/api/settings/endpoints", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(endpoint),
    }),
  deleteEndpoint: (name: string) =>
    req<EndpointsState>(`/api/settings/endpoints/${encodeURIComponent(name)}`, {
      method: "DELETE",
    }),
  mcp: () => req<McpStatus>("/api/mcp"),
  saveMcp: (servers: McpServer[]) =>
    req<McpStatus>("/api/mcp", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(servers),
    }),
  listMessages: (id: number) => req<Message[]>(`/api/campaigns/${id}/messages`),
  listGenerators: (id: number) => req<GeneratorInfo[]>(`/api/campaigns/${id}/generators`),
  getGeneratorMessages: async (
    generatorId: number,
    options: { limit?: number; after?: number; before?: number } = {}
  ): Promise<{ messages: GeneratorMessage[]; hasMore: boolean }> => {
    const params = new URLSearchParams()
    if (options.limit) params.set("limit", String(options.limit))
    if (options.after) params.set("after", String(options.after))
    if (options.before) params.set("before", String(options.before))
    const query = params.toString()
    const res = await fetch(`/api/generators/${generatorId}/messages${query ? `?${query}` : ""}`)
    if (!res.ok) throw new Error(await errorText(res))
    return {
      messages: (await res.json()) as GeneratorMessage[],
      hasMore: res.headers.get("x-has-more") === "1",
    }
  },
  listHypotheses: (id: number) => req<Hypothesis[]>(`/api/campaigns/${id}/hypotheses`),
  getCampaignVm: (id: number, lines = 40) =>
    req<CampaignVm>(`/api/campaigns/${id}/vm?lines=${lines}`),
  listWorkers: (id: number) => req<WorkerInfo[]>(`/api/campaigns/${id}/workers`),
  listHunts: (id: number) => req<Hunt[]>(`/api/campaigns/${id}/hunts`),
  getWorkerMessages: async (
    workerId: number,
    options: { limit?: number; after?: number; before?: number } = {}
  ): Promise<{ messages: WorkerMessage[]; hasMore: boolean }> => {
    const params = new URLSearchParams()
    if (options.limit) params.set("limit", String(options.limit))
    if (options.after) params.set("after", String(options.after))
    if (options.before) params.set("before", String(options.before))
    const query = params.toString()
    const res = await fetch(`/api/workers/${workerId}/messages${query ? `?${query}` : ""}`)
    if (!res.ok) throw new Error(await errorText(res))
    return {
      messages: (await res.json()) as WorkerMessage[],
      hasMore: res.headers.get("x-has-more") === "1",
    }
  },
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
  listWorkFiles: (campaignId: number) =>
    req<{ files: WorkFile[] }>(`/api/campaigns/${campaignId}/files`).then((r) => r.files),
  readWorkFile: (campaignId: number, path: string) =>
    req<WorkFileContent>(
      `/api/campaigns/${campaignId}/file?path=${encodeURIComponent(path)}`
    ),
  workFileRawUrl: (campaignId: number, path: string) =>
    `/api/campaigns/${campaignId}/file/raw?path=${encodeURIComponent(path)}`,
}
