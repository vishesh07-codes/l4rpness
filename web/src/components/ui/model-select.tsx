import { useMemo, useRef, useState } from "react"
import { cn } from "cn"

import { Search } from "@/components/icons"
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"

// Dedicated model picker: a Select whose dropdown is a capped scroll region
// (see .select-viewport-scroll) with models grouped by provider, following
// the shadcn "scrollable + groups" select pattern. Once the list passes
// FILTER_THRESHOLD the dropdown grows a filter field (pinned above the
// scroll region via SelectContent's header slot) so a long provider catalog
// narrows as you type instead of scrolling forever.
// Shared with the composer's model picker in prompt-input.tsx: above this
// many models the dropdown grows a filter field.
export const MODEL_FILTER_THRESHOLD = 8

function splitModel(value: string): [provider: string, short: string] {
  const slash = value.indexOf("/")
  if (slash === -1) return ["other", value]
  return [value.slice(0, slash), value.slice(slash + 1)]
}

export function ModelSelect({
  id,
  value,
  onValueChange,
  models,
  placeholder = "Choose a model",
  disabled,
  triggerClassName,
  itemClassName,
}: {
  id?: string
  value: string
  onValueChange: (value: string) => void
  models: string[]
  placeholder?: string
  disabled?: boolean
  triggerClassName?: string
  itemClassName?: string
}) {
  const [query, setQuery] = useState("")
  const inputRef = useRef<HTMLInputElement>(null)
  const filterable = models.length > MODEL_FILTER_THRESHOLD

  // Group the full catalog once: items stay mounted while filtering and
  // non-matches are only hidden + disabled below. Unmounting on every
  // keystroke would churn Radix's item collection, which re-fires its
  // focus-selected-item effect and rips focus out of the filter.
  // Disabled items are skipped by Radix's arrow nav and typeahead, and
  // display:none removes them from Tab order, so hidden rows are truly gone.
  // NOTE: the composer's picker (prompt-input.tsx) unmounts non-matches
  // instead — safe there because the custom motion select has no
  // focus-selected-item effect. Do not "unify" the two without re-checking.
  const groups = useMemo(() => {
    const byProvider = new Map<string, Array<[full: string, short: string]>>()
    for (const model of models) {
      const [provider, short] = splitModel(model)
      const list = byProvider.get(provider)
      if (list) list.push([model, short])
      else byProvider.set(provider, [[model, short]])
    }
    return [...byProvider]
  }, [models])

  const q = query.trim().toLowerCase()
  const isMatch = (full: string) => !q || full.toLowerCase().includes(q)
  const visibleCount = groups.reduce(
    (n, [, items]) => n + items.filter(([full]) => isMatch(full)).length,
    0,
  )
  const grouped = groups.length > 1

  return (
    <Select
      value={value}
      onValueChange={onValueChange}
      disabled={disabled}
      onOpenChange={(open) => {
        if (!open) {
          setQuery("")
          return
        }
        // with a long catalog, land in the filter so typing narrows the
        // list immediately; short lists keep Radix's default focus
        if (filterable) requestAnimationFrame(() => inputRef.current?.focus())
      }}
    >
      <SelectTrigger id={id} className={cn("w-full font-mono", triggerClassName)}>
        <SelectValue placeholder={placeholder} />
      </SelectTrigger>
      <SelectContent
        // popper, not item-aligned: the list filters as you type, and
        // item-aligned would re-anchor the panel on every keystroke
        position="popper"
        align="start"
        scrollable={filterable}
        header={
          filterable ? (
            <div className="border-b border-line px-1 pt-1 pb-1.5">
              {/* keydown stays local (except Escape, which must still close
                  the dropdown) so typing never drives Radix typeahead.
                  ArrowDown drops into the list, combobox-style — Tab works
                  too, this is just the shorter path. */}
              <div
                className="relative"
                onKeyDown={(e) => {
                  if (e.key === "Escape") return
                  e.stopPropagation()
                  if (e.key === "ArrowDown") {
                    e.preventDefault()
                    const listbox = inputRef.current?.closest('[role="listbox"]')
                    listbox
                      ?.querySelector<HTMLElement>('[role="option"]:not([data-disabled])')
                      ?.focus({ preventScroll: true })
                  }
                }}
              >
                <Search className="pointer-events-none absolute top-1/2 left-2 size-3.5 -translate-y-1/2 text-ink-3" />
                <input
                  ref={inputRef}
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="Filter models…"
                  aria-label="Filter models"
                  className="h-7 w-full rounded-md bg-inset pr-2 pl-7 font-mono text-xs outline-none placeholder:text-ink-3 focus-visible:ring-2 focus-visible:ring-ring/40"
                />
              </div>
            </div>
          ) : undefined
        }
      >
        {grouped
          ? groups.map(([provider, items]) => {
              const visible = items.filter(([full]) => isMatch(full))
              return (
                <SelectGroup key={provider}>
                  {visible.length > 0 && (
                    <SelectLabel className="font-mono">{provider}</SelectLabel>
                  )}
                  {items.map(([full, short]) => {
                    const show = isMatch(full)
                    return (
                      <SelectItem
                        key={full}
                        value={full}
                        title={full}
                        disabled={!show}
                        className={cn("font-mono", itemClassName, !show && "hidden")}
                      >
                        {short}
                      </SelectItem>
                    )
                  })}
                </SelectGroup>
              )
            })
          : (groups[0]?.[1] ?? []).map(([full]) => {
              const show = isMatch(full)
              return (
                <SelectItem
                  key={full}
                  value={full}
                  title={full}
                  disabled={!show}
                  className={cn("font-mono", itemClassName, !show && "hidden")}
                >
                  {full}
                </SelectItem>
              )
            })}
        {visibleCount === 0 && (
          <p className="px-2 py-4 text-center text-xs text-ink-3">
            No models match “{query.trim()}”
          </p>
        )}
      </SelectContent>
    </Select>
  )
}
