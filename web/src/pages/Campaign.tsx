import {
  memo,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ClipboardEvent,
} from "react";
import { toast } from "sonner";
import { useNavigate, useParams } from "react-router-dom";
import {
  ArrowDown,
  ArrowLeft,
  ChevronDown,
  FileText,
  Paperclip,
  Play,
  RefreshCw,
  Sparkles,
  Square,
  Trash2,
  TriangleAlert,
} from "@/components/icons";

import { AttachmentCard, AttachmentChip } from "@/components/Attachment";
import { FilesPane } from "@/components/campaign/FilesPane";
import { FilesContext } from "@/components/campaign/files-context";
import { fmtSize } from "@/lib/filetype";
import { useWorkFiles } from "@/lib/use-work-files";
import { AgentProgress } from "@/components/agents/loading-states/agent-progress";
import { ReasoningText } from "@/components/agents/loading-states/reasoning-text";
import { MessageScroller } from "@/components/agents/message-scroller";
import { PromptInput } from "@/components/agents/prompt-input";
import { TodoList } from "@/components/agents/todo-list";
import { StatusBadge } from "@/components/StatusBadge";
import {
  AssistantMessage,
  MessageRow,
  UserMessage,
} from "@/components/chat/Message";
import LoadingState from "@/components/primitives/LoadingState";
import {
  Alert,
  AlertAction,
  AlertDescription,
  AlertTitle,
} from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { SegmentedControl } from "@/components/ui/segmented";
import { ModelSelect } from "@/components/ui/model-select";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  api,
  type AppConfig,
  type Attachment,
  type Campaign as CampaignData,
  type GeneratorInfo,
  type GeneratorMessage,
  type Hypothesis,
  type Message,
  type Part,
  type ToolCall,
} from "@/lib/api";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { IconAction } from "@/components/IconAction";
import { TransitionLink } from "@/components/TransitionLink";
import { latestTodos } from "@/lib/todos";
import { cn, fmtDate, fmtTokens } from "@/lib/utils";
import { withViewTransition } from "@/lib/view-transition";

const SECTIONS = [
  { key: "info", label: "Info" },
  { key: "instructions", label: "Instructions" },
  { key: "description", label: "Description" },
  { key: "scope", label: "Scope" },
] as const;

const SUGGESTIONS = [
  "Recon this target",
  "What would you probe first?",
  "Draft a testing plan",
];

type LiveReply = { parts: Part[]; pendingTool: string | null };

// the transcript can hold thousands of rows, each carrying full tool
// output — holding all of it in state (let alone rendering it) balloons
// tab memory into the gigabytes. The browser keeps only this trailing
// window; anything older stays server-side behind "load earlier".
const MAX_HELD_MESSAGES = 200;
// Absolute ceiling for explicitly user-requested history (load-earlier).
// Automatic paths always trim to MAX_HELD_MESSAGES; this only bounds the
// manual path so repeated clicks cannot grow state without limit.
const MAX_LOADED_MESSAGES = 1000;
const PAGE_SIZE = 60;
const CATCH_UP_LIMIT = 300;
const GENERATOR_PAGE = 40;
const HYPOTHESIS_PAGE = 60;
const PREVIEW_TITLE = 56;
const PREVIEW_DESCRIPTION = 88;

// right-pane resize bounds (lg+); the transcript keeps at least PANE_LEFT_MIN
const PANE_MIN = 320;
const PANE_DEFAULT = 460;
const PANE_LEFT_MIN = 360;
const paneMax = (rowWidth: number) =>
  Math.max(PANE_MIN, Math.min(2400, Math.round(rowWidth - PANE_LEFT_MIN)));

function sameGenerators(a: GeneratorInfo[], b: GeneratorInfo[]) {
  return (
    a.length === b.length &&
    a.every((item, index) => {
      const next = b[index];
      return (
        item.id === next.id &&
        item.status === next.status &&
        item.hypothesis_count === next.hypothesis_count &&
        item.last_snippet === next.last_snippet &&
        item.updated_at === next.updated_at
      );
    })
  );
}

function sameHypotheses(a: Hypothesis[], b: Hypothesis[]) {
  return (
    a.length === b.length &&
    a.every((item, index) => {
      const next = b[index];
      return (
        item.id === next.id &&
        item.status === next.status &&
        item.result === next.result &&
        item.updated_at === next.updated_at
      );
    })
  );
}

function mergeGeneratorMessages(
  prev: GeneratorMessage[],
  incoming: GeneratorMessage[],
) {
  if (incoming.length === 0) return prev;
  const byId = new Map(prev.map((message) => [message.id, message]));
  for (const message of incoming) byId.set(message.id, message);
  return [...byId.values()].sort((a, b) => a.id - b.id);
}

function mergeMessages(prev: Message[], incoming: Message[]): Message[] {
  if (incoming.length === 0) return prev;
  const byId = new Map(prev.map((message) => [message.id, message]));
  let changed = false;
  for (const message of incoming) {
    const old = byId.get(message.id);
    if (
      !old ||
      old.content !== message.content ||
      old.reasoning !== message.reasoning ||
      old.parts !== message.parts ||
      old.tool_calls !== message.tool_calls
    ) {
      if (old) partsCache.delete(message.id);
      changed = true;
    }
    byId.set(message.id, message);
  }
  if (!changed) return prev;
  return [...byId.values()].sort((a, b) => a.id - b.id);
}

function previewText(message: Message) {
  if (message.role === "user") {
    return (message.content ?? "").replace(/\s+/g, " ").trim();
  }
  return partsOf(message)
    .filter(
      (part): part is Extract<Part, { type: "content" }> =>
        part.type === "content",
    )
    .map((part) => part.text)
    .join(" ")
    .replace(/\s+/g, " ")
    .trim();
}

function truncatePreview(text: string, limit: number) {
  if (text.length <= limit) return text;
  const excerpt = text.slice(0, limit);
  const boundary = excerpt.lastIndexOf(" ");
  return `${excerpt.slice(0, boundary > limit * 0.65 ? boundary : limit).trim()}…`;
}

// keep only the trailing window in state and report whether anything was
// dropped, so callers can mark older rows as still available server-side
function trimHeld(messages: Message[]): {
  messages: Message[];
  trimmed: boolean;
} {
  if (messages.length <= MAX_HELD_MESSAGES) return { messages, trimmed: false };
  return { messages: messages.slice(-MAX_HELD_MESSAGES), trimmed: true };
}
function messagePreview(message: Message, nextMessage?: Message) {
  const label = truncatePreview(
    previewText(message) || "Message",
    PREVIEW_TITLE,
  );
  const nextText = nextMessage ? previewText(nextMessage) : "";
  return {
    label,
    description: nextText
      ? truncatePreview(nextText, PREVIEW_DESCRIPTION)
      : undefined,
  };
}

// context-window usage: last prompt size against the model's window.
// Live-aware via the SSE usage feed; the bar is a static cue plus motion
// (150ms width transition), never motion alone — the % label always reads.
function ContextBar({
  ctx,
  limit,
  guessed,
}: {
  ctx: number;
  limit: number;
  guessed?: boolean;
}) {
  const pct = limit > 0 ? Math.min(100, (ctx / limit) * 100) : 0;
  const label = ctx > 0 && pct < 1 ? "<1" : `${Math.round(pct)}`;
  return (
    <span
      className="inline-flex items-center gap-1.5 font-mono text-[11px] tabular-nums text-ink-3"
      title={`Context: ${ctx.toLocaleString()} of ${limit.toLocaleString()} tokens (${fmtTokens(ctx)} / ${fmtTokens(limit)})${
        guessed
          ? " — window unknown for this model, 128k guess. Pin it under [llm.limits] in config.toml."
          : ""
      }`}
    >
      <span
        role="progressbar"
        aria-label="Context window usage"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(pct)}
        className="h-1 w-16 overflow-hidden rounded-full bg-inset"
      >
        <span
          className={cn(
            "block h-full rounded-full transition-[width] duration-150 ease-out",
            pct >= 90 ? "bg-red" : pct >= 75 ? "bg-orange" : "bg-ink-3",
          )}
          style={{ width: `${pct}%` }}
        />
      </span>
      {label}%
    </span>
  );
}

export function Campaign() {
  const { id } = useParams();
  const nav = useNavigate();
  const campaignId = Number(id);

  const [campaign, setCampaign] = useState<CampaignData | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [stream, setStream] = useState<LiveReply | null>(null);
  const [configured, setConfigured] = useState(true);
  const [runtimeConfig, setRuntimeConfig] = useState<AppConfig | null>(null);
  const [runtimeSaving, setRuntimeSaving] = useState(false);
  const [input, setInput] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [pending, setPending] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [resuming, setResuming] = useState(false);
  const [liveUsage, setLiveUsage] = useState<{
    in: number;
    out: number;
    ctx: number;
  } | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [error, setError] = useState("");
  const [following, setFollowing] = useState(true);
  const [hasEarlier, setHasEarlier] = useState(false);
  const [windowSize, setWindowSize] = useState(PAGE_SIZE);
  const [loadingEarlier, setLoadingEarlier] = useState(false);
  const [tab, setTab] = useState<"generators" | "hypotheses" | "workspace">(
    "generators",
  );
  // right pane width (lg+), persisted; dragged via the divider. The max grows
  // with the window so the pane can get very wide but never eats the transcript.
  const rowRef = useRef<HTMLDivElement>(null);
  const [paneWidth, setPaneWidth] = useState(() => {
    if (typeof window === "undefined") return PANE_DEFAULT;
    const saved = Number(window.localStorage.getItem("campaignPane"));
    return Number.isFinite(saved) && saved >= PANE_MIN ? saved : PANE_DEFAULT;
  });
  useEffect(() => {
    window.localStorage.setItem("campaignPane", String(paneWidth));
  }, [paneWidth]);
  const [paneMaxWidth, setPaneMaxWidth] = useState(2400);

  // keep the pane within bounds when the window shrinks
  useEffect(() => {
    const clamp = () => {
      const row = rowRef.current;
      if (!row) return;
      const max = paneMax(row.getBoundingClientRect().width);
      setPaneMaxWidth(max);
      setPaneWidth((w) => Math.min(w, max));
    };
    clamp();
    window.addEventListener("resize", clamp);
    return () => window.removeEventListener("resize", clamp);
  }, []);

  // work-dir files, shared by the Files pane and the file-reference chips
  const {
    files: workFiles,
    error: workFilesError,
    refresh: refreshWork,
  } = useWorkFiles(campaignId);
  const [workspacePath, setWorkspacePath] = useState<string | null>(null);
  const openInWorkspace = useCallback((path: string) => {
    setWorkspacePath(path);
    setTab("workspace");
  }, []);
  const filesValue = useMemo(
    () => ({
      paths: new Set(
        (workFiles ?? []).filter((f) => f.type === "file").map((f) => f.path),
      ),
      open: openInWorkspace,
    }),
    [workFiles, openInWorkspace],
  );

  // hand a work-dir file to the agent: appends an `@path` mention to the prompt
  function insertRef(path: string) {
    setInput(
      (prev) => `${prev}${prev && !prev.endsWith(" ") ? " " : ""}@${path} `,
    );
  }

  function resizeStart(event: React.PointerEvent<HTMLDivElement>) {
    const row = rowRef.current;
    if (!row) return;
    const handle = event.currentTarget;
    event.preventDefault();
    // capture the pointer so the drag keeps updating even if it leaves the handle
    handle.setPointerCapture(event.pointerId);
    const rect = row.getBoundingClientRect();
    const max = paneMax(rect.width);
    const onMove = (e: PointerEvent) =>
      setPaneWidth(
        Math.min(
          max,
          Math.max(PANE_MIN, Math.round(rect.right - e.clientX - 6)),
        ),
      );
    const onUp = () => {
      handle.removeEventListener("pointermove", onMove);
      handle.removeEventListener("pointerup", onUp);
      handle.removeEventListener("pointercancel", onUp);
      if (handle.hasPointerCapture(event.pointerId))
        handle.releasePointerCapture(event.pointerId);
    };
    handle.addEventListener("pointermove", onMove);
    handle.addEventListener("pointerup", onUp);
    handle.addEventListener("pointercancel", onUp);
  }

  function resizeKey(event: React.KeyboardEvent) {
    const row = rowRef.current;
    const max = row ? paneMax(row.getBoundingClientRect().width) : 2400;
    if (event.key === "ArrowLeft") {
      event.preventDefault();
      setPaneWidth((w) => Math.min(max, w + 24));
    } else if (event.key === "ArrowRight") {
      event.preventDefault();
      setPaneWidth((w) => Math.max(PANE_MIN, w - 24));
    }
  }
  const [generators, setGenerators] = useState<GeneratorInfo[]>([]);
  const [selectedGenerator, setSelectedGenerator] = useState<number | null>(
    null,
  );
  const [generatorMessages, setGeneratorMessages] = useState<
    GeneratorMessage[]
  >([]);
  const [generatorHasEarlier, setGeneratorHasEarlier] = useState(false);
  const [generatorWindowSize, setGeneratorWindowSize] =
    useState(GENERATOR_PAGE);
  const [hypotheses, setHypotheses] = useState<Hypothesis[]>([]);
  const [hypothesesShown, setHypothesesShown] = useState(HYPOTHESIS_PAGE);
  const initialIds = useRef<Set<number> | null>(null);
  const lastIdRef = useRef(0);
  // oldest message id ever held: trimming the state window must not move the
  // "load earlier" cursor forward, or it would refetch rows it just dropped
  const oldestIdRef = useRef(0);
  // Mirror of messages for flag decisions outside setState updaters.
  // Updaters must stay pure (StrictMode re-invokes them), so pagination flags
  // are derived from this ref — the actual trimmed state — never from a stale
  // render closure.
  const messagesRef = useRef<Message[]>([]);
  const syncHeld = (next: Message[]) => {
    messagesRef.current = next;
    setMessages(next);
  };
  const selectedGeneratorRef = useRef<number | null>(null);
  const generatorLoadRef = useRef(0);
  const lastGeneratorsLoad = useRef(0);
  const lastHypothesesLoad = useRef(0);
  const tabRef = useRef<"generators" | "hypotheses" | "workspace">(
    "generators",
  );
  const viewportRef = useRef<HTMLElement | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!campaignId) return;
    Promise.all([
      api.getCampaign(campaignId),
      api.listMessagesPage(campaignId, { limit: PAGE_SIZE }),
      api.config(),
      api.listGenerators(campaignId),
      api.listHypotheses(campaignId),
    ])
      .then(([c, page, cfg, gens, hypos]) => {
        setCampaign(c);
        partsCache.clear();
        syncHeld(page.messages);
        setHasEarlier(page.hasMore);
        setWindowSize(Math.max(page.messages.length, PAGE_SIZE));
        lastIdRef.current = page.messages.length
          ? page.messages[page.messages.length - 1].id
          : 0;
        oldestIdRef.current = page.messages.length ? page.messages[0].id : 0;
        setConfigured(cfg.agent_configured);
        setRuntimeConfig(cfg);
        setGenerators(gens);
        setHypotheses(hypos);
        setHypothesesShown(HYPOTHESIS_PAGE);
        initialIds.current = new Set(
          page.messages.map((message) => message.id),
        );
      })
      .catch((e: Error) => setError(e.message));
  }, [campaignId]);

  useEffect(() => {
    selectedGeneratorRef.current = selectedGenerator;
  }, [selectedGenerator]);

  useEffect(() => {
    tabRef.current = tab;
  }, [tab]);

  // incremental sync: fetch only what is new (plus a small overlap so the
  // in-flight assistant row is replaced by its final version)
  async function catchUpMessages() {
    const page = lastIdRef.current
      ? // a small overlap lets the in-flight assistant row be replaced by its
        // final version once the chain ended
        await api.listMessagesPage(campaignId, {
          after: Math.max(1, lastIdRef.current - 3),
          limit: CATCH_UP_LIMIT,
        })
      : await api.listMessagesPage(campaignId, { limit: PAGE_SIZE });
    if (page.messages.length >= CATCH_UP_LIMIT) {
      // too much was missed to merge piecemeal; the tail is all the window shows
      const tail = await api.listMessagesPage(campaignId, { limit: PAGE_SIZE });
      syncHeld(tail.messages);
      setHasEarlier(tail.hasMore);
      lastIdRef.current = tail.messages.length
        ? tail.messages[tail.messages.length - 1].id
        : 0;
      oldestIdRef.current = tail.messages.length
        ? tail.messages[0].id
        : lastIdRef.current;
      return;
    }
    if (page.messages.length === 0) return;
    lastIdRef.current = Math.max(
      lastIdRef.current,
      page.messages[page.messages.length - 1].id,
    );
    oldestIdRef.current = Math.min(
      oldestIdRef.current || page.messages[0].id,
      page.messages[0].id,
    );
    // catch-up only adds newer rows: merge against the ref mirror, trim to
    // the held window, and derive the flag from the actual trimmed result —
    // never from a stale render closure.
    const merged = trimHeld(mergeMessages(messagesRef.current, page.messages));
    if (merged.trimmed) setHasEarlier(true);
    syncHeld(merged.messages);
  }

  async function loadEarlier() {
    if (loadingEarlier) return;
    if (messagesRef.current.length > windowSize) {
      setWindowSize((size) => size + PAGE_SIZE);
      return;
    }
    if (!hasEarlier || messagesRef.current.length === 0) return;
    setLoadingEarlier(true);
    try {
      const page = await api.listMessagesPage(campaignId, {
        before: oldestIdRef.current || messagesRef.current[0].id,
        limit: PAGE_SIZE,
      });
      if (page.messages.length > 0) {
        oldestIdRef.current = page.messages[0].id;
        // Explicitly user-requested history: honor the request but enforce
        // the absolute ceiling so repeated clicks cannot grow state forever.
        const merged = mergeMessages(messagesRef.current, page.messages);
        const capped =
          merged.length > MAX_LOADED_MESSAGES
            ? merged.slice(-MAX_LOADED_MESSAGES)
            : merged;
        syncHeld(capped);
        setWindowSize((size) => size + page.messages.length);
      }
      setHasEarlier(page.hasMore);
    } catch (e) {
      toast.error("Could not load earlier messages", {
        description: (e as Error).message,
      });
    } finally {
      setLoadingEarlier(false);
    }
  }

  // generator events arrive for every worker step; refetching the lists on
  // each one is wasteful, so they are throttled and skip no-op state updates
  async function loadGenerators(force = false) {
    const now = Date.now();
    if (!force && now - lastGeneratorsLoad.current < 1500) return;
    lastGeneratorsLoad.current = now;
    try {
      const fresh = await api.listGenerators(campaignId);
      setGenerators((prev) => (sameGenerators(prev, fresh) ? prev : fresh));
    } catch {
      // the next refresh retries
    }
  }

  async function loadHypotheses(force = false) {
    const now = Date.now();
    if (!force && now - lastHypothesesLoad.current < 1500) return;
    lastHypothesesLoad.current = now;
    try {
      const fresh = await api.listHypotheses(campaignId);
      setHypotheses((prev) => (sameHypotheses(prev, fresh) ? prev : fresh));
    } catch {
      // the next refresh retries
    }
  }

  async function openGenerator(generatorId: number) {
    setSelectedGenerator(generatorId);
    selectedGeneratorRef.current = generatorId;
    setGeneratorMessages([]);
    setGeneratorHasEarlier(false);
    setGeneratorWindowSize(GENERATOR_PAGE);
    const loadId = ++generatorLoadRef.current;
    try {
      const page = await api.getGeneratorMessages(generatorId, {
        limit: GENERATOR_PAGE,
      });
      if (generatorLoadRef.current !== loadId) return;
      setGeneratorMessages(page.messages);
      setGeneratorHasEarlier(page.hasMore);
    } catch {
      // leave it empty; the next refresh retries
    }
  }

  function loadEarlierGenerator() {
    if (generatorMessages.length > generatorWindowSize) {
      setGeneratorWindowSize((size) => size + GENERATOR_PAGE * 2);
      return;
    }
    const generatorId = selectedGeneratorRef.current;
    if (
      !generatorHasEarlier ||
      generatorId === null ||
      generatorMessages.length === 0
    )
      return;
    const loadId = generatorLoadRef.current;
    void api
      .getGeneratorMessages(generatorId, {
        before: generatorMessages[0].id,
        limit: GENERATOR_PAGE,
      })
      .then((page) => {
        if (generatorLoadRef.current !== loadId) return;
        if (page.messages.length > 0) {
          setGeneratorMessages((prev) =>
            mergeGeneratorMessages(prev, page.messages),
          );
          setGeneratorWindowSize((size) => size + page.messages.length);
        }
        setGeneratorHasEarlier(page.hasMore);
      })
      .catch(() => {
        // retry on the next click
      });
  }

  useEffect(() => {
    if (tab !== "generators") return;
    void loadGenerators(true);
    const selected = selectedGenerator;
    if (selected !== null) void openGenerator(selected);
  }, [tab, campaignId, selectedGenerator]);

  // backstop for missed stream events while the worker tabs are open
  useEffect(() => {
    if (!campaign?.running || (tab !== "generators" && tab !== "hypotheses"))
      return;
    const timer = setInterval(() => {
      if (tabRef.current === "generators") void loadGenerators();
      else void loadHypotheses();
    }, 4000);
    return () => clearInterval(timer);
  }, [tab, campaign?.running, campaignId]);

  // Escape stops the agent — the visible path is the stop button, this is the
  // one people reach for
  useEffect(() => {
    if (!pending && !campaign?.running) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") stop();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [pending, campaign?.running, stopping]);

  function jumpToLatest() {
    const viewport = viewportRef.current;
    viewport?.scrollTo({ top: viewport.scrollHeight, behavior: "smooth" });
  }

  // live turns stream over SSE so the transcript tracks the model token for
  // token; the browser reconnects on drops and re-syncs from the snapshot
  useEffect(() => {
    if (!campaign?.running) return;

    partsCache.clear();
    setStream({ parts: [], pendingTool: null });

    const source = new EventSource(`/api/campaigns/${campaignId}/stream`);

    const refresh = async () => {
      try {
        await catchUpMessages();
      } catch {
        // the next chain_end or reconnect retries
      }
    };

    const append = (kind: "thinking" | "content", text: string) =>
      setStream((s) => {
        const base = s ?? { parts: [], pendingTool: null };
        const last = base.parts[base.parts.length - 1];
        if (last?.type === kind) {
          return {
            ...base,
            parts: [
              ...base.parts.slice(0, -1),
              { ...last, text: last.text + text },
            ],
          };
        }
        return { ...base, parts: [...base.parts, { type: kind, text }] };
      });

    // tokens and tool output chunks arrive many times per second; coalescing
    // them into one state update per frame keeps a long command from
    // re-rendering the transcript on every chunk
    let textBuffer: { kind: "thinking" | "content"; text: string } | null =
      null;
    let outputBuffer: Map<string, string> | null = null;
    let frame = 0;

    const flush = () => {
      frame = 0;
      const text = textBuffer;
      textBuffer = null;
      if (text) append(text.kind, text.text);
      const outputs = outputBuffer;
      outputBuffer = null;
      if (outputs) {
        setStream((s) =>
          s
            ? {
                ...s,
                parts: s.parts.map((part) =>
                  part.type === "tool" && outputs.has(part.id)
                    ? {
                        ...part,
                        output: `${part.output ?? ""}${outputs.get(part.id)}`,
                      }
                    : part,
                ),
              }
            : s,
        );
      }
    };

    const flushNow = () => {
      if (frame) {
        cancelAnimationFrame(frame);
        frame = 0;
      }
      flush();
    };

    const queueText = (kind: "thinking" | "content", text: string) => {
      if (textBuffer && textBuffer.kind !== kind) flushNow();
      if (textBuffer) textBuffer.text += text;
      else textBuffer = { kind, text };
      if (!frame) frame = requestAnimationFrame(flush);
    };

    const queueOutput = (id: string, chunk: string) => {
      if (!outputBuffer) outputBuffer = new Map();
      outputBuffer.set(id, (outputBuffer.get(id) ?? "") + chunk);
      if (!frame) frame = requestAnimationFrame(flush);
    };

    const on = (event: string, handler: (data: any) => void) => {
      source.addEventListener(event, (e) => {
        try {
          // the batched events must not flush each other, or batching is lost
          if (
            event !== "thinking" &&
            event !== "content" &&
            event !== "tool_output"
          ) {
            flushNow();
          }
          handler(JSON.parse((e as MessageEvent).data));
        } catch {
          // malformed frame; the next one will resync
        }
      });
    };

    on("snapshot", (d) => {
      setStream({ parts: d.parts ?? [], pendingTool: d.pending ?? null });
      if (d.usage) setLiveUsage(d.usage);
      void refresh();
    });
    on("thinking", (d) => queueText("thinking", d.text));
    on("content", (d) => queueText("content", d.text));
    on("usage", (d) => setLiveUsage({ in: d.in, out: d.out, ctx: d.ctx }));
    on("notice", (d) =>
      setStream((s) => {
        const base = s ?? { parts: [], pendingTool: null };
        return {
          ...base,
          parts: [...base.parts, { type: "notice", text: d.text }],
        };
      }),
    );
    on("compacted", (d) =>
      setStream((s) => {
        const base = s ?? { parts: [], pendingTool: null };
        return {
          ...base,
          parts: [
            ...base.parts,
            {
              type: "notice",
              text: `context compacted — ${d.messages} earlier messages folded into memory`,
            },
          ],
        };
      }),
    );
    on("tool_start", (d) =>
      setStream((s) => (s ? { ...s, pendingTool: d.name } : s)),
    );
    on("tool_call", (d) =>
      setStream((s) => {
        const base = s ?? { parts: [], pendingTool: null };
        return {
          parts: [
            ...base.parts,
            { type: "tool", id: d.id, name: d.name, args: d.args },
          ],
          pendingTool: null,
        };
      }),
    );
    on("tool_output", (d) => queueOutput(d.id, d.chunk));
    on("tool_result", (d) =>
      setStream((s) =>
        s
          ? {
              ...s,
              parts: s.parts.map((part) =>
                part.type === "tool" && part.id === d.id
                  ? { ...part, output: d.output, ok: d.ok, ms: d.ms }
                  : part,
              ),
            }
          : s,
      ),
    );
    on("generator", (d) => {
      void loadGenerators();
      if (d.message) void loadHypotheses();
      if (d.message && selectedGeneratorRef.current === d.generator_id) {
        const message = d.message as GeneratorMessage;
        if (tabRef.current === "generators") {
          setGeneratorMessages((prev) =>
            prev.some((m) => m.id === message.id) ? prev : [...prev, message],
          );
        }
      }
    });
    on("chain_end", () => {
      setStream({ parts: [], pendingTool: null });
      void refresh();
      refreshWork();
    });
    on("closed", async () => {
      source.close();
      setStream(null);
      setCampaign((c) => (c ? { ...c, running: false } : c));
      await refresh();
      refreshWork();
    });
    on("idle", async () => {
      source.close();
      setStream(null);
      const fresh = await api.getCampaign(campaignId);
      setCampaign(fresh);
      await refresh();
      refreshWork();
    });

    return () => {
      flushNow();
      source.close();
    };
  }, [campaign?.running, campaignId]);

  // only re-scan the tail of the history when it changes, not every message
  // and not on every streamed token
  const messageTodos = useMemo(
    () =>
      latestTodos(
        ...messages
          .slice(-8)
          .map((m) => (m.role === "assistant" ? partsOf(m) : [])),
      ),
    [messages],
  );
  const streamTodos = latestTodos(stream?.parts ?? []);
  const todos = streamTodos.length > 0 ? streamTodos : messageTodos;

  const visibleMessages = useMemo(
    () =>
      windowSize >= messages.length ? messages : messages.slice(-windowSize),
    [messages, windowSize],
  );
  const visibleGeneratorMessages = useMemo(
    () =>
      generatorWindowSize >= generatorMessages.length
        ? generatorMessages
        : generatorMessages.slice(-generatorWindowSize),
    [generatorMessages, generatorWindowSize],
  );
  const shownHypotheses = useMemo(
    () => hypotheses.slice(0, hypothesesShown),
    [hypotheses, hypothesesShown],
  );
  const railPreviews = useMemo(
    () =>
      visibleMessages.map((message, index) =>
        messagePreview(message, visibleMessages[index + 1]),
      ),
    [visibleMessages],
  );

  function addFiles(picked: File[]) {
    if (picked.length === 0) return;
    setFiles((prev) => [...prev, ...picked].slice(0, 8));
  }

  function onPasteFiles(event: ClipboardEvent<HTMLTextAreaElement>) {
    // only fires while the composer textarea is focused
    const pasted = Array.from(event.clipboardData?.files ?? []);
    if (pasted.length === 0) {
      for (const item of Array.from(event.clipboardData?.items ?? [])) {
        if (item.kind !== "file") continue;
        const file = item.getAsFile();
        if (file) pasted.push(file);
      }
    }
    if (pasted.length === 0) return;
    event.preventDefault();
    addFiles(pasted);
  }

  async function send() {
    const content = input.trim();
    const sending = files;
    if ((!content && sending.length === 0) || pending) return;

    setInput("");
    setFiles([]);
    setError("");
    setPending(true);

    const optimistic: Message = {
      id: -Date.now(),
      role: "user",
      content,
      reasoning: "",
      localFiles: sending.map((file) => ({ filename: file.name })),
      created_at: "",
    };
    {
      const next = trimHeld([...messagesRef.current, optimistic]);
      if (next.trimmed) setHasEarlier(true);
      syncHeld(next.messages);
    }

    try {
      // the supervisor picks queued messages up when the next chain starts,
      // the same way mid-task input works in a coding harness
      const stored = await api.steerCampaign(campaignId, content, sending);
      lastIdRef.current = Math.max(lastIdRef.current, stored.id);
      {
        const next = trimHeld(
          mergeMessages(
            messagesRef.current.filter((m) => m.id !== optimistic.id),
            [stored],
          ),
        );
        if (next.trimmed) setHasEarlier(true);
        syncHeld(next.messages);
      }
      // steering a stopped campaign resumes the loop
      setCampaign(await api.getCampaign(campaignId));
    } catch (e) {
      syncHeld(messagesRef.current.filter((m) => m.id !== optimistic.id));
      setInput(content);
      setFiles(sending);
      setError((e as Error).message);
    } finally {
      setPending(false);
    }
  }

  async function stop() {
    if (stopping) return;
    setStopping(true);
    try {
      await api.stopCampaign(campaignId);
      const fresh = await api.getCampaign(campaignId);
      setCampaign(fresh);
      if (!fresh.running) {
        setStream(null);
        await catchUpMessages();
      }
    } catch (e) {
      toast.error("Could not stop the agent", {
        description: (e as Error).message,
      });
    } finally {
      setStopping(false);
    }
  }

  async function resume() {
    if (resuming) return;
    setResuming(true);
    try {
      await api.resumeCampaign(campaignId);
      setCampaign(await api.getCampaign(campaignId));
      toast.success("Agent resumed");
    } catch (e) {
      toast.error("Could not resume the agent", {
        description: (e as Error).message,
      });
    } finally {
      setResuming(false);
    }
  }

  async function updateRuntime(values: { model?: string; effort?: string }) {
    if (runtimeSaving) return;
    setRuntimeSaving(true);
    try {
      const updated = await api.updateCampaignRuntime(campaignId, values);
      setCampaign(updated);
      toast.success("Supervisor settings updated", {
        description: "The next supervisor chain will use the new settings.",
      });
    } catch (e) {
      toast.error("Could not update supervisor settings", {
        description: (e as Error).message,
      });
    } finally {
      setRuntimeSaving(false);
    }
  }

  async function remove() {
    if (!campaign) return;

    setDeleting(true);
    try {
      await api.deleteCampaign(campaignId);
      setConfirmDelete(false);
      toast.success("Campaign deleted");
      withViewTransition(() => nav("/"));
    } catch (e) {
      toast.error("Could not delete the campaign", {
        description: (e as Error).message,
      });
      setDeleting(false);
    }
  }

  if (!campaign) {
    return (
      <div className="mx-auto flex h-svh max-w-4xl items-center px-6 py-6 text-sm text-ink-2 max-md:pt-16">
        {error || <LoadingState label="Loading campaign" />}
      </div>
    );
  }

  const attachments = campaign.attachments ?? [];
  const targetFiles = attachments.filter((a) => a.field === "target");
  const tabs = [
    {
      key: "generators" as const,
      label: "Generators",
      count: generators.filter((g) => g.status === "running").length,
    },
    {
      key: "hypotheses" as const,
      label: "Hypotheses",
      count: hypotheses.filter(
        (h) => h.status === "new" || h.status === "working",
      ).length,
    },
    { key: "workspace" as const, label: "Workspace", count: 0 },
  ];
  const selectedGeneratorInfo =
    generators.find((g) => g.id === selectedGenerator) ?? null;
  const usageIn = liveUsage?.in ?? campaign.usage_in ?? 0;
  const usageOut = liveUsage?.out ?? campaign.usage_out ?? 0;
  const usageCtx = liveUsage?.ctx ?? campaign.last_prompt_tokens ?? 0;

  return (
    <FilesContext.Provider value={filesValue}>
      <div className="campaign-page flex h-svh flex-col gap-4 overflow-hidden px-6 py-6 max-md:pt-16">
        <div className="flex flex-col gap-3">
          <TransitionLink
            to="/"
            className="flex w-fit items-center gap-1 text-[13px] text-ink-2 transition-colors hover:text-foreground"
          >
            <ArrowLeft className="size-3.5" strokeWidth={1.5} />
            Campaigns
          </TransitionLink>

          <header className="flex items-start justify-between gap-4">
            <div className="min-w-0">
              <h1
                className="truncate font-mono text-sm font-medium"
                title={`${campaign.target}\ncreated ${fmtDate(campaign.created_at)}`}
              >
                {campaign.target.split("\n")[0]}
              </h1>
              <div className="mt-2 flex flex-wrap items-center gap-1.5 text-xs text-ink-2">
                {runtimeConfig && (
                  <>
                    <ModelSelect
                      value={campaign.model}
                      onValueChange={(model) => void updateRuntime({ model })}
                      models={runtimeConfig.models}
                      disabled={runtimeSaving}
                      triggerClassName="h-7 max-w-56 text-[11px]"
                    />
                    <span className="text-ink-3">.</span>
                    <Select
                      value={campaign.effort}
                      onValueChange={(effort) => void updateRuntime({ effort })}
                      disabled={runtimeSaving}
                    >
                      <SelectTrigger className="h-7 w-auto min-w-16 text-[11px] capitalize">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {runtimeConfig.efforts.map((effort) => (
                          <SelectItem
                            key={effort}
                            value={effort}
                            className="capitalize"
                          >
                            {effort}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    <span className="text-ink-3">.</span>
                  </>
                )}

                <Badge variant="secondary">
                  {campaign.mode === "goal" ? "goal" : "24×7"}
                </Badge>

                {(usageIn > 0 || usageOut > 0) && (
                  <Badge
                    variant="secondary"
                    className="font-mono"
                    title={`${usageIn.toLocaleString()} input · ${usageOut.toLocaleString()} output${
                      usageCtx
                        ? ` · context ${usageCtx.toLocaleString()} tokens`
                        : ""
                    }`}
                  >
                    ↑{fmtTokens(usageIn)} ↓{fmtTokens(usageOut)}
                  </Badge>
                )}

                {campaign.context_window ? (
                  <ContextBar
                    ctx={usageCtx}
                    limit={campaign.context_window}
                    guessed={campaign.context_window_source === "fallback"}
                  />
                ) : null}
              </div>
              {targetFiles.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-2">
                  {targetFiles.map((a) => (
                    <AttachmentChip
                      key={a.id}
                      name={a.filename}
                      mime={a.content_type}
                      href={api.attachmentUrl(a.id)}
                    />
                  ))}
                </div>
              )}
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <span className="flex items-center gap-1.5 text-[11px] text-ink-3">
                <span
                  className={cn(
                    "size-1.5 rounded-full",
                    configured ? "bg-green" : "bg-ink-3",
                  )}
                />
                {configured ? "ready" : "offline"}
              </span>
              <StatusBadge status={campaign.status} />
              {campaign.running && (
                <IconAction
                  variant="ghost"
                  size="icon-xs"
                  label="Stop the agent"
                  description={
                    campaign.mode === "goal"
                      ? "Stop the until-goal run"
                      : "Stop the 24x7 loop"
                  }
                  onClick={stop}
                  disabled={stopping}
                  className="text-ink-3 hover:text-destructive"
                >
                  <Square className="size-3.5" strokeWidth={1.5} />
                </IconAction>
              )}
              {!campaign.running && (
                <IconAction
                  variant="ghost"
                  size="icon-xs"
                  label="Resume the agent"
                  description={
                    campaign.mode === "goal"
                      ? "Restart the until-goal run from where it left off"
                      : "Restart the 24x7 loop from where it left off"
                  }
                  onClick={resume}
                  disabled={resuming || !configured}
                  className="text-ink-3 hover:text-ink"
                >
                  <Play
                    className="size-3.5 translate-x-[1px] fill-current"
                    strokeWidth={1.5}
                  />
                </IconAction>
              )}
              <IconAction
                variant="ghost"
                size="icon-xs"
                label="Delete campaign"
                onClick={() => setConfirmDelete(true)}
                disabled={deleting || pending}
                className="text-ink-3 hover:text-destructive"
              >
                <Trash2 className="size-3.5" strokeWidth={1.5} />
              </IconAction>
            </div>
          </header>
        </div>

        <div
          ref={rowRef}
          className="flex min-h-0 flex-1 gap-3 max-lg:flex-col max-lg:overflow-y-auto"
        >
          {/* left — the supervisor transcript */}
          <div className="flex min-h-0 min-w-0 flex-1 flex-col gap-3 max-lg:h-[70svh] max-lg:flex-none">
            <ContextPanel
              campaign={campaign}
              attachments={attachments}
              defaultOpen={messages.length === 0 && !stream}
            />
            <section className="flex min-h-0 flex-1 flex-col">
              <div className="relative flex min-h-0 flex-1 flex-col">
                <MessageScroller
                  className="min-h-0 flex-1"
                  viewportClassName="pb-4"
                  contentClassName={cn(
                    "flex min-h-full flex-col gap-4",
                    messages.length === 0 && !stream && "justify-center",
                  )}
                  label="Conversation"
                  busy={pending}
                  navigation={visibleMessages.length > 4 ? "rail" : undefined}
                  previews={railPreviews}
                  viewportRef={viewportRef}
                  onFollowChange={setFollowing}
                >
                  {messages.length === 0 && !stream && (
                    <EmptyState configured={configured} onPick={setInput} />
                  )}

                  {(hasEarlier || messages.length > windowSize) && (
                    <div className="flex justify-center pb-1">
                      <button
                        type="button"
                        onClick={loadEarlier}
                        disabled={loadingEarlier}
                        className="material-key rounded-full px-3 py-1 text-[12px] text-ink-2 outline-none transition-[background-color,box-shadow,color,scale] duration-150 ease-[var(--ease-out-strong)] hover:text-ink focus-visible:ring-2 focus-visible:ring-ring/60 active:scale-[0.96] disabled:opacity-60"
                      >
                        {loadingEarlier
                          ? "Loading earlier messages…"
                          : "Load earlier messages"}
                      </button>
                    </div>
                  )}

                  {visibleMessages.map((m) => {
                    // an empty assistant row is the placeholder of a run that is still
                    // streaming; the live block below already renders it
                    if (
                      m.role === "assistant" &&
                      !m.content &&
                      partsOf(m).length === 0
                    )
                      return null;
                    return m.role === "user" ? (
                      <MessageRow
                        key={m.id}
                        role="user"
                        scrollRoot={
                          visibleMessages.length > 40 ? viewportRef : undefined
                        }
                      >
                        <UserMessage
                          content={m.content}
                          attachments={m.attachments}
                          localFiles={m.localFiles}
                          animate={!initialIds.current?.has(m.id)}
                        />
                      </MessageRow>
                    ) : (
                      <MessageRow
                        key={m.id}
                        role="assistant"
                        scrollRoot={
                          visibleMessages.length > 40 ? viewportRef : undefined
                        }
                      >
                        <AssistantMessage
                          parts={partsOf(m)}
                          collapseReasoning={false}
                        />
                      </MessageRow>
                    );
                  })}

                  {stream && (
                    <MessageRow role="assistant" scrollRoot={viewportRef}>
                      <div className="flex w-full flex-col gap-3">
                        {stream.pendingTool ? (
                          <AgentProgress
                            label={
                              stream.pendingTool === "write_todos"
                                ? "Planning"
                                : `Running ${stream.pendingTool}`
                            }
                          />
                        ) : stream.parts.length === 0 ? (
                          <ReasoningText />
                        ) : null}
                        {stream.parts.length > 0 && (
                          <AssistantMessage
                            parts={stream.parts}
                            live
                            collapseReasoning={false}
                          />
                        )}
                      </div>
                    </MessageRow>
                  )}
                </MessageScroller>

                {!following && messages.length > 0 && (
                  <button
                    type="button"
                    onClick={jumpToLatest}
                    className="absolute bottom-4 left-1/2 z-10 flex -translate-x-1/2 items-center gap-1.5 rounded-full bg-surface px-3 py-1.5 text-[12px] font-medium text-ink-2 shadow-raised transition-colors hover:text-ink focus-visible:ring-2 focus-visible:ring-ring/60"
                  >
                    <ArrowDown className="size-3.5" strokeWidth={1.5} />
                    Jump to latest
                  </button>
                )}
              </div>

              {error && (
                <Alert variant="destructive" className="mb-3">
                  <TriangleAlert />
                  <AlertTitle>Message failed</AlertTitle>
                  <AlertDescription>{error}</AlertDescription>
                  <AlertAction>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => send()}
                      disabled={pending}
                    >
                      <RefreshCw className="size-3.5" strokeWidth={1.5} />
                      Retry
                    </Button>
                  </AlertAction>
                </Alert>
              )}

              <div className="pt-1">
                {todos.length > 0 && (
                  <div className="mb-2">
                    <TodoList items={todos} title="Plan" defaultOpen />
                  </div>
                )}

                {files.length > 0 && (
                  <div className="scrollbar-hide mb-2 flex gap-2 overflow-x-auto pb-1">
                    {files.map((file, index) => (
                      <PendingCard
                        key={`${file.name}-${index}`}
                        file={file}
                        onRemove={() =>
                          setFiles((prev) => prev.filter((_, i) => i !== index))
                        }
                      />
                    ))}
                  </div>
                )}

                <PromptInput
                  value={input}
                  onValueChange={setInput}
                  onSubmit={send}
                  loading={pending || campaign.running}
                  onStop={stop}
                  mentions={(workFiles ?? [])
                    .filter((f) => f.type === "file")
                    .map((f) => f.path)}
                  disabled={!configured}
                  placeholder={
                    configured
                      ? campaign.running
                        ? "Agent running — press stop to interrupt"
                        : "Message the agent…"
                      : "Chat disabled"
                  }
                  minRows={1}
                  maxRows={8}
                  onPaste={onPasteFiles}
                  leadingAction={
                    <>
                      <input
                        ref={fileInput}
                        type="file"
                        multiple
                        hidden
                        onChange={(event) => {
                          addFiles(Array.from(event.target.files ?? []));
                          event.target.value = "";
                        }}
                      />
                      <IconAction
                        variant="ghost"
                        size="icon-sm"
                        label="Attach files or images"
                        description="Attach files"
                        disabled={!configured || pending}
                        onClick={() => fileInput.current?.click()}
                        className="rounded-full text-ink-3 hover:text-ink"
                      >
                        <Paperclip className="size-4" strokeWidth={1.5} />
                      </IconAction>
                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <Button
                            variant="ghost"
                            size="icon-sm"
                            aria-label="Reference a file"
                            title="Reference a file from the workspace"
                            disabled={
                              !configured ||
                              !workFiles?.some((f) => f.type === "file")
                            }
                            className="rounded-full text-ink-3 hover:text-ink"
                          >
                            <FileText className="size-4" strokeWidth={1.5} />
                          </Button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent
                          align="start"
                          className="max-h-72 w-72 overflow-y-auto"
                        >
                          {(workFiles ?? [])
                            .filter((f) => f.type === "file")
                            .map((f) => (
                              <DropdownMenuItem
                                key={f.path}
                                onSelect={() => insertRef(f.path)}
                              >
                                <FileText
                                  className="size-3.5 text-ink-3"
                                  strokeWidth={1.5}
                                />
                                <span className="min-w-0 truncate font-mono text-[12px]">
                                  {f.path}
                                </span>
                              </DropdownMenuItem>
                            ))}
                        </DropdownMenuContent>
                      </DropdownMenu>
                      <span
                        title={`Model · ${campaign.model} · effort ${campaign.effort}`}
                        className="inline-flex items-center gap-1.5 rounded-full bg-surface px-2.5 py-1 font-mono text-[11px] font-medium text-ink-2 shadow-btn"
                      >
                        <Sparkles
                          className="size-3 text-ink-2"
                          strokeWidth={1.5}
                        />
                        {campaign.model}
                      </span>
                    </>
                  }
                />
              </div>
            </section>
          </div>

          {/* divider — drag to resize (lg+) */}
          <div
            role="separator"
            aria-orientation="vertical"
            aria-label="Resize workspace panel"
            aria-valuenow={paneWidth}
            aria-valuemin={PANE_MIN}
            aria-valuemax={paneMaxWidth}
            tabIndex={0}
            onPointerDown={resizeStart}
            onKeyDown={resizeKey}
            className="group hidden w-3 shrink-0 cursor-col-resize touch-none items-center justify-center outline-none lg:flex"
          >
            <span className="h-full w-px rounded-full bg-line transition-colors group-hover:bg-line-strong group-focus-visible:w-0.5 group-focus-visible:bg-line-strong" />
          </div>

          {/* right — instruments */}
          <aside
            style={{ "--pane-w": `${paneWidth}px` } as React.CSSProperties}
            className="flex min-h-0 shrink-0 flex-col gap-3 max-lg:h-auto max-lg:w-full lg:w-[var(--pane-w)]"
          >
            <SegmentedControl
              value={tab}
              onValueChange={setTab}
              options={tabs.map((entry) => ({
                value: entry.key,
                label: entry.label,
                count: entry.count,
              }))}
            />

            {tab === "generators" && (
              <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl bg-surface shadow-card">
                {selectedGenerator === null || !selectedGeneratorInfo ? (
                  <>
                    <div className="flex shrink-0 items-center gap-2 border-b border-line px-3 py-2">
                      <span className="text-[12px] font-medium text-ink-2">
                        Workers
                      </span>
                      <span className="font-mono text-[11px] tabular-nums text-ink-3">
                        {generators.length}
                      </span>
                    </div>
                    <div className="min-h-0 flex-1 overflow-y-auto p-2">
                      {generators.length === 0 ? (
                        <p className="p-3 text-[12px] text-ink-3">
                          No hypothesis workers yet. The supervisor spawns them
                          when the backlog runs dry.
                        </p>
                      ) : (
                        <div className="flex flex-col gap-1.5">
                        {generators.map((generator, index) => (
                          <button
                            key={generator.id}
                            type="button"
                            onClick={() => openGenerator(generator.id)}
                            style={
                              index < 8
                                ? { animationDelay: `${index * 35}ms` }
                                : undefined
                            }
                            className="generator-row material-key flex w-full flex-col gap-1 rounded-md px-2.5 py-2 text-left transition-[background-color,box-shadow,color,transform] duration-150 ease-[var(--ease-out-strong)] active:scale-[0.98]"
                          >
                            <span className="flex items-center gap-2">
                              <span className="font-mono text-[11px] text-ink-3">
                                #{generator.id}
                              </span>
                              <span className="min-w-0 flex-1 truncate text-[12px] font-medium">
                                {generator.focus || "general"}
                              </span>
                              <StatusBadge status={generator.status} />
                            </span>
                            <span className="flex items-center gap-2 text-[11px] text-ink-3">
                              <span className="shrink-0">
                                {generator.hypothesis_count} hypotheses
                              </span>
                              {generator.last_snippet && (
                                <span className="min-w-0 flex-1 truncate">
                                  {generator.last_snippet}
                                </span>
                              )}
                            </span>
                          </button>
                        ))}
                        </div>
                      )}
                    </div>
                  </>
                ) : (
                  <>
                    <div className="flex shrink-0 flex-col gap-1.5 border-b border-line px-4 py-3">
                      <button
                        type="button"
                        onClick={() => setSelectedGenerator(null)}
                        className="-ml-1 flex w-fit items-center gap-1 rounded-md px-1 py-1 text-[13px] text-ink-2 outline-none transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/60 active:scale-[0.97]"
                      >
                        <ArrowLeft
                          className="size-4"
                          strokeWidth={1.5}
                        />
                        Workers
                      </button>
                      <div className="flex items-center gap-2">
                        <span className="font-mono text-[11px] text-ink-3">
                          #{selectedGeneratorInfo.id}
                        </span>
                        <span
                          className="min-w-0 flex-1 truncate text-[13px] font-medium text-ink"
                          title={selectedGeneratorInfo.focus || "general"}
                        >
                          {selectedGeneratorInfo.focus || "general"}
                        </span>
                        <StatusBadge status={selectedGeneratorInfo.status} />
                      </div>
                      <span className="text-[11px] text-ink-3">
                        {selectedGeneratorInfo.hypothesis_count} hypotheses
                        {selectedGeneratorInfo.last_snippet
                          ? ` · ${selectedGeneratorInfo.last_snippet}`
                          : ""}
                      </span>
                    </div>
                    <div className="generator-detail min-h-0 flex-1 overflow-y-auto p-3">
                      {generatorMessages.length === 0 ? (
                        <LoadingState label="Loading worker" />
                      ) : (
                        <div className="flex flex-col gap-3">
                          {(generatorHasEarlier ||
                            generatorMessages.length >
                              generatorWindowSize) && (
                            <div className="flex justify-center">
                              <button
                                type="button"
                                onClick={loadEarlierGenerator}
                                className="material-key rounded-full px-3 py-1 text-[12px] text-ink-2 outline-none transition-[background-color,box-shadow,color,scale] duration-150 ease-[var(--ease-out-strong)] hover:text-ink focus-visible:ring-2 focus-visible:ring-ring/60 active:scale-[0.96]"
                              >
                                Load earlier output
                              </button>
                            </div>
                          )}
                          {visibleGeneratorMessages.map((message) => (
                            <AssistantMessage
                              key={message.id}
                              parts={message.parts}
                            />
                          ))}
                        </div>
                      )}
                    </div>
                  </>
                )}
              </div>
            )}

            {tab === "hypotheses" && (
              <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl bg-surface shadow-card">
                <div className="flex shrink-0 items-center gap-2 border-b border-line px-3 py-2">
                  <span className="text-[12px] font-medium text-ink-2">
                    Hypotheses
                  </span>
                  <span className="font-mono text-[11px] tabular-nums text-ink-3">
                    {hypotheses.length}
                  </span>
                </div>
                <div className="min-h-0 flex-1 overflow-y-auto p-1.5">
                  {hypotheses.length === 0 ? (
                    <p className="p-3 text-[12px] text-ink-3">
                      No hypotheses yet. They appear here as the generators file
                      them.
                    </p>
                  ) : (
                    <>
                      <ul className="flex flex-col gap-0.5">
                        {shownHypotheses.map((hypothesis) => (
                          <HypothesisCard
                            key={hypothesis.id}
                            hypothesis={hypothesis}
                          />
                        ))}
                      </ul>
                      {hypotheses.length > shownHypotheses.length && (
                        <div className="mt-2 flex justify-center pb-1">
                          <button
                            type="button"
                            onClick={() =>
                              setHypothesesShown(
                                (count) => count + HYPOTHESIS_PAGE,
                              )
                            }
                            className="material-key rounded-full px-3 py-1 text-[12px] text-ink-2 outline-none transition-[background-color,box-shadow,color,scale] duration-150 ease-[var(--ease-out-strong)] hover:text-ink focus-visible:ring-2 focus-visible:ring-ring/60 active:scale-[0.96]"
                          >
                            Show {hypotheses.length - shownHypotheses.length}{" "}
                            more
                          </button>
                        </div>
                      )}
                    </>
                  )}
                </div>
              </div>
            )}

            {tab === "workspace" && (
              <FilesPane
                campaignId={campaignId}
                files={workFiles}
                filesError={workFilesError}
                onRefresh={refreshWork}
                selected={workspacePath}
                onSelect={openInWorkspace}
              />
            )}
          </aside>
        </div>

        <ConfirmDialog
          open={confirmDelete}
          onOpenChange={setConfirmDelete}
          title="Delete this campaign?"
          description={
            <>
              <span className="font-mono break-all">
                {campaign.target.split("\n")[0]}
              </span>{" "}
              and all of its messages and files are removed. This cannot be
              undone.
            </>
          }
          confirmLabel="Delete campaign"
          busy={deleting}
          onConfirm={remove}
        />
      </div>
    </FilesContext.Provider>
  );
}

// assistant rows are checkpointed while a run streams, so a row's parts can
// change after it was first fetched — clear the cache on every refetch.
const partsCache = new Map<number, Part[]>();
const PARTS_CACHE_MAX = 240;

function cacheParts(id: number, parts: Part[]) {
  if (partsCache.size >= PARTS_CACHE_MAX && !partsCache.has(id)) {
    const oldest = partsCache.keys().next().value;
    if (oldest !== undefined) partsCache.delete(oldest);
  }
  partsCache.set(id, parts);
}

function partsOf(message: Message): Part[] {
  const cached = partsCache.get(message.id);
  if (cached) return cached;

  let parts: Part[] = [];
  if (message.parts) {
    try {
      const parsed = JSON.parse(message.parts) as Part[];
      if (parsed.length > 0) parts = parsed;
    } catch {
      // fall back to the legacy columns
    }
  }

  if (parts.length === 0) {
    if (message.reasoning)
      parts.push({ type: "thinking", text: message.reasoning });
    for (const call of callsOf(message)) parts.push({ type: "tool", ...call });
    if (message.content) parts.push({ type: "content", text: message.content });
  }

  cacheParts(message.id, parts);
  return parts;
}

function callsOf(message: Message): ToolCall[] {
  if (!message.tool_calls) return [];
  try {
    return JSON.parse(message.tool_calls) as ToolCall[];
  } catch {
    return [];
  }
}

const HypothesisCard = memo(
  function HypothesisCard({ hypothesis }: { hypothesis: Hypothesis }) {
    const [open, setOpen] = useState(false);
    return (
      <li className="overflow-hidden rounded-md">
        <button
          type="button"
          aria-expanded={open}
          onClick={() => setOpen((value) => !value)}
          className={cn(
            "flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-left outline-none transition-colors hover:bg-hover/60 focus-visible:ring-2 focus-visible:ring-ring/60",
            open && "bg-hover/40",
          )}
        >
          <span className="font-mono text-[11px] tabular-nums text-ink-3">
            #{hypothesis.id}
          </span>
          <StatusBadge status={hypothesis.status} />
          <span className="min-w-0 flex-1 truncate text-[12px] text-ink">
            {hypothesis.title}
          </span>
          {hypothesis.vuln_class && (
            <span className="shrink-0 rounded-full bg-inset px-1.5 font-mono text-[10px] text-ink-3">
              {hypothesis.vuln_class}
            </span>
          )}
          <ChevronDown
            className={cn(
              "size-3.5 shrink-0 text-ink-3 transition-transform",
              open && "rotate-180",
            )}
            strokeWidth={1.5}
          />
        </button>
        {open && (
          <div className="border-t border-line px-2.5 py-2">
            <p className="text-[12px] font-medium text-pretty text-ink">
              {hypothesis.title}
            </p>
            {hypothesis.angle && (
              <p className="mt-1 text-[12px] text-ink-2">{hypothesis.angle}</p>
            )}
            {hypothesis.rationale && (
              <p className="mt-1 text-[12px] leading-relaxed text-ink-3">
                {hypothesis.rationale}
              </p>
            )}
            {hypothesis.result && (
              <p className="mt-1.5 text-[12px] text-ink-2">
                result: {hypothesis.result}
              </p>
            )}
            {hypothesis.generator_id != null && (
              <p className="mt-1 text-[11px] text-ink-3">
                worker #{hypothesis.generator_id}
              </p>
            )}
          </div>
        )}
      </li>
    );
  },
  (prev, next) =>
    prev.hypothesis.id === next.hypothesis.id &&
    prev.hypothesis.status === next.hypothesis.status &&
    prev.hypothesis.result === next.hypothesis.result &&
    prev.hypothesis.updated_at === next.hypothesis.updated_at,
);

function EmptyState({
  configured,
  onPick,
}: {
  configured: boolean;
  onPick: (value: string) => void;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-4 px-6 text-center">
      <div className="flex size-11 items-center justify-center rounded-full bg-hover text-ink-2">
        <Sparkles className="size-5" />
      </div>
      <div>
        <p className="text-[13px] font-medium text-balance">
          Ask about this target
        </p>
        <p className="mt-1 text-xs text-pretty text-ink-3">
          {configured
            ? "Ask the agent to run something. It has bash and file tools in a per-campaign work dir."
            : "DEEPSEEK_API_KEY is not set. Add it to .env and restart."}
        </p>
      </div>
      {configured && (
        <div className="flex flex-wrap justify-center gap-2">
          {SUGGESTIONS.map((suggestion) => (
            <button
              key={suggestion}
              type="button"
              onClick={() => onPick(suggestion)}
              className="material-key rounded-full px-3 py-1 text-[12px] text-ink-2 outline-none transition-[background-color,box-shadow,color,scale] duration-150 ease-[var(--ease-out-strong)] hover:text-ink focus-visible:ring-2 focus-visible:ring-ring/60 active:scale-[0.96]"
            >
              {suggestion}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function ContextPanel({
  campaign,
  attachments,
  defaultOpen,
}: {
  campaign: CampaignData;
  attachments: Attachment[];
  defaultOpen: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);

  const sections = SECTIONS.map(({ key, label }) => {
    const value = campaign[key].trim();
    const files = attachments.filter((a) => a.field === key);
    return { key, label, value, files };
  }).filter((section) => section.value || section.files.length > 0);

  if (sections.length === 0) return null;

  return (
    <div className="rounded-[12px] bg-surface shadow-card">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        className="flex w-full items-center gap-2 rounded-[12px] px-3.5 py-2.5 text-left transition-colors hover:bg-hover"
      >
        <FileText className="size-3.5 shrink-0 text-ink-3" />
        <span className="text-[13px] font-medium">Brief</span>
        <span className="text-[12px] text-ink-3">
          {sections.length} {sections.length === 1 ? "section" : "sections"}
        </span>
        <ChevronDown
          className={cn(
            "ml-auto size-4 text-ink-3 transition-transform duration-300",
            open && "rotate-180",
          )}
        />
      </button>

      <div
        className="grid transition-[grid-template-rows,opacity] duration-300 ease-[cubic-bezier(0.23,1,0.32,1)]"
        style={{
          gridTemplateRows: open ? "1fr" : "0fr",
          opacity: open ? 1 : 0,
        }}
      >
        <div className="overflow-hidden">
          <div className="grid max-h-[40vh] gap-3 overflow-y-auto border-t border-line px-3.5 py-3 [scrollbar-color:var(--line-strong)_transparent] [scrollbar-width:thin] sm:grid-cols-2">
            {sections.map((section) => (
              <div
                key={section.key}
                className="rounded-[12px] bg-inset px-3 py-2.5"
              >
                <div className="text-[11px] font-medium tracking-wide text-ink-3 uppercase">
                  {section.label}
                </div>
                {section.value && (
                  <p className="mt-1 text-[13px] leading-relaxed whitespace-pre-wrap text-ink-2">
                    {section.value}
                  </p>
                )}
                {section.files.length > 0 && (
                  <div className="mt-2 flex flex-wrap gap-2">
                    {section.files.map((a) => (
                      <AttachmentChip
                        key={a.id}
                        name={a.filename}
                        mime={a.content_type}
                        href={api.attachmentUrl(a.id)}
                      />
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

// composer tray card — owns the object-URL lifecycle for image previews
function PendingCard({ file, onRemove }: { file: File; onRemove: () => void }) {
  const [preview, setPreview] = useState<string>();

  useEffect(() => {
    if (!file.type.startsWith("image/")) return;
    const url = URL.createObjectURL(file);
    setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);

  return (
    <AttachmentCard
      name={file.name}
      mime={file.type}
      previewUrl={preview}
      meta={fmtSize(file.size)}
      onRemove={onRemove}
    />
  );
}
