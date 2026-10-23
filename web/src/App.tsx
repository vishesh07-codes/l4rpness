import { Menu } from "@/components/icons"
import { lazy, Suspense, useEffect, useRef, useState, type MouseEvent } from "react"
import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom"

import { AppSidebar } from "@/components/AppSidebar"
import { Toaster } from "@/components/ui/sonner"
import { TooltipProvider } from "@/components/ui/tooltip"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
// pages load on demand so the shell (and the dashboard) stay small
const Dashboard = lazy(() => import("@/pages/Dashboard").then((m) => ({ default: m.Dashboard })))
const NewCampaign = lazy(() =>
  import("@/pages/NewCampaign").then((m) => ({ default: m.NewCampaign }))
)
const Campaign = lazy(() => import("@/pages/Campaign").then((m) => ({ default: m.Campaign })))
const Skills = lazy(() => import("@/pages/Skills").then((m) => ({ default: m.Skills })))
const Mcp = lazy(() => import("@/pages/Mcp").then((m) => ({ default: m.Mcp })))

export default function App() {
  const [darkMode, setDarkMode] = useState(() => {
    if (typeof window === "undefined") return true
    // dark-first: default on unless the user has explicitly chosen light
    const savedTheme = window.localStorage.getItem("theme")
    return savedTheme ? savedTheme === "dark" : true
  })

  const [collapsed, setCollapsed] = useState(
    () => typeof window !== "undefined" && window.localStorage.getItem("sidebar") === "collapsed"
  )
  // separate from `collapsed`: below md the rail overlays instead of docking
  const [mobileOpen, setMobileOpen] = useState(false)

  useEffect(() => {
    document.documentElement.classList.toggle("dark", darkMode)
    window.localStorage.setItem("theme", darkMode ? "dark" : "light")
    document.querySelector('meta[name="color-scheme"]')?.setAttribute("content", darkMode ? "dark" : "light")
    document.querySelector('meta[name="theme-color"]')?.setAttribute("content", darkMode ? "#0d0d0d" : "#ffffff")
  }, [darkMode])

  useEffect(() => {
    window.localStorage.setItem("sidebar", collapsed ? "collapsed" : "expanded")
  }, [collapsed])

  function toggleTheme(event: MouseEvent<HTMLButtonElement>) {
    const bounds = event.currentTarget.getBoundingClientRect()
    const root = document.documentElement
    root.style.setProperty("--theme-x", `${bounds.left + bounds.width / 2}px`)
    root.style.setProperty("--theme-y", `${bounds.top + bounds.height / 2}px`)

    const updateTheme = () => setDarkMode((enabled) => !enabled)
    const transitionDocument = document as Document & {
      startViewTransition?: (
        callback: () => void
      ) => { finished?: Promise<void> } | undefined
    }

    // a theme flip changes color/background/border/shadow on nearly every
    // element; without this they all transition together and the swap smears
    // instead of snapping. Force a reflow so the kill-switch applies first.
    const style = document.createElement("style")
    style.textContent = "*,*::before,*::after{transition:none !important}"
    document.head.appendChild(style)
    void document.body.offsetHeight
    const restore = () => style.remove()

    if (!transitionDocument.startViewTransition) {
      updateTheme()
      requestAnimationFrame(restore)
      return
    }

    // the circle-wipe CSS hangs off this class, so route changes (which also
    // use the View Transitions API) stay a plain cross-fade
    root.classList.add("theme-transition")
    const done = () => {
      root.classList.remove("theme-transition")
      restore()
    }
    const transition = transitionDocument.startViewTransition(updateTheme)
    if (transition?.finished) transition.finished.finally(done)
    else window.setTimeout(done, 500)
  }

  return (
    <BrowserRouter>
     <TooltipProvider delayDuration={500} skipDelayDuration={300}>
      {/* soft lights behind the shell; glass surfaces blur this layer */}
      <div aria-hidden className="app-backdrop" />

      {/* keyboard users land here first, ahead of the rail's nav + history */}
      <a
        href="#content"
        className="sr-only z-50 rounded-[10px] bg-surface px-3 py-2 text-[13px] font-medium text-ink shadow-overlay focus:not-sr-only focus:fixed focus:top-3 focus:left-3"
      >
        Skip to content
      </a>

      <AppSidebar
        collapsed={collapsed}
        onToggle={() => setCollapsed((v) => !v)}
        mobileOpen={mobileOpen}
        onMobileOpenChange={setMobileOpen}
        darkMode={darkMode}
        onToggleTheme={toggleTheme}
      />

      {/* below md the rail is off-canvas, so give it a way back. Hidden while
          it's open, otherwise it floats on top of the rail itself. */}
      {!mobileOpen && (
        <Button
          variant="ghost"
          size="icon"
          className="fixed top-3 left-3 z-30 rounded-full bg-background/80 shadow-sm backdrop-blur md:hidden after:absolute after:top-1/2 after:left-1/2 after:size-11 after:-translate-1/2"
          onClick={() => setMobileOpen(true)}
          aria-label="Open navigation"
          title="Open navigation"
        >
          <Menu className="size-4" strokeWidth={1.5} />
        </Button>
      )}

      <main
        id="content"
        className={cn(
          "transition-[padding] duration-200 ease-[var(--ease-out-strong)]",
          // the rail only reserves space from md up; below that it overlays
          collapsed ? "pl-0 md:pl-[60px]" : "pl-0 md:pl-60"
        )}
      >
        <RoutedContent />
      </main>

      <Toaster theme={darkMode ? "dark" : "light"} position="bottom-right" />
     </TooltipProvider>
    </BrowserRouter>
  )
}

/**
 * The routed page, keyed by pathname so a fresh mount replays the enter
 * animation on every navigation. This is the universal fallback; where the
 * View Transitions API is available the cross-fade drives it instead and
 * `html.vt-active` suppresses this animation so the two don't stack.
 */
function RoutedContent() {
  const { pathname } = useLocation()
  // the enter animation is for navigation only — skip it on the first paint so
  // a hard refresh doesn't "animate the app in"
  const firstPaint = useRef(true)
  useEffect(() => {
    firstPaint.current = false
  }, [])

  return (
    <div key={pathname} className={firstPaint.current ? undefined : "page-enter"}>
      <Suspense fallback={<div className="px-8 py-10 text-sm text-ink-2">Loading…</div>}>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/campaigns/new" element={<NewCampaign />} />
          <Route path="/campaigns/:id" element={<Campaign />} />
          <Route path="/skills" element={<Skills />} />
          <Route path="/mcp" element={<Mcp />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Suspense>
    </div>
  )
}
