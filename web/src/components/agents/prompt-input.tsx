"use client";
// www.agentui.pro/components/agents/prompt-input

import { ArrowUp, FileText, Plus, Search, Square } from "@/components/icons";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import {
  type ChangeEvent,
  type FormEvent,
  type KeyboardEvent,
  type ReactNode,
  type TextareaHTMLAttributes,
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { Button } from "@/components/motion/button";
import {
  MorphPopover,
  MorphPopoverContent,
  MorphPopoverTrigger,
} from "@/components/motion/popover-morph";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
} from "@/components/motion/select";
import { MODEL_FILTER_THRESHOLD } from "@/components/ui/model-select";
import { SPRING_SWAP } from "@/lib/ease";
import { cn } from "@/lib/utils";

export interface PromptModel {
  value: string;
  label: ReactNode;
  icon?: ReactNode;
  disabled?: boolean;
}

export interface PromptAction {
  value: string;
  label: ReactNode;
  description?: ReactNode;
  icon?: ReactNode;
  disabled?: boolean;
}

export interface PromptInputProps extends Omit<
  TextareaHTMLAttributes<HTMLTextAreaElement>,
  "value" | "defaultValue" | "onChange" | "onSubmit" | "children"
> {
  value?: string;
  defaultValue?: string;
  onValueChange?: (value: string) => void;
  models?: PromptModel[];
  model?: string;
  defaultModel?: string;
  onModelChange?: (model: string) => void;
  actions?: PromptAction[];
  onAction?: (action: string) => void;
  onSubmit?: (value: string, model?: string) => void | Promise<void>;
  loading?: boolean;
  onStop?: () => void;
  minRows?: number;
  maxRows?: number;
  leadingAction?: ReactNode;
  /** Paths offered by an `@`-mention autocomplete in the textarea. */
  mentions?: string[];
  className?: string;
}

export function PromptInput({
  value,
  defaultValue = "",
  onValueChange,
  models = [],
  model,
  defaultModel,
  onModelChange,
  actions = [],
  onAction,
  onSubmit,
  loading = false,
  onStop,
  minRows = 2,
  maxRows = 8,
  leadingAction,
  mentions = [],
  className,
  disabled,
  placeholder = "Ask the agent to do something…",
  "aria-label": ariaLabel = "Prompt",
  onKeyDown,
  ...textareaProps
}: PromptInputProps) {
  const reduce = useReducedMotion() ?? false;
  const mentionListId = useId();
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const measurementRef = useRef<HTMLDivElement>(null);
  const [internalValue, setInternalValue] = useState(defaultValue);
  const [internalModel, setInternalModel] = useState(
    defaultModel ?? models[0]?.value,
  );
  const [actionsOpen, setActionsOpen] = useState(false);
  const [modelOpen, setModelOpen] = useState(false);
  const [modelQuery, setModelQuery] = useState("");
  const modelFilterRef = useRef<HTMLInputElement>(null);
  const mentionStart = useRef(-1);
  const [mentionQuery, setMentionQuery] = useState("");
  const [mentionIndex, setMentionIndex] = useState(0);
  const [mentionsOpen, setMentionsOpen] = useState(false);
  const currentValue = value ?? internalValue;
  const currentModelValue = model ?? internalModel;
  const currentModel = models.find(
    (option) => option.value === currentModelValue,
  );
  const modelFilterable = models.length > MODEL_FILTER_THRESHOLD;
  const modelMatches = useMemo(() => {
    const q = modelQuery.trim().toLowerCase();
    if (!q) return models;
    // NOTE: non-matches unmount here (unlike ModelSelect, which hides +
    // disables). Safe only because this custom motion select has no
    // focus-selected-item effect — Radix would steal filter focus on every
    // keystroke if its collection churned. Do not "unify" without re-checking.
    return models.filter((option) => {
      if (option.value.toLowerCase().includes(q)) return true;
      return typeof option.label === "string" && option.label.toLowerCase().includes(q);
    });
  }, [models, modelQuery]);
  const canSubmit = Boolean(currentValue.trim()) && !disabled && !loading;

  const resizeTextarea = useCallback(() => {
    const textarea = textareaRef.current;
    const measurement = measurementRef.current;
    if (!textarea || !measurement || textarea.value !== currentValue) return;

    const lineHeight = 24;
    const nextHeight = Math.min(
      Math.max(measurement.scrollHeight, minRows * lineHeight),
      maxRows * lineHeight,
    );
    const height = `${nextHeight}px`;
    if (textarea.style.height !== height) textarea.style.height = height;
  }, [currentValue, maxRows, minRows]);

  useLayoutEffect(() => {
    resizeTextarea();
  }, [resizeTextarea]);

  useEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(resizeTextarea);
    observer.observe(textarea);
    return () => observer.disconnect();
  }, [resizeTextarea]);

  const setValue = (next: string) => {
    if (value === undefined) setInternalValue(next);
    onValueChange?.(next);
  };

  const setModel = (next: string) => {
    if (model === undefined) setInternalModel(next);
    onModelChange?.(next);
  };

  // `@`-mention autocomplete over the provided paths
  const mentionMatches = useMemo(() => {
    if (!mentionsOpen) return [];
    const q = mentionQuery.toLowerCase();
    const pool = q ? mentions.filter((m) => m.toLowerCase().includes(q)) : mentions;
    return pool.slice(0, 50);
  }, [mentionsOpen, mentionQuery, mentions]);

  const handleChange = (event: ChangeEvent<HTMLTextAreaElement>) => {
    const next = event.target.value;
    setValue(next);
    if (!mentions.length) return;
    const caret = event.target.selectionStart ?? next.length;
    const before = next.slice(0, caret);
    const at = before.lastIndexOf("@");
    const valid =
      at !== -1 &&
      (at === 0 || /\s/.test(before[at - 1])) &&
      !/\s/.test(before.slice(at + 1));
    if (valid) {
      mentionStart.current = at;
      setMentionQuery(before.slice(at + 1));
      setMentionIndex(0);
      setMentionsOpen(true);
    } else {
      setMentionsOpen(false);
    }
  };

  const applyMention = (path: string) => {
    const textarea = textareaRef.current;
    const start = mentionStart.current;
    if (!textarea || start < 0) return;
    const current = textarea.value;
    const caret = textarea.selectionStart ?? current.length;
    const next = `${current.slice(0, start)}@${path} ${current.slice(caret)}`;
    setValue(next);
    setMentionsOpen(false);
    const pos = start + path.length + 2;
    requestAnimationFrame(() => {
      textarea.focus();
      textarea.setSelectionRange(pos, pos);
    });
  };

  const submit = (event?: FormEvent) => {
    event?.preventDefault();
    const prompt = currentValue.trim();
    if (!prompt || disabled || loading) return;

    onSubmit?.(prompt, currentModelValue);
    if (value === undefined) setInternalValue("");
    textareaRef.current?.focus({ preventScroll: true });
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    onKeyDown?.(event);
    if (mentionsOpen && mentionMatches.length) {
      if (event.key === "ArrowDown") {
        event.preventDefault();
        setMentionIndex((i) => Math.min(i + 1, mentionMatches.length - 1));
        return;
      }
      if (event.key === "ArrowUp") {
        event.preventDefault();
        setMentionIndex((i) => Math.max(i - 1, 0));
        return;
      }
      if (event.key === "Enter" || event.key === "Tab") {
        event.preventDefault();
        applyMention(mentionMatches[mentionIndex]);
        return;
      }
      if (event.key === "Escape") {
        event.preventDefault();
        setMentionsOpen(false);
        return;
      }
    }
    if (
      event.defaultPrevented ||
      event.key !== "Enter" ||
      event.shiftKey ||
      event.nativeEvent.isComposing
    ) {
      return;
    }
    event.preventDefault();
    submit();
  };

  return (
    <form
      onSubmit={submit}
      className={cn(
        "relative w-full rounded-2xl bg-surface p-2 shadow-card transition-shadow duration-150 ease-[var(--ease-out-strong)] focus-within:ring-1 focus-within:ring-ring/40",
        disabled && "opacity-60",
        className,
      )}
    >
      <div
        ref={measurementRef}
        aria-hidden="true"
        className="pointer-events-none invisible absolute inset-x-2 top-0 whitespace-pre-wrap px-2 text-sm leading-6 [overflow-wrap:break-word]"
      >
        {`${currentValue}\u200b`}
      </div>
      <textarea
        ref={textareaRef}
        value={currentValue}
        disabled={disabled}
        placeholder={placeholder}
        aria-label={ariaLabel}
        aria-autocomplete={mentions.length ? "list" : undefined}
        aria-controls={mentionsOpen ? mentionListId : undefined}
        aria-activedescendant={
          mentionsOpen && mentionMatches.length
            ? `${mentionListId}-${mentionIndex}`
            : undefined
        }
        rows={minRows}
        {...textareaProps}
        onChange={handleChange}
        onKeyDown={handleKeyDown}
        className="scrollbar-hide block w-full resize-none overflow-y-auto bg-transparent px-2 pt-1.5 text-sm leading-6 text-foreground outline-none placeholder:text-muted-foreground/55"
      />

      {mentionsOpen && mentionMatches.length > 0 && (
        <div
          id={mentionListId}
          role="listbox"
          aria-label="File references"
          className="absolute bottom-full left-2 z-30 mb-2 max-h-56 w-72 overflow-y-auto rounded-xl bg-surface p-1 shadow-overlay"
        >
          {mentionMatches.map((path, i) => (
            <button
              key={path}
              id={`${mentionListId}-${i}`}
              type="button"
              role="option"
              aria-selected={i === mentionIndex}
              onMouseDown={(event) => {
                event.preventDefault();
                applyMention(path);
              }}
              className={cn(
                "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left font-mono text-[12px] outline-none",
                i === mentionIndex
                  ? "material-key text-ink"
                  : "text-ink-2 hover:bg-hover/60",
              )}
            >
              <FileText className="size-3.5 shrink-0 text-ink-3" strokeWidth={1.5} />
              <span className="min-w-0 flex-1 truncate">{path}</span>
            </button>
          ))}
        </div>
      )}

      <div className="mt-1 flex min-h-8 items-center gap-1">
        {actions.length ? (
          <MorphPopover open={actionsOpen} onOpenChange={setActionsOpen}>
            <MorphPopoverTrigger>
              <Button
                type="button"
                variant="ghost"
                size="icon"
                disabled={disabled || loading}
                aria-label="Add to prompt"
                className="size-8 rounded-full"
              >
                <motion.span
                  aria-hidden="true"
                  animate={{ rotate: actionsOpen ? 45 : 0 }}
                  transition={reduce ? { duration: 0 } : SPRING_SWAP}
                >
                  <Plus className="size-4" />
                </motion.span>
              </Button>
            </MorphPopoverTrigger>

            <MorphPopoverContent
              side="top"
              align="start"
              sideOffset={8}
              radius={12}
              className="w-56 p-1.5"
            >
              {actions.map((action) => (
                <button
                  key={action.value}
                  type="button"
                  disabled={action.disabled}
                  onClick={() => {
                    onAction?.(action.value);
                    setActionsOpen(false);
                  }}
                  className="flex w-full items-start gap-2.5 rounded-lg px-2.5 py-2 text-left outline-none transition-colors hover:bg-muted focus-visible:bg-muted disabled:pointer-events-none disabled:opacity-50"
                >
                  {action.icon ? (
                    <span className="mt-0.5 grid size-5 shrink-0 place-items-center text-muted-foreground [&_svg]:size-4">
                      {action.icon}
                    </span>
                  ) : null}
                  <span className="min-w-0">
                    <span className="block text-sm text-foreground">
                      {action.label}
                    </span>
                    {action.description ? (
                      <span className="mt-0.5 block text-xs leading-4 text-muted-foreground">
                        {action.description}
                      </span>
                    ) : null}
                  </span>
                </button>
              ))}
            </MorphPopoverContent>
          </MorphPopover>
        ) : null}
        {leadingAction}
        {models.length ? (
          <Select
            value={currentModelValue}
            onValueChange={setModel}
            disabled={disabled || loading}
            className="min-w-0"
            open={modelOpen}
            onOpenChange={(open) => {
              setModelOpen(open);
              if (!open) {
                setModelQuery("");
                return;
              }
              // long catalog: land in the filter so typing narrows it at once
              if (modelFilterable)
                requestAnimationFrame(() => modelFilterRef.current?.focus());
            }}
          >
            <SelectTrigger className="h-8 w-auto max-w-52 rounded-xl border-0 bg-transparent px-2 py-0 text-xs hover:bg-muted focus-visible:ring-2">
              <span className="flex min-w-0 items-center gap-1.5">
                {currentModel?.icon ? (
                  <span className="grid size-4 shrink-0 place-items-center text-muted-foreground [&_svg]:size-3.5">
                    {currentModel.icon}
                  </span>
                ) : null}
                <span className="min-w-0 truncate text-muted-foreground">
                  {currentModel?.label ?? "Choose model"}
                </span>
              </span>
            </SelectTrigger>
            <SelectContent
              className="right-auto w-72 shadow-none"
              header={
                modelFilterable ? (
                  <div className="border-b border-line px-1 pt-1 pb-1.5">
                    <div className="relative">
                      <Search className="pointer-events-none absolute top-1/2 left-2 size-3.5 -translate-y-1/2 text-ink-3" />
                      <input
                        ref={modelFilterRef}
                        value={modelQuery}
                        onChange={(e) => setModelQuery(e.target.value)}
                        // this panel lives inline inside the prompt <form>:
                        // Enter must not implicitly submit it. ArrowDown
                        // drops into the list, combobox-style.
                        onKeyDown={(e) => {
                          if (e.key === "Enter") e.preventDefault();
                          if (e.key === "ArrowDown") {
                            e.preventDefault();
                            const listbox = modelFilterRef.current?.closest(
                              '[role="listbox"]',
                            );
                            listbox
                              ?.querySelector<HTMLElement>('[role="option"]:not([disabled])')
                              ?.focus({ preventScroll: true });
                          }
                        }}
                        placeholder="Filter models…"
                        aria-label="Filter models"
                        className="h-7 w-full rounded-md bg-inset pr-2 pl-7 font-mono text-xs outline-none placeholder:text-ink-3 focus-visible:ring-2 focus-visible:ring-ring/40"
                      />
                    </div>
                  </div>
                ) : undefined
              }
            >
              {modelMatches.map((option) => (
                <SelectItem
                  key={option.value}
                  value={option.value}
                  disabled={option.disabled}
                  className="py-2"
                >
                  <span className="flex min-w-0 items-center gap-2">
                    {option.icon ? (
                      <span className="grid size-5 shrink-0 place-items-center text-muted-foreground [&_svg]:size-4">
                        {option.icon}
                      </span>
                    ) : null}
                    <span className="min-w-0 truncate text-sm text-foreground">
                      {option.label}
                    </span>
                  </span>
                </SelectItem>
              ))}
              {modelMatches.length === 0 && (
                <p className="px-2 py-4 text-center text-xs text-ink-3">
                  No models match “{modelQuery.trim()}”
                </p>
              )}
            </SelectContent>
          </Select>
        ) : null}

        <Button
          type={loading ? "button" : "submit"}
          size="icon"
          disabled={loading ? !onStop : !canSubmit}
          aria-label={loading ? "Stop generating" : "Send prompt"}
          onClick={loading ? onStop : undefined}
          className="relative ml-auto size-8 rounded-full after:absolute after:top-1/2 after:left-1/2 after:size-10 after:-translate-1/2"
        >
          <AnimatePresence initial={false} mode="popLayout">
            <motion.span
              key={loading ? "stop" : "send"}
              initial={reduce ? { opacity: 1 } : { opacity: 0, y: 3, scale: 0.8 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={reduce ? { opacity: 0 } : { opacity: 0, y: -3, scale: 0.8 }}
              transition={reduce ? { duration: 0 } : SPRING_SWAP}
              className="grid place-items-center"
            >
              {loading ? (
                <Square className="size-3 fill-current" />
              ) : (
                <ArrowUp className="size-4" />
              )}
            </motion.span>
          </AnimatePresence>
        </Button>
      </div>
    </form>
  );
}
