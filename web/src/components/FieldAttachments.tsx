import { useRef } from "react"
import { Paperclip, X } from "lucide-react"

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
        variant="ghost"
        size="sm"
        className="text-muted-foreground"
        disabled={disabled}
        onClick={() => picker.current?.click()}
      >
        <Paperclip className="size-3.5" />
        Attach files
      </Button>
      {files.map((file, i) => (
        <span
          key={`${file.name}-${i}`}
          className="flex items-center gap-1 rounded-md border bg-muted/50 px-2 py-1 text-xs"
        >
          {file.name}
          <button
            type="button"
            className="text-muted-foreground hover:text-foreground"
            onClick={() => onChange(files.filter((_, j) => j !== i))}
          >
            <X className="size-3" />
          </button>
        </span>
      ))}
    </div>
  )
}
