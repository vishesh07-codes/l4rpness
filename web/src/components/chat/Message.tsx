import type { ReactNode } from "react"

import { ThinkingShimmer } from "@/components/agents/loading-states/thinking-shimmer"
import { MessageBubble, MessageBubbleContent } from "@/components/agents/message-bubble"
import { StreamingResponse } from "@/components/agents/streaming-response"
import { TodoList, type TodoItem, type TodoItemStatus } from "@/components/agents/todo-list"
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

function ReasoningBlock({ text, live }: { text: string; live?: boolean }) {
  return (
    <div className="border-l-2 border-line pl-3">
      <div className="mb-1 text-[11px] font-medium text-ink-3">
        {live ? <ThinkingShimmer>Reasoning</ThinkingShimmer> : "Reasoning"}
      </div>
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

function normalizeTodoStatus(value: unknown): TodoItemStatus {
  const status = String(value ?? "").toLowerCase()
  if (["in-progress", "in_progress", "active", "running"].includes(status)) return "in-progress"
  if (["completed", "complete", "done"].includes(status)) return "completed"
  if (["cancelled", "canceled"].includes(status)) return "cancelled"
  return "pending"
}

function todosFrom(args: Record<string, unknown>): TodoItem[] {
  const raw = Array.isArray(args.todos) ? args.todos : []
  return raw
    .map((entry, index) => {
      const todo = (entry && typeof entry === "object" ? entry : {}) as Record<string, unknown>
      const title = typeof todo.title === "string" ? todo.title : String(todo.title ?? "")
      return { id: String(todo.id ?? index), title, status: normalizeTodoStatus(todo.status) }
    })
    .filter((todo) => todo.title)
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

  // the plan is rewritten on every write_todos call, so show only the latest
  const lastTodoIndex = parts.reduce(
    (latest, part, index) =>
      part.type === "tool" && part.name === "write_todos" ? index : latest,
    -1
  )

  return (
    <div className="group/response flex w-full flex-col gap-3">
      {parts.map((part, index) => {
        if (part.type === "thinking") {
          return <ReasoningBlock key={`thinking-${index}`} text={part.text} live={live} />
        }
        if (part.type === "tool") {
          if (part.name === "write_todos") {
            return index === lastTodoIndex ? (
              <TodoList key={`todo-${index}`} items={todosFrom(part.args)} />
            ) : null
          }
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
