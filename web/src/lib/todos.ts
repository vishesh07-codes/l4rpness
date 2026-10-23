import type { TodoItem, TodoItemStatus } from "@/components/agents/todo-list"
import type { Part } from "@/lib/api"

export function normalizeTodoStatus(value: unknown): TodoItemStatus {
  const status = String(value ?? "").toLowerCase()
  if (["in-progress", "in_progress", "active", "running"].includes(status)) return "in-progress"
  if (["completed", "complete", "done"].includes(status)) return "completed"
  if (["cancelled", "canceled"].includes(status)) return "cancelled"
  return "pending"
}

export function todosFrom(args: Record<string, unknown>): TodoItem[] {
  const raw = Array.isArray(args.todos) ? args.todos : []
  return raw
    .map((entry, index) => {
      const todo = (entry && typeof entry === "object" ? entry : {}) as Record<string, unknown>
      const title = typeof todo.title === "string" ? todo.title : String(todo.title ?? "")
      return { id: String(todo.id ?? index), title, status: normalizeTodoStatus(todo.status) }
    })
    .filter((todo) => todo.title)
}

/** The most recent plan the agent published, across historical parts and the live stream. */
export function latestTodos(...groups: Part[][]): TodoItem[] {
  let latest: (Part & { type: "tool" }) | undefined
  for (const parts of groups) {
    for (const part of parts) {
      if (part.type === "tool" && part.name === "write_todos") latest = part
    }
  }
  return latest ? todosFrom(latest.args) : []
}
