import type { ComponentProps, ReactNode } from "react"

import { Button } from "@/components/ui/button"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"

/**
 * An icon-only button with its label in a tooltip and on the accessible name.
 *
 * Use this instead of a bare `title` attribute: `title` renders the OS tooltip
 * after an uncontrollable ~1s delay and can't be styled. The shared
 * TooltipProvider in App.tsx handles the delay, and skips it once one tooltip
 * is already open.
 */
export function IconAction({
  label,
  description,
  children,
  side = "top",
  ...props
}: ComponentProps<typeof Button> & {
  /** shown in the tooltip and used as the accessible name unless `aria-label` is set */
  label: string
  /** longer accessible name when the tooltip text alone is ambiguous */
  description?: string
  children: ReactNode
  side?: "top" | "right" | "bottom" | "left"
}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button aria-label={description ?? label} {...props}>
          {children}
        </Button>
      </TooltipTrigger>
      <TooltipContent side={side}>{label}</TooltipContent>
    </Tooltip>
  )
}
