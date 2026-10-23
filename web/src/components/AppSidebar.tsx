import { LayoutGrid } from "lucide-react"
import { Link, useLocation } from "react-router-dom"

import { cn } from "@/lib/utils"

const nav = [{ to: "/", label: "Dashboard", icon: LayoutGrid }]

export function AppSidebar() {
  const { pathname } = useLocation()

  return (
    <aside className="fixed inset-y-0 left-0 z-10 flex w-56 flex-col border-r bg-muted/40">
      <div className="flex h-14 items-center px-5 text-sm font-semibold tracking-tight">
        l4rpness
      </div>
      <nav className="flex flex-col gap-1 px-3">
        {nav.map(({ to, label, icon: Icon }) => {
          const active = to === "/" ? pathname === "/" : pathname.startsWith(to)
          return (
            <Link
              key={to}
              to={to}
              className={cn(
                "flex items-center gap-2 rounded-md px-2 py-1.5 text-sm text-muted-foreground hover:bg-accent hover:text-foreground",
                active && "bg-accent text-foreground"
              )}
            >
              <Icon className="size-4" />
              {label}
            </Link>
          )
        })}
      </nav>
    </aside>
  )
}
