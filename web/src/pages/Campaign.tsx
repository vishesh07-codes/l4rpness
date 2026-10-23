import { useEffect, useRef, useState } from "react"
import { Link, useNavigate, useParams } from "react-router-dom"
import {
  ArrowDown,
  ArrowLeft,
  ChevronDown,
  FileText,
  Paperclip,
  RefreshCw,
  Sparkles,
  Trash2,
  TriangleAlert,
} from "lucide-react"

import { ThinkingShimmer } from "@/components/agents/loading-states/thinking-shimmer"
import { MessageScroller } from "@/components/agents/message-scroller"
import { PromptInput } from "@/components/agents/prompt-input"
import { StatusBadge } from "@/components/StatusBadge"
import { AssistantMessage, MessageRow, UserMessage } from "@/components/chat/Message"
import LoadingState from "@/components/primitives/LoadingState"
import { Badge } from "@/components/ui/badge"
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

const SUGGESTIONS = [
  "Recon this target",
  "What would you probe first?",
  "Draft a testing plan",
]

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
  const [following, setFollowing] = useState(true)
  const initialIds = useRef<Set<number> | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  const viewportRef = useRef<HTMLElement | null>(null)

  useEffect(() => {
    if (!campaignId) return
    Promise.all([api.getCampaign(campaignId), api.listMessages(campaignId), api.config()])
      .then(([c, m, cfg]) => {
        setCampaign(c)
        setMessages(m)
        setConfigured(cfg.agent_configured)
        initialIds.current = new Set(m.map((message) => message.id))
      })
      .catch((e: Error) => setError(e.message))
  }, [campaignId])

  function jumpToLatest() {
    const viewport = viewportRef.current
    viewport?.scrollTo({ top: viewport.scrollHeight, behavior: "smooth" })
  }

  async function send() {
    const content = input.trim()
    if (!content || pending) return

    setInput("")
    setError("")
    setPending(true)
    setStream({ parts: [], pendingTool: null })

    const controller = new AbortController()
    abortRef.current = controller

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
    let stopped = false

    try {
      await api.streamMessage(
        campaignId,
        content,
        (event) => {
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
            // swap the live stream for the saved message in one render, otherwise
            // the reply (reasoning included) paints twice for a frame
            setMessages((prev) => [
              ...prev.filter((m) => m.id !== optimistic.id),
              event.user,
              event.assistant,
            ])
            setStream(null)
          } else {
            failure = event.detail
            saved = event.saved ?? false
          }
        },
        controller.signal
      )
    } catch (e) {
      if ((e as Error).name === "AbortError") stopped = true
      else failure = (e as Error).message
    }

    if (!completed && !failure && !stopped) {
      failure = "connection lost before the reply finished"
    }

    if (stopped || failure) {
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
      if (failure) setError(failure)
    }

    abortRef.current = null
    setStream(null)
    setPending(false)
  }

  function stop() {
    abortRef.current?.abort()
  }

  async function remove() {
    if (!campaign) return
    if (!confirm(`Delete "${campaign.target.split("\n")[0]}"? Messages and files are removed too.`)) {
      return
    }

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
      <div className="mx-auto flex h-svh max-w-3xl items-center px-6 py-6 text-sm text-muted-foreground">
        {error || <LoadingState label="Loading campaign" />}
      </div>
    )
  }

  const attachments = campaign.attachments ?? []
  const targetFiles = attachments.filter((a) => a.field === "target")

  return (
    <div className="mx-auto flex h-svh max-w-3xl flex-col gap-4 px-6 py-6">
      <div className="flex flex-col gap-3">
        <Link
          to="/"
          className="flex w-fit items-center gap-1 text-[13px] text-muted-foreground transition-colors hover:text-foreground"
        >
          <ArrowLeft className="size-3.5" strokeWidth={1.5} />
          Campaigns
        </Link>

        <header className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <h1 className="truncate font-mono text-sm font-medium" title={campaign.target}>
              {campaign.target.split("\n")[0]}
            </h1>
            <div className="mt-2 flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
              <Badge variant="secondary">{campaign.model}</Badge>
              <Badge variant="secondary">effort · {campaign.effort}</Badge>
              <span className="px-0.5">created {fmtDate(campaign.created_at)}</span>
            </div>
            {targetFiles.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-2">
                {targetFiles.map((a) => (
                  <AttachmentChip key={a.id} attachment={a} />
                ))}
              </div>
            )}
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <span className="flex items-center gap-1.5 text-[11px] text-ink-3">
              <span
                className={cn("size-1.5 rounded-full", configured ? "bg-green" : "bg-ink-3")}
              />
              {configured ? "ready" : "offline"}
            </span>
            <StatusBadge status={campaign.status} />
            <button
              type="button"
              title="Delete campaign"
              onClick={remove}
              disabled={deleting || pending}
              className="relative flex size-7 items-center justify-center rounded-md text-ink-3 outline-none transition-colors after:absolute after:top-1/2 after:left-1/2 after:size-9 after:-translate-1/2 hover:bg-hover hover:text-destructive focus-visible:ring-2 focus-visible:ring-ring/60 disabled:opacity-50"
            >
              <Trash2 className="size-3.5" strokeWidth={1.5} />
            </button>
          </div>
        </header>

        <ContextPanel
          campaign={campaign}
          attachments={attachments}
          defaultOpen={messages.length === 0 && !stream}
        />
      </div>

      <section className="relative flex min-h-0 flex-1 flex-col">
        <MessageScroller
          className="min-h-0 flex-1"
          viewportClassName="pb-4"
          contentClassName={cn(
            "flex min-h-full flex-col gap-4",
            messages.length === 0 && !stream && "justify-center"
          )}
          label="Conversation"
          busy={pending}
          navigation={messages.length > 4 ? "rail" : undefined}
          viewportRef={viewportRef}
          onFollowChange={setFollowing}
        >
          {messages.length === 0 && !stream && (
            <EmptyState configured={configured} onPick={setInput} />
          )}

          {messages.map((m) =>
            m.role === "user" ? (
              <MessageRow key={m.id} role="user">
                <UserMessage content={m.content} animate={!initialIds.current?.has(m.id)} />
              </MessageRow>
            ) : (
              <MessageRow key={m.id} role="assistant">
                <AssistantMessage parts={partsOf(m)} />
              </MessageRow>
            )
          )}

          {stream && (
            <MessageRow role="assistant">
              <div className="flex w-full flex-col gap-3">
                {stream.pendingTool && (
                  <div className="text-[13px]">
                    <ThinkingShimmer>
                      {stream.pendingTool === "write_todos"
                        ? "Planning…"
                        : `Calling ${stream.pendingTool}…`}
                    </ThinkingShimmer>
                  </div>
                )}
                <AssistantMessage parts={stream.parts} live />
              </div>
            </MessageRow>
          )}
        </MessageScroller>

        {!following && messages.length > 0 && (
          <button
            type="button"
            onClick={jumpToLatest}
            className="absolute bottom-24 left-1/2 flex -translate-x-1/2 items-center gap-1.5 rounded-full border border-line bg-surface px-3 py-1.5 text-[12px] font-medium text-ink-2 shadow-raised transition-colors hover:text-ink"
          >
            <ArrowDown className="size-3.5" strokeWidth={1.5} />
            Jump to latest
          </button>
        )}

        {error && (
          <div className="mb-3 flex items-center gap-2 rounded-[10px] border border-destructive/30 bg-destructive/5 px-3 py-2 text-[12px] text-destructive">
            <TriangleAlert className="size-3.5 shrink-0" strokeWidth={1.5} />
            <span className="min-w-0 flex-1">{error}</span>
            <button
              type="button"
              onClick={() => send()}
              disabled={pending}
              className="flex shrink-0 items-center gap-1 rounded-md border border-line px-2 py-0.5 font-medium text-ink-2 transition-colors hover:bg-hover hover:text-ink disabled:opacity-50"
            >
              <RefreshCw className="size-3" strokeWidth={1.5} />
              Retry
            </button>
          </div>
        )}

        <div className="pt-1">
          <PromptInput
            value={input}
            onValueChange={setInput}
            onSubmit={send}
            loading={pending}
            onStop={stop}
            disabled={!configured}
            placeholder={configured ? "Message the agent…" : "Chat disabled"}
            minRows={1}
            maxRows={8}
          />
        </div>
      </section>
    </div>
  )
}

function partsOf(message: Message): Part[] {
  if (message.parts) {
    try {
      const parts = JSON.parse(message.parts) as Part[]
      if (parts.length > 0) return parts
    } catch {
      // fall back to the legacy columns
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

function EmptyState({
  configured,
  onPick,
}: {
  configured: boolean
  onPick: (value: string) => void
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-4 px-6 text-center">
      <div className="flex size-11 items-center justify-center rounded-full bg-accent-tint text-accent-ink">
        <Sparkles className="size-5" />
      </div>
      <div>
        <p className="text-[13px] font-medium text-balance">Ask about this target</p>
        <p className="mt-1 text-xs text-pretty text-ink-3">
          {configured
            ? "Ask the agent to run something. It has bash and file tools in a per-campaign work dir."
            : "DEEPSEEK_API_KEY is not set. Add it to .env and restart."}
        </p>
      </div>
      {configured && (
        <div className="flex flex-wrap justify-center gap-2">
          {SUGGESTIONS.map((suggestion) => (
            <button
              key={suggestion}
              type="button"
              onClick={() => onPick(suggestion)}
              className="rounded-full border border-line bg-surface px-3 py-1 text-[12px] text-ink-2 outline-none transition-[color,background-color,scale] duration-150 ease-out hover:bg-hover hover:text-ink focus-visible:ring-2 focus-visible:ring-ring/60 active:scale-[0.96]"
            >
              {suggestion}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

function ContextPanel({
  campaign,
  attachments,
  defaultOpen,
}: {
  campaign: CampaignData
  attachments: Attachment[]
  defaultOpen: boolean
}) {
  const [open, setOpen] = useState(defaultOpen)

  const sections = SECTIONS.map(({ key, label }) => {
    const value = campaign[key].trim()
    const files = attachments.filter((a) => a.field === key)
    return { key, label, value, files }
  }).filter((section) => section.value || section.files.length > 0)

  if (sections.length === 0) return null

  return (
    <div className="rounded-[12px] bg-surface shadow-hairline">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        className="flex w-full items-center gap-2 rounded-[12px] px-3.5 py-2.5 text-left transition-colors hover:bg-hover"
      >
        <FileText className="size-3.5 shrink-0 text-ink-3" />
        <span className="text-[13px] font-medium">Brief</span>
        <span className="text-[12px] text-ink-3">
          {sections.length} {sections.length === 1 ? "section" : "sections"}
        </span>
        <ChevronDown
          className={cn(
            "ml-auto size-4 text-ink-3 transition-transform duration-300",
            open && "rotate-180"
          )}
        />
      </button>

      <div
        className="grid transition-[grid-template-rows,opacity] duration-300 ease-[cubic-bezier(0.23,1,0.32,1)]"
        style={{ gridTemplateRows: open ? "1fr" : "0fr", opacity: open ? 1 : 0 }}
      >
        <div className="overflow-hidden">
          <div className="grid max-h-[40vh] gap-3 overflow-y-auto border-t border-line px-3.5 py-3 [scrollbar-color:var(--line-strong)_transparent] [scrollbar-width:thin] sm:grid-cols-2">
            {sections.map((section) => (
              <div key={section.key} className="rounded-[10px] bg-inset px-3 py-2.5">
                <div className="text-[11px] font-medium tracking-wide text-ink-3 uppercase">
                  {section.label}
                </div>
                {section.value && (
                  <p className="mt-1 text-[13px] leading-relaxed whitespace-pre-wrap text-ink-2">
                    {section.value}
                  </p>
                )}
                {section.files.length > 0 && (
                  <div className="mt-2 flex flex-wrap gap-2">
                    {section.files.map((a) => (
                      <AttachmentChip key={a.id} attachment={a} />
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}

function AttachmentChip({ attachment }: { attachment: Attachment }) {
  return (
    <a
      href={api.attachmentUrl(attachment.id)}
      className="inline-flex items-center gap-1 rounded-[6px] border border-line bg-surface px-2 py-0.5 font-mono text-[11px] text-ink-2 transition-colors hover:bg-hover hover:text-ink"
    >
      <Paperclip className="size-3" strokeWidth={1.5} />
      {attachment.filename}
    </a>
  )
}
