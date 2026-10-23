"use client";

import { useEffect, useRef, useState } from "react";

/* ─────────────────────────────────────────────────────────
 * STREAM TEXT — reusable streaming primitive
 * Reveals characters quickly (fast, like a real token stream);
 * the leading edge resolves out of a soft blur, and the caret
 * stays solid while streaming, then blinks once the text
 * settles. Inherits typography from its context, so it drops
 * into any text surface (Selection Actions, chat, etc.).
 * ───────────────────────────────────────────────────────── */

export function StreamText({
  text,
  charsPerTick = 2,
  tickMs = 9,
  blurTail = 6,
  caret = true,
  live = false,
  className,
  onProgress,
  onDone,
}: {
  text: string;
  /** characters revealed per tick — higher is faster */
  charsPerTick?: number;
  /** interval between reveals, ms */
  tickMs?: number;
  /** how many trailing characters carry the soft blur edge */
  blurTail?: number;
  /** render the caret (solid while streaming, blinks once idle) */
  caret?: boolean;
  /**
   * keep the revealed prefix as `text` grows, so a real incremental
   * stream is smoothed into the same character reveal instead of
   * restarting on every chunk
   */
  live?: boolean;
  className?: string;
  /** fires each tick — useful for re-anchoring UI to reflowing text */
  onProgress?: () => void;
  /** fires once the full string is shown */
  onDone?: () => void;
}) {
  const [count, setCount] = useState(0);
  const countRef = useRef(0);
  const onProgressRef = useRef(onProgress);
  const onDoneRef = useRef(onDone);
  onProgressRef.current = onProgress;
  onDoneRef.current = onDone;

  useEffect(() => {
    if (!live) {
      countRef.current = 0;
      setCount(0);
    }
    let i = Math.min(countRef.current, text.length);
    if (i >= text.length) {
      onDoneRef.current?.();
      return;
    }
    const id = setInterval(() => {
      i = Math.min(i + charsPerTick, text.length);
      countRef.current = i;
      setCount(i);
      onProgressRef.current?.();
      if (i >= text.length) {
        clearInterval(id);
        onDoneRef.current?.();
      }
    }, tickMs);
    return () => clearInterval(id);
  }, [text, charsPerTick, tickMs, live]);

  const streaming = count < text.length;
  const shown = text.slice(0, count);
  const split = streaming ? Math.max(0, shown.length - blurTail) : shown.length;

  return (
    <span className={className}>
      {shown.slice(0, split)}
      {split < shown.length && (
        <span className="stream-tail">{shown.slice(split)}</span>
      )}
      {caret && (
        <span
          aria-hidden
          className={`stream-caret${streaming ? " is-streaming" : ""}`}
        />
      )}
    </span>
  );
}
