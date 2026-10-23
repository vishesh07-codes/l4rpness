import { useEffect, useState } from "react"
import { Link } from "react-router-dom"
import { Crosshair, Plus, Square, Trash2 } from "lucide-react"

import { Pulse } from "@/components/Pulse"
import { StatusBadge } from "@/components/StatusBadge"
import { Button } from "@/components/ui/button"
import { api, type Campaign } from "@/lib/api"
import { fmtDate } from "@/lib/utils"

export function Dashboard() {
  const [campaigns, setCampaigns] = useState<Campaign[] | null>(null)
  const [error, setError] = useState("")
  const [busy, setBusy] = useState(0)

  const load = () =>
    api
      .listCampaigns()
      .then(setCampaigns)
      .catch((e: Error) => setError(e.message))

  useEffect(() => {
    load()
  }, [])

  const anyRunning = campaigns?.some((c) => c.running) ?? false

  useEffect(() => {
    if (!anyRunning) return
    const timer = setInterval(load, 3000)
    return () => clearInterval(timer)
  }, [anyRunning])

  async function stop(campaign: Campaign) {
    setBusy(campaign.id)
    setError("")
    try {
      await api.stopCampaign(campaign.id)
      await load()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(0)
    }
  }


  async function remove(campaign: Campaign) {
    if (!confirm(`Delete "${campaign.target.split("\n")[0]}"? Messages and files are removed too.`)) {
      return
    }

    setBusy(campaign.id)
    setError("")
    try {
      await api.deleteCampaign(campaign.id)
      setCampaigns((prev) => prev?.filter((c) => c.id !== campaign.id) ?? prev)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(0)
    }
  }

  return (
    <div className="mx-auto max-w-5xl px-8 py-10">
      <header className="mb-6 flex items-center justify-between gap-4">
        <div className="min-w-0">
          <h1 className="text-lg font-semibold text-balance">Campaigns</h1>
          <p className="text-sm text-pretty text-muted-foreground">
            Black-box targets you are testing.
          </p>
        </div>
        <Button asChild size="sm" className="shrink-0">
          <Link to="/campaigns/new">
            <Plus /> New campaign
          </Link>
        </Button>
      </header>

      {error && <p className="mb-4 text-sm text-destructive">{error}</p>}

      {!error && campaigns === null && <TableSkeleton />}

      {campaigns?.length === 0 && (
        <div className="flex flex-col items-center justify-center rounded-[12px] border border-dashed py-16">
          <div className="flex size-10 items-center justify-center rounded-full bg-muted text-muted-foreground">
            <Crosshair className="size-5" strokeWidth={1.5} />
          </div>
          <p className="mt-4 text-sm font-medium text-balance">No campaigns yet</p>
          <p className="mt-1 max-w-xs text-center text-sm text-pretty text-muted-foreground">
            Create a campaign to start probing a target and capture what it does.
          </p>
          <Button asChild variant="outline" size="sm" className="mt-4">
            <Link to="/campaigns/new">Create the first one</Link>
          </Button>
        </div>
      )}

      {campaigns && campaigns.length > 0 && (
        <div className="overflow-hidden rounded-[12px] shadow-hairline">
          <table className="w-full text-sm">
            <thead className="bg-muted/50 text-left text-xs text-muted-foreground">
              <tr>
                <th className="px-4 py-2 font-medium">Target</th>
                <th className="px-4 py-2 font-medium">Model</th>
                <th className="px-4 py-2 font-medium">Status</th>
                <th className="px-4 py-2 text-right font-medium tabular-nums">Messages</th>
                <th className="px-4 py-2 font-medium">Created</th>
                <th className="w-10 px-4 py-2" />
              </tr>
            </thead>
            <tbody>
              {campaigns.map((c) => (
                <tr
                  key={c.id}
                  className="border-t transition-colors duration-100 hover:bg-muted/30"
                >
                  <td className="px-4 py-2.5">
                    <div className="max-w-md truncate">
                      <Link
                        to={`/campaigns/${c.id}`}
                        className="rounded font-mono text-[13px] outline-none hover:underline focus-visible:ring-2 focus-visible:ring-ring/50"
                        title={c.target}
                      >
                        {c.target.split("\n")[0]}
                      </Link>
                    </div>
                  </td>
                  <td className="px-4 py-2.5 text-muted-foreground">{c.model}</td>
                  <td className="px-4 py-2.5">
                    {c.running ? (
                      <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
                        <Pulse />
                        running
                      </span>
                    ) : (
                      <StatusBadge status={c.status} />
                    )}
                  </td>
                  <td className="px-4 py-2.5 text-right text-muted-foreground tabular-nums">
                    {c.message_count ?? 0}
                  </td>
                  <td className="px-4 py-2.5 whitespace-nowrap text-muted-foreground tabular-nums">
                    {fmtDate(c.created_at)}
                  </td>
                  <td className="px-4 py-2.5 text-right">
                    <div className="flex items-center justify-end gap-1">
                      {c.running && (
                        <Button
                          variant="ghost"
                          size="icon"
                          className="relative size-7 text-muted-foreground after:absolute after:top-1/2 after:left-1/2 after:size-9 after:-translate-1/2"
                          title="Stop campaign"
                          disabled={busy === c.id}
                          onClick={() => stop(c)}
                        >
                          <Square className="size-3.5" strokeWidth={1.5} />
                        </Button>
                      )}
                      <Button
                        variant="ghost"
                        size="icon"
                        className="relative size-7 text-muted-foreground after:absolute after:top-1/2 after:left-1/2 after:size-9 after:-translate-1/2 hover:text-destructive"
                        title="Delete campaign"
                        disabled={busy === c.id}
                        onClick={() => remove(c)}
                      >
                        <Trash2 className="size-3.5" strokeWidth={1.5} />
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

function TableSkeleton() {
  return (
    <div className="overflow-hidden rounded-[12px] shadow-hairline" aria-hidden>
      <div className="flex gap-4 bg-muted/50 px-4 py-2">
        {[40, 20, 14, 10, 24].map((width, i) => (
          <div key={i} className="h-3 rounded-full bg-muted" style={{ width: `${width}%` }} />
        ))}
      </div>
      {[0, 1, 2, 3, 4].map((row) => (
        <div
          key={row}
          className="flex animate-pulse items-center gap-4 border-t px-4 py-3"
          style={{ animationDelay: `${row * 80}ms` }}
        >
          <div className="h-3.5 w-[38%] rounded bg-muted" />
          <div className="h-3.5 w-[18%] rounded bg-muted" />
          <div className="h-4 w-[12%] rounded-full bg-muted" />
          <div className="ml-auto h-3.5 w-[8%] rounded bg-muted" />
          <div className="h-3.5 w-[22%] rounded bg-muted" />
        </div>
      ))}
    </div>
  )
}
