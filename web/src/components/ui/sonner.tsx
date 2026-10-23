import { Toaster as Sonner, type ToasterProps } from "sonner"

import { CircleCheck, CircleX, Info, Loader2, TriangleAlert } from "@/components/icons"

/**
 * The registry version reads `next-themes`. This app owns its theme (a `.dark`
 * class toggled in App.tsx), so the caller passes `theme` directly — one
 * switching mechanism instead of two disagreeing ones.
 */
const Toaster = ({ theme = "dark", ...props }: ToasterProps) => {
  return (
    <Sonner
      theme={theme}
      className="toaster group"
      gap={8}
      icons={{
        success: <CircleCheck className="size-4 text-green" />,
        info: <Info className="size-4" />,
        warning: <TriangleAlert className="size-4 text-orange" />,
        error: <CircleX className="size-4 text-destructive" />,
        loading: <Loader2 className="size-4 animate-spin" />,
      }}
      style={
        {
          "--normal-bg": "var(--popover)",
          "--normal-text": "var(--popover-foreground)",
          "--normal-border": "var(--border)",
          "--border-radius": "var(--radius)",
        } as React.CSSProperties
      }
      {...props}
    />
  )
}

export { Toaster }
