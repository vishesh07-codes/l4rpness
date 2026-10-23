import { Moon, Sun } from "lucide-react"
import { useEffect, useState, type MouseEvent } from "react"
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom"

import { AppSidebar } from "@/components/AppSidebar"
import { Button } from "@/components/ui/button"
import { Campaign } from "@/pages/Campaign"
import { Dashboard } from "@/pages/Dashboard"
import { NewCampaign } from "@/pages/NewCampaign"

export default function App() {
  const [darkMode, setDarkMode] = useState(() => {
    if (typeof window === "undefined") return false
    const savedTheme = window.localStorage.getItem("theme")
    return savedTheme ? savedTheme === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches
  })

  useEffect(() => {
    document.documentElement.classList.toggle("dark", darkMode)
    window.localStorage.setItem("theme", darkMode ? "dark" : "light")
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
        variant="outline"
        size="icon"
        className="fixed right-4 top-4 z-50 rounded-full bg-background/80 shadow-sm backdrop-blur"
        onClick={toggleTheme}
        aria-label={`Switch to ${darkMode ? "light" : "dark"} mode`}
        title={`Switch to ${darkMode ? "light" : "dark"} mode`}
      >
        {darkMode ? <Sun className="theme-icon theme-icon-sun size-4" /> : <Moon className="theme-icon theme-icon-moon size-4" />}
      </Button>
      <main className="pl-56">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/campaigns/new" element={<NewCampaign />} />
          <Route path="/campaigns/:id" element={<Campaign />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </BrowserRouter>
  )
}
