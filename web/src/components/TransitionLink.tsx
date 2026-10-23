import { forwardRef, type MouseEvent } from "react"
import { Link, useNavigate, type LinkProps } from "react-router-dom"

import { withViewTransition } from "@/lib/view-transition"

/**
 * A `Link` whose navigation runs inside a View Transition, so route changes
 * cross-fade. Intermediate clicks (new tab, modified keys) pass through to the
 * browser; only plain left-clicks get the transition.
 */
export const TransitionLink = forwardRef<HTMLAnchorElement, LinkProps>(
  function TransitionLink({ to, replace, state, target, onClick, ...rest }, ref) {
    const navigate = useNavigate()

    function handleClick(event: MouseEvent<HTMLAnchorElement>) {
      onClick?.(event)
      if (event.defaultPrevented) return
      if (target && target !== "_self") return
      if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey)
        return

      event.preventDefault()
      withViewTransition(() => navigate(to, { replace, state }))
    }

    return (
      <Link
        ref={ref}
        to={to}
        replace={replace}
        state={state}
        target={target}
        onClick={handleClick}
        {...rest}
      />
    )
  }
)
