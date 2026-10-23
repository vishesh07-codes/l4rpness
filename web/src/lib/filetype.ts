import type { ComponentType } from "react"

import {
  ArchiveFile,
  AudioFile,
  Braces,
  FileDoc,
  FileText,
  Folder,
  ImageIcon,
  Sheet,
  VideoFile,
} from "@/components/icons"

type IconProps = { className?: string; strokeWidth?: number }
export type FileTone = "red" | "green" | "orange" | "accent" | "neutral"

export type FileKind = {
  icon: ComponentType<IconProps>
  /** short type label shown in the card footer, e.g. "PDF", "Image" */
  label: string
  tone: FileTone
  isImage: boolean
}

const EXT: Record<string, FileKind> = {
  pdf: { icon: FileText, label: "PDF", tone: "red", isImage: false },
  doc: { icon: FileDoc, label: "Doc", tone: "accent", isImage: false },
  docx: { icon: FileDoc, label: "Doc", tone: "accent", isImage: false },
  txt: { icon: FileText, label: "Text", tone: "neutral", isImage: false },
  md: { icon: FileText, label: "Markdown", tone: "neutral", isImage: false },
  csv: { icon: Sheet, label: "CSV", tone: "green", isImage: false },
  xls: { icon: Sheet, label: "Spreadsheet", tone: "green", isImage: false },
  xlsx: { icon: Sheet, label: "Spreadsheet", tone: "green", isImage: false },
  json: { icon: Braces, label: "JSON", tone: "orange", isImage: false },
  zip: { icon: ArchiveFile, label: "Archive", tone: "neutral", isImage: false },
  gz: { icon: ArchiveFile, label: "Archive", tone: "neutral", isImage: false },
  tar: { icon: ArchiveFile, label: "Archive", tone: "neutral", isImage: false },
  mp3: { icon: AudioFile, label: "Audio", tone: "accent", isImage: false },
  wav: { icon: AudioFile, label: "Audio", tone: "accent", isImage: false },
  mp4: { icon: VideoFile, label: "Video", tone: "accent", isImage: false },
  mov: { icon: VideoFile, label: "Video", tone: "accent", isImage: false },
  png: { icon: ImageIcon, label: "Image", tone: "accent", isImage: true },
  jpg: { icon: ImageIcon, label: "Image", tone: "accent", isImage: true },
  jpeg: { icon: ImageIcon, label: "Image", tone: "accent", isImage: true },
  gif: { icon: ImageIcon, label: "Image", tone: "accent", isImage: true },
  webp: { icon: ImageIcon, label: "Image", tone: "accent", isImage: true },
  svg: { icon: ImageIcon, label: "Image", tone: "accent", isImage: true },
}

const FALLBACK: FileKind = { icon: FileText, label: "File", tone: "neutral", isImage: false }
const FOLDER: FileKind = { icon: Folder, label: "Folder", tone: "orange", isImage: false }

/** Resolve a display icon, type label and tone from a filename + optional mime. */
export function fileKind(name: string, mime?: string): FileKind {
  if (mime?.startsWith("image/")) return { ...EXT.png }
  const ext = name.includes(".") ? name.split(".").pop()!.toLowerCase() : ""
  if (!ext) return mime === "folder" ? FOLDER : FALLBACK
  return EXT[ext] ?? FALLBACK
}

/** Human-readable byte size, e.g. "12.4 KB". */
export function fmtSize(bytes?: number): string | undefined {
  if (bytes == null || Number.isNaN(bytes)) return undefined
  if (bytes < 1024) return `${bytes} B`
  const kb = bytes / 1024
  if (kb < 1024) return `${kb < 10 ? kb.toFixed(1) : Math.round(kb)} KB`
  const mb = kb / 1024
  return `${mb < 10 ? mb.toFixed(1) : Math.round(mb)} MB`
}

/** Tailwind text-color class for a tone (used on the type icon). */
export const TONE_TEXT: Record<FileTone, string> = {
  red: "text-red",
  green: "text-green",
  orange: "text-orange",
  accent: "text-accent-ink",
  neutral: "text-ink-3",
}
