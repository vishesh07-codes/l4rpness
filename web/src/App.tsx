import { Moon, Sun } from "lucide-react"
import { useEffect, useState, type MouseEvent } from "react"
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom"

import { AppSidebar } from "@/components/AppSidebar"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import { Campaign } from "@/pages/Campaign"
import { Dashboard } from "@/pages/Dashboard"
import { NewCampaign } from "@/pages/NewCampaign"
import { Skills } from "@/pages/Skills"

export default function App() {
  const [darkMode, setDarkMode] = useState(() => {
    if (typeof window === "undefined") return false
    const savedTheme = window.localStorage.getItem("theme")
    return savedTheme ? savedTheme === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches
  })

  useEffect(() => {
    document.documentElement.classList.toggle("dark", darkMode)
    window.localStorage.setItem("theme", darkMode ? "dark" : "light")
    document.querySelector('meta[name="color-scheme"]')?.setAttribute("content", darkMode ? "dark" : "light")
    document.querySelector('meta[name="theme-color"]')?.setAttribute("content", darkMode ? "#1f1f1f" : "#ffffff")
  }, [darkMode])

  function toggleTheme(event: MouseEvent<HTMLButtonElement>) {
    const bounds = event.currentTarget.getBoundingClientRect()
    document.documentElement.style.setProperty("--theme-x", `${bounds.left + bounds.width / 2}px`)
    document.documentElement.style.setProperty("--theme-y", `${bounds.top + bounds.height / 2}px`)

    const updateTheme = () => setDarkMode((enabled) => !enabled)
    const transitionDocument = document as Document & {
      startViewTransition?: (callback: () => void) => unknown
    }

    transitionDocument.startViewTransition?.(updateTheme) ?? updateTheme()
  }

  return (
    <BrowserRouter>
      <AppSidebar />
      <Button
        variant="ghost"
        size="icon"
        className="fixed right-4 top-4 z-50 rounded-full bg-background/80 shadow-sm backdrop-blur after:absolute after:top-1/2 after:left-1/2 after:size-11 after:-translate-1/2"
        onClick={toggleTheme}
        aria-label={`Switch to ${darkMode ? "light" : "dark"} mode`}
        title={`Switch to ${darkMode ? "light" : "dark"} mode`}
      >
        <span className="relative size-4">
          <Sun
            aria-hidden
            className={cn(
              "absolute inset-0 size-4 transition-[opacity,scale,filter] duration-300 ease-[cubic-bezier(0.2,0,0,1)]",
              darkMode ? "scale-100 opacity-100 blur-0" : "scale-[0.25] opacity-0 blur-[4px]"
            )}
          />
          <Moon
            aria-hidden
            className={cn(
              "size-4 transition-[opacity,scale,filter] duration-300 ease-[cubic-bezier(0.2,0,0,1)]",
              darkMode ? "scale-[0.25] opacity-0 blur-[4px]" : "scale-100 opacity-100 blur-0"
            )}
          />
        </span>
      </Button>
      <main className="pl-56">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/campaigns/new" element={<NewCampaign />} />
          <Route path="/campaigns/:id" element={<Campaign />} />
          <Route path="/skills" element={<Skills />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </BrowserRouter>
  )
}
