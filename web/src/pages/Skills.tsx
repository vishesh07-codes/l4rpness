import { useEffect, useState } from "react"
import { Plus } from "@/components/icons"

import { Markdown } from "@/components/Markdown"
import { PageHeader } from "@/components/PageHeader"
import { Search, TriangleAlert } from "@/components/icons"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Skeleton } from "@/components/ui/skeleton"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { api, type Skill, type SkillDetail } from "@/lib/api"
import { cn } from "@/lib/utils"

type Draft = { name: string; description: string; body: string }

const EMPTY: Draft = { name: "", description: "", body: "" }

export function Skills() {
  const [skills, setSkills] = useState<Skill[] | null>(null)
  const [selected, setSelected] = useState<SkillDetail | null>(null)
  const [draft, setDraft] = useState<Draft | null>(null)
  const [editing, setEditing] = useState(false)
  const [error, setError] = useState("")
  const [busy, setBusy] = useState(false)
  const [query, setQuery] = useState("")

  const load = () =>
    api
      .listSkills()
      .then(setSkills)
      .catch((e: Error) => setError(e.message))

  useEffect(() => {
    load()
  }, [])

  function open(name: string) {
    setDraft(null)
    setError("")
    api
      .getSkill(name)
      .then(setSelected)
      .catch((e: Error) => setError(e.message))
  }

  function startCreate() {
    setSelected(null)
    setDraft(EMPTY)
    setEditing(false)
    setError("")
  }

  function startEdit() {
    if (!selected) return
    setDraft({ name: selected.name, description: selected.description, body: selected.body })
    setEditing(true)
  }

  async function save() {
    if (!draft) return

    setBusy(true)
    setError("")
    try {
      const name = editing
        ? draft.name
        : (await api.createSkill(draft)).name
      if (editing) {
        await api.updateSkill(draft.name, { description: draft.description, body: draft.body })
      }
      await load()
      setDraft(null)
      setEditing(false)
      open(name)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const q = query.trim().toLowerCase()
  const shown =
    skills && q
      ? skills.filter((s) =>
          [s.name, s.description, s.source].some((v) => v?.toLowerCase().includes(q))
        )
      : skills

  return (
    <div className="mx-auto flex h-svh max-w-6xl flex-col px-8 py-10 max-md:pt-16">
      <PageHeader
        title="Skills"
        description="Methodology the agent loads on demand. Built-ins come from the repo, custom ones from the data volume."
        action={
          <Button size="sm" onClick={startCreate}>
            <Plus className="size-4" strokeWidth={2} /> New skill
          </Button>
        }
      />

      {error && (
        <Alert variant="destructive" className="mb-4">
          <TriangleAlert />
          <AlertTitle>Could not load skills</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      <div className="flex min-h-0 flex-1 overflow-hidden rounded-[12px] border border-line">
        <div className="flex w-72 shrink-0 flex-col border-r border-line">
          {skills && skills.length > 5 && (
            <div className="relative shrink-0 border-b border-line p-2">
              <Search
                className="pointer-events-none absolute top-1/2 left-4.5 size-3.5 -translate-y-1/2 text-ink-3"
                strokeWidth={1.5}
                aria-hidden
              />
              <Input
                type="search"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Filter skills"
                aria-label="Filter skills"
                className="h-8 pl-8 text-[13px]"
              />
            </div>
          )}
          <div className="min-h-0 flex-1 overflow-y-auto">
          {skills === null &&
            [0, 1, 2, 3, 4].map((i) => (
              <div key={i} className="flex flex-col gap-2 border-b border-line px-4 py-3" aria-hidden>
                <Skeleton className="h-3.5 w-[55%]" />
                <Skeleton className="h-3 w-[80%]" />
              </div>
            ))}
          {skills?.length === 0 && (
            <div className="p-4">
              <p className="text-sm font-medium text-ink">No skills yet</p>
              <p className="mt-1 text-[13px] text-ink-2">
                Skills hold methodology the agent loads on demand.
              </p>
            </div>
          )}
          {shown?.map((skill) => (
            <button
              key={skill.name}
              onClick={() => open(skill.name)}
              className={cn(
                "flex w-full flex-col gap-1 border-b px-4 py-3 text-left hover:bg-hover/40",
                selected?.name === skill.name && !draft && "bg-hover"
              )}
            >
              <span className="flex items-center gap-2">
                <span className="truncate font-mono text-[13px]">{skill.name}</span>
                <Badge variant="secondary" className="shrink-0 text-[10px] capitalize">
                  {skill.source}
                </Badge>
              </span>
              <span className="line-clamp-2 text-xs text-ink-2">{skill.description}</span>
            </button>
          ))}
          {skills && skills.length > 0 && shown?.length === 0 && (
            <div className="p-4">
              <p className="text-sm font-medium text-ink">No skills match “{query.trim()}”</p>
              <Button variant="outline" size="sm" className="mt-3" onClick={() => setQuery("")}>
                Clear filter
              </Button>
            </div>
          )}
          </div>
        </div>

        <div className="min-w-0 flex-1 overflow-y-auto p-6">
          {draft ? (
            <form
              className="flex max-w-2xl flex-col gap-4"
              onSubmit={(e) => {
                e.preventDefault()
                save()
              }}
            >
              <div className="flex flex-col gap-2">
                <Label>Name</Label>
                <Input
                  value={draft.name}
                  onChange={(e) => setDraft({ ...draft, name: e.target.value })}
                  disabled={editing}
                  placeholder="my-skill"
                />
              </div>
              <div className="flex flex-col gap-2">
                <Label>Description</Label>
                <Input
                  value={draft.description}
                  onChange={(e) => setDraft({ ...draft, description: e.target.value })}
                  placeholder="Use when the task involves ..."
                />
              </div>
              <div className="flex flex-col gap-2">
                <Label>Body</Label>
                <Textarea
                  value={draft.body}
                  onChange={(e) => setDraft({ ...draft, body: e.target.value })}
                  rows={14}
                  className="font-mono text-[13px]"
                />
              </div>
              <div className="flex justify-end gap-2">
                <Button type="button" variant="ghost" onClick={() => setDraft(null)}>
                  Cancel
                </Button>
                <Button type="submit" disabled={busy || !draft.name.trim() || !draft.description.trim()}>
                  {busy ? "Saving…" : "Save"}
                </Button>
              </div>
            </form>
          ) : selected ? (
            <div className="max-w-3xl">
              <div className="flex items-start justify-between gap-4">
                <div className="min-w-0">
                  <h2 className="font-mono text-base">{selected.name}</h2>
                  <p className="mt-1 text-sm text-ink-2">{selected.description}</p>
                </div>
                {selected.source === "custom" && (
                  <Button variant="outline" size="sm" className="shrink-0" onClick={startEdit}>
                    Edit
                  </Button>
                )}
              </div>
              {selected.files.length > 0 && (
                <p className="mt-3 text-xs text-ink-2">
                  Files: {selected.files.join(", ")}
                </p>
              )}
              <div className="mt-5">
                <Markdown text={selected.body} />
              </div>
            </div>
          ) : (
            <div>
              <p className="text-sm font-medium text-ink">No skill selected</p>
              <p className="mt-1 text-[13px] text-ink-2">
                Choose a skill from the list to read it, or create a new one.
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
