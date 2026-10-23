"use client";

import {
  type CSSProperties,
  Fragment,
  useEffect,
  useRef,
  useState,
} from "react";
import type { Highlighter } from "shiki";
import { cn } from "@/lib/utils";

export type AgentCodeLanguage =
  | "bash"
  | "diff"
  | "json"
  | "text"
  | "tsx"
  | "typescript";

export interface AgentCodeToken {
  content: string;
  offset: number;
  light?: string;
  dark?: string;
}

export type AgentCodeTokenLines = AgentCodeToken[][];

export interface AgentCodeProps {
  code: string;
  language?: AgentCodeLanguage;
  className?: string;
}

export interface AgentCodeLineProps {
  code: string;
  tokens?: AgentCodeToken[];
  className?: string;
}

const LIGHT_THEME = "github-light-high-contrast";
const DARK_THEME = "github-dark-high-contrast";
// highlighting a huge blob produces hundreds of thousands of token spans, so
// past this size the code is rendered as plain text instead
const HIGHLIGHT_MAX_CHARS = 40_000;
const TOKEN_CACHE_MAX = 200;
let agentCodeHighlighter: Promise<Highlighter> | null = null;
const tokenCache = new Map<string, AgentCodeTokenLines>();

function getAgentCodeHighlighter() {
  if (!agentCodeHighlighter) {
    // shiki is heavy; keep it out of the initial bundle and load it the first
    // time a tool result needs syntax highlighting
    agentCodeHighlighter = import("shiki").then(({ createHighlighter }) =>
      createHighlighter({
        themes: [LIGHT_THEME, DARK_THEME],
        langs: ["bash", "diff", "json", "tsx", "typescript"],
      }),
    );
  }
  return agentCodeHighlighter;
}

function tokenCacheKey(code: string, language: AgentCodeLanguage) {
  return `${language}\u0000${code}`;
}

export function useAgentCodeTokens(
  code: string,
  language: AgentCodeLanguage,
) {
  const highlightable = code.length <= HIGHLIGHT_MAX_CHARS;
  const key = highlightable ? tokenCacheKey(code, language) : "";
  const cached = highlightable ? tokenCache.get(key) : undefined;
  // streaming output grows per token batch and every intermediate version is
  // a cache miss, so an unthrottled hook re-tokenizes the whole block per
  // chunk (O(n^2) highlighting). Hold the previous pass until the code has
  // settled: new lines render plain meanwhile, the transcript refetch on
  // chain end remounts with final text for the definitive pass.
  const lastPass = useRef<{ code: string; at: number } | null>(null);
  const [result, setResult] = useState<{
    key: string;
    code: string;
    language: AgentCodeLanguage;
    lines: AgentCodeTokenLines;
  } | null>(cached ? { key, code, language, lines: cached } : null);

  useEffect(() => {
    if (!highlightable) return;
    const current = tokenCache.get(key);
    if (current) {
      setResult({ key, code, language, lines: current });
      return;
    }

    const prev = lastPass.current;
    if (prev && code.startsWith(prev.code) && Date.now() - prev.at < 1500) return;

    let cancelled = false;
    getAgentCodeHighlighter().then((highlighter) => {
      if (cancelled) return;
      const lines = highlighter
        .codeToTokensWithThemes(code, {
          lang: language,
          themes: {
            light: LIGHT_THEME,
            dark: DARK_THEME,
          },
        })
        .map((line) =>
          line.map((token) => ({
            content: token.content,
            offset: token.offset,
            light: token.variants.light?.color,
            dark: token.variants.dark?.color,
          })),
        );
      if (tokenCache.size >= TOKEN_CACHE_MAX && !tokenCache.has(key)) {
        const oldest = tokenCache.keys().next().value;
        if (oldest !== undefined) tokenCache.delete(oldest);
      }
      tokenCache.set(key, lines);
      lastPass.current = { code, at: Date.now() };
      setResult({ key, code, language, lines });
    });
    return () => {
      cancelled = true;
    };
  }, [code, highlightable, key, language]);

  if (!highlightable) return null;
  if (result?.key === key) return result.lines;
  if (result?.language === language && code.startsWith(result.code)) {
    return result.lines;
  }
  return null;
}

export function AgentCodeLine({
  code,
  tokens,
  className,
}: AgentCodeLineProps) {
  return (
    <span className={className}>
      {tokens
        ? tokens.map((token) => (
            <span
              key={`${token.offset}-${token.content}`}
              style={
                {
                  "--agent-code-light": token.light ?? "currentColor",
                  "--agent-code-dark": token.dark ?? token.light ?? "currentColor",
                } as CSSProperties
              }
              className="text-[var(--agent-code-light)] dark:text-[var(--agent-code-dark)]"
            >
              {token.content}
            </span>
          ))
        : code}
    </span>
  );
}

export function AgentCode({
  code,
  language = "bash",
  className,
}: AgentCodeProps) {
  const tokens = useAgentCodeTokens(code, language);
  let offset = 0;
  const lines = code.split("\n").map((content) => {
    const line = { content, offset };
    offset += content.length + 1;
    return line;
  });

  return (
    <pre
      className={cn(
        "m-0 overflow-x-auto whitespace-pre font-mono text-xs leading-5 text-foreground/85",
        className,
      )}
    >
      <code>
        {lines.map((line, index) => (
          <Fragment key={line.offset}>
            <AgentCodeLine code={line.content} tokens={tokens?.[index]} />
            {index < lines.length - 1 ? "\n" : null}
          </Fragment>
        ))}
      </code>
    </pre>
  );
}
