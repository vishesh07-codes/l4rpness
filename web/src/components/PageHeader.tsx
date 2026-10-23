import type { ReactNode } from "react"
import { Link } from "react-router-dom"

import { ArrowLeft } from "@/components/icons"
import { cn } from "@/lib/utils"

/**
 * The shared page header. Every top-level page uses this so titles,
 * descriptions and primary actions line up identically. See design.md §3.
 */
export function PageHeader({
  title,
  description,
  action,
  back,
  className,
}: {
  title: ReactNode
  description?: ReactNode
  action?: ReactNode
  /** optional breadcrumb link rendered above the title */
  back?: { to: string; label: string }
  className?: string
}) {
  return (
    <header className={cn("glass mb-6 rounded-xl border border-glass-border px-5 py-4", className)}>
      {back && (
        <Link
          to={back.to}
          className="mb-3 flex w-fit items-center gap-1.5 text-[13px] text-ink-3 outline-none transition-colors hover:text-ink focus-visible:text-ink"
        >
          <ArrowLeft className="size-3.5" strokeWidth={1.5} />
          {back.label}
        </Link>
      )}
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="text-lg font-semibold tracking-[-0.01em] text-balance text-ink">{title}</h1>
          {description && (
            <p className="mt-1 max-w-prose text-sm text-pretty text-ink-2">{description}</p>
          )}
        </div>
        {action && <div className="shrink-0">{action}</div>}
      </div>
    </header>
  )
}
