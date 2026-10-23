import { useEffect, useState, type ChangeEvent, type FormEvent, type ReactNode } from "react"
import { Link, useNavigate } from "react-router-dom"
import { ArrowLeft } from "lucide-react"

import { FieldAttachments } from "@/components/FieldAttachments"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { api, FIELDS, type FieldName } from "@/lib/api"

type FileMap = Record<FieldName, File[]>

const NO_FILES: FileMap = { target: [], info: [], instructions: [], description: [], scope: [] }

const EMPTY_TEXT = { target: "", info: "", instructions: "", description: "", scope: "" }

export function NewCampaign() {
  const nav = useNavigate()
  const [models, setModels] = useState<string[]>([])
  const [model, setModel] = useState("")
  const [efforts, setEfforts] = useState<string[]>([])
  const [effort, setEffort] = useState("")
  const [text, setText] = useState(EMPTY_TEXT)
  const [files, setFiles] = useState<FileMap>(NO_FILES)
  const [error, setError] = useState("")
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api
      .config()
      .then((c) => {
        setModels(c.models)
        setModel(c.default_model)
        setEfforts(c.efforts)
        setEffort(c.default_effort)
      })
      .catch((e: Error) => setError(e.message))
  }, [])

  const edit = (field: FieldName) => (e: ChangeEvent<HTMLTextAreaElement>) =>
    setText((prev) => ({ ...prev, [field]: e.target.value }))

  const attach = (field: FieldName) => (picked: File[]) =>
    setFiles((prev) => ({ ...prev, [field]: picked }))

  async function submit(e: FormEvent) {
    e.preventDefault()
    const form = new FormData()
    form.set("model", model)
    form.set("effort", effort)
    for (const field of FIELDS) {
      form.set(field, text[field])
      for (const file of files[field]) form.append(`${field}_files`, file)
    }

    setBusy(true)
    setError("")
    try {
      const campaign = await api.createCampaign(form)
      nav(`/campaigns/${campaign.id}`)
    } catch (err) {
      setError((err as Error).message)
      setBusy(false)
    }
  }

  return (
    <div className="mx-auto max-w-2xl px-8 py-10">
      <Link
        to="/"
        className="flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="size-3.5" />
        Campaigns
      </Link>

      <h1 className="mt-4 text-lg font-semibold">New campaign</h1>
      <p className="text-sm text-muted-foreground">Only the target is required.</p>

      <form onSubmit={submit} className="mt-8 flex flex-col gap-6">
        <Field label="Target" hint="URL, IP, host — or paragraphs, anything you want.">
          <Textarea
            value={text.target}
            onChange={edit("target")}
            rows={3}
            placeholder="https://example.com"
            autoFocus
          />
          <FieldAttachments files={files.target} onChange={attach("target")} disabled={busy} />
        </Field>

        <div className="grid grid-cols-2 gap-4">
          <div className="flex flex-col gap-2">
            <Label>Model</Label>
            <select
              value={model}
              onChange={(e) => setModel(e.target.value)}
              className="h-9 w-full rounded-md border bg-transparent px-3 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
            >
              {models.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          </div>

          <div className="flex flex-col gap-2">
            <Label>Effort</Label>
            <select
              value={effort}
              onChange={(e) => setEffort(e.target.value)}
              className="h-9 w-full rounded-md border bg-transparent px-3 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
            >
              {efforts.map((level) => (
                <option key={level} value={level}>
                  {level}
                </option>
              ))}
            </select>
          </div>
        </div>

        <Field label="Info" hint="What the application is, anything the agent should know.">
          <Textarea value={text.info} onChange={edit("info")} rows={4} />
          <FieldAttachments files={files.info} onChange={attach("info")} disabled={busy} />
        </Field>

        <Field label="Instructions" hint="What you want done.">
          <Textarea value={text.instructions} onChange={edit("instructions")} rows={4} />
          <FieldAttachments
            files={files.instructions}
            onChange={attach("instructions")}
            disabled={busy}
          />
        </Field>

        <Field label="Description" hint="Free notes about the target.">
          <Textarea value={text.description} onChange={edit("description")} rows={3} />
          <FieldAttachments
            files={files.description}
            onChange={attach("description")}
            disabled={busy}
          />
        </Field>

        <Field label="Scope" hint="What is in and out of bounds.">
          <Textarea value={text.scope} onChange={edit("scope")} rows={3} />
          <FieldAttachments files={files.scope} onChange={attach("scope")} disabled={busy} />
        </Field>

        {error && <p className="text-sm text-destructive">{error}</p>}

        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" asChild>
            <Link to="/">Cancel</Link>
          </Button>
          <Button type="submit" disabled={busy || !text.target.trim()}>
            {busy ? "Creating…" : "Create campaign"}
          </Button>
        </div>
      </form>
    </div>
  )
}

function Field({
  label,
  hint,
  children,
}: {
  label: string
  hint?: string
  children: ReactNode
}) {
  return (
    <div className="flex flex-col gap-2">
      <div>
        <Label>{label}</Label>
        {hint && <p className="mt-0.5 text-xs text-muted-foreground">{hint}</p>}
      </div>
      {children}
    </div>
  )
}
