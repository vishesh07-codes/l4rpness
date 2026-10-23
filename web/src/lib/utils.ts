import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export function fmtDate(value: string) {
  const date = new Date(value.replace(" ", "T") + "Z")
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })
}

export function fmtDuration(seconds: number) {
  if (seconds < 1) return "a moment"
  if (seconds < 60) return `${Math.round(seconds)}s`
  const minutes = Math.floor(seconds / 60)
  return `${minutes}m ${Math.round(seconds % 60)}s`
}

export function fmtTokens(count: number) {
  if (count < 1000) return `${count}`
  if (count < 100_000) return `${(count / 1000).toFixed(1).replace(/\.0$/, "")}k`
  if (count < 10_000_000) return `${Math.round(count / 1000)}k`
  return `${(count / 1_000_000).toFixed(1).replace(/\.0$/, "")}M`
}
