import * as React from "react"
import { Slot } from "@radix-ui/react-slot"
import { cva, type VariantProps } from "class-variance-authority"

import { cn } from "@/lib/utils"

const buttonVariants = cva(
  "inline-flex shrink-0 items-center justify-center gap-2 whitespace-nowrap rounded-md text-sm font-medium outline-none transition-[color,background-color,border-color,box-shadow,scale] duration-150 ease-[var(--ease-out-strong)] focus-visible:ring-2 focus-visible:ring-ring/50 disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4",
  {
    variants: {
      variant: {
        default: "material-key material-accent font-semibold text-key-accent-fg",
        destructive: "material-key material-danger font-semibold text-key-fg",
        outline: "material-key text-ink",
        secondary: "material-key text-ink",
        ghost: "hover:bg-accent hover:text-accent-foreground",
        link: "text-primary underline-offset-4 hover:underline",
      },
      size: {
        default: "h-9 px-4 py-2",
        sm: "h-8 rounded-md px-3",
        lg: "h-10 rounded-md px-6",
        // icon sizes extend their hit area to 44px (touch-target min) without
        // changing the visible control size
        icon: "relative size-9 after:absolute after:top-1/2 after:left-1/2 after:size-11 after:-translate-1/2",
        // the chat components (attachment actions) ask for these
        "icon-sm":
          "relative size-8 rounded-md after:absolute after:top-1/2 after:left-1/2 after:size-11 after:-translate-1/2",
        "icon-xs":
          "relative size-6 rounded-md after:absolute after:top-1/2 after:left-1/2 after:size-11 after:-translate-1/2",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  }
)

type ButtonProps = React.ComponentProps<"button"> &
  VariantProps<typeof buttonVariants> & { asChild?: boolean; static?: boolean }

function Button({
  className,
  variant,
  size,
  asChild = false,
  static: isStatic = false,
  ...props
}: ButtonProps) {
  const Comp = asChild ? Slot : "button"
  return (
    <Comp
      data-slot="button"
      className={cn(
        buttonVariants({ variant, size }),
        !isStatic && "active:not-disabled:scale-[0.96]",
        className
      )}
      {...props}
    />
  )
}

export { Button, buttonVariants }
