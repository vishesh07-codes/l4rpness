import { Fragment, useEffect, useMemo, useState } from "react"

import Editor from "@monaco-editor/react"
import type { editor } from "monaco-editor"

import { Markdown } from "@/components/Markdown"
import { ChevronRight, FileText, Folder, RefreshCw, Search } from "@/components/icons"
import { SegmentedControl } from "@/components/ui/segmented"
import { Skeleton } from "@/components/ui/skeleton"
import { api, type WorkFile, type WorkFileContent } from "@/lib/api"
import { fmtSize } from "@/lib/filetype"
import { cn } from "@/lib/utils"

const MONACO_OPTIONS: editor.IStandaloneEditorConstructionOptions = {
  readOnly: true,
  domReadOnly: true,
  minimap: { enabled: false },
  lineNumbers: "on",
  scrollBeyondLastLine: false,
  renderLineHighlight: "none",
  overviewRulerLanes: 0,
  fontSize: 12,
  fontFamily: "var(--font-mono), ui-monospace, monospace",
  automaticLayout: true,
  wordWrap: "off",
  padding: { top: 8, bottom: 8 },
  scrollbar: { verticalScrollbarSize: 8, horizontalScrollbarSize: 8 },
}

type TreeNode = {
  name: string
  path: string
  type: "file" | "dir"
  size: number
  children: TreeNode[]
}

/** Fold the flat file list into a nested tree (dirs first, then files). */
function buildTree(files: WorkFile[]): TreeNode[] {
  const root: TreeNode = { name: "", path: "", type: "dir", size: 0, children: [] }
  const index = new Map<string, TreeNode>([["", root]])

  for (const file of files) {
    const parts = file.path.split("/")
    let parent = root
    let acc = ""
    parts.forEach((part, i) => {
      acc = acc ? `${acc}/${part}` : part
      let node = index.get(acc)
      if (!node) {
        const isFile = i === parts.length - 1 && file.type === "file"
        node = {
          name: part,
          path: acc,
          type: isFile ? "file" : "dir",
          size: isFile ? file.size : 0,
          children: [],
        }
        index.set(acc, node)
        parent.children.push(node)
      }
      parent = node
    })
  }

  const sortNodes = (nodes: TreeNode[]) => {
    nodes.sort((a, b) =>
      a.type === b.type ? a.name.localeCompare(b.name) : a.type === "dir" ? -1 : 1
    )
    nodes.forEach((n) => sortNodes(n.children))
  }
  sortNodes(root.children)
  return root.children
}

function TreeItems({
  nodes,
  depth,
  expanded,
  onToggle,
  selected,
  onOpen,
}: {
  nodes: TreeNode[]
  depth: number
  expanded: Set<string>
  onToggle: (path: string) => void
  selected: string | null
  onOpen: (path: string) => void
}) {
  return (
    <>
      {nodes.map((node) => {
        const pad = 6 + depth * 12
        if (node.type === "dir") {
          const open = expanded.has(node.path)
          return (
            <Fragment key={node.path}>
              <button
                type="button"
                onClick={() => onToggle(node.path)}
                className="flex w-full items-center gap-1.5 rounded-md py-1 pr-1.5 text-left text-[12px] text-ink-2 outline-none transition-colors hover:bg-hover/60 hover:text-ink focus-visible:ring-2 focus-visible:ring-ring/60"
                style={{ paddingLeft: pad }}
              >
                <ChevronRight
                  className={cn(
                    "size-3.5 shrink-0 text-ink-3 transition-transform",
                    open && "rotate-90"
                  )}
                  strokeWidth={1.5}
                />
                <Folder className="size-3.5 shrink-0 text-ink-3" strokeWidth={1.5} />
                <span className="min-w-0 flex-1 truncate">{node.name}</span>
              </button>
              {open && (
                <TreeItems
                  nodes={node.children}
                  depth={depth + 1}
                  expanded={expanded}
                  onToggle={onToggle}
                  selected={selected}
                  onOpen={onOpen}
                />
              )}
            </Fragment>
          )
        }
        return (
          <button
            key={node.path}
            type="button"
            onClick={() => onOpen(node.path)}
            className={cn(
              "flex w-full items-center gap-1.5 rounded-md py-1 pr-1.5 text-left text-[12px] outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring/60",
              selected === node.path
                ? "material-key text-ink"
                : "text-ink-2 hover:bg-hover/60 hover:text-ink"
            )}
            style={{ paddingLeft: pad + 14 }}
          >
            <FileText className="size-3.5 shrink-0 text-ink-3" strokeWidth={1.5} />
            <span className="min-w-0 flex-1 truncate font-mono">{node.name}</span>
            <span className="shrink-0 font-mono text-[10px] tabular-nums text-ink-3">
              {fmtSize(node.size)}
            </span>
          </button>
        )
      })}
    </>
  )
}

/**
 * Read-only explorer for the agent's work directory: a file tree beside a
 * Monaco viewer. Every path is sandboxed to `WORK_DIR/<campaign_id>` by the
 * backend.
 */
export function FilesPane({
  campaignId,
  files,
  onRefresh,
  selected,
  onSelect,
}: {
  campaignId: number
  files: WorkFile[] | null
  onRefresh: () => void
  selected: string | null
  onSelect: (path: string) => void
}) {
  const [content, setContent] = useState<WorkFileContent | null>(null)
  const [loading, setLoading] = useState(false)
  const [contentError, setContentError] = useState("")
  const [preview, setPreview] = useState(false)
  const [query, setQuery] = useState("")
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  // Monaco is heavy; load its chunk (and configure its workers) only once the
  // files pane is actually mounted, not with the Campaign route.
  const [monacoReady, setMonacoReady] = useState(false)

  useEffect(() => {
    let active = true
    import("@/lib/monaco").then(() => active && setMonacoReady(true))
    return () => {
      active = false
    }
  }, [])

  // expand folders by default, except dotted ones (build artifacts)
  useEffect(() => {
    if (!files) return
    setExpanded(
      new Set(files.filter((f) => f.type === "dir" && !f.name.startsWith(".")).map((f) => f.path))
    )
  }, [files])

  useEffect(() => {
    if (!selected) {
      setContent(null)
      return
    }
    let active = true
    setContent(null)
    setContentError("")
    setPreview(false)
    setLoading(true)
    api
      .readWorkFile(campaignId, selected)
      .then((c) => active && setContent(c))
      .catch((e: Error) => active && setContentError(e.message))
      .finally(() => active && setLoading(false))
    return () => {
      active = false
    }
  }, [campaignId, selected])

  function toggle(path: string) {
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(path)) next.delete(path)
      else next.add(path)
      return next
    })
  }

  const tree = useMemo(() => {
    if (!files) return []
    const q = query.trim().toLowerCase()
    return buildTree(q ? files.filter((f) => f.path.toLowerCase().includes(q)) : files)
  }, [files, query])

  const isDark =
    typeof document !== "undefined" && document.documentElement.classList.contains("dark")
  const isBinary = content?.content.includes("\u0000") ?? false
  const isMarkdown = /\.(md|markdown)$/i.test(content?.path ?? "")

  return (
    <div className="flex min-h-0 flex-1 gap-2">
      {/* file tree */}
      <div className="flex w-44 shrink-0 flex-col overflow-hidden rounded-xl bg-surface shadow-card">
        <div className="flex shrink-0 items-center gap-1 border-b border-line py-1.5 pr-1.5 pl-2.5">
          <span className="text-[12px] font-medium text-ink-2">Files</span>
          <button
            type="button"
            onClick={onRefresh}
            aria-label="Refresh files"
            className="ml-auto grid size-6 place-items-center rounded-md text-ink-3 outline-none transition-colors hover:bg-hover hover:text-ink focus-visible:ring-2 focus-visible:ring-ring/60"
          >
            <RefreshCw className="size-3.5" strokeWidth={1.5} />
          </button>
        </div>

        <div className="relative shrink-0 border-b border-line p-1.5">
          <Search
            className="pointer-events-none absolute top-1/2 left-3.5 size-3 -translate-y-1/2 text-ink-3"
            strokeWidth={1.5}
            aria-hidden
          />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Filter…"
            aria-label="Filter files"
            className="h-7 w-full rounded-md bg-inset pr-2 pl-7 text-[12px] outline-none placeholder:text-ink-3 focus-visible:ring-2 focus-visible:ring-ring/40"
          />
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto p-1">
          {files === null ? (
            <div className="flex flex-col gap-1.5 p-1.5" aria-hidden>
              {[0, 1, 2, 3, 4].map((i) => (
                <Skeleton key={i} className="h-3.5 rounded" style={{ width: `${80 - i * 10}%` }} />
              ))}
            </div>
          ) : tree.length === 0 ? (
            <p className="p-2.5 text-[12px] text-pretty text-ink-3">
              {query ? "No files match." : "No files yet. Outputs the agent writes here show up."}
            </p>
          ) : (
            <TreeItems
              nodes={tree}
              depth={0}
              expanded={expanded}
              onToggle={toggle}
              selected={selected}
              onOpen={onSelect}
            />
          )}
        </div>
      </div>

      {/* viewer */}
      <div className="flex min-w-0 flex-1 flex-col overflow-hidden rounded-xl bg-surface shadow-card">
        {selected === null ? (
          <p className="p-3 text-[12px] text-ink-3">Pick a file to view it.</p>
        ) : loading ? (
          <div className="flex flex-col gap-2 p-3" aria-hidden>
            {[0, 1, 2, 3, 4].map((i) => (
              <Skeleton key={i} className="h-3 rounded" style={{ width: `${85 - i * 8}%` }} />
            ))}
          </div>
        ) : content ? (
          <>
            <div className="flex shrink-0 items-center gap-2 border-b border-line py-1.5 pr-2 pl-3">
              <div className="flex min-w-0 flex-1 flex-wrap items-center gap-1.5 font-mono text-[11px]">
                {content.path.split("/").map((segment, i, all) => (
                  <span key={i} className="flex items-center gap-1.5">
                    {i > 0 && <span className="text-ink-3">/</span>}
                    {i === all.length - 1 && (
                      <FileText className="size-3.5 text-ink-3" strokeWidth={1.5} />
                    )}
                    <span className={i === all.length - 1 ? "text-ink" : "text-ink-3"}>
                      {segment}
                    </span>
                  </span>
                ))}
              </div>
              {isMarkdown && (
                <SegmentedControl
                  value={preview ? "preview" : "code"}
                  onValueChange={(v) => setPreview(v === "preview")}
                  layoutId="file-view"
                  className="shrink-0"
                  options={[
                    { value: "code", label: "Code" },
                    { value: "preview", label: "Preview" },
                  ]}
                />
              )}
              <a
                href={api.workFileRawUrl(campaignId, content.path)}
                target="_blank"
                rel="noreferrer"
                className="shrink-0 text-[11px] text-ink-3 outline-none transition-colors hover:text-ink focus-visible:ring-2 focus-visible:ring-ring/60"
              >
                raw
              </a>
            </div>
            {isBinary ? (
              <p className="p-3 text-[12px] text-ink-3">Binary file — open raw to download.</p>
            ) : isMarkdown && preview ? (
              <div className="min-h-0 flex-1 overflow-y-auto p-4">
                <Markdown text={content.content} />
              </div>
            ) : monacoReady ? (
              <div className="min-h-0 flex-1">
                <Editor
                  height="100%"
                  path={content.path}
                  value={content.content}
                  theme={isDark ? "l4rpness-dark" : "l4rpness-light"}
                  options={MONACO_OPTIONS}
                />
              </div>
            ) : (
              <div className="flex flex-col gap-2 p-3" aria-hidden>
                {[0, 1, 2, 3, 4].map((i) => (
                  <Skeleton key={i} className="h-3 rounded" style={{ width: `${85 - i * 8}%` }} />
                ))}
              </div>
            )}
            {content.truncated && (
              <p className="shrink-0 border-t border-line px-3 py-2 text-[11px] text-ink-3">
                Truncated — open raw for the full file.
              </p>
            )}
          </>
        ) : (
          <p className="p-3 text-[12px] text-ink-3">{contentError || "Could not load this file."}</p>
        )}
      </div>
    </div>
  )
}
