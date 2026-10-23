import { useEffect, useState } from "react"

import { UsageDashboard } from "@/components/agents/usage-dashboard"
import { api, type UsageRecord } from "@/lib/api"

export function Usage() {
  const [records, setRecords] = useState<UsageRecord[] | null>(null)
  const [error, setError] = useState("")

  useEffect(() => {
    api
      .usage(30)
      .then(setRecords)
      .catch((e: Error) => setError(e.message))
  }, [])

  return (
    <div className="mx-auto max-w-5xl px-8 py-10">
      {error && <p className="text-sm text-destructive">{error}</p>}
      {!error && records === null && <UsageSkeleton />}
      {records && <UsageDashboard records={records} defaultRange={30} />}
    </div>
  )
}

function UsageSkeleton() {
  return (
    <div
      className="w-full max-w-5xl animate-pulse rounded-[1.35rem] border border-border bg-card p-4 sm:p-7"
      aria-hidden
    >
      <div className="h-8 w-56 rounded bg-muted" />
      <div className="mt-2 h-4 w-72 rounded bg-muted" />
      <div className="mt-7 grid gap-8 border-b border-border pb-7 lg:grid-cols-[minmax(200px,0.85fr)_minmax(0,2fr)]">
        <div className="space-y-3">
          <div className="h-4 w-24 rounded bg-muted" />
          <div className="h-16 w-32 rounded bg-muted" />
        </div>
        <div className="h-52 rounded-xl bg-muted" />
      </div>
      <div className="mt-7 grid grid-cols-2 gap-x-5 gap-y-6 sm:grid-cols-4">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="h-12 rounded bg-muted" />
        ))}
      </div>
    </div>
  )
}
