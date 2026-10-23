import type { ComponentProps } from "react"

import { Label } from "@/components/ui/label"
import { cn } from "@/lib/utils"

/** Form field wrapper (Coss UI pattern): label + control + description + error. */
function Field({ className, ...props }: ComponentProps<"div">) {
  return (
    <div data-slot="field" className={cn("flex flex-col gap-2", className)} {...props} />
  )
}

function FieldLabel({ className, ...props }: ComponentProps<typeof Label>) {
  return <Label data-slot="field-label" className={cn(className)} {...props} />
}

function FieldDescription({ className, ...props }: ComponentProps<"p">) {
  return (
    <p
      data-slot="field-description"
      className={cn("text-xs text-pretty text-ink-3", className)}
      {...props}
    />
  )
}

function FieldError({ className, ...props }: ComponentProps<"p">) {
  return (
    <p
      data-slot="field-error"
      role="alert"
      className={cn("text-[13px] text-destructive", className)}
      {...props}
    />
  )
}

export { Field, FieldLabel, FieldDescription, FieldError }
