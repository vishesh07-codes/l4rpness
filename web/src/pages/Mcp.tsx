import { useEffect, useState } from "react"

import { PageHeader } from "@/components/PageHeader"
import { Plus, Trash2, TriangleAlert } from "@/components/icons"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { api, type McpServer, type McpStatus } from "@/lib/api"
import { cn } from "@/lib/utils"

const BLANK: McpServer = {
  name: "",
  command: [],
  url: "",
  env: {},
  headers: {},
  enabled: [],
  connected: false,
  error: "",
  available: [],
}

function envText(env: Record<string, string>) {
  return Object.entries(env)
    .map(([key, value]) => `${key}=${value}`)
    .join("\n")
}

function parseEnv(text: string) {
  const env: Record<string, string> = {}
  for (const line of text.split("\n")) {
    const trimmed = line.trim()
    if (!trimmed || !trimmed.includes("=")) continue
    const index = trimmed.indexOf("=")
    env[trimmed.slice(0, index).trim()] = trimmed.slice(index + 1).trim()
  }
  return env
}

export function Mcp() {
  const [status, setStatus] = useState<McpStatus | null>(null)
  const [servers, setServers] = useState<McpServer[]>([])
  const [selected, setSelected] = useState(-1)
  const [error, setError] = useState("")
  const [busy, setBusy] = useState(false)
  const [dirty, setDirty] = useState(false)

  const load = () =>
    api
      .mcp()
      .then((data) => {
        setStatus(data)
        setServers(data.servers)
        setSelected(data.servers.length ? 0 : -1)
      })
      .catch((e: Error) => setError(e.message))

  useEffect(() => {
    load()
  }, [])

  // warn before a refresh/close throws away edited servers
  useEffect(() => {
    if (!dirty) return
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault()
      e.returnValue = ""
    }
    window.addEventListener("beforeunload", onBeforeUnload)
    return () => window.removeEventListener("beforeunload", onBeforeUnload)
  }, [dirty])

  function edit(index: number, patch: Partial<McpServer>) {
    setServers((prev) => prev.map((server, i) => (i === index ? { ...server, ...patch } : server)))
    setDirty(true)
  }

  function addServer() {
    setServers((prev) => [...prev, { ...BLANK }])
    setSelected(servers.length)
    setDirty(true)
  }

  function removeServer(index: number) {
    setServers((prev) => prev.filter((_, i) => i !== index))
    setSelected((current) => (current === index ? -1 : current > index ? current - 1 : current))
    setDirty(true)
  }

  async function save() {
    setBusy(true)
    setError("")
    try {
      const data = await api.saveMcp(servers)
      setStatus(data)
      setServers(data.servers)
      setDirty(false)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const server = selected >= 0 ? servers[selected] : null
  const toolNames = server ? [...new Set([...server.available, ...server.enabled])].sort() : []

  return (
    <div className="mx-auto flex h-svh max-w-6xl flex-col px-8 py-10 max-md:pt-16">
      <PageHeader
        title="MCP"
        description="External MCP servers the agent can call. Tools are namespaced as server_tool."
        action={
          <Button size="sm" onClick={addServer}>
            <Plus className="size-4" /> New server
          </Button>
        }
      />

      <Alert className="mb-4">
        <TriangleAlert className="size-4" />
        <AlertTitle>Restart to apply</AlertTitle>
        <AlertDescription>
          Saving writes data/mcp.json. Run docker compose restart to connect new or changed servers.
        </AlertDescription>
      </Alert>

      {error && (
        <Alert variant="destructive" className="mb-4">
          <TriangleAlert />
          <AlertTitle>Could not save MCP servers</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-2xl shadow-card lg:flex-row">
        <div className="max-h-[45svh] w-full shrink-0 overflow-y-auto border-b border-line p-2 lg:max-h-none lg:w-72 lg:border-b-0 lg:border-r">
          {servers.length === 0 && (
            <p className="p-4 text-sm text-ink-2">No servers configured.</p>
          )}
          {servers.map((entry, index) => {
            const live = status?.servers[index]
            return (
              <button
                key={`${entry.name}-${index}`}
                type="button"
                onClick={() => setSelected(index)}
                aria-current={selected === index ? "true" : undefined}
                className={cn(
                  "flex w-full flex-col gap-1 rounded-lg px-4 py-3 text-left transition-colors duration-100 hover:bg-hover/50",
                  selected === index && "material-key"
                )}
              >
                <span className="flex items-center gap-2">
                  <span className="min-w-0 truncate font-mono text-[13px]">{entry.name || "unnamed"}</span>
                  <Badge variant={live?.connected ? "secondary" : "outline"} className="shrink-0 text-[10px]">
                    {live?.connected ? "connected" : live?.error ? "failed" : "not connected"}
                  </Badge>
                </span>
                <span className="min-w-0 truncate text-xs text-ink-2">
                  {entry.command.length ? entry.command.join(" ") : entry.url || "no command"}
                </span>
              </button>
            )
          })}
        </div>

        <div className="min-w-0 flex-1 overflow-y-auto p-6">
          {server ? (
            <form
              className="flex max-w-2xl flex-col gap-4"
              onSubmit={(e) => {
                e.preventDefault()
                save()
              }}
            >
              {status?.servers[selected]?.error && (
                <p className="text-sm text-destructive">
                  Startup error: {status.servers[selected].error}
                </p>
              )}

              <div className="flex flex-col gap-2">
                <Label htmlFor="mcp-name">Name</Label>
                <Input
                  id="mcp-name"
                  value={server.name}
                  onChange={(e) => edit(selected, { name: e.target.value })}
                  placeholder="frank"
                />
              </div>

              <div className="flex flex-col gap-2">
                <Label htmlFor="mcp-command">Command</Label>
                <Input
                  id="mcp-command"
                  value={server.command.join(" ")}
                  onChange={(e) =>
                    edit(selected, { command: e.target.value.split(" ").filter(Boolean) })
                  }
                  placeholder="python /frank/server.py"
                />
              </div>

              <div className="flex flex-col gap-2">
                <Label htmlFor="mcp-url">URL</Label>
                <Input
                  id="mcp-url"
                  value={server.url}
                  onChange={(e) => edit(selected, { url: e.target.value })}
                  placeholder="http://host:port/mcp"
                />
              </div>

              <div className="flex flex-col gap-2">
                <Label htmlFor="mcp-env">Env</Label>
                <Textarea
                  id="mcp-env"
                  value={envText(server.env)}
                  onChange={(e) => edit(selected, { env: parseEnv(e.target.value) })}
                  rows={3}
                  className="font-mono text-[13px]"
                  placeholder="KEY=value"
                />
              </div>

              <div className="flex flex-col gap-2">
                <Label>Tools</Label>
                {toolNames.length === 0 && (
                  <p className="text-xs text-ink-2">
                    No tools discovered yet. Restart after saving to list them.
                  </p>
                )}
                <div className="flex flex-wrap gap-3">
                  {toolNames.map((tool) => (
                    <label key={tool} className="flex items-center gap-2 text-sm">
                      <input
                        type="checkbox"
                        checked={server.enabled.includes(tool)}
                        onChange={(e) =>
                          edit(selected, {
                            enabled: e.target.checked
                              ? [...server.enabled, tool]
                              : server.enabled.filter((name) => name !== tool),
                          })
                        }
                      />
                      <span className="font-mono text-[13px]">{tool}</span>
                    </label>
                  ))}
                </div>
              </div>

              <div className="flex items-center justify-between">
                <Button
                  type="button"
                  variant="ghost"
                  className="text-destructive hover:text-destructive"
                  onClick={() => removeServer(selected)}
                >
                  <Trash2 className="size-4" /> Delete server
                </Button>
                <div className="flex items-center gap-2">
                  {dirty && <span className="text-xs text-ink-2">unsaved changes</span>}
                  <Button type="submit" disabled={busy || !server.name.trim()}>
                    {busy ? "Saving…" : "Save"}
                  </Button>
                </div>
              </div>
            </form>
          ) : (
            <p className="text-sm text-ink-2">Pick a server on the left, or add one.</p>
          )}
        </div>
      </div>
    </div>
  )
}
