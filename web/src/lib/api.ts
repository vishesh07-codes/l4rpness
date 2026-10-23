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
  status: string
  created_at: string
  updated_at: string
  message_count?: number
  attachments?: Attachment[]
}

export type Message = {
  id: number
  role: "user" | "assistant"
  content: string
  created_at: string
}

export type AppConfig = {
  models: string[]
  default_model: string
  agent_configured: boolean
}

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

export const api = {
  config: () => req<AppConfig>("/api/config"),
  listCampaigns: () => req<Campaign[]>("/api/campaigns"),
  getCampaign: (id: number) => req<Campaign>(`/api/campaigns/${id}`),
  createCampaign: (form: FormData) =>
    req<Campaign>("/api/campaigns", { method: "POST", body: form }),
  listMessages: (id: number) => req<Message[]>(`/api/campaigns/${id}/messages`),
  sendMessage: (id: number, content: string) =>
    req<{ user: Message; assistant: Message }>(`/api/campaigns/${id}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    }),
  attachmentUrl: (id: number) => `/api/attachments/${id}`,
}
