"use client"

import type { ReactNode } from "react"
import { motion, useReducedMotion } from "motion/react"

import { SPRING_LAYOUT } from "@/lib/ease"
import { cn } from "@/lib/utils"

export type SegmentedOption<T extends string> = {
  value: T
  label: ReactNode
  count?: number
  disabled?: boolean
}

/**
 * A tactile segmented control: a recessed track (`.material-well`) with a
 * raised key (`.material-key`) that slides between options via a shared
 * layout. See design.md §7.
 */
export function SegmentedControl<T extends string>({
  value,
  onValueChange,
  options,
  layoutId = "segmented-pill",
  className,
}: {
  value: T
  onValueChange: (value: T) => void
  options: SegmentedOption<T>[]
  /** unique per instance so two controls on one page don't share the pill */
  layoutId?: string
  className?: string
}) {
  const reduce = useReducedMotion() ?? false

  return (
    <div
      role="group"
      className={cn(
        "material-well inline-flex items-center gap-0.5 rounded-lg p-0.5",
        className
      )}
    >
      {options.map((option) => {
        const selected = option.value === value
        return (
          <button
            key={option.value}
            type="button"
            aria-pressed={selected}
            disabled={option.disabled}
            onClick={() => onValueChange(option.value)}
            className={cn(
              "relative flex items-center gap-1.5 rounded-md px-2.5 py-1 text-[12px] font-medium outline-none transition-colors duration-150 focus-visible:ring-2 focus-visible:ring-ring/60 disabled:pointer-events-none disabled:opacity-50",
              selected ? "text-ink" : "text-ink-3 hover:text-ink"
            )}
          >
            {selected ? (
              <motion.span
                aria-hidden
                layoutId={layoutId}
                className="material-key absolute inset-0 rounded-md"
                transition={reduce ? { duration: 0 } : SPRING_LAYOUT}
              />
            ) : null}
            <span className="relative z-10 flex items-center gap-1.5">
              {option.label}
              {option.count && option.count > 0 ? (
                <span className="rounded-full bg-hover-2 px-1.5 font-mono text-[10px] text-ink-2">
                  {option.count}
                </span>
              ) : null}
            </span>
          </button>
        )
      })}
    </div>
  )
}
