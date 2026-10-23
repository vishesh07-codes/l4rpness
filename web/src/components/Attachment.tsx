import type { ReactNode } from "react"

import { X } from "@/components/icons"
import {
  Attachment,
  AttachmentAction,
  AttachmentActions,
  AttachmentContent,
  AttachmentDescription,
  AttachmentMedia,
  AttachmentTitle,
  AttachmentTrigger,
} from "@/components/ui/attachment"
import { fileKind, TONE_TEXT } from "@/lib/filetype"
import { cn } from "@/lib/utils"

export { AttachmentGroup } from "@/components/ui/attachment"

type Base = {
  name: string
  mime?: string
  previewUrl?: string
  href?: string
  onRemove?: () => void
  className?: string
}

/** Upload lifecycle, passed straight through to the shadcn primitive. */
type State = "idle" | "uploading" | "processing" | "error" | "done"

/**
 * Compact attachment pill — type icon (or image thumbnail), filename and an
 * optional remove action. Used inline: message bubbles, the brief panel.
 * Built on shadcn's `Attachment` primitives.
 */
export function AttachmentChip({
  name,
  mime,
  previewUrl,
  href,
  onRemove,
  state = "done",
  className,
}: Base & { state?: State }) {
  const kind = fileKind(name, mime)
  const Icon = kind.icon
  const showThumb = kind.isImage && previewUrl

  return (
    <Attachment size="sm" state={state} className={cn("min-w-0", className)}>
      <AttachmentMedia variant={showThumb ? "image" : "icon"}>
        {showThumb ? <img src={previewUrl} alt="" /> : <Icon className={TONE_TEXT[kind.tone]} />}
      </AttachmentMedia>
      <AttachmentContent>
        <AttachmentTitle className="font-mono text-[11px]">{name}</AttachmentTitle>
      </AttachmentContent>
      {href && (
        <AttachmentTrigger asChild>
          <a href={href} target="_blank" rel="noreferrer" aria-label={`Open ${name}`} />
        </AttachmentTrigger>
      )}
      {onRemove && (
        <AttachmentActions>
          <AttachmentAction onClick={onRemove} aria-label={`Remove ${name}`}>
            <X />
          </AttachmentAction>
        </AttachmentActions>
      )}
    </Attachment>
  )
}

/**
 * Rich attachment card — filename, a meta line and the file type, with images
 * rendering as a thumbnail. Used where attachments deserve room: the composer
 * tray. Built on shadcn's `Attachment` primitives.
 */
export function AttachmentCard({
  name,
  mime,
  previewUrl,
  meta,
  href,
  onRemove,
  state = "done",
  className,
}: Base & { meta?: ReactNode; state?: State }) {
  const kind = fileKind(name, mime)
  const Icon = kind.icon
  const showThumb = kind.isImage && previewUrl

  return (
    <Attachment orientation="vertical" state={state} className={className}>
      <AttachmentMedia variant={showThumb ? "image" : "icon"}>
        {showThumb ? (
          <img
            src={previewUrl}
            alt={name}
            className="outline outline-1 -outline-offset-1 outline-black/10 dark:outline-white/10"
          />
        ) : (
          <Icon className={TONE_TEXT[kind.tone]} />
        )}
      </AttachmentMedia>
      <AttachmentContent>
        <AttachmentTitle className="text-[13px]">{name}</AttachmentTitle>
        <AttachmentDescription>{meta ?? kind.label}</AttachmentDescription>
      </AttachmentContent>
      {href && (
        <AttachmentTrigger asChild>
          <a href={href} target="_blank" rel="noreferrer" aria-label={`Open ${name}`} />
        </AttachmentTrigger>
      )}
      {onRemove && (
        <AttachmentActions>
          <AttachmentAction
            onClick={onRemove}
            aria-label={`Remove ${name}`}
            className="bg-surface/80 backdrop-blur"
          >
            <X />
          </AttachmentAction>
        </AttachmentActions>
      )}
    </Attachment>
  )
}
