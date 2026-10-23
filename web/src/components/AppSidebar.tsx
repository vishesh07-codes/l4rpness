import { useEffect, useState, type MouseEvent } from "react"
import { useLocation } from "react-router-dom"

import { BookOpenText, Compose, LayoutGrid, Settings, Shield, SidebarToggle, Wrench } from "@/components/icons"
import { ThemeToggle } from "@/components/ThemeToggle"
import { api, type Campaign } from "@/lib/api"
import { TransitionLink } from "@/components/TransitionLink"
import { Wordmark } from "@/components/Wordmark"
import { cn } from "@/lib/utils"

const nav = [
  { to: "/", label: "Dashboard", icon: LayoutGrid },
  // a sparkle collapses into a solid blob at 18px; a book also says
  // "methodology the agent loads on demand" better than a sparkle did
  { to: "/skills", label: "Skills", icon: BookOpenText },
  { to: "/mcp", label: "MCP", icon: Wrench },
  { to: "/settings", label: "Settings", icon: Settings },
]

export function AppSidebar({
  collapsed,
  onToggle,
  mobileOpen,
  onMobileOpenChange,
  darkMode,
  onToggleTheme,
}: {
  collapsed: boolean
  onToggle: () => void
  mobileOpen: boolean
  onMobileOpenChange: (open: boolean) => void
  darkMode: boolean
  onToggleTheme: (event: MouseEvent<HTMLButtonElement>) => void
}) {
  const { pathname } = useLocation()
  // null = still loading, so the list reserves height instead of popping in
  const [recent, setRecent] = useState<Campaign[] | null>(null)
  const onNew = pathname.startsWith("/campaigns/new")
  // the overlay rail is always full width, so ignore `collapsed` while it's open
  const rail = collapsed && !mobileOpen

  // navigating closes the overlay; Escape does too
  useEffect(() => {
    onMobileOpenChange(false)
  }, [pathname])

  useEffect(() => {
    if (!mobileOpen) return
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onMobileOpenChange(false)
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [mobileOpen])

  // the history list mirrors grok's sidebar; refreshed when the route changes
  // so a newly created or deleted campaign shows up without a reload
  useEffect(() => {
    let active = true
    api
      .listCampaigns()
      .then((list) => active && setRecent(list.slice(0, 8)))
      .catch(() => active && setRecent([]))
    return () => {
      active = false
    }
  }, [pathname])

  return (
    <>
      {/* backdrop for the overlay rail below md */}
      {mobileOpen && (
        <button
          type="button"
          aria-label="Close navigation"
          onClick={() => onMobileOpenChange(false)}
          className="fixed inset-0 z-10 bg-black/50 md:hidden"
        />
      )}
      <aside
        data-collapsed={collapsed || undefined}
        className={cn(
          "app-sidebar glass-strong fixed inset-y-0 left-0 z-20 flex flex-col border-r border-glass-border transition-[width,transform] duration-200 ease-[var(--ease-out-strong)]",
          // below md the rail overlays at full width and slides away when closed
          "w-60 max-md:shadow-overlay",
          mobileOpen ? "max-md:translate-x-0" : "max-md:-translate-x-full",
          collapsed ? "md:w-16" : "md:w-60"
        )}
      >
      {/* brand + collapse */}
      <div
        className={cn(
          "flex h-14 shrink-0 items-center",
          rail ? "justify-center px-2" : "justify-between pr-2 pl-4"
        )}
      >
        {!rail && (
          <TransitionLink
            to="/"
            className="flex items-center rounded-[10px] outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
          >
            <Wordmark className="h-[22px] w-auto" />
          </TransitionLink>
        )}
        <button
          type="button"
          onClick={onToggle}
          aria-label={rail ? "Expand sidebar" : "Collapse sidebar"}
          title={rail ? "Expand sidebar" : "Collapse sidebar"}
          className={cn(
            "flex items-center justify-center rounded-[10px] outline-none transition-colors duration-150 hover:text-ink focus-visible:ring-2 focus-visible:ring-ring/60",
            rail ? "material-key size-9 justify-center text-ink-3" : "size-8 text-ink-3 hover:bg-hover"
          )}
        >
          <SidebarToggle className={rail ? "size-4 opacity-60" : "size-[18px]"} strokeWidth={1.5} />
        </button>
      </div>

      {/* compose + nav — grok keeps these as quiet rows, not filled buttons */}
      <nav className={cn("flex flex-col px-2", rail ? "gap-1" : "gap-0.5")}>
        <Row
          to="/campaigns/new"
          label="New campaign"
          icon={Compose}
          active={onNew}
          collapsed={rail}
        />
        {nav.map(({ to, label, icon }) => (
          <Row
            key={to}
            to={to}
            label={label}
            icon={icon}
            active={to === "/" ? pathname === "/" : pathname.startsWith(to)}
            collapsed={rail}
          />
        ))}
      </nav>

      {/* collapsed rail: the wordmark turns on its side and centres in the
          otherwise-empty middle, reading bottom-to-top */}
      {rail && (
        <div className="flex min-h-0 flex-1 items-center justify-center py-4">
          <div className="flex h-28 w-8 items-center justify-center">
            <Wordmark className="h-7 w-auto shrink-0 -rotate-90" />
          </div>
        </div>
      )}

      {/* history — the block keeps its height while loading so the rail
          doesn't reflow when campaigns arrive */}
      {!rail && (recent === null || recent.length > 0) && (
        <div className="mt-5 flex min-h-0 flex-1 flex-col">
          <p className="px-4 pb-1.5 font-mono text-[10px] font-medium tracking-[0.14em] text-ink-3 uppercase">
            Recent
          </p>
          <div className="scrollbar-hide min-h-0 flex-1 overflow-y-auto px-2 pb-2">
            {recent === null &&
              [0, 1, 2, 3].map((i) => (
                <div key={i} className="flex items-center px-2 py-1.5" aria-hidden>
                  <span
                    className="h-3 animate-pulse rounded bg-hover"
                    style={{ width: `${70 - i * 8}%`, animationDelay: `${i * 80}ms` }}
                  />
                </div>
              ))}
            {recent?.map((c) => {
              const active = pathname === `/campaigns/${c.id}`
              return (
                <TransitionLink
                  key={c.id}
                  to={`/campaigns/${c.id}`}
                  aria-current={active ? "page" : undefined}
                  title={c.target}
                  className={cn(
                    "flex items-center gap-2 rounded-[10px] px-2 py-1.5 text-[13px] outline-none transition-colors duration-100 focus-visible:ring-2 focus-visible:ring-ring/60",
                    active ? "material-key text-ink" : "text-ink-2 hover:bg-hover/60 hover:text-ink"
                  )}
                >
                  {c.running && (
                    <>
                      <span
                        className="size-1.5 shrink-0 animate-pulse rounded-full bg-orange text-orange led"
                        aria-hidden
                      />
                      {/* the dot is color + motion; keep a text cue for both
                          screen readers and reduced-motion users */}
                      <span className="sr-only">Running: </span>
                    </>
                  )}
                  <span className="min-w-0 truncate">{c.target.split("\n")[0]}</span>
                </TransitionLink>
              )
            })}
          </div>
        </div>
      )}

      {/* footer */}
      <div className={cn("mt-auto shrink-0 px-2 pb-3", rail && "px-2")}>
        {/* theme lives here rather than floating over the page, where it
            collided with page headers at narrow widths */}
        <ThemeToggle darkMode={darkMode} rail={rail} onToggleTheme={onToggleTheme} />

        <div
          className={cn(
            "flex items-center gap-2 rounded-[10px] px-2 py-2 text-ink-3",
            rail && "justify-center px-0"
          )}
          title="Authorized targets only"
        >
          <Shield className={cn("shrink-0 text-green", rail ? "size-4 opacity-70" : "size-[18px]")} strokeWidth={1.5} />
          {!rail && (
            <span className="min-w-0 text-[11px] leading-tight text-ink-3">
              Authorized targets only
            </span>
          )}
        </div>
      </div>
      </aside>
    </>
  )
}

function Row({
  to,
  label,
  icon: Icon,
  active,
  collapsed,
}: {
  to: string
  label: string
  icon: (props: { className?: string; strokeWidth?: number }) => React.ReactElement
  active: boolean
  collapsed: boolean
}) {
  return (
    <TransitionLink
      to={to}
      aria-current={active ? "page" : undefined}
      title={collapsed ? label : undefined}
      className={cn(
        "group relative flex items-center gap-2.5 rounded-[10px] text-[13px] font-medium outline-none focus-visible:ring-2 focus-visible:ring-ring/60",
        collapsed ? "material-key mx-auto size-9 shrink-0 justify-center" : "px-2.5 py-2",
        active ? "text-ink" : "text-ink-2",
        !collapsed && active && "material-key",
        !collapsed && !active && "transition-colors duration-100 hover:bg-hover/60 hover:text-ink"
      )}
    >
      {collapsed && active && (
        <span
          aria-hidden
          className="absolute top-1/2 -left-2 h-5 w-[3px] -translate-y-1/2 rounded-full bg-ink"
        />
      )}
      <Icon
        className={cn(
          "shrink-0 transition-[color,opacity] duration-100",
          collapsed ? "size-4" : "size-[18px]",
          active
            ? "text-ink"
            : collapsed
              ? "text-ink-3 opacity-60 group-hover:opacity-90"
              : "text-ink-3 group-hover:text-ink"
        )}
        strokeWidth={1.5}
      />
      {!collapsed && label}
    </TransitionLink>
  )
}
