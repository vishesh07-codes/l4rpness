import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"

const TONES: Record<string, string> = {
  created: "bg-ink-3 text-ink-3",
  queued: "bg-ink-3 text-ink-3",
  idle: "bg-ink-3 text-ink-3",
  running: "bg-orange text-orange",
  interrupted: "bg-orange text-orange",
  active: "bg-green text-green",
  done: "bg-green text-green",
  complete: "bg-green text-green",
  failed: "bg-red text-red",
  error: "bg-red text-red",
}

export function StatusBadge({ status }: { status: string }) {
  return (
    <Badge variant="secondary" className="gap-1.5 capitalize">
      <span className={cn("led size-1.5 rounded-full", TONES[status] ?? "bg-ink-3 text-ink-3")} aria-hidden />
      {status}
    </Badge>
  )
}
