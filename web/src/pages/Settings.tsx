import { useEffect, useRef, useState } from "react"
import { toast } from "sonner"

import { PageHeader } from "@/components/PageHeader"
import { ConfirmDialog } from "@/components/ConfirmDialog"
import { TriangleAlert } from "@/components/icons"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import {
  api,
  type HostEnvState,
  type ProviderKeyStatus,
  type RuntimeConfig,
  type RuntimeField,
} from "@/lib/api"

function fmtRuntime(value: unknown) {
  if (Array.isArray(value)) return value.join(", ")
  if (value === null || value === undefined) return "—"
  return String(value)
}

function sourceLabel(row: ProviderKeyStatus) {
  if (row.source === "settings") return "set in Settings"
  if (row.source === "environment") return "set via environment"
  return "not set"
}

function SettingsError({ error }: { error: string }) {
  return (
    <Alert variant="destructive" className="mb-4">
      <TriangleAlert />
      <AlertTitle>Settings request failed</AlertTitle>
      <AlertDescription>{error}</AlertDescription>
    </Alert>
  )
}

function SettingsSkeleton() {
  return (
    <div className="flex flex-col gap-2" aria-hidden>
      {[0, 1].map((i) => (
        <div key={i} className="rounded-2xl bg-surface p-4 shadow-card">
          <Skeleton className="h-4 w-32" />
          <Skeleton className="mt-2 h-9 w-full" />
        </div>
      ))}
    </div>
  )
}

export function Settings() {
  const [rows, setRows] = useState<ProviderKeyStatus[] | null>(null)
  const [runtime, setRuntime] = useState<RuntimeConfig | null>(null)
  const [host, setHost] = useState<HostEnvState | null>(null)
  // presence means "edited"; the value is the unsent draft
  const [keyDrafts, setKeyDrafts] = useState<Record<string, string>>({})
  const [runtimeDrafts, setRuntimeDrafts] = useState<Record<string, string>>({})
  const [error, setError] = useState("")
  const [busy, setBusy] = useState(false)
  const [confirmReset, setConfirmReset] = useState(false)
  const lastAppliedAt = useRef<number | null>(null)
  // the poll interval below closes over render state, so reads of drafts go
  // through this mirror instead of a stale closure
  const keyDraftsRef = useRef(keyDrafts)
  keyDraftsRef.current = keyDrafts

  const keysDirty = Object.keys(keyDrafts).length > 0
  const runtimeDirty = Object.keys(runtimeDrafts).length > 0

  useEffect(() => {
    Promise.all([api.settingsProviders(), api.settingsConfig(), api.hostEnv()])
      .then(([providers, config, hostEnv]) => {
        setRows(providers)
        setRuntime(config)
        setHost(hostEnv)
        lastAppliedAt.current = hostEnv.last_applied.at
      })
      .catch((e: Error) => setError(e.message))
  }, [])

  // the backend applies outside host .env edits by itself; this just notices
  // so the form can refresh and say what happened. Deliberately independent
  // of `busy`: a background refresh must never lock the Save buttons or flip
  // their labels, nor be unlocked early by a concurrent user-initiated save.
  useEffect(() => {
    const id = window.setInterval(() => {
      api
        .hostEnv()
        .then((state) => {
          setHost(state)
          const at = state.last_applied.at
          if (at == null) return
          if (lastAppliedAt.current != null && at <= lastAppliedAt.current) return
          lastAppliedAt.current = at
          const vars = state.last_applied.vars
          const dropped = vars.filter((v) => v in keyDraftsRef.current)
          if (dropped.length > 0) {
            // the host change just overrode these edits — keeping the stale
            // drafts would clobber the applied values on the next save
            setKeyDrafts((prev) => {
              const next = { ...prev }
              dropped.forEach((v) => delete next[v])
              return next
            })
          }
          void (async () => {
            try {
              setRows(await api.settingsProviders())
              toast.success(
                vars.length > 0
                  ? `Host .env changed — applied: ${vars.join(", ")}${
                      dropped.length > 0 ? " (discarded conflicting edits)" : ""
                    }`
                  : "Host .env changed — applied"
              )
            } catch {
              // silent: the next poll retries
            }
          })()
        })
        .catch(() => {})
    }, 10000)
    return () => window.clearInterval(id)
  }, [])

  function editKey(key: string, value: string) {
    setKeyDrafts((drafts) => ({ ...drafts, [key]: value }))
  }

  function editRuntime(key: string, value: string) {
    setRuntimeDrafts((drafts) => ({ ...drafts, [key]: value }))
  }

  function clearKey(row: ProviderKeyStatus) {
    if (keyDrafts[row.env_var] !== undefined) {
      setKeyDrafts(({ [row.env_var]: _, ...rest }) => rest)
      return
    }
    // clearing only drops the Settings override — for host-backed keys there
    // is no override to drop, so there is nothing to stage
    if (row.source === "settings") {
      setKeyDrafts((drafts) => ({ ...drafts, [row.env_var]: "" }))
    }
  }

  async function runBusy(action: () => Promise<void>) {
    setBusy(true)
    setError("")
    try {
      await action()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  // saving writes everywhere at once: overlay + live env, plus the host file
  // when it is writable. Clearing a row drops the override; host lines stay.
  async function saveKeys() {
    const names = Object.keys(keyDrafts)
    if (names.length === 0) return
    const fresh = await api.saveProviderKeys({ ...keyDrafts })
    let pushed: string[] = []
    if (host?.writable) {
      try {
        const res = await api.pushHostEnv({ ...keyDrafts })
        setRows(res.providers)
        setHost(res.host)
        pushed = res.written
      } catch (e) {
        setRows(fresh)
        setHost(await api.hostEnv())
        throw e
      }
    } else {
      setRows(fresh)
    }
    setKeyDrafts({})
    toast.success(
      pushed.length > 0
        ? `Saved ${names.join(", ")} · host .env updated`
        : `Saved ${names.join(", ")}`
    )
  }

  async function saveRuntime() {
    const names = Object.keys(runtimeDrafts)
    if (names.length === 0) return
    setRuntime(await api.saveRuntimeConfig({ ...runtimeDrafts }))
    setRuntimeDrafts({})
    toast.success(
      names.length === 1 ? `Saved ${names[0]}` : `Saved ${names.length} runtime settings`
    )
  }

  async function doReset() {
    setRuntime(await api.resetRuntimeConfig())
    setRuntimeDrafts({})
    toast.success("Runtime settings reset to config.toml")
  }

  return (
    <div className="mx-auto max-w-2xl px-8 py-10 max-md:pt-16">
      <PageHeader
        title="Settings"
        description="Provider keys and runtime knobs. Saves apply immediately everywhere; edits to the host .env outside the app are picked up automatically."
      />

      {error && <SettingsError error={error} />}

      {!rows && !error && <SettingsSkeleton />}

      {rows && (
        <ProviderKeys
          rows={rows}
          host={host}
          drafts={keyDrafts}
          dirty={keysDirty}
          busy={busy}
          onEdit={editKey}
          onClear={clearKey}
          onSave={() => void runBusy(saveKeys)}
        />
      )}

      {runtime && (
        <RuntimeSettings
          runtime={runtime}
          drafts={runtimeDrafts}
          dirty={runtimeDirty}
          busy={busy}
          onEdit={editRuntime}
          onSave={() => void runBusy(saveRuntime)}
          onReset={() => setConfirmReset(true)}
        />
      )}

      <ConfirmDialog
        open={confirmReset}
        onOpenChange={(open) => !open && !busy && setConfirmReset(false)}
        title="Reset runtime settings?"
        description="Every UI-managed runtime override will be removed and config.toml wins again."
        confirmLabel="Reset to defaults"
        busy={busy}
        onConfirm={() => {
          setConfirmReset(false)
          void runBusy(doReset)
        }}
      />
    </div>
  )
}

function ProviderKeys({
  rows,
  host,
  drafts,
  dirty,
  busy,
  onEdit,
  onClear,
  onSave,
}: {
  rows: ProviderKeyStatus[]
  host: HostEnvState | null
  drafts: Record<string, string>
  dirty: boolean
  busy: boolean
  onEdit: (key: string, value: string) => void
  onClear: (row: ProviderKeyStatus) => void
  onSave: () => void
}) {
  return (
    <section aria-label="Provider API keys" className="rounded-2xl bg-surface p-4 shadow-card">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="min-w-0 flex-1 text-[13px] font-medium">Provider keys</h2>
        {dirty && <span className="text-xs text-ink-2">unsaved changes</span>}
        <Button
          type="button"
          size="sm"
          onClick={onSave}
          disabled={!dirty || busy}
          className="ml-auto shrink-0"
        >
          {busy ? "Saving…" : "Save keys"}
        </Button>
      </div>
      <p className="mt-2 font-mono text-[10px] text-ink-3">
        {host == null ? (
          "host .env: checking…"
        ) : host.mounted ? (
          <>host .env · tracked{host.writable ? " · writable" : " · read-only"} · auto-sync on</>
        ) : (
          "host .env: not mounted — keys live in the Settings overlay"
        )}
      </p>
      <div className="mt-3 flex flex-col gap-4">
        {rows.map((row) => {
          const draft = drafts[row.env_var] ?? ""
          const staged = drafts[row.env_var] !== undefined
          // clearing a host-backed key is a no-op (it only drops overrides),
          // so the button stays off unless there is typing to discard or an
          // override to stage for removal
          const clearable = draft !== "" || row.source === "settings"
          const id = `key-${row.env_var}`
          return (
            <div key={row.provider}>
              <div className="flex items-center gap-2">
                <span className="min-w-0 flex-1 truncate text-[13px] font-medium capitalize">
                  {row.provider}
                </span>
                <Badge
                  variant={row.configured ? "secondary" : "outline"}
                  className="shrink-0 text-[10px]"
                >
                  {sourceLabel(row)}
                </Badge>
              </div>
              <Label htmlFor={id} className="mt-1.5 block font-mono text-[12px] text-ink-2">
                {row.env_var}
              </Label>
              <div className="mt-2 flex gap-2">
                <Input
                  id={id}
                  type="password"
                  autoComplete="new-password"
                  value={draft}
                  onChange={(e) => onEdit(row.env_var, e.target.value)}
                  placeholder={
                    row.configured && row.suffix
                      ? `••••${row.suffix} (${row.length} chars) — type to replace`
                      : "paste a new key"
                  }
                  className="font-mono text-[13px]"
                />
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    onClick={() => onClear(row)}
                    disabled={!clearable}
                    className="shrink-0 text-ink-3 hover:text-destructive"
                  >
                    Clear
                  </Button>
              </div>
              {staged && (
                <p className="mt-1 text-[11px] text-ink-3">
                  {draft ? "Staged — press Save keys to apply." : "Staged for removal."}
                </p>
              )}
            </div>
          )
        })}
      </div>
    </section>
  )
}

function RuntimeSettings({
  runtime,
  drafts,
  dirty,
  busy,
  onEdit,
  onSave,
  onReset,
}: {
  runtime: RuntimeConfig
  drafts: Record<string, string>
  dirty: boolean
  busy: boolean
  onEdit: (key: string, value: string) => void
  onSave: () => void
  onReset: () => void
}) {
  const effective = (key: string) => drafts[key] ?? fmtRuntime(runtime.values[key])
  return (
    <section aria-label="Runtime settings" className="mt-3">
      {runtime.sections.map((section) => {
        const fields = runtime.fields.filter((f) => f.section === section.id)
        if (fields.length === 0) return null
        return (
          <div
            key={section.id}
            className="mt-3 rounded-2xl bg-surface p-4 shadow-card first:mt-0"
          >
            <h3 className="text-[13px] font-medium">{section.title}</h3>
            <div className="mt-3 grid gap-4 sm:grid-cols-2">
              {fields.map((field) => (
                <RuntimeFieldInput
                  key={field.key}
                  field={field}
                  value={effective(field.key)}
                  custom={runtime.sources[field.key] === "settings"}
                  onChange={(value) => onEdit(field.key, value)}
                />
              ))}
            </div>
          </div>
        )
      })}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        {dirty && <span className="text-xs text-ink-2">unsaved changes</span>}
        <div className="ml-auto flex gap-2">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={onReset}
            disabled={busy}
            className="text-ink-3 hover:text-destructive"
          >
            Reset to defaults
          </Button>
          <Button type="button" size="sm" onClick={onSave} disabled={!dirty || busy}>
            {busy ? "Saving…" : "Save runtime"}
          </Button>
        </div>
      </div>
      <p className="mt-3 text-xs text-pretty text-ink-3">
        Saved keys and overrides apply immediately and persist in the data volume. When the
        host .env is mounted and writable, key saves also update it; clearing a row drops the
        override but never deletes host lines. Server binding, storage paths, and provider
        wiring stay config.toml-only and need a recreate.
      </p>
    </section>
  )
}

function RuntimeFieldInput({
  field,
  value,
  custom,
  onChange,
}: {
  field: RuntimeField
  value: string
  custom: boolean
  onChange: (value: string) => void
}) {
  const id = `cfg-${field.key}`
  return (
    <div className="flex min-w-0 flex-col gap-1.5">
      <div className="flex items-center gap-2">
        <Label htmlFor={id} className="text-[13px]">
          {field.label}
        </Label>
        {custom && (
          <span className="rounded-full bg-inset px-1.5 font-mono text-[10px] text-ink-3">
            custom
          </span>
        )}
      </div>
      {field.kind === "select" ? (
        <Select value={value} onValueChange={onChange}>
          <SelectTrigger id={id} className="w-full font-mono text-[13px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {(field.options ?? []).map((option) => (
              <SelectItem key={option} value={option} className="font-mono text-[13px]">
                {option}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      ) : (
        <Input
          id={id}
          type={field.kind === "text" || field.kind === "list" ? "text" : "number"}
          min={field.min}
          max={field.max}
          step={field.step ?? (field.kind === "int" ? 1 : "any")}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className="font-mono text-[13px]"
        />
      )}
      {field.hint && <p className="text-xs text-ink-3">{field.hint}</p>}
    </div>
  )
}
