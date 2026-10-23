import { useEffect, useRef, useState, type KeyboardEvent } from "react"
import { Link, useNavigate, useParams } from "react-router-dom"
import { ArrowLeft, Send, Trash2 } from "lucide-react"

import { StatusBadge } from "@/components/StatusBadge"
import { Markdown } from "@/components/Markdown"
import { Pulse } from "@/components/Pulse"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import {
  api,
  type Attachment,
  type Campaign as CampaignData,
  type Message,
  type Part,
  type ToolCall,
} from "@/lib/api"
import { cn, fmtDate } from "@/lib/utils"

const SECTIONS = [
  { key: "info", label: "Info" },
  { key: "instructions", label: "Instructions" },
  { key: "description", label: "Description" },
  { key: "scope", label: "Scope" },
] as const

type LiveReply = { parts: Part[]; pendingTool: string | null }

export function Campaign() {
  const { id } = useParams()
  const nav = useNavigate()
  const campaignId = Number(id)

  const [campaign, setCampaign] = useState<CampaignData | null>(null)
  const [messages, setMessages] = useState<Message[]>([])
  const [stream, setStream] = useState<LiveReply | null>(null)
  const [configured, setConfigured] = useState(true)
  const [input, setInput] = useState("")
  const [pending, setPending] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [error, setError] = useState("")
  const endRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!campaignId) return
    Promise.all([api.getCampaign(campaignId), api.listMessages(campaignId), api.config()])
      .then(([c, m, cfg]) => {
        setCampaign(c)
        setMessages(m)
        setConfigured(cfg.agent_configured)
      })
      .catch((e: Error) => setError(e.message))
  }, [campaignId])

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" })
  }, [messages, stream, pending])

  async function send() {
    const content = input.trim()
    if (!content || pending) return

    setInput("")
    setError("")
    setPending(true)
    setStream({ parts: [], pendingTool: null })

    const optimistic: Message = {
      id: -Date.now(),
      role: "user",
      content,
      reasoning: "",
      created_at: "",
    }
    setMessages((prev) => [...prev, optimistic])

    const pushPart = (part: Part) =>
      setStream((s) => {
        if (!s) return s
        const last = s.parts[s.parts.length - 1]

        if (last?.type === "thinking" && part.type === "thinking") {
          return { ...s, parts: [...s.parts.slice(0, -1), { ...last, text: last.text + part.text }] }
        }
        if (last?.type === "content" && part.type === "content") {
          return { ...s, parts: [...s.parts.slice(0, -1), { ...last, text: last.text + part.text }] }
        }
        return { ...s, parts: [...s.parts, part] }
      })

    let failure = ""
    let saved = false
    let completed = false

    try {
      await api.streamMessage(campaignId, content, (event) => {
        if (event.type === "thinking") {
          pushPart({ type: "thinking", text: event.text })
        } else if (event.type === "content") {
          pushPart({ type: "content", text: event.text })
        } else if (event.type === "tool_start") {
          setStream((s) => (s ? { ...s, pendingTool: event.name } : s))
        } else if (event.type === "tool_call") {
          pushPart({ type: "tool", id: event.id, name: event.name, args: event.args })
          setStream((s) => (s ? { ...s, pendingTool: null } : s))
        } else if (event.type === "tool_result") {
          setStream((s) =>
            s
              ? {
                  ...s,
                  parts: s.parts.map((part) =>
                    part.type === "tool" && part.id === event.id
                      ? { ...part, output: event.output, ok: event.ok, ms: event.ms }
                      : part
                  ),
                }
              : s
          )
        } else if (event.type === "done") {
          completed = true
          setMessages((prev) => [
            ...prev.filter((m) => m.id !== optimistic.id),
            event.user,
            event.assistant,
          ])
        } else if (event.type === "error") {
          failure = event.detail
          saved = event.saved ?? false
        }
      })
    } catch (e) {
      failure = (e as Error).message
    }

    if (!completed && !failure) failure = "connection lost before the reply finished"

    if (failure) {
      if (saved) {
        try {
          setMessages(await api.listMessages(campaignId))
        } catch {
          // keep the optimistic message if the refresh fails
        }
      } else {
        setMessages((prev) => prev.filter((m) => m.id !== optimistic.id))
        setInput(content)
      }
      setError(failure)
    }

    setStream(null)
    setPending(false)
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault()
      send()
    }
  }

  async function remove() {
    if (!campaign) return
    if (!confirm(`Delete "${campaign.target.split("\n")[0]}"? Messages and files are removed too.`)) return

    setDeleting(true)
    setError("")
    try {
      await api.deleteCampaign(campaignId)
      nav("/")
    } catch (e) {
      setError((e as Error).message)
      setDeleting(false)
    }
  }

  if (!campaign) {
    return (
      <div className="mx-auto max-w-3xl px-8 py-10 text-sm text-muted-foreground">
        {error || "Loading…"}
      </div>
    )
  }

  const attachments = campaign.attachments ?? []
  const targetFiles = attachments.filter((a) => a.field === "target")

  return (
    <div className="mx-auto flex h-svh max-w-3xl flex-col px-8 py-10">
      <Link
        to="/"
        className="flex w-fit items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="size-3.5" />
        Campaigns
      </Link>

      <header className="mt-4 flex items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="truncate font-mono text-base" title={campaign.target}>
            {campaign.target}
          </h1>
          <p className="mt-1 text-xs text-muted-foreground">
            {campaign.model} · effort {campaign.effort} · created {fmtDate(campaign.created_at)}
          </p>
          {targetFiles.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-2">
              {targetFiles.map((a) => (
                <AttachmentChip key={a.id} attachment={a} />
              ))}
            </div>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <StatusBadge status={campaign.status} />
          <Button
            variant="ghost"
            size="icon"
            className="size-8 text-muted-foreground hover:text-destructive"
            title="Delete campaign"
            disabled={deleting || pending}
            onClick={remove}
          >
            <Trash2 className="size-4" />
          </Button>
        </div>
      </header>

      <div className="mt-6 grid gap-3">
        {SECTIONS.map(({ key, label }) => {
          const value = campaign[key].trim()
          const files = attachments.filter((a) => a.field === key)
          if (!value && files.length === 0) return null
          return (
            <div key={key} className="rounded-md border bg-muted/30 px-3 py-2.5 text-sm">
              <div className="text-xs font-medium text-muted-foreground">{label}</div>
              {value && <p className="mt-1 whitespace-pre-wrap">{value}</p>}
              {files.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-2">
                  {files.map((a) => (
                    <AttachmentChip key={a.id} attachment={a} />
                  ))}
                </div>
              )}
            </div>
          )
        })}
      </div>

      <div className="mt-6 flex min-h-0 flex-1 flex-col rounded-lg border">
        <div className="flex-1 space-y-4 overflow-y-auto p-4">
          {messages.length === 0 && !stream && (
            <p className="text-sm text-muted-foreground">
              Ask the agent to run something against this target.
            </p>
          )}
          {messages.map((m) =>
            m.role === "user" ? (
              <div key={m.id} className="flex justify-end">
                <div className="max-w-[80%] whitespace-pre-wrap rounded-lg bg-muted px-3 py-2 text-sm">
                  {m.content}
                </div>
              </div>
            ) : (
              <AssistantMessage key={m.id} message={m} />
            )
          )}
          {stream && (
            <div className="max-w-[85%]">
              <Parts parts={stream.parts} live />
            </div>
          )}
          {pending && stream && (
            <p className="flex items-center gap-2 text-sm text-muted-foreground">
              <Pulse />
              {activity(stream)}
            </p>
          )}
          <div ref={endRef} />
        </div>

        <div className="border-t p-3">
          {error && <p className="mb-2 text-sm text-destructive">{error}</p>}
          {!configured && (
            <p className="mb-2 text-sm text-muted-foreground">
              DEEPSEEK_API_KEY is not set. Add it to .env and restart.
            </p>
          )}
          <div className="flex items-end gap-2">
            <Textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={onKeyDown}
              rows={2}
              placeholder={configured ? "Message the agent…" : "Chat disabled"}
              disabled={!configured || pending}
              className="min-h-0 resize-none"
            />
            <Button size="icon" onClick={send} disabled={!configured || pending || !input.trim()}>
              <Send />
            </Button>
          </div>
        </div>
      </div>
    </div>
  )
}

function AssistantMessage({ message }: { message: Message }) {
  return (
    <div className="max-w-[85%]">
      <Parts parts={partsOf(message)} />
    </div>
  )
}

function Parts({ parts, live }: { parts: Part[]; live?: boolean }) {
  if (parts.length === 0) {
    return live ? <p className="text-sm text-muted-foreground">Thinking…</p> : null
  }

  return (
    <>
      {parts.map((part, index) =>
        part.type === "thinking" ? (
          <ThinkingBlock key={index} text={part.text} live={live} />
        ) : part.type === "tool" ? (
          <ToolCallBlock key={part.id || index} call={part} />
        ) : (
          <Markdown key={index} text={part.text} />
        )
      )}
    </>
  )
}

function partsOf(message: Message): Part[] {
  if (message.parts) {
    try {
      const parts = JSON.parse(message.parts) as Part[]
      if (parts.length > 0) return parts
    } catch {
      // fall back to the old columns
    }
  }

  const parts: Part[] = []
  if (message.reasoning) parts.push({ type: "thinking", text: message.reasoning })
  for (const call of callsOf(message)) parts.push({ type: "tool", ...call })
  if (message.content) parts.push({ type: "content", text: message.content })
  return parts
}

function callsOf(message: Message): ToolCall[] {
  if (!message.tool_calls) return []
  try {
    return JSON.parse(message.tool_calls) as ToolCall[]
  } catch {
    return []
  }
}

function activity(stream: LiveReply): string {
  if (stream.pendingTool) return `Calling ${stream.pendingTool}`
  const last = stream.parts[stream.parts.length - 1]
  if (last?.type === "tool" && last.output == null) return `Running ${last.name}`
  if (last?.type === "content") return "Writing"
  return "Thinking"
}

function ToolCallBlock({ call }: { call: ToolCall }) {
  return (
    <details className="mb-2 overflow-hidden rounded-md border bg-muted/30 text-xs">
      <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2">
        {call.output == null ? (
          <Pulse />
        ) : (
          <span
            className={cn("size-1.5 shrink-0 rounded-full", call.ok ? "bg-emerald-500" : "bg-destructive")}
          />
        )}
        <span className="font-mono">{call.name}</span>
        <span className="min-w-0 flex-1 truncate text-muted-foreground">
          {JSON.stringify(call.args)}
        </span>
        {call.ms != null && <span className="shrink-0 text-muted-foreground">{call.ms}ms</span>}
      </summary>
      <pre className="max-h-64 overflow-auto whitespace-pre-wrap border-t bg-background/60 px-3 py-2 font-mono leading-relaxed">
        {call.output || "running…"}
      </pre>
    </details>
  )
}

function ThinkingBlock({ text, live }: { text: string; live?: boolean }) {
  const box = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const el = box.current
    if (el && live) el.scrollTop = el.scrollHeight
  }, [text, live])

  return (
    <div className="mb-2 rounded-md border bg-muted/40 px-3 py-2">
      <div className="text-xs font-medium text-muted-foreground">Thinking</div>
      <div
        ref={box}
        className="mt-1 max-h-72 overflow-y-auto whitespace-pre-wrap break-words text-xs leading-relaxed text-muted-foreground"
      >
        {text}
      </div>
    </div>
  )
}

function AttachmentChip({ attachment }: { attachment: Attachment }) {
  return (
    <a
      href={api.attachmentUrl(attachment.id)}
      className="rounded border bg-background px-2 py-0.5 text-xs hover:bg-accent"
    >
      {attachment.filename}
    </a>
  )
}
