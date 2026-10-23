import { useEffect, useMemo, useRef, useState, type ClipboardEvent } from "react"
import { toast } from "sonner"
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
} from "@/components/icons"

import { AttachmentCard, AttachmentChip } from "@/components/Attachment"
import { fmtSize } from "@/lib/filetype"
import { AgentProgress } from "@/components/agents/loading-states/agent-progress"
import { ReasoningText } from "@/components/agents/loading-states/reasoning-text"
import { MessageScroller } from "@/components/agents/message-scroller"
import { PromptInput } from "@/components/agents/prompt-input"
import { TodoList } from "@/components/agents/todo-list"
import { StatusBadge } from "@/components/StatusBadge"
import { AssistantMessage, MessageRow, UserMessage } from "@/components/chat/Message"
import LoadingState from "@/components/primitives/LoadingState"
import { Alert, AlertAction, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  api,
  type Attachment,
  type Campaign as CampaignData,
  type Message,
  type Part,
  type ToolCall,
} from "@/lib/api"
import { ConfirmDialog } from "@/components/ConfirmDialog"
import { IconAction } from "@/components/IconAction"
import { latestTodos } from "@/lib/todos"
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
  const [files, setFiles] = useState<File[]>([])
  const [pending, setPending] = useState(false)
  const [stopping, setStopping] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [error, setError] = useState("")
  const [following, setFollowing] = useState(true)
  const initialIds = useRef<Set<number> | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  const viewportRef = useRef<HTMLElement | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)

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

  // Escape stops a run in flight — the composer's stop button is the visible
  // path, this is the one people reach for
  useEffect(() => {
    if (!pending) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") stop()
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [pending, stopping])

  function jumpToLatest() {
    const viewport = viewportRef.current
    viewport?.scrollTo({ top: viewport.scrollHeight, behavior: "smooth" })
  }

  useEffect(() => {
    if (!campaign?.running || pending) return

    let active = true
    const tick = async () => {
      try {
        const live = await api.liveCampaign(campaignId)
        if (!active) return
        if (live.running) {
          setStream({ parts: live.parts, pendingTool: live.pending })
        } else {
          setStream(null)
          setMessages(await api.listMessages(campaignId))
          setCampaign((c) => (c ? { ...c, running: false } : c))
        }
      } catch {
        // the next tick retries
      }
    }

    tick()
    const timer = setInterval(tick, 1500)
    return () => {
      active = false
      clearInterval(timer)
    }
  }, [campaign?.running, pending, campaignId])

  // only re-scan the full history when it changes, not on every streamed token
  const messageTodos = useMemo(
    () => latestTodos(...messages.map((m) => (m.role === "assistant" ? partsOf(m) : []))),
    [messages]
  )
  const streamTodos = latestTodos(stream?.parts ?? [])
  const todos = streamTodos.length > 0 ? streamTodos : messageTodos

  function addFiles(picked: File[]) {
    if (picked.length === 0) return
    setFiles((prev) => [...prev, ...picked].slice(0, 8))
  }

  function onPasteFiles(event: ClipboardEvent<HTMLTextAreaElement>) {
    // only fires while the composer textarea is focused
    const pasted = Array.from(event.clipboardData?.files ?? [])
    if (pasted.length === 0) {
      for (const item of Array.from(event.clipboardData?.items ?? [])) {
        if (item.kind !== "file") continue
        const file = item.getAsFile()
        if (file) pasted.push(file)
      }
    }
    if (pasted.length === 0) return
    event.preventDefault()
    addFiles(pasted)
  }

  async function send() {
    const content = input.trim()
    const sending = files
    if ((!content && sending.length === 0) || pending) return

    setInput("")
    setFiles([])
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
      localFiles: sending.map((file) => ({ filename: file.name })),
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
        sending,
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
      failure = "Connection lost before the reply finished. Retry to send it again."
    }

    if (failure && saved) {
      // the server kept a partial reply (a user stop included) — reload it
      try {
        setMessages(await api.listMessages(campaignId))
      } catch {
        // keep the optimistic message if the refresh fails
      }
    } else if (failure || stopped) {
      // nothing was saved — drop the optimistic row and give it back
      setMessages((prev) => prev.filter((m) => m.id !== optimistic.id))
      setInput(content)
      setFiles(sending)
    }

    // a user stop is not an error worth surfacing
    if (failure && failure !== "stopped by user") setError(failure)
    abortRef.current = null
    setStream(null)
    setPending(false)
    setStopping(false)
  }

  async function stop() {
    if (!pending || stopping) return
    setStopping(true)
    try {
      // ask the server to stop; it saves the partial reply and closes the
      // stream, which send() then picks up via the "saved" error event
      await api.stopCampaign(campaignId)
    } catch {
      // server unreachable — fall back to cutting the local stream
      abortRef.current?.abort()
      abortRef.current = null
      setStream(null)
      setPending(false)
      setStopping(false)
    }
  }

  async function remove() {
    if (!campaign) return

    setDeleting(true)
    try {
      await api.deleteCampaign(campaignId)
      setConfirmDelete(false)
      toast.success("Campaign deleted")
      nav("/")
    } catch (e) {
      toast.error("Could not delete the campaign", { description: (e as Error).message })
      setDeleting(false)
    }
  }

  if (!campaign) {
    return (
      <div className="mx-auto flex h-svh max-w-3xl items-center px-6 py-6 text-sm text-ink-2 max-md:pt-16">
        {error || <LoadingState label="Loading campaign" />}
      </div>
    )
  }

  const attachments = campaign.attachments ?? []
  const targetFiles = attachments.filter((a) => a.field === "target")

  return (
    <div className="mx-auto flex h-svh max-w-3xl flex-col gap-4 px-6 py-6 max-md:pt-16">
      <div className="flex flex-col gap-3">
        <Link
          to="/"
          className="flex w-fit items-center gap-1 text-[13px] text-ink-2 transition-colors hover:text-foreground"
        >
          <ArrowLeft className="size-3.5" strokeWidth={1.5} />
          Campaigns
        </Link>

        <header className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <h1 className="truncate font-mono text-sm font-medium" title={campaign.target}>
              {campaign.target.split("\n")[0]}
            </h1>
            <div className="mt-2 flex flex-wrap items-center gap-1.5 text-xs text-ink-2">
              <Badge variant="secondary">{campaign.model}</Badge>
              <Badge variant="secondary">effort · {campaign.effort}</Badge>
              <span className="px-0.5">created {fmtDate(campaign.created_at)}</span>
            </div>
            {targetFiles.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-2">
                {targetFiles.map((a) => (
                  <AttachmentChip
                    key={a.id}
                    name={a.filename}
                    mime={a.content_type}
                    href={api.attachmentUrl(a.id)}
                  />
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
            <IconAction
              variant="ghost"
              size="icon-xs"
              label="Delete campaign"
              onClick={() => setConfirmDelete(true)}
              disabled={deleting || pending}
              className="text-ink-3 hover:text-destructive"
            >
              <Trash2 className="size-3.5" strokeWidth={1.5} />
            </IconAction>
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
                <UserMessage
                  content={m.content}
                  attachments={m.attachments}
                  localFiles={m.localFiles}
                  animate={!initialIds.current?.has(m.id)}
                />
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
                {stream.pendingTool ? (
                  <AgentProgress
                    label={
                      stream.pendingTool === "write_todos"
                        ? "Planning"
                        : `Running ${stream.pendingTool}`
                    }
                  />
                ) : stream.parts.length === 0 ? (
                  <ReasoningText />
                ) : null}
                {stream.parts.length > 0 && <AssistantMessage parts={stream.parts} live />}
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
          <Alert variant="destructive" className="mb-3">
            <TriangleAlert />
            <AlertTitle>Message failed</AlertTitle>
            <AlertDescription>{error}</AlertDescription>
            <AlertAction>
              <Button variant="outline" size="sm" onClick={() => send()} disabled={pending}>
                <RefreshCw className="size-3.5" strokeWidth={1.5} />
                Retry
              </Button>
            </AlertAction>
          </Alert>
        )}

        <div className="pt-1">
          {todos.length > 0 && (
            <div className="mb-2">
              <TodoList items={todos} title="Plan" defaultOpen />
            </div>
          )}

          {files.length > 0 && (
            <div className="scrollbar-hide mb-2 flex gap-2 overflow-x-auto pb-1">
              {files.map((file, index) => (
                <PendingCard
                  key={`${file.name}-${index}`}
                  file={file}
                  onRemove={() => setFiles((prev) => prev.filter((_, i) => i !== index))}
                />
              ))}
            </div>
          )}

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
            onPaste={onPasteFiles}
            leadingAction={
              <>
                <input
                  ref={fileInput}
                  type="file"
                  multiple
                  hidden
                  onChange={(event) => {
                    addFiles(Array.from(event.target.files ?? []))
                    event.target.value = ""
                  }}
                />
                <IconAction
                  variant="ghost"
                  size="icon-sm"
                  label="Attach files or images"
                  description="Attach files"
                  disabled={!configured || pending}
                  onClick={() => fileInput.current?.click()}
                  className="rounded-full text-ink-3 hover:text-ink"
                >
                  <Paperclip className="size-4" strokeWidth={1.5} />
                </IconAction>
                <span
                  title={`Model · ${campaign.model} · effort ${campaign.effort}`}
                  className="inline-flex items-center gap-1.5 rounded-full border border-line bg-inset px-2.5 py-1 font-mono text-[11px] font-medium text-ink-2"
                >
                  <Sparkles className="size-3 text-accent-ink" strokeWidth={1.5} />
                  {campaign.model}
                </span>
              </>
            }
          />
        </div>
      </section>

      <ConfirmDialog
        open={confirmDelete}
        onOpenChange={setConfirmDelete}
        title="Delete this campaign?"
        description={`"${campaign.target.split("\n")[0]}" and all of its messages and files are removed. This cannot be undone.`}
        confirmLabel="Delete campaign"
        busy={deleting}
        onConfirm={remove}
      />
    </div>
  )
}

// messages are immutable, so parse each one's JSON once. Without this a long
// chat re-parsed every message on every streamed token.
const partsCache = new Map<number, Part[]>()

function partsOf(message: Message): Part[] {
  const cached = partsCache.get(message.id)
  if (cached) return cached

  let parts: Part[] = []
  if (message.parts) {
    try {
      const parsed = JSON.parse(message.parts) as Part[]
      if (parsed.length > 0) parts = parsed
    } catch {
      // fall back to the legacy columns
    }
  }

  if (parts.length === 0) {
    if (message.reasoning) parts.push({ type: "thinking", text: message.reasoning })
    for (const call of callsOf(message)) parts.push({ type: "tool", ...call })
    if (message.content) parts.push({ type: "content", text: message.content })
  }

  partsCache.set(message.id, parts)
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
              <div key={section.key} className="rounded-[12px] bg-inset px-3 py-2.5">
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
                      <AttachmentChip
                        key={a.id}
                        name={a.filename}
                        mime={a.content_type}
                        href={api.attachmentUrl(a.id)}
                      />
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

// composer tray card — owns the object-URL lifecycle for image previews
function PendingCard({ file, onRemove }: { file: File; onRemove: () => void }) {
  const [preview, setPreview] = useState<string>()

  useEffect(() => {
    if (!file.type.startsWith("image/")) return
    const url = URL.createObjectURL(file)
    setPreview(url)
    return () => URL.revokeObjectURL(url)
  }, [file])

  return (
    <AttachmentCard
      name={file.name}
      mime={file.type}
      previewUrl={preview}
      meta={fmtSize(file.size)}
      onRemove={onRemove}
    />
  )
}
