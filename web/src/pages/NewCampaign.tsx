import { useEffect, useRef, useState, type ChangeEvent, type FormEvent, type ReactNode } from "react"
import { Link, useNavigate } from "react-router-dom"

import { FieldAttachments } from "@/components/FieldAttachments"
import { PageHeader } from "@/components/PageHeader"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
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
  const [invalid, setInvalid] = useState(false)
  const targetRef = useRef<HTMLTextAreaElement>(null)

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

    // keep submit enabled and validate here, so the reason is stated rather
    // than left for the user to infer from a dead button
    if (!text.target.trim()) {
      setInvalid(true)
      setError("")
      targetRef.current?.focus()
      return
    }
    setInvalid(false)

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
    <div className="mx-auto max-w-2xl px-8 py-10 max-md:pt-16">
      <PageHeader
        title="New campaign"
        description="Only the target is required."
        back={{ to: "/", label: "Campaigns" }}
      />

      <form onSubmit={submit} className="flex flex-col gap-6">
        <Field
          label="Target"
          hint="URL, IP, host — or paragraphs, anything you want. The agent starts immediately and keeps testing until you stop it."
        >
          <Textarea
            ref={targetRef}
            id="target"
            value={text.target}
            onChange={(e) => {
              setInvalid(false)
              edit("target")(e)
            }}
            rows={3}
            placeholder="https://example.com"
            aria-invalid={invalid || undefined}
            aria-describedby={invalid ? "target-error" : undefined}
            autoFocus
          />
          {invalid && (
            <p id="target-error" className="text-[13px] text-destructive">
              Enter a target to test — a URL, host, IP range, or a description.
            </p>
          )}
          <FieldAttachments files={files.target} onChange={attach("target")} disabled={busy} />
        </Field>

        <div className="grid gap-4 sm:grid-cols-2">
          <div className="flex flex-col gap-2">
            <Label htmlFor="model">Model</Label>
            <Select value={model} onValueChange={setModel}>
              <SelectTrigger id="model" className="w-full font-mono">
                <SelectValue placeholder="Choose a model" />
              </SelectTrigger>
              <SelectContent>
                {models.map((m) => (
                  <SelectItem key={m} value={m} className="font-mono">
                    {m}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="flex flex-col gap-2">
            <Label htmlFor="effort">Effort</Label>
            <Select value={effort} onValueChange={setEffort}>
              <SelectTrigger id="effort" className="w-full capitalize">
                <SelectValue placeholder="Choose an effort level" />
              </SelectTrigger>
              <SelectContent>
                {efforts.map((level) => (
                  <SelectItem key={level} value={level} className="capitalize">
                    {level}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
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
          <Button type="submit" disabled={busy}>
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
        {hint && <p className="mt-0.5 text-xs text-ink-2">{hint}</p>}
      </div>
      {children}
    </div>
  )
}
