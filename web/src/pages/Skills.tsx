import { useEffect, useState } from "react"
import { Plus } from "lucide-react"

import { Markdown } from "@/components/Markdown"
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

  return (
    <div className="mx-auto flex h-svh max-w-6xl flex-col px-8 py-10">
      <header className="mb-6 flex items-center justify-between gap-4">
        <div>
          <h1 className="text-lg font-semibold">Skills</h1>
          <p className="text-sm text-muted-foreground">
            Methodology the agent loads on demand. Built-ins come from the repo, custom ones from
            the data volume.
          </p>
        </div>
        <Button size="sm" className="shrink-0" onClick={startCreate}>
          <Plus /> New skill
        </Button>
      </header>

      {error && <p className="mb-4 text-sm text-destructive">{error}</p>}

      <div className="flex min-h-0 flex-1 overflow-hidden rounded-lg border">
        <div className="w-72 shrink-0 overflow-y-auto border-r">
          {skills === null && <p className="p-4 text-sm text-muted-foreground">Loading…</p>}
          {skills?.length === 0 && (
            <p className="p-4 text-sm text-muted-foreground">No skills found.</p>
          )}
          {skills?.map((skill) => (
            <button
              key={skill.name}
              onClick={() => open(skill.name)}
              className={cn(
                "flex w-full flex-col gap-1 border-b px-4 py-3 text-left hover:bg-muted/40",
                selected?.name === skill.name && !draft && "bg-muted/60"
              )}
            >
              <span className="flex items-center gap-2">
                <span className="truncate font-mono text-[13px]">{skill.name}</span>
                <Badge variant="secondary" className="shrink-0 text-[10px] capitalize">
                  {skill.source}
                </Badge>
              </span>
              <span className="line-clamp-2 text-xs text-muted-foreground">{skill.description}</span>
            </button>
          ))}
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
                  <p className="mt-1 text-sm text-muted-foreground">{selected.description}</p>
                </div>
                {selected.source === "custom" && (
                  <Button variant="outline" size="sm" className="shrink-0" onClick={startEdit}>
                    Edit
                  </Button>
                )}
              </div>
              {selected.files.length > 0 && (
                <p className="mt-3 text-xs text-muted-foreground">
                  Files: {selected.files.join(", ")}
                </p>
              )}
              <div className="mt-5">
                <Markdown text={selected.body} />
              </div>
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">Pick a skill on the left.</p>
          )}
        </div>
      </div>
    </div>
  )
}
