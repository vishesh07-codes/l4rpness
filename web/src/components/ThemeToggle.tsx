import { useEffect, useRef, useState, type MouseEvent } from "react"

import hit from "@/assets/badapple/badapple-hook.mp3"
import idle from "@/assets/badapple/badapple-idle.webp"
import { Moon, Sun, VolumeHigh, VolumeMute } from "@/components/icons"
import { cn } from "@/lib/utils"

const SOUND_KEY = "theme-dance-sound"

/**
 * The theme switch, haunted by the Bad Apple shadow-art silhouette. The idle
 * loop sits where the sun/moon glyphs used to; clicking starts the hook while
 * the view-transition mask (see index.css) dances the new palette in through
 * her silhouette, beat by beat, then lets it burst across the screen.
 */
export function ThemeToggle({
  darkMode,
  rail,
  onToggleTheme,
}: {
  darkMode: boolean
  rail: boolean
  onToggleTheme: (event: MouseEvent<HTMLButtonElement>) => void
}) {
  const [sound, setSound] = useState(() => {
    if (typeof window === "undefined") return true
    return window.localStorage.getItem(SOUND_KEY) !== "off"
  })
  const [idleBroken, setIdleBroken] = useState(false)
  const audioRef = useRef<HTMLAudioElement | null>(null)

  useEffect(() => {
    window.localStorage.setItem(SOUND_KEY, sound ? "on" : "off")
  }, [sound])

  function handleToggle(event: MouseEvent<HTMLButtonElement>) {
    if (sound) {
      const el = audioRef.current
      if (el) {
        el.currentTime = 0
        void el.play().catch(() => {})
      }
    }
    onToggleTheme(event)
  }

  function toggleSound() {
    const next = !sound
    setSound(next)
    if (!next) audioRef.current?.pause()
  }

  return (
    <>
      <audio ref={audioRef} src={hit} preload="auto" aria-hidden />
      <div className={cn("mb-1 flex items-center gap-1", rail && "justify-center")}>
        <button
          type="button"
          onClick={handleToggle}
          aria-label={`Switch to ${darkMode ? "light" : "dark"} mode`}
          title={`Switch to ${darkMode ? "light" : "dark"} mode`}
          className={cn(
            "flex items-center gap-2.5 rounded-[10px] text-[13px] font-medium text-ink-2 outline-none transition-colors duration-100 hover:text-ink focus-visible:ring-2 focus-visible:ring-ring/60",
            rail ? "material-key size-9 justify-center" : "min-w-0 flex-1 px-2.5 py-2 hover:bg-hover"
          )}
        >
          <span className={cn("relative shrink-0", rail ? "size-4" : "size-[18px]")}>
            {idleBroken ? (
              <>
                <Sun
                  aria-hidden
                  className={cn(
                    "absolute inset-0 transition-[opacity,scale,filter] duration-300 ease-[cubic-bezier(0.2,0,0,1)]",
                    rail ? "size-4" : "size-[18px]",
                    darkMode ? "scale-100 opacity-100 blur-0" : "scale-[0.25] opacity-0 blur-[4px]"
                  )}
                  strokeWidth={1.5}
                />
                <Moon
                  aria-hidden
                  className={cn(
                    "transition-[opacity,scale,filter] duration-300 ease-[cubic-bezier(0.2,0,0,1)]",
                    rail ? "size-4" : "size-[18px]",
                    darkMode ? "scale-[0.25] opacity-0 blur-[4px]" : "scale-100 opacity-100 blur-0"
                  )}
                  strokeWidth={1.5}
                />
              </>
            ) : (
              <img
                src={idle}
                alt=""
                draggable={false}
                onError={() => setIdleBroken(true)}
                className={cn("size-full select-none object-contain", darkMode && "invert")}
              />
            )}
          </span>
          {!rail && (darkMode ? "Light mode" : "Dark mode")}
        </button>
        {!rail && (
          <button
            type="button"
            onClick={toggleSound}
            aria-pressed={sound}
            aria-label={sound ? "Mute the theme ditty" : "Unmute the theme ditty"}
            title={sound ? "Mute theme sound" : "Unmute theme sound"}
            className="flex size-8 shrink-0 items-center justify-center rounded-[10px] text-ink-3 outline-none transition-colors duration-100 hover:bg-hover hover:text-ink focus-visible:ring-2 focus-visible:ring-ring/60"
          >
            {sound ? (
              <VolumeHigh className="size-4" strokeWidth={1.5} />
            ) : (
              <VolumeMute className="size-4" strokeWidth={1.5} />
            )}
          </button>
        )}
      </div>
    </>
  )
}
