import { memo, type ReactNode } from "react"
import ReactMarkdown, { type Components } from "react-markdown"
import remarkBreaks from "remark-breaks"
import remarkGfm from "remark-gfm"

import { useFiles } from "@/components/campaign/files-context"
import { FileText } from "@/components/icons"
import { cn } from "@/lib/utils"

/**
 * Inline code that names a work-dir file becomes a chip that opens it in the
 * Workspace pane. Outside the campaign (no provider) it renders as plain code.
 */
function InlineCode({ className, children }: { className?: string; children?: ReactNode }) {
  const { paths, open } = useFiles()
  const value = String(children)
  const block = className?.startsWith("language-") || value.includes("\n")

  if (!block) {
    const ref = value.trim().replace(/^@/, "")
    if (ref && paths.has(ref)) {
      return (
        <button
          type="button"
          onClick={() => open(ref)}
          title={`Open ${ref} in the workspace`}
          className="mx-0.5 inline-flex items-center gap-1 rounded-[6px] bg-surface px-1.5 py-0.5 align-baseline font-mono text-[12px] text-ink-2 shadow-btn outline-none transition-colors hover:text-ink focus-visible:ring-2 focus-visible:ring-ring/60"
        >
          <FileText className="size-3 text-ink-3" strokeWidth={1.5} />
          {ref}
        </button>
      )
    }
  }

  return block ? (
    <code className={cn("font-mono", className)}>{children}</code>
  ) : (
    <code className="rounded bg-muted px-1 py-0.5 font-mono text-[13px]">{children}</code>
  )
}

const components: Components = {
  h1: ({ children }) => <h1 className="mt-4 mb-2 text-base font-semibold">{children}</h1>,
  h2: ({ children }) => <h2 className="mt-4 mb-2 text-[15px] font-semibold">{children}</h2>,
  h3: ({ children }) => <h3 className="mt-3 mb-1.5 text-sm font-semibold">{children}</h3>,
  h4: ({ children }) => <h4 className="mt-3 mb-1.5 text-[13px] font-medium">{children}</h4>,
  p: ({ children }) => <p className="my-2 first:mt-0 last:mb-0">{children}</p>,
  ul: ({ children }) => <ul className="my-2 list-disc pl-5">{children}</ul>,
  ol: ({ children }) => <ol className="my-2 list-decimal pl-5">{children}</ol>,
  li: ({ children }) => <li className="my-0.5">{children}</li>,
  a: ({ children, href }) => (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="underline underline-offset-2 hover:text-foreground"
    >
      {children}
    </a>
  ),
  pre: ({ children }) => (
    <pre className="my-2 overflow-x-auto rounded-md bg-muted p-3 text-[13px] leading-relaxed">
      {children}
    </pre>
  ),
  code: ({ className, children }) => (
    <InlineCode className={className}>{children}</InlineCode>
  ),
  blockquote: ({ children }) => (
    <blockquote className="my-2 border-l-2 pl-3 text-ink-2">{children}</blockquote>
  ),
  table: ({ children }) => (
    <div className="my-2 overflow-x-auto">
      <table className="w-full border-collapse text-sm">{children}</table>
    </div>
  ),
  th: ({ children }) => <th className="border px-2 py-1 text-left font-medium">{children}</th>,
  td: ({ children }) => <td className="border px-2 py-1 align-top">{children}</td>,
  hr: () => <hr className="my-4 border-border" />,
  img: ({ src, alt }) => (
    <img
      src={src}
      alt={alt}
      loading="lazy"
      decoding="async"
      className="my-2 max-w-full rounded-md outline outline-1 -outline-offset-1 outline-black/10 dark:outline-white/10"
    />
  ),
}

export const Markdown = memo(function Markdown({
  text,
  className,
}: {
  text: string
  className?: string
}) {
  return (
    <div className={cn("text-sm leading-relaxed", className)}>
      <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]} components={components}>
        {text}
      </ReactMarkdown>
    </div>
  )
})
