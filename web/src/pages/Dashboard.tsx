import { useEffect, useState } from "react"
import { Link } from "react-router-dom"
import { Plus, Trash2 } from "lucide-react"

import { StatusBadge } from "@/components/StatusBadge"
import { Button } from "@/components/ui/button"
import { api, type Campaign } from "@/lib/api"
import { fmtDate } from "@/lib/utils"

export function Dashboard() {
  const [campaigns, setCampaigns] = useState<Campaign[] | null>(null)
  const [error, setError] = useState("")
  const [busy, setBusy] = useState(0)

  useEffect(() => {
    api
      .listCampaigns()
      .then(setCampaigns)
      .catch((e: Error) => setError(e.message))
  }, [])

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
      <header className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold">Campaigns</h1>
          <p className="text-sm text-muted-foreground">Black-box targets you are testing.</p>
        </div>
        <Button asChild size="sm">
          <Link to="/campaigns/new">
            <Plus /> New campaign
          </Link>
        </Button>
      </header>

      {error && <p className="mb-4 text-sm text-destructive">{error}</p>}

      {!error && campaigns === null && (
        <p className="text-sm text-muted-foreground">Loading…</p>
      )}

      {campaigns?.length === 0 && (
        <div className="flex flex-col items-center justify-center rounded-lg border border-dashed py-16">
          <p className="text-sm text-muted-foreground">No campaigns yet.</p>
          <Button asChild variant="outline" size="sm" className="mt-4">
            <Link to="/campaigns/new">Create the first one</Link>
          </Button>
        </div>
      )}

      {campaigns && campaigns.length > 0 && (
        <div className="overflow-hidden rounded-lg border">
          <table className="w-full text-sm">
            <thead className="bg-muted/50 text-left text-xs text-muted-foreground">
              <tr>
                <th className="px-4 py-2 font-medium">Target</th>
                <th className="px-4 py-2 font-medium">Model</th>
                <th className="px-4 py-2 font-medium">Status</th>
                <th className="px-4 py-2 font-medium">Created</th>
                <th className="w-10 px-4 py-2" />
              </tr>
            </thead>
            <tbody>
              {campaigns.map((c) => (
                <tr key={c.id} className="border-t hover:bg-muted/30">
                  <td className="px-4 py-2.5">
                    <div className="max-w-md truncate">
                      <Link
                        to={`/campaigns/${c.id}`}
                        className="font-mono text-[13px] hover:underline"
                        title={c.target}
                      >
                        {c.target.split("\n")[0]}
                      </Link>
                    </div>
                  </td>
                  <td className="px-4 py-2.5 text-muted-foreground">{c.model}</td>
                  <td className="px-4 py-2.5">
                    <StatusBadge status={c.status} />
                  </td>
                  <td className="px-4 py-2.5 text-muted-foreground">{fmtDate(c.created_at)}</td>
                  <td className="px-4 py-2.5 text-right">
                    <Button
                      variant="ghost"
                      size="icon"
                      className="size-7 text-muted-foreground hover:text-destructive"
                      title="Delete campaign"
                      disabled={busy === c.id}
                      onClick={() => remove(c)}
                    >
                      <Trash2 className="size-3.5" />
                    </Button>
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
