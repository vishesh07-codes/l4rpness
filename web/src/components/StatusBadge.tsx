import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"

const TONES: Record<string, string> = {
  created: "bg-ink-3",
  queued: "bg-ink-3",
  idle: "bg-ink-3",
  running: "bg-orange",
  interrupted: "bg-orange",
  active: "bg-green",
  done: "bg-green",
  complete: "bg-green",
  failed: "bg-red",
  error: "bg-red",
}

export function StatusBadge({ status }: { status: string }) {
  return (
    <Badge variant="secondary" className="gap-1.5 capitalize">
      <span className={cn("size-1.5 rounded-full", TONES[status] ?? "bg-ink-3")} aria-hidden />
      {status}
    </Badge>
  )
}
