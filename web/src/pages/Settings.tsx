import { useEffect, useRef, useState } from "react"
import { toast } from "sonner"

import { PageHeader } from "@/components/PageHeader"
import { ConfirmDialog } from "@/components/ConfirmDialog"
import { PencilLine, Plus, Trash2, TriangleAlert } from "@/components/icons"
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
  type Endpoint,
  type EndpointsState,
  type HostConfigState,
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
  const [hostConfig, setHostConfig] = useState<HostConfigState | null>(null)
  const [endpoints, setEndpoints] = useState<EndpointsState | null>(null)
  // presence means "edited"; the value is the unsent draft
  const [keyDrafts, setKeyDrafts] = useState<Record<string, string>>({})
  const [runtimeDrafts, setRuntimeDrafts] = useState<Record<string, string>>({})
  const [error, setError] = useState("")
  const [busy, setBusy] = useState(false)
  const [confirmReset, setConfirmReset] = useState(false)
  const lastAppliedAt = useRef<number | null>(null)
  const lastConfigAppliedAt = useRef<number | null>(null)
  // the poll intervals below close over render state, so reads of drafts go
  // through these mirrors instead of a stale closure
  const keyDraftsRef = useRef(keyDrafts)
  keyDraftsRef.current = keyDrafts
  const runtimeDraftsRef = useRef(runtimeDrafts)
  runtimeDraftsRef.current = runtimeDrafts

  const keysDirty = Object.keys(keyDrafts).length > 0
  const runtimeDirty = Object.keys(runtimeDrafts).length > 0

  useEffect(() => {
    Promise.all([
      api.settingsProviders(),
      api.settingsConfig(),
      api.hostEnv(),
      api.hostConfig(),
      api.settingsEndpoints(),
    ])
      .then(([providers, config, hostEnv, hostCfg, eps]) => {
        setRows(providers)
        setRuntime(config)
        setHost(hostEnv)
        setHostConfig(hostCfg)
        setEndpoints(eps)
        lastAppliedAt.current = hostEnv.last_applied.at
        lastConfigAppliedAt.current = hostCfg.last_applied.at
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

  // same as above for the host config.toml watcher
  useEffect(() => {
    const id = window.setInterval(() => {
      api
        .hostConfig()
        .then((state) => {
          setHostConfig(state)
          const at = state.last_applied.at
          if (at == null) return
          if (lastConfigAppliedAt.current != null && at <= lastConfigAppliedAt.current) return
          lastConfigAppliedAt.current = at
          const keys = state.last_applied.keys
          const dropped = keys.filter((k) => k in runtimeDraftsRef.current)
          if (dropped.length > 0) {
            // the host change just overrode these edits — keeping the stale
            // drafts would clobber the applied values on the next save
            setRuntimeDrafts((prev) => {
              const next = { ...prev }
              dropped.forEach((k) => delete next[k])
              return next
            })
          }
          void (async () => {
            try {
              const [config, eps] = await Promise.all([
                api.settingsConfig(),
                api.settingsEndpoints(),
              ])
              setRuntime(config)
              setEndpoints(eps)
              toast.success(
                keys.length > 0
                  ? `Host config.toml changed — applied: ${keys.join(", ")}${
                      dropped.length > 0 ? " (discarded conflicting edits)" : ""
                    }`
                  : "Host config.toml changed — applied"
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

  // like saveKeys: overlay + live settings always, plus the host file when
  // it is writable; pushing drops the now-redundant overlay copies
  async function saveRuntime() {
    const names = Object.keys(runtimeDrafts)
    if (names.length === 0) return
    const fresh = await api.saveRuntimeConfig({ ...runtimeDrafts })
    let pushed: string[] = []
    if (hostConfig?.writable) {
      try {
        const res = await api.pushHostConfig({ ...runtimeDrafts })
        setRuntime(res.runtime)
        setHostConfig(res.host)
        pushed = res.written
      } catch (e) {
        setRuntime(fresh)
        setHostConfig(await api.hostConfig())
        throw e
      }
    } else {
      setRuntime(fresh)
    }
    setRuntimeDrafts({})
    const label = names.length === 1 ? names[0] : `${names.length} runtime settings`
    toast.success(pushed.length > 0 ? `Saved ${label} · host config.toml updated` : `Saved ${label}`)
  }

  async function doReset() {
    setRuntime(await api.resetRuntimeConfig())
    setRuntimeDrafts({})
    toast.success("Runtime settings reset to config.toml")
  }

  // endpoint edits touch config.toml itself, so refresh everything derived:
  // provider key rows, the "Default provider" options and the host state
  async function refreshAfterEndpoints(next: EndpointsState) {
    setEndpoints(next)
    const [providers, config, hostCfg, hostEnv] = await Promise.all([
      api.settingsProviders(),
      api.settingsConfig(),
      api.hostConfig(),
      api.hostEnv(),
    ])
    setRows(providers)
    setRuntime(config)
    setHostConfig(hostCfg)
    setHost(hostEnv)
  }

  return (
    <div className="mx-auto max-w-2xl px-8 py-10 max-md:pt-16">
      <PageHeader
        title="Settings"
        description="Provider keys, model endpoints and runtime knobs. Saves apply immediately everywhere; edits to the host .env and config.toml outside the app are picked up automatically."
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

      {endpoints && (
        <EndpointSettings state={endpoints} host={host} onChanged={refreshAfterEndpoints} />
      )}

      {runtime && (
        <RuntimeSettings
          runtime={runtime}
          host={hostConfig}
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
  host,
  drafts,
  dirty,
  busy,
  onEdit,
  onSave,
  onReset,
}: {
  runtime: RuntimeConfig
  host: HostConfigState | null
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
      <p className="mt-3 font-mono text-[10px] text-ink-3">
        {host == null ? (
          "host config.toml: checking…"
        ) : host.mounted ? (
          <>host config.toml · tracked{host.writable ? " · writable" : " · read-only"} · auto-sync on</>
        ) : (
          "host config.toml: not mounted — overrides live in the Settings overlay"
        )}
      </p>
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
        host .env or config.toml is mounted and writable, saves also write through to it, and
        outside edits to either file are applied automatically. Endpoints are edited directly
        in config.toml. Server binding and storage paths stay config.toml-only and need a
        recreate.
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

type EndpointForm = {
  name: string
  base_url: string
  api_key_env: string
  api_key: string
  thinking: boolean
}

const EMPTY_ENDPOINT: EndpointForm = {
  name: "",
  base_url: "",
  api_key_env: "",
  api_key: "",
  thinking: false,
}

// `myrouter` -> `MYROUTER_API_KEY`; saves people from inventing var names
function deriveKeyVar(name: string) {
  const slug = name
    .trim()
    .toUpperCase()
    .replace(/[^A-Z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
  return slug ? `${slug}_API_KEY` : ""
}

const ENDPOINT_NAME_RE = /^[A-Za-z0-9][A-Za-z0-9_-]*$/
const KEY_VAR_RE = /^[A-Z][A-Z0-9_]*$/

function endpointNameError(value: string) {
  const text = value.trim()
  if (!text) return "name is required"
  if (!ENDPOINT_NAME_RE.test(text))
    return "letters, digits, '-' and '_' only (must start with a letter or digit)"
  return ""
}

function endpointUrlError(value: string) {
  const text = value.trim()
  if (!text) return "base URL is required"
  if (!/^https?:\/\//.test(text)) return "must start with http:// or https://"
  return ""
}

function endpointKeyVarError(value: string) {
  const text = value.trim()
  if (!text) return "key variable name is required"
  if (!KEY_VAR_RE.test(text)) return "upper-case letters, digits and '_' only, e.g. NEWROUTER_API_KEY"
  return ""
}

const FIELD_ERROR = "mt-1 text-[11px] text-destructive"

// Model endpoints, straight from config.toml's `llm.providers` tables. Each
// edit is written through to the host file immediately, and the rest of the
// page (provider keys, default-provider options) refreshes from onChanged.
function EndpointSettings({
  state,
  host,
  onChanged,
}: {
  state: EndpointsState
  host: HostEnvState | null
  onChanged: (next: EndpointsState) => Promise<void>
}) {
  // "" opens the new-endpoint form; an endpoint name edits that row
  const [editing, setEditing] = useState<string | null>(null)
  const [form, setForm] = useState<EndpointForm>(EMPTY_ENDPOINT)
  const [envTouched, setEnvTouched] = useState(false)
  const [submitted, setSubmitted] = useState(false)
  const [busy, setBusy] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null)

  const locked = busy || !state.writable

  async function run(action: () => Promise<EndpointsState>, done?: () => void) {
    setBusy(true)
    try {
      await onChanged(await action())
      done?.()
    } catch (e) {
      toast.error((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  // typing the name fills the key variable until someone edits it by hand
  function patchForm(patch: Partial<EndpointForm>) {
    if (patch.api_key_env !== undefined) setEnvTouched(true)
    setForm((prev) => {
      const next = { ...prev, ...patch }
      if (patch.name !== undefined && !envTouched) {
        next.api_key_env = deriveKeyVar(patch.name)
      }
      return next
    })
  }

  function startNew() {
    setEditing("")
    setForm({ ...EMPTY_ENDPOINT })
    setEnvTouched(false)
    setSubmitted(false)
  }

  function startEdit(endpoint: Endpoint) {
    setEditing(endpoint.name)
    setForm({
      name: endpoint.name,
      base_url: endpoint.base_url,
      api_key_env: endpoint.api_key_env,
      api_key: "",
      thinking: endpoint.thinking,
    })
    setEnvTouched(true)
    setSubmitted(false)
  }

  function cancel() {
    setEditing(null)
    setSubmitted(false)
  }

  function save() {
    if (
      endpointNameError(form.name) ||
      endpointUrlError(form.base_url) ||
      endpointKeyVarError(form.api_key_env)
    ) {
      setSubmitted(true)
      return
    }
    const key = form.api_key.trim()
    const keyVar = form.api_key_env.trim()
    void run(
      async () => {
        const next = await api.saveEndpoint({
          name: form.name.trim(),
          base_url: form.base_url.trim(),
          api_key_env: keyVar,
          thinking: form.thinking,
        })
        if (key) {
          // same store as the Provider keys section: the overlay now, the
          // host .env too when it is writable
          await api.saveProviderKeys({ [keyVar]: key })
          if (host?.writable) {
            try {
              await api.pushHostEnv({ [keyVar]: key })
            } catch (e) {
              toast.warning(
                `Key saved in Settings, but the host .env update failed: ${(e as Error).message}`
              )
            }
          }
        }
        return next
      },
      () => {
        toast.success(
          key ? `Saved endpoint ${form.name} and its API key` : `Saved endpoint ${form.name}`
        )
        setEditing(null)
      }
    )
  }

  return (
    <section aria-label="Model endpoints" className="mt-3 rounded-2xl bg-surface p-4 shadow-card">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="min-w-0 flex-1 text-[13px] font-medium">Endpoints</h2>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={startNew}
          disabled={locked || editing !== null}
          className="ml-auto shrink-0"
        >
          <Plus className="size-3.5" /> Add endpoint
        </Button>
      </div>
      <p className="mt-2 font-mono text-[10px] text-ink-3">
        config.toml · llm.providers ·{" "}
        {!state.mounted
          ? "not mounted — editing needs the file"
          : state.writable
            ? "writable"
            : "read-only — editing needs a writable mount"}
      </p>

      {editing === "" && (
        <EndpointFormRow
          id="endpoint-new"
          newEndpoint
          form={form}
          busy={busy}
          submitted={submitted}
          onChange={patchForm}
          onSave={save}
          onCancel={cancel}
        />
      )}

      <div className="mt-3 flex flex-col gap-3">
        {state.endpoints.length === 0 && (
          <p className="text-xs text-ink-3">No endpoints configured.</p>
        )}
        {state.endpoints.map((endpoint) =>
          editing === endpoint.name ? (
            <EndpointFormRow
              key={endpoint.name}
              id={`endpoint-${endpoint.name}`}
              form={form}
              busy={busy}
              submitted={submitted}
              keyConfigured={endpoint.key_configured}
              onChange={patchForm}
              onSave={save}
              onCancel={cancel}
            />
          ) : (
            <div key={endpoint.name} className="rounded-[12px] bg-inset px-3 py-2.5">
              <div className="flex items-center gap-2">
                <span className="min-w-0 flex-1 truncate font-mono text-[13px] font-medium">
                  {endpoint.name}
                </span>
                {endpoint.default && (
                  <Badge variant="secondary" className="shrink-0 text-[10px]">
                    default
                  </Badge>
                )}
                <Badge
                  variant={endpoint.key_configured ? "secondary" : "outline"}
                  className="shrink-0 text-[10px]"
                >
                  {endpoint.key_configured ? "key set" : "no key"}
                </Badge>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={() => startEdit(endpoint)}
                  disabled={locked}
                  aria-label={`Edit ${endpoint.name}`}
                  className="shrink-0 text-ink-3"
                >
                  <PencilLine className="size-3.5" />
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={() => setConfirmDelete(endpoint.name)}
                  disabled={locked || endpoint.default}
                  aria-label={`Delete ${endpoint.name}`}
                  title={
                    endpoint.default
                      ? "the default endpoint cannot be deleted"
                      : `Delete ${endpoint.name}`
                  }
                  className="shrink-0 text-ink-3 hover:text-destructive"
                >
                  <Trash2 className="size-3.5" />
                </Button>
              </div>
              <p className="mt-1 truncate font-mono text-[11px] text-ink-2">{endpoint.base_url}</p>
              <p className="mt-0.5 font-mono text-[11px] text-ink-3">
                {endpoint.api_key_env}
                {endpoint.thinking ? " · thinking" : ""}
              </p>
            </div>
          )
        )}
      </div>

      <ConfirmDialog
        open={confirmDelete !== null}
        onOpenChange={(open) => !open && !busy && setConfirmDelete(null)}
        title={`Delete endpoint ${confirmDelete ?? ""}?`}
        description="It will be removed from config.toml. This cannot be undone."
        confirmLabel="Delete endpoint"
        busy={busy}
        onConfirm={() => {
          const name = confirmDelete
          setConfirmDelete(null)
          if (name) {
            void run(
              () => api.deleteEndpoint(name),
              () => {
                toast.success(`Deleted endpoint ${name}`)
                setEditing(null)
              }
            )
          }
        }}
      />
    </section>
  )
}

function EndpointFormRow({
  id,
  form,
  newEndpoint = false,
  keyConfigured = false,
  submitted,
  busy,
  onChange,
  onSave,
  onCancel,
}: {
  id: string
  form: EndpointForm
  newEndpoint?: boolean
  keyConfigured?: boolean
  submitted: boolean
  busy: boolean
  onChange: (patch: Partial<EndpointForm>) => void
  onSave: () => void
  onCancel: () => void
}) {
  const nameError = endpointNameError(form.name)
  const urlError = endpointUrlError(form.base_url)
  const keyVarError = endpointKeyVarError(form.api_key_env)
  return (
    <div className="mt-3 rounded-[12px] bg-inset px-3 py-3">
      <div className="grid gap-3 sm:grid-cols-2">
        {newEndpoint && (
          <div className="flex min-w-0 flex-col gap-1.5">
            <Label htmlFor={`${id}-name`} className="text-[12px]">
              Name
            </Label>
            <Input
              id={`${id}-name`}
              value={form.name}
              onChange={(e) => onChange({ name: e.target.value })}
              placeholder="myrouter"
              className="font-mono text-[13px]"
              autoFocus
            />
            {submitted && nameError && <p className={FIELD_ERROR}>{nameError}</p>}
          </div>
        )}
        <div className="flex min-w-0 flex-col gap-1.5">
          <Label htmlFor={`${id}-url`} className="text-[12px]">
            Base URL
          </Label>
          <Input
            id={`${id}-url`}
            value={form.base_url}
            onChange={(e) => onChange({ base_url: e.target.value })}
            placeholder="https://api.example.com/v1"
            className="font-mono text-[13px]"
          />
          {submitted && urlError && <p className={FIELD_ERROR}>{urlError}</p>}
        </div>
        <div className="flex min-w-0 flex-col gap-1.5">
          <Label htmlFor={`${id}-key`} className="text-[12px]">
            Key variable name
          </Label>
          <Input
            id={`${id}-key`}
            value={form.api_key_env}
            onChange={(e) => onChange({ api_key_env: e.target.value })}
            placeholder="MYROUTER_API_KEY"
            className="font-mono text-[13px]"
          />
          <p className="text-[11px] text-ink-3">
            Auto-filled from the name; this variable holds the key.
          </p>
          {submitted && keyVarError && <p className={FIELD_ERROR}>{keyVarError}</p>}
        </div>
        <div className="flex min-w-0 flex-col gap-1.5">
          <Label htmlFor={`${id}-apikey`} className="text-[12px]">
            API key (optional)
          </Label>
          <Input
            id={`${id}-apikey`}
            type="password"
            autoComplete="new-password"
            value={form.api_key}
            onChange={(e) => onChange({ api_key: e.target.value })}
            placeholder={
              keyConfigured ? "•••• already set — type to replace" : "paste the key, or set it later"
            }
            className="font-mono text-[13px]"
          />
          <p className="text-[11px] text-ink-3">
            Stored with your provider keys; you can also set it later in Provider keys.
          </p>
        </div>
        <label className="flex items-center gap-2 self-start text-[13px] sm:self-end sm:pb-2">
          <input
            type="checkbox"
            checked={form.thinking}
            onChange={(e) => onChange({ thinking: e.target.checked })}
          />
          thinking mode
        </label>
      </div>
      <div className="mt-3 flex justify-end gap-2">
        <Button type="button" variant="ghost" size="sm" onClick={onCancel} disabled={busy}>
          Cancel
        </Button>
        <Button type="button" size="sm" onClick={onSave} disabled={busy}>
          {busy ? "Saving…" : "Save endpoint"}
        </Button>
      </div>
    </div>
  )
}
