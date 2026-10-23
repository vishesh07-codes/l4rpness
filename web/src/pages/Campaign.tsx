import { useEffect, useRef, useState, type KeyboardEvent } from "react"
import { Link, useParams } from "react-router-dom"
import { ArrowLeft, Send } from "lucide-react"

import { StatusBadge } from "@/components/StatusBadge"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { api, type Attachment, type Campaign as CampaignData, type Message } from "@/lib/api"
import { fmtDate } from "@/lib/utils"

const SECTIONS = [
  { key: "info", label: "Info" },
  { key: "instructions", label: "Instructions" },
  { key: "description", label: "Description" },
  { key: "scope", label: "Scope" },
] as const

export function Campaign() {
  const { id } = useParams()
  const campaignId = Number(id)

  const [campaign, setCampaign] = useState<CampaignData | null>(null)
  const [messages, setMessages] = useState<Message[]>([])
  const [configured, setConfigured] = useState(true)
  const [input, setInput] = useState("")
  const [pending, setPending] = useState(false)
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
  }, [messages, pending])

  async function send() {
    const content = input.trim()
    if (!content || pending) return

    setInput("")
    setError("")
    setPending(true)
    const optimistic: Message = { id: -Date.now(), role: "user", content, created_at: "" }
    setMessages((prev) => [...prev, optimistic])

    try {
      const res = await api.sendMessage(campaignId, content)
      setMessages((prev) => [...prev.filter((m) => m.id !== optimistic.id), res.user, res.assistant])
    } catch (e) {
      setMessages((prev) => prev.filter((m) => m.id !== optimistic.id))
      setInput(content)
      setError((e as Error).message)
    } finally {
      setPending(false)
    }
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault()
      send()
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
            {campaign.model} · created {fmtDate(campaign.created_at)}
          </p>
          {targetFiles.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-2">
              {targetFiles.map((a) => (
                <AttachmentChip key={a.id} attachment={a} />
              ))}
            </div>
          )}
        </div>
        <StatusBadge status={campaign.status} />
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
          {messages.length === 0 && !pending && (
            <p className="text-sm text-muted-foreground">
              Ask about this target. The agent has no tools yet.
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
              <div key={m.id} className="max-w-[85%] whitespace-pre-wrap text-sm leading-relaxed">
                {m.content}
              </div>
            )
          )}
          {pending && <p className="text-sm text-muted-foreground">Thinking…</p>}
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
