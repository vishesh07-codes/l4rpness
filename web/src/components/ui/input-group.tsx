import type { ComponentProps } from "react"

import { cn } from "@/lib/utils"

type Align = "inline-start" | "inline-end" | "block-start" | "block-end"

/** Input with addons sharing one inset well (Coss UI pattern). */
function InputGroup({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      data-slot="input-group"
      className={cn(
        "inset-shadow-well flex w-full items-center gap-2 rounded-[10px] bg-inset px-3 transition-[box-shadow] focus-within:ring-2 focus-within:ring-ring/40",
        className
      )}
      {...props}
    />
  )
}

function InputGroupInput({ className, ...props }: ComponentProps<"input">) {
  return (
    <input
      data-slot="input-group-input"
      className={cn(
        "h-9 min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground disabled:cursor-not-allowed disabled:opacity-50",
        className
      )}
      {...props}
    />
  )
}

function InputGroupAddon({
  className,
  align = "inline-start",
  ...props
}: ComponentProps<"div"> & { align?: Align }) {
  return (
    <div
      data-slot="input-group-addon"
      data-align={align}
      className={cn(
        "flex shrink-0 items-center gap-1.5 text-ink-3 [&_svg]:size-3.5",
        align === "inline-start" && "order-first",
        align === "inline-end" && "order-last",
        className
      )}
      {...props}
    />
  )
}

function InputGroupText({ className, ...props }: ComponentProps<"span">) {
  return (
    <span
      data-slot="input-group-text"
      className={cn("text-[13px] text-ink-3 [&_svg]:size-3.5", className)}
      {...props}
    />
  )
}

export { InputGroup, InputGroupInput, InputGroupAddon, InputGroupText }
