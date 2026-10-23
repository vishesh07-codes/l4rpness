import { Badge } from "@/components/ui/badge"

export function StatusBadge({ status }: { status: string }) {
  return (
    <Badge variant="secondary" className="capitalize">
      {status}
    </Badge>
  )
}
