import { flushSync } from "react-dom"

type ViewTransitionDocument = Document & {
  startViewTransition?: (callback: () => void) => { finished: Promise<void> } | undefined
}

export function prefersReducedMotion() {
  return (
    typeof window !== "undefined" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  )
}

/**
 * Run a DOM update inside the View Transitions API when the browser supports
 * it, falling back to a plain synchronous update (and always when the user
 * prefers reduced motion). Used to cross-fade route changes.
 *
 * The update is committed with `flushSync` so React has painted the new tree
 * before the browser takes its "after" snapshot — otherwise the transition can
 * animate between two identical frames.
 */
export function withViewTransition(update: () => void) {
  const doc = document as ViewTransitionDocument
  if (!doc.startViewTransition || prefersReducedMotion()) {
    update()
    return
  }
  // marks the document so the CSS page-enter animation stands down while the
  // View Transition (which is a cross-fade) drives the change instead
  const root = document.documentElement
  root.classList.add("vt-active")
  const done = () => root.classList.remove("vt-active")
  const transition = doc.startViewTransition(() => flushSync(update))
  if (transition?.finished) transition.finished.finally(done)
  else window.setTimeout(done, 500)
}
