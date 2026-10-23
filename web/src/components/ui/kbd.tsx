import type { ComponentProps } from "react"

import { cn } from "@/lib/utils"

/** A keyboard key chip (Coss UI pattern), rendered as a small raised key. */
function Kbd({ className, ...props }: ComponentProps<"kbd">) {
  return (
    <kbd
      data-slot="kbd"
      className={cn(
        "inline-flex h-5 min-w-5 items-center justify-center rounded-[6px] bg-surface px-1.5 font-mono text-[11px] font-medium text-ink-2 shadow-btn",
        className
      )}
      {...props}
    />
  )
}

export { Kbd }
