import { memo, useEffect, useRef, useState, type ReactNode } from "react";

import { AgentDisclosure } from "@/components/agents/agent-disclosure";
import { ThinkingShimmer } from "@/components/agents/loading-states/thinking-shimmer";
import {
  MessageBubble,
  MessageBubbleContent,
} from "@/components/agents/message-bubble";
import { StreamingResponse } from "@/components/agents/streaming-response";
import { ToolResult, ToolResultOutput } from "@/components/agents/tool-result";
import { StreamText } from "@/components/atoms/StreamText";
import { AttachmentChip } from "@/components/Attachment";
import { ChevronRight } from "@/components/icons";
import { Markdown } from "@/components/Markdown";
import {
  api,
  type LocalFile,
  type MessageAttachment,
  type Part,
} from "@/lib/api";
import { cn } from "@/lib/utils";

// tool outputs can reach tens of thousands of characters and a single turn can
// hold over a thousand steps; both are capped so the DOM stays small
const TOOL_PREVIEW_CHARS = 8_000;
const TOOL_EXPANDED_CHARS = 60_000;
const PARTS_WINDOW = 60;
const PARTS_CHUNK = 120;

export function MessageRow({
  role,
  children,
  scrollRoot,
}: {
  role: "user" | "assistant";
  children: ReactNode;
  // viewport the transcript scrolls in; far rows unmount their content (see below)
  scrollRoot?: React.RefObject<HTMLElement | null>;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [nearby, setNearby] = useState(true);
  const [rootEl, setRootEl] = useState<HTMLElement | null | undefined>(undefined);

  // Resolve the scroll root asynchronously: rows often mount before the
  // scroller sets viewportRef.current, and ref-object identity never changes
  // on .current mutation, so a single read would leave the observer dead.
  // Retry briefly, then fall back to the viewport (root:null) instead of
  // never observing.
  useEffect(() => {
    let raf = 0;
    let tries = 0;
    const resolve = () => {
      const el = scrollRoot?.current ?? null;
      if (el || !scrollRoot || tries >= 20) {
        setRootEl(el);
        return;
      }
      tries += 1;
      raf = requestAnimationFrame(resolve);
    };
    raf = requestAnimationFrame(resolve);
    return () => cancelAnimationFrame(raf);
  }, [scrollRoot]);

  // Visibility tracking keeps children mounted (React state like expanded
  // tool output and parts window survives) while letting the browser skip
  // offscreen rendering via content-visibility.
  useEffect(() => {
    if (rootEl === undefined) return;
    const el = ref.current;
    if (!el || typeof IntersectionObserver === "undefined") return;
    const io = new IntersectionObserver(
      ([entry]) => {
        setNearby(entry.isIntersecting);
      },
      { root: rootEl, rootMargin: "1200px 0px" },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [rootEl]);

  return (
    <div
      ref={ref}
      data-slot="message"
      data-from={role}
      style={
        nearby
          ? undefined
          : {
              contentVisibility: "auto",
              containIntrinsicSize: "auto 400px",
            }
      }
    >
      {children}
    </div>
  );
}

type Preview = {
  key: string | number;
  filename: string;
  url?: string;
  image: boolean;
};

function attachmentsOf(
  attachments?: MessageAttachment[],
  localFiles?: LocalFile[],
): Preview[] {
  if (attachments && attachments.length) {
    return attachments.map((a) => ({
      key: a.id,
      filename: a.filename,
      url: api.attachmentUrl(a.id),
      image: a.content_type.startsWith("image/"),
    }));
  }
  return (localFiles ?? []).map((file, index) => ({
    key: index,
    filename: file.filename,
    url: file.preview,
    image: Boolean(file.preview),
  }));
}

export const UserMessage = memo(function UserMessage({
  content,
  attachments,
  localFiles,
  animate = true,
}: {
  content: string;
  attachments?: MessageAttachment[];
  localFiles?: LocalFile[];
  animate?: boolean;
}) {
  const items = attachmentsOf(attachments, localFiles);

  return (
    <MessageBubble align="end" variant="tint" animateIn={animate}>
      <MessageBubbleContent>
        {items.length > 0 && (
          <div className="mb-1.5 flex flex-wrap justify-end gap-2">
            {items.map((item) =>
              item.image && item.url ? (
                <a
                  key={item.key}
                  href={item.url}
                  target="_blank"
                  rel="noreferrer"
                >
                  <img
                    src={item.url}
                    alt={item.filename}
                    className="h-24 w-24 rounded-[12px] object-cover outline outline-1 -outline-offset-1 outline-black/10 dark:outline-white/10"
                  />
                </a>
              ) : (
                <AttachmentChip
                  key={item.key}
                  name={item.filename}
                  href={item.url}
                />
              ),
            )}
          </div>
        )}
        {content && (
          <span className="whitespace-pre-wrap break-words">{content}</span>
        )}
      </MessageBubbleContent>
    </MessageBubble>
  );
});

function ReasoningBlock({
  text,
  live,
  collapsible,
  label,
}: {
  text: string;
  live?: boolean;
  collapsible: boolean;
  label?: boolean;
}) {
  const [open, setOpen] = useState(false);

  // the supervisor transcript shows reasoning in full; the worker output folds it
  if (!collapsible) {
    return (
      <div className="border-l-2 border-line pl-3">
        {label && (
          <div className="mb-1 text-[11px] font-medium text-ink-3">
            {live ? <ThinkingShimmer>Reasoning</ThinkingShimmer> : "Reasoning"}
          </div>
        )}
        <div className="text-[13px] leading-relaxed whitespace-pre-wrap text-ink-2">
          {live ? <StreamText live text={text} reveal={false} /> : text}
        </div>
      </div>
    );
  }

  return (
    <div className="border-l-2 border-line pl-3">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        className="flex w-full items-center gap-1.5 rounded-sm text-left text-[11px] font-medium text-ink-3 outline-none transition-colors hover:text-ink-2 focus-visible:ring-2 focus-visible:ring-ring/60"
      >
        <ChevronRight
          className={cn(
            "size-3 shrink-0 transition-transform",
            open && "rotate-90",
          )}
          strokeWidth={1.5}
        />
        {live ? <ThinkingShimmer>Reasoning</ThinkingShimmer> : "Reasoning"}
      </button>
      <AgentDisclosure open={open} className="mt-1">
        <div className="text-[13px] leading-relaxed whitespace-pre-wrap text-ink-2">
          {live ? <StreamText live text={text} reveal={false} /> : text}
        </div>
      </AgentDisclosure>
    </div>
  );
}

type ToolPart = Extract<Part, { type: "tool" }>;

function toolLabel(part: ToolPart) {
  const args = part.args ?? {};
  const value = args.cmd ?? args.path ?? args.url;
  return typeof value === "string" && value ? value : part.name;
}

function ToolBlock({ part }: { part: ToolPart }) {
  // output streams in while the tool runs, so ok — not output — marks the end
  const status = part.ok == null ? "running" : part.ok ? "success" : "error";
  const output = part.output ?? "";
  const [expanded, setExpanded] = useState(false);
  const cap = expanded ? TOOL_EXPANDED_CHARS : TOOL_PREVIEW_CHARS;
  const shown = output.length > cap ? output.slice(0, cap) : output;
  const hidden = output.length - shown.length;

  return (
    <ToolResult
      tool={part.name}
      title={toolLabel(part)}
      status={status}
      kind={part.name === "bash" ? "terminal" : "custom"}
      meta={part.ms != null ? `${part.ms}ms` : undefined}
      copyText={output || undefined}
      collapseOnComplete
    >
      <ToolResultOutput language={part.name === "bash" ? "bash" : "text"}>
        {shown || "running…"}
      </ToolResultOutput>
      {hidden > 0 && (
        <div className="mt-1.5 text-[11px] text-muted-foreground">
          <button
            type="button"
            onClick={() => setExpanded((value) => !value)}
            className="underline underline-offset-2 outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/60"
          >
            {expanded
              ? "Show less"
              : `Show ${hidden.toLocaleString()} more characters`}
          </button>
          {expanded && (
            <span className="ml-2">
              display capped, copy has the full output
            </span>
          )}
        </div>
      )}
    </ToolResult>
  );
}

export const AssistantMessage = memo(function AssistantMessage({
  parts,
  live = false,
  collapseReasoning = true,
}: {
  parts: Part[];
  live?: boolean;
  /** Fold reasoning into a disclosure. Off for the supervisor transcript. */
  collapseReasoning?: boolean;
}) {
  // one long autonomous turn keeps appending tool steps to the same message;
  // rendering thousands of them at once freezes the tab, so show a tail window
  const [windowSize, setWindowSize] = useState(PARTS_WINDOW);
  const from = Math.max(0, parts.length - windowSize);
  const visible = from > 0 ? parts.slice(from) : parts;

  if (parts.length === 0) {
    return (
      <div className="w-full">
        <ThinkingShimmer>Thinking…</ThinkingShimmer>
      </div>
    );
  }

  const contentText = parts
    .filter(
      (part): part is Extract<Part, { type: "content" }> =>
        part.type === "content",
    )
    .map((part) => part.text)
    .join("");

  // label the first reasoning block only; shimmer the one still streaming.
  // a reasoning block streams only while it is the last part overall —
  // once a tool/content block follows, the reasoning is settled.
  const firstThinkingIndex = parts.findIndex(
    (part) => part.type === "thinking",
  );
  const lastThinkingIndex = parts.reduce(
    (latest, part, index) => (part.type === "thinking" ? index : latest),
    -1,
  );
  const lastPartIndex = parts.length - 1;
  const thinkingParts = visible.flatMap((part, offset) =>
    part.type === "thinking"
      ? [{ part, index: from + offset }]
      : [],
  );
  const responseParts = visible.flatMap((part, offset) =>
    part.type === "thinking"
      ? []
      : [{ part, index: from + offset }],
  );

  return (
    <div className="group/response flex w-full flex-col gap-3">
      {from > 0 && (
        <div className="flex">
          <button
            type="button"
            onClick={() => setWindowSize((size) => size + PARTS_CHUNK)}
            className="material-key rounded-full px-3 py-1 text-[11px] text-ink-2 outline-none transition-[background-color,box-shadow,color,scale] duration-150 ease-[var(--ease-out-strong)] hover:text-ink focus-visible:ring-2 focus-visible:ring-ring/60 active:scale-[0.96]"
          >
            Show {from.toLocaleString()} earlier steps
          </button>
        </div>
      )}
      {thinkingParts.map(({ part, index }) => {
        const streaming =
          live && index === lastThinkingIndex && index === lastPartIndex;
        return (
          <ReasoningBlock
            key={`thinking-${index}`}
            text={part.text}
            live={streaming}
            collapsible={collapseReasoning}
            label={index === firstThinkingIndex || streaming}
          />
        );
      })}
      {responseParts.map(({ part, index }) => {
        if (part.type === "notice") {
          return (
            <div
              key={`notice-${index}`}
              className="flex items-center gap-3 text-[11px] text-ink-3"
            >
              <span className="h-px flex-1 bg-line" aria-hidden />
              {part.text}
              <span className="h-px flex-1 bg-line" aria-hidden />
            </div>
          );
        }
        if (part.type === "tool") {
          // the plan is shown attached to the composer, not in the transcript
          if (part.name === "write_todos") return null;
          return <ToolBlock key={part.id || `tool-${index}`} part={part} />;
        }
        return (
          <Markdown
            key={`content-${index}`}
            text={part.text}
            className={live ? "md-streaming" : undefined}
          />
        );
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
  );
});
