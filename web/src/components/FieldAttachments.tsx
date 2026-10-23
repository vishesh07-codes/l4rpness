import { useRef } from "react"

import { AttachmentChip } from "@/components/Attachment"
import { Paperclip } from "@/components/icons"
import { Button } from "@/components/ui/button"

type Props = {
  files: File[]
  onChange: (files: File[]) => void
  disabled?: boolean
}

export function FieldAttachments({ files, onChange, disabled }: Props) {
  const picker = useRef<HTMLInputElement>(null)

  return (
    <div className="flex flex-wrap items-center gap-2">
      <input
        ref={picker}
        type="file"
        multiple
        hidden
        onChange={(e) => {
          const picked = Array.from(e.target.files ?? [])
          if (picked.length) onChange([...files, ...picked].slice(0, 5))
          e.target.value = ""
        }}
      />
      <Button
        type="button"
        variant="outline"
        size="sm"
        className="gap-1.5 text-ink-2"
        disabled={disabled}
        onClick={() => picker.current?.click()}
      >
        <Paperclip className="size-3.5" strokeWidth={1.5} />
        Attach files
      </Button>
      {files.map((file, i) => (
        <AttachmentChip
          key={`${file.name}-${i}`}
          name={file.name}
          mime={file.type}
          onRemove={() => onChange(files.filter((_, j) => j !== i))}
        />
      ))}
    </div>
  )
}
