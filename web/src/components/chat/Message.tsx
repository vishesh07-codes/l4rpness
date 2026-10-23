import type { ReactNode } from "react"

import { ThinkingShimmer } from "@/components/agents/loading-states/thinking-shimmer"
import { MessageBubble, MessageBubbleContent } from "@/components/agents/message-bubble"
import { StreamingResponse } from "@/components/agents/streaming-response"
import { ToolResult, ToolResultOutput } from "@/components/agents/tool-result"
import { StreamText } from "@/components/atoms/StreamText"
import { Markdown } from "@/components/Markdown"
import type { Part } from "@/lib/api"

export function MessageRow({
  role,
  children,
}: {
  role: "user" | "assistant"
  children: ReactNode
}) {
  return (
    <div data-slot="message" data-from={role}>
      {children}
    </div>
  )
}

export function UserMessage({ content, animate = true }: { content: string; animate?: boolean }) {
  return (
    <MessageBubble align="end" variant="tint" animateIn={animate}>
      <MessageBubbleContent>
        <span className="whitespace-pre-wrap">{content}</span>
      </MessageBubbleContent>
    </MessageBubble>
  )
}

function ReasoningBlock({ text, live, label }: { text: string; live?: boolean; label?: boolean }) {
  return (
    <div className="border-l-2 border-line pl-3">
      {label && (
        <div className="mb-1 text-[11px] font-medium text-ink-3">
          {live ? <ThinkingShimmer>Reasoning</ThinkingShimmer> : "Reasoning"}
        </div>
      )}
      <div className="text-[13px] leading-relaxed whitespace-pre-wrap text-ink-2">
        {live ? <StreamText live text={text} /> : text}
      </div>
    </div>
  )
}

type ToolPart = Extract<Part, { type: "tool" }>

function toolLabel(part: ToolPart) {
  const args = part.args ?? {}
  const value = args.cmd ?? args.path ?? args.url
  return typeof value === "string" && value ? value : part.name
}

function ToolBlock({ part }: { part: ToolPart }) {
  const status = part.output == null ? "running" : part.ok ? "success" : "error"
  return (
    <ToolResult
      tool={part.name}
      title={toolLabel(part)}
      status={status}
      kind={part.name === "bash" ? "terminal" : "custom"}
      meta={part.ms != null ? `${part.ms}ms` : undefined}
      copyText={part.output ?? undefined}
      collapseOnComplete
    >
      <ToolResultOutput language={part.name === "bash" ? "bash" : "text"}>
        {part.output ?? "running…"}
      </ToolResultOutput>
    </ToolResult>
  )
}

export function AssistantMessage({ parts, live = false }: { parts: Part[]; live?: boolean }) {
  if (parts.length === 0) {
    return (
      <div className="w-full">
        <ThinkingShimmer>Thinking…</ThinkingShimmer>
      </div>
    )
  }

  const contentText = parts
    .filter((part): part is Extract<Part, { type: "content" }> => part.type === "content")
    .map((part) => part.text)
    .join("")

  // reasoning runs once per tool step, so label the first block only and shimmer the active one
  const firstThinkingIndex = parts.findIndex((part) => part.type === "thinking")
  const lastThinkingIndex = parts.reduce(
    (latest, part, index) => (part.type === "thinking" ? index : latest),
    -1
  )

  return (
    <div className="group/response flex w-full flex-col gap-3">
      {parts.map((part, index) => {
        if (part.type === "thinking") {
          const streaming = live && index === lastThinkingIndex
          return (
            <ReasoningBlock
              key={`thinking-${index}`}
              text={part.text}
              live={streaming}
              label={index === firstThinkingIndex || streaming}
            />
          )
        }
        if (part.type === "tool") {
          // the plan is shown attached to the composer, not in the transcript
          if (part.name === "write_todos") return null
          return <ToolBlock key={part.id || `tool-${index}`} part={part} />
        }
        return (
          <Markdown
            key={`content-${index}`}
            text={part.text}
            className={live ? "md-streaming" : undefined}
          />
        )
      })}

      {!live && contentText ? (
        <StreamingResponse
          status="complete"
          copyText={contentText}
          announce={false}
          actionsClassName="opacity-100 transition-opacity duration-150 [@media(hover:hover)]:opacity-0 group-hover/response:opacity-100 group-focus-within/response:opacity-100"
        >
          {null}
        </StreamingResponse>
      ) : null}
    </div>
  )
}
