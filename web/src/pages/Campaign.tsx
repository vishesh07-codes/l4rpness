import { useEffect, useMemo, useRef, useState, type ClipboardEvent } from "react"
import { toast } from "sonner"
import { useNavigate, useParams } from "react-router-dom"
import {
  ArrowDown,
  ArrowLeft,
  ChevronDown,
  FileText,
  Paperclip,
  Play,
  RefreshCw,
  Sparkles,
  Square,
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
  type GeneratorInfo,
  type GeneratorMessage,
  type Hypothesis,
  type Message,
  type Part,
  type ToolCall,
} from "@/lib/api"
import { ConfirmDialog } from "@/components/ConfirmDialog"
import { IconAction } from "@/components/IconAction"
import { TransitionLink } from "@/components/TransitionLink"
import { latestTodos } from "@/lib/todos"
import { cn, fmtDate, fmtTokens } from "@/lib/utils"
import { withViewTransition } from "@/lib/view-transition"

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

// a window, not the whole history: the transcript can hold thousands of rows
// and each one carries markdown and tool cards that are expensive to render
const PAGE_SIZE = 60
const PREVIEW_TITLE = 56
const PREVIEW_DESCRIPTION = 88

function mergeMessages(prev: Message[], incoming: Message[]): Message[] {
  if (incoming.length === 0) return prev
  const byId = new Map(prev.map((message) => [message.id, message]))
  let changed = false
  for (const message of incoming) {
    const old = byId.get(message.id)
    if (
      !old ||
      old.content !== message.content ||
      old.reasoning !== message.reasoning ||
      old.parts !== message.parts ||
      old.tool_calls !== message.tool_calls
    ) {
      if (old) partsCache.delete(message.id)
      changed = true
    }
    byId.set(message.id, message)
  }
  if (!changed) return prev
  return [...byId.values()].sort((a, b) => a.id - b.id)
}

function previewText(message: Message) {
  if (message.role === "user") {
    return (message.content ?? "").replace(/\s+/g, " ").trim()
  }
  return partsOf(message)
    .filter((part): part is Extract<Part, { type: "content" }> => part.type === "content")
    .map((part) => part.text)
    .join(" ")
    .replace(/\s+/g, " ")
    .trim()
}

function truncatePreview(text: string, limit: number) {
  if (text.length <= limit) return text
  const excerpt = text.slice(0, limit)
  const boundary = excerpt.lastIndexOf(" ")
  return `${excerpt.slice(0, boundary > limit * 0.65 ? boundary : limit).trim()}…`
}

function messagePreview(message: Message, nextMessage?: Message) {
  const label = truncatePreview(previewText(message) || "Message", PREVIEW_TITLE)
  const nextText = nextMessage ? previewText(nextMessage) : ""
  return {
    label,
    description: nextText ? truncatePreview(nextText, PREVIEW_DESCRIPTION) : undefined,
  }
}

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
  const [resuming, setResuming] = useState(false)
  const [liveUsage, setLiveUsage] = useState<{ in: number; out: number; ctx: number } | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [error, setError] = useState("")
  const [following, setFollowing] = useState(true)
  const [hasEarlier, setHasEarlier] = useState(false)
  const [windowSize, setWindowSize] = useState(PAGE_SIZE)
  const [loadingEarlier, setLoadingEarlier] = useState(false)
  const [tab, setTab] = useState<"supervisor" | "generators" | "hypotheses">("supervisor")
  const [generators, setGenerators] = useState<GeneratorInfo[]>([])
  const [selectedGenerator, setSelectedGenerator] = useState<number | null>(null)
  const [generatorMessages, setGeneratorMessages] = useState<GeneratorMessage[]>([])
  const [hypotheses, setHypotheses] = useState<Hypothesis[]>([])
  const initialIds = useRef<Set<number> | null>(null)
  const lastIdRef = useRef(0)
  const selectedGeneratorRef = useRef<number | null>(null)
  const tabRef = useRef<"supervisor" | "generators" | "hypotheses">("supervisor")
  const viewportRef = useRef<HTMLElement | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (!campaignId) return
    Promise.all([
      api.getCampaign(campaignId),
      api.listMessagesPage(campaignId, { limit: PAGE_SIZE }),
      api.config(),
      api.listGenerators(campaignId),
      api.listHypotheses(campaignId),
    ])
      .then(([c, page, cfg, gens, hypos]) => {
        setCampaign(c)
        partsCache.clear()
        setMessages(page.messages)
        setHasEarlier(page.hasMore)
        setWindowSize(Math.max(page.messages.length, PAGE_SIZE))
        lastIdRef.current = page.messages.length
          ? page.messages[page.messages.length - 1].id
          : 0
        setConfigured(cfg.agent_configured)
        setGenerators(gens)
        setHypotheses(hypos)
        setSelectedGenerator((prev) => prev ?? (gens[0]?.id ?? null))
        initialIds.current = new Set(page.messages.map((message) => message.id))
      })
      .catch((e: Error) => setError(e.message))
  }, [campaignId])

  useEffect(() => {
    selectedGeneratorRef.current = selectedGenerator
  }, [selectedGenerator])

  useEffect(() => {
    tabRef.current = tab
  }, [tab])

  // incremental sync: fetch only what is new (plus a small overlap so the
  // in-flight assistant row is replaced by its final version)
  async function catchUpMessages() {
    const page = lastIdRef.current
      ? // a small overlap lets the in-flight assistant row be replaced by its
        // final version once the chain ended
        await api.listMessagesPage(campaignId, {
          after: Math.max(1, lastIdRef.current - 3),
        })
      : await api.listMessagesPage(campaignId, { limit: PAGE_SIZE })
    if (page.messages.length === 0) return
    lastIdRef.current = Math.max(
      lastIdRef.current,
      page.messages[page.messages.length - 1].id
    )
    setMessages((prev) => mergeMessages(prev, page.messages))
  }

  async function loadEarlier() {
    if (loadingEarlier) return
    if (messages.length > windowSize) {
      setWindowSize((size) => size + PAGE_SIZE)
      return
    }
    if (!hasEarlier || messages.length === 0) return
    setLoadingEarlier(true)
    try {
      const page = await api.listMessagesPage(campaignId, {
        before: messages[0].id,
        limit: PAGE_SIZE,
      })
      if (page.messages.length > 0) {
        setMessages((prev) => mergeMessages(prev, page.messages))
        setWindowSize((size) => size + page.messages.length)
      }
      setHasEarlier(page.hasMore)
    } catch (e) {
      toast.error("Could not load earlier messages", { description: (e as Error).message })
    } finally {
      setLoadingEarlier(false)
    }
  }

  async function loadGenerators() {
    try {
      setGenerators(await api.listGenerators(campaignId))
    } catch {
      // the next refresh retries
    }
  }

  async function loadHypotheses() {
    try {
      setHypotheses(await api.listHypotheses(campaignId))
    } catch {
      // the next refresh retries
    }
  }

  async function openGenerator(generatorId: number) {
    setSelectedGenerator(generatorId)
    selectedGeneratorRef.current = generatorId
    setGeneratorMessages([])
    try {
      setGeneratorMessages(await api.getGeneratorMessages(generatorId))
    } catch {
      // leave it empty; the next refresh retries
    }
  }

  useEffect(() => {
    if (tab !== "generators") return
    void loadGenerators()
    const selected = selectedGeneratorRef.current
    if (selected !== null) {
      void api
        .getGeneratorMessages(selected)
        .then(setGeneratorMessages)
        .catch(() => {})
    }
  }, [tab, campaignId])

  // backstop for missed stream events while the worker tabs are open
  useEffect(() => {
    if (!campaign?.running || (tab !== "generators" && tab !== "hypotheses")) return
    const timer = setInterval(() => {
      if (tabRef.current === "generators") void loadGenerators()
      else void loadHypotheses()
    }, 4000)
    return () => clearInterval(timer)
  }, [tab, campaign?.running, campaignId])

  // Escape stops the agent — the visible path is the stop button, this is the
  // one people reach for
  useEffect(() => {
    if (!pending && !campaign?.running) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") stop()
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [pending, campaign?.running, stopping])

  function jumpToLatest() {
    const viewport = viewportRef.current
    viewport?.scrollTo({ top: viewport.scrollHeight, behavior: "smooth" })
  }

  // live turns stream over SSE so the transcript tracks the model token for
  // token; the browser reconnects on drops and re-syncs from the snapshot
  useEffect(() => {
    if (!campaign?.running) return

    partsCache.clear()
    setStream({ parts: [], pendingTool: null })

    const source = new EventSource(`/api/campaigns/${campaignId}/stream`)

    const refresh = async () => {
      try {
        await catchUpMessages()
      } catch {
        // the next chain_end or reconnect retries
      }
    }

    const append = (kind: "thinking" | "content", text: string) =>
      setStream((s) => {
        const base = s ?? { parts: [], pendingTool: null }
        const last = base.parts[base.parts.length - 1]
        if (last?.type === kind) {
          return {
            ...base,
            parts: [...base.parts.slice(0, -1), { ...last, text: last.text + text }],
          }
        }
        return { ...base, parts: [...base.parts, { type: kind, text }] }
      })

    const on = (event: string, handler: (data: any) => void) => {
      source.addEventListener(event, (e) => {
        try {
          handler(JSON.parse((e as MessageEvent).data))
        } catch {
          // malformed frame; the next one will resync
        }
      })
    }

    on("snapshot", (d) => {
      setStream({ parts: d.parts ?? [], pendingTool: d.pending ?? null })
      if (d.usage) setLiveUsage(d.usage)
      void refresh()
    })
    on("thinking", (d) => append("thinking", d.text))
    on("content", (d) => append("content", d.text))
    on("usage", (d) => setLiveUsage({ in: d.in, out: d.out, ctx: d.ctx }))
    on("notice", (d) =>
      setStream((s) => {
        const base = s ?? { parts: [], pendingTool: null }
        return { ...base, parts: [...base.parts, { type: "notice", text: d.text }] }
      })
    )
    on("compacted", (d) =>
      setStream((s) => {
        const base = s ?? { parts: [], pendingTool: null }
        return {
          ...base,
          parts: [
            ...base.parts,
            {
              type: "notice",
              text: `context compacted — ${d.messages} earlier messages folded into memory`,
            },
          ],
        }
      })
    )
    on("tool_start", (d) => setStream((s) => (s ? { ...s, pendingTool: d.name } : s)))
    on("tool_call", (d) =>
      setStream((s) => {
        const base = s ?? { parts: [], pendingTool: null }
        return {
          parts: [...base.parts, { type: "tool", id: d.id, name: d.name, args: d.args }],
          pendingTool: null,
        }
      })
    )
    on("tool_output", (d) =>
      setStream((s) =>
        s
          ? {
              ...s,
              parts: s.parts.map((part) =>
                part.type === "tool" && part.id === d.id
                  ? { ...part, output: `${part.output ?? ""}${d.chunk}` }
                  : part
              ),
            }
          : s
      )
    )
    on("tool_result", (d) =>
      setStream((s) =>
        s
          ? {
              ...s,
              parts: s.parts.map((part) =>
                part.type === "tool" && part.id === d.id
                  ? { ...part, output: d.output, ok: d.ok, ms: d.ms }
                  : part
              ),
            }
          : s
      )
    )
    on("generator", (d) => {
      void loadGenerators()
      if (d.message) void loadHypotheses()
      if (d.message && selectedGeneratorRef.current === d.generator_id) {
        const message = d.message as GeneratorMessage
        setGeneratorMessages((prev) =>
          prev.some((m) => m.id === message.id) ? prev : [...prev, message]
        )
      }
    })
    on("chain_end", () => {
      setStream({ parts: [], pendingTool: null })
      void refresh()
    })
    on("closed", async () => {
      source.close()
      setStream(null)
      setCampaign((c) => (c ? { ...c, running: false } : c))
      await refresh()
    })
    on("idle", async () => {
      source.close()
      setStream(null)
      const fresh = await api.getCampaign(campaignId)
      setCampaign(fresh)
      await refresh()
    })

    return () => source.close()
  }, [campaign?.running, campaignId])

  // only re-scan the tail of the history when it changes, not every message
  // and not on every streamed token
  const messageTodos = useMemo(
    () =>
      latestTodos(
        ...messages
          .slice(-8)
          .map((m) => (m.role === "assistant" ? partsOf(m) : []))
      ),
    [messages]
  )
  const streamTodos = latestTodos(stream?.parts ?? [])
  const todos = streamTodos.length > 0 ? streamTodos : messageTodos

  const visibleMessages = useMemo(
    () => (windowSize >= messages.length ? messages : messages.slice(-windowSize)),
    [messages, windowSize]
  )
  const railPreviews = useMemo(
    () =>
      visibleMessages.map((message, index) =>
        messagePreview(message, visibleMessages[index + 1])
      ),
    [visibleMessages]
  )

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

    const optimistic: Message = {
      id: -Date.now(),
      role: "user",
      content,
      reasoning: "",
      localFiles: sending.map((file) => ({ filename: file.name })),
      created_at: "",
    }
    setMessages((prev) => [...prev, optimistic])

    try {
      // the supervisor picks queued messages up when the next chain starts,
      // the same way mid-task input works in a coding harness
      const stored = await api.steerCampaign(campaignId, content, sending)
      lastIdRef.current = Math.max(lastIdRef.current, stored.id)
      setMessages((prev) =>
        mergeMessages(prev.filter((m) => m.id !== optimistic.id), [stored])
      )
      // steering a stopped campaign resumes the loop
      setCampaign(await api.getCampaign(campaignId))
    } catch (e) {
      setMessages((prev) => prev.filter((m) => m.id !== optimistic.id))
      setInput(content)
      setFiles(sending)
      setError((e as Error).message)
    } finally {
      setPending(false)
    }
  }

  async function stop() {
    if (stopping) return
    setStopping(true)
    try {
      await api.stopCampaign(campaignId)
      const fresh = await api.getCampaign(campaignId)
      setCampaign(fresh)
      if (!fresh.running) {
        setStream(null)
        await catchUpMessages()
      }
    } catch (e) {
      toast.error("Could not stop the agent", { description: (e as Error).message })
    } finally {
      setStopping(false)
    }
  }

  async function resume() {
    if (resuming) return
    setResuming(true)
    try {
      await api.resumeCampaign(campaignId)
      setCampaign(await api.getCampaign(campaignId))
      toast.success("Agent resumed")
    } catch (e) {
      toast.error("Could not resume the agent", { description: (e as Error).message })
    } finally {
      setResuming(false)
    }
  }

  async function remove() {
    if (!campaign) return

    setDeleting(true)
    try {
      await api.deleteCampaign(campaignId)
      setConfirmDelete(false)
      toast.success("Campaign deleted")
      withViewTransition(() => nav("/"))
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
  const tabs = [
    { key: "supervisor" as const, label: "Supervisor", count: 0 },
    {
      key: "generators" as const,
      label: "Generators",
      count: generators.filter((g) => g.status === "running").length,
    },
    {
      key: "hypotheses" as const,
      label: "Hypotheses",
      count: hypotheses.filter((h) => h.status === "new" || h.status === "working").length,
    },
  ]
  const usageIn = liveUsage?.in ?? campaign.usage_in ?? 0
  const usageOut = liveUsage?.out ?? campaign.usage_out ?? 0
  const usageCtx = liveUsage?.ctx ?? campaign.last_prompt_tokens ?? 0

  return (
    <div className="mx-auto flex h-svh max-w-3xl flex-col gap-4 px-6 py-6 max-md:pt-16">
      <div className="flex flex-col gap-3">
        <TransitionLink
          to="/"
          className="flex w-fit items-center gap-1 text-[13px] text-ink-2 transition-colors hover:text-foreground"
        >
          <ArrowLeft className="size-3.5" strokeWidth={1.5} />
          Campaigns
        </TransitionLink>

        <header className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <h1 className="truncate font-mono text-sm font-medium" title={campaign.target}>
              {campaign.target.split("\n")[0]}
            </h1>
            <div className="mt-2 flex flex-wrap items-center gap-1.5 text-xs text-ink-2">
              <Badge variant="secondary">{campaign.model}</Badge>
              <Badge variant="secondary">effort · {campaign.effort}</Badge>
              {(usageIn > 0 || usageOut > 0) && (
                <Badge
                  variant="secondary"
                  className="font-mono"
                  title={`${usageIn.toLocaleString()} input · ${usageOut.toLocaleString()} output${
                    usageCtx ? ` · context ${usageCtx.toLocaleString()} tokens` : ""
                  }`}
                >
                  ↑{fmtTokens(usageIn)} ↓{fmtTokens(usageOut)}
                </Badge>
              )}
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
            {campaign.running && (
              <IconAction
                variant="ghost"
                size="icon-xs"
                label="Stop the agent"
                description="Stop the 24x7 loop"
                onClick={stop}
                disabled={stopping}
                className="text-ink-3 hover:text-destructive"
              >
                <Square className="size-3.5" strokeWidth={1.5} />
              </IconAction>
            )}
            {!campaign.running && (
              <IconAction
                variant="ghost"
                size="icon-xs"
                label="Resume the agent"
                description="Restart the 24x7 loop from where it left off"
                onClick={resume}
                disabled={resuming || !configured}
                className="text-ink-3 hover:text-ink"
              >
                <Play className="size-3.5 fill-current" strokeWidth={1.5} />
              </IconAction>
            )}
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

        <div className="flex items-center gap-1">
          {tabs.map((entry) => (
            <button
              key={entry.key}
              type="button"
              onClick={() => setTab(entry.key)}
              className={cn(
                "flex items-center gap-1.5 rounded-md px-2.5 py-1 text-[12px] font-medium transition-colors",
                tab === entry.key
                  ? "bg-inset text-ink"
                  : "text-ink-3 hover:bg-hover hover:text-ink"
              )}
            >
              {entry.label}
              {entry.count > 0 && (
                <span className="rounded-full bg-accent-tint px-1.5 font-mono text-[10px] text-accent-ink">
                  {entry.count}
                </span>
              )}
            </button>
          ))}
        </div>

        {tab === "supervisor" && (
          <ContextPanel
            campaign={campaign}
            attachments={attachments}
            defaultOpen={messages.length === 0 && !stream}
          />
        )}
      </div>

      {tab === "supervisor" && (
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
          navigation={visibleMessages.length > 4 ? "rail" : undefined}
          previews={railPreviews}
          viewportRef={viewportRef}
          onFollowChange={setFollowing}
        >
          {messages.length === 0 && !stream && (
            <EmptyState configured={configured} onPick={setInput} />
          )}

          {(hasEarlier || messages.length > windowSize) && (
            <div className="flex justify-center pb-1">
              <button
                type="button"
                onClick={loadEarlier}
                disabled={loadingEarlier}
                className="rounded-full border border-line bg-surface px-3 py-1 text-[12px] text-ink-2 outline-none transition-colors hover:bg-hover hover:text-ink focus-visible:ring-2 focus-visible:ring-ring/60 disabled:opacity-60"
              >
                {loadingEarlier ? "Loading earlier messages…" : "Load earlier messages"}
              </button>
            </div>
          )}

          {visibleMessages.map((m) => {
            // an empty assistant row is the placeholder of a run that is still
            // streaming; the live block below already renders it
            if (m.role === "assistant" && !m.content && partsOf(m).length === 0) return null
            return m.role === "user" ? (
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
          })}

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
            placeholder={
              configured
                ? campaign.running
                  ? "Steer the agent — queued for the next turn…"
                  : "Message the agent…"
                : "Chat disabled"
            }
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
      )}

      {tab === "generators" && (
        <div className="flex min-h-0 flex-1 gap-3 max-md:flex-col">
          <div className="w-72 shrink-0 overflow-y-auto rounded-[12px] bg-surface p-1 shadow-hairline max-md:max-h-44">
            {generators.length === 0 ? (
              <p className="p-3 text-[12px] text-ink-3">
                No hypothesis workers yet. The supervisor spawns them when the backlog runs dry.
              </p>
            ) : (
              generators.map((generator) => (
                <button
                  key={generator.id}
                  type="button"
                  onClick={() => openGenerator(generator.id)}
                  className={cn(
                    "flex w-full flex-col gap-1 rounded-md px-2.5 py-2 text-left transition-colors",
                    selectedGenerator === generator.id ? "bg-inset" : "hover:bg-hover"
                  )}
                >
                  <span className="flex items-center gap-2">
                    <span className="font-mono text-[11px] text-ink-3">#{generator.id}</span>
                    <span className="min-w-0 flex-1 truncate text-[12px] font-medium">
                      {generator.focus || "general"}
                    </span>
                    <StatusBadge status={generator.status} />
                  </span>
                  <span className="flex items-center gap-2 text-[11px] text-ink-3">
                    <span className="shrink-0">{generator.hypothesis_count} hypotheses</span>
                    {generator.last_snippet && (
                      <span className="min-w-0 flex-1 truncate">{generator.last_snippet}</span>
                    )}
                  </span>
                </button>
              ))
            )}
          </div>

          <div className="min-w-0 flex-1 overflow-y-auto rounded-[12px] bg-surface p-3 shadow-hairline">
            {selectedGenerator === null ? (
              <p className="text-[12px] text-ink-3">Pick a worker on the left.</p>
            ) : generatorMessages.length === 0 ? (
              <LoadingState label="Loading worker" />
            ) : (
              <div className="flex flex-col gap-3">
                {generatorMessages.map((message) => (
                  <div key={message.id} className="border-l-2 border-line pl-3">
                    <AssistantMessage parts={message.parts} />
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      {tab === "hypotheses" && (
        <div className="min-h-0 flex-1 overflow-y-auto">
          {hypotheses.length === 0 ? (
            <p className="text-[12px] text-ink-3">
              No hypotheses yet. They appear here as the generators file them.
            </p>
          ) : (
            <ul className="flex flex-col gap-2">
              {hypotheses.map((hypothesis) => (
                <li
                  key={hypothesis.id}
                  className="rounded-[12px] bg-surface px-3.5 py-3 shadow-hairline"
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono text-[11px] text-ink-3">#{hypothesis.id}</span>
                    <StatusBadge status={hypothesis.status} />
                    {hypothesis.vuln_class && (
                      <span className="rounded-full border border-line px-1.5 font-mono text-[10px] text-ink-3">
                        {hypothesis.vuln_class}
                      </span>
                    )}
                    {hypothesis.generator_id != null && (
                      <span className="text-[11px] text-ink-3">
                        worker #{hypothesis.generator_id}
                      </span>
                    )}
                  </div>
                  <p className="mt-1.5 text-[13px] font-medium">{hypothesis.title}</p>
                  {hypothesis.angle && (
                    <p className="mt-0.5 text-[12px] text-ink-2">{hypothesis.angle}</p>
                  )}
                  {hypothesis.rationale && (
                    <p className="mt-1 text-[12px] leading-relaxed text-ink-3">
                      {hypothesis.rationale}
                    </p>
                  )}
                  {hypothesis.result && (
                    <p className="mt-1.5 text-[12px] text-ink-2">
                      result: {hypothesis.result}
                    </p>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

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

// assistant rows are checkpointed while a run streams, so a row's parts can
// change after it was first fetched — clear the cache on every refetch.
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
