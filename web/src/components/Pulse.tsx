export function Pulse() {
  return (
    <span className="inline-flex h-3 shrink-0 items-end gap-0.5" aria-hidden>
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          className="h-full w-0.5 origin-bottom rounded-sm bg-muted-foreground animate-[pulse-bar_1.1s_ease-in-out_infinite]"
          style={{ animationDelay: `${i * 0.15}s` }}
        />
      ))}
    </span>
  )
}
