import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";

import { PromptInput } from "@/components/agents/prompt-input";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { IconAction } from "@/components/IconAction";
import {
  Crosshair,
  Play,
  Plus,
  Search,
  Square,
  Trash2,
  TriangleAlert,
} from "@/components/icons";
import { Pulse } from "@/components/Pulse";
import { StatusBadge } from "@/components/StatusBadge";
import { TransitionLink } from "@/components/TransitionLink";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { SegmentedControl } from "@/components/ui/segmented";
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty";
import {
  InputGroup,
  InputGroupAddon,
  InputGroupInput,
} from "@/components/ui/input-group";
import { Kbd } from "@/components/ui/kbd";
import { Skeleton } from "@/components/ui/skeleton";
import { api, FIELDS, type Campaign } from "@/lib/api";
import { cn, fmtDate, fmtTokens } from "@/lib/utils";
import { withViewTransition } from "@/lib/view-transition";

const SUGGESTIONS = [
  "https://example.com",
  "Recon 10.0.0.0/24 and map exposed services",
  "Review this staging API for auth flaws",
];

export function Dashboard() {
  const nav = useNavigate();
  const [campaigns, setCampaigns] = useState<Campaign[] | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(0);

  // the hero composer starts a campaign from just a target
  const [target, setTarget] = useState("");
  const [creating, setCreating] = useState(false);
  const [defaults, setDefaults] = useState<{
    model: string;
    effort: string;
  } | null>(null);
  const [models, setModels] = useState<string[]>([]);
  const [model, setModel] = useState<string | undefined>(undefined);
  const [mode, setMode] = useState<"continuous" | "goal">("continuous");
  const [pendingDelete, setPendingDelete] = useState<Campaign | null>(null);
  const [query, setQuery] = useState("");
  const composerRef = useRef<HTMLDivElement>(null);
  // the list polls every 3s while a campaign runs; the entrance animation must
  // run on the first paint only, never on a refresh
  const revealed = useRef(false);

  const load = () =>
    api
      .listCampaigns()
      .then((list) => {
        setCampaigns(list);
        // let the stagger play for this paint, then never again
        requestAnimationFrame(() => {
          revealed.current = true;
        });
      })
      .catch((e: Error) => setError(e.message));

  useEffect(() => {
    load();
    api
      .config()
      .then((c) => {
        setDefaults({ model: c.default_model, effort: c.default_effort });
        setModels(c.models);
        setModel(c.default_model);
      })
      .catch(() => setDefaults(null));
  }, []);

  const q = query.trim().toLowerCase();
  const shown =
    campaigns && q
      ? campaigns.filter((c) =>
          [c.target, c.model, c.status].some((v) =>
            v?.toLowerCase().includes(q),
          ),
        )
      : campaigns;

  const anyRunning = campaigns?.some((c) => c.running) ?? false;

  useEffect(() => {
    if (!anyRunning) return;
    const timer = setInterval(load, 3000);
    return () => clearInterval(timer);
  }, [anyRunning]);

  // "/" jumps to the composer, the way chat products do. Ignored while the
  // user is already typing somewhere.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "/" || e.metaKey || e.ctrlKey || e.altKey) return;
      const el = document.activeElement;
      if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement)
        return;
      e.preventDefault();
      composerRef.current?.querySelector("textarea")?.focus();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  async function create(value?: string, submitModel?: string) {
    const v = (value ?? target).trim();
    if (!v || creating) return;

    setCreating(true);
    setError("");
    try {
      const form = new FormData();
      form.set("model", submitModel || model || defaults?.model || "");
      if (defaults) form.set("effort", defaults.effort);
      form.set("mode", mode);
      for (const field of FIELDS) form.set(field, field === "target" ? v : "");
      const campaign = await api.createCampaign(form);
      withViewTransition(() => nav(`/campaigns/${campaign.id}`));
    } catch (e) {
      setError((e as Error).message);
      setCreating(false);
    }
  }

  async function resume(campaign: Campaign) {
    setBusy(campaign.id);
    try {
      await api.resumeCampaign(campaign.id);
      toast.success("Agent resumed");
      await load();
    } catch (e) {
      toast.error("Could not resume the campaign", {
        description: (e as Error).message,
      });
    } finally {
      setBusy(0);
    }
  }

  async function stop(campaign: Campaign) {
    setBusy(campaign.id);
    try {
      await api.stopCampaign(campaign.id);
      await load();
    } catch (e) {
      toast.error("Could not stop the campaign", {
        description: (e as Error).message,
      });
    } finally {
      setBusy(0);
    }
  }

  async function remove() {
    const campaign = pendingDelete;
    if (!campaign) return;

    setBusy(campaign.id);
    try {
      await api.deleteCampaign(campaign.id);
      setCampaigns((prev) => prev?.filter((c) => c.id !== campaign.id) ?? prev);
      setPendingDelete(null);
      toast.success("Campaign deleted");
    } catch (e) {
      toast.error("Could not delete the campaign", {
        description: (e as Error).message,
      });
    } finally {
      setBusy(0);
    }
  }

  return (
    <div className="mx-auto max-w-5xl px-6 py-10 sm:px-8 max-md:pt-16">
      {/* hero — the front door: name a target, start probing */}
      <section className="flex flex-col items-center pt-6 pb-10 text-center sm:pt-14">
        <h1 className="text-2xl font-semibold tracking-[-0.02em] text-balance text-ink sm:text-[28px]">
          What are we testing?
        </h1>
        <p className="mt-3 max-w-md text-sm text-pretty text-ink-2">
          {mode === "goal"
            ? "Name a target and the agent starts probing it in an isolated work dir — and keeps working until the goal is met."
            : "Name a target and the agent starts probing it in an isolated work dir — and keeps going until you stop it."}
        </p>

        <div ref={composerRef} className="mt-8 w-full max-w-3xl text-left">
          <PromptInput
            value={target}
            onValueChange={setTarget}
            onSubmit={create}
            loading={creating}
            placeholder="https://example.com — or describe the target"
            minRows={2}
            maxRows={8}
            aria-label="New campaign target"
            models={models.map((m) => ({ value: m, label: m }))}
            model={model}
            onModelChange={setModel}
            leadingAction={
              <SegmentedControl
                value={mode}
                onValueChange={setMode}
                layoutId="hero-mode"
                options={[
                  { value: "continuous", label: "24×7" },
                  { value: "goal", label: "Until goal" },
                ]}
                className="shrink-0"
              />
            }
          />

          <div className="mt-3 flex items-center justify-center gap-1.5 text-[11px] text-ink-3">
            {mode === "goal"
              ? "Runs until the goal in the brief is met"
              : "Runs until you stop it"}
            <span aria-hidden className="mx-1">
              ·
            </span>
            Press <Kbd>/</Kbd> to focus
          </div>

          <div className="mt-4 flex flex-wrap justify-center gap-2">
            {SUGGESTIONS.map((s) => (
              <button
                key={s}
                type="button"
                onClick={() => setTarget(s)}
                className="material-key max-w-full truncate rounded-full px-3 py-1.5 text-[12px] text-ink-2 outline-none transition-[background-color,box-shadow,color,scale] duration-150 ease-[var(--ease-out-strong)] hover:text-ink focus-visible:ring-2 focus-visible:ring-ring/60 active:scale-[0.96]"
              >
                {s}
              </button>
            ))}
            <TransitionLink
              to="/campaigns/new"
              className="material-key inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-[12px] text-ink-3 outline-none transition-[background-color,box-shadow,color,scale] duration-150 ease-[var(--ease-out-strong)] hover:text-ink focus-visible:ring-2 focus-visible:ring-ring/60 active:scale-[0.96]"
            >
              <Plus className="size-3.5" strokeWidth={2} />
              Full brief
            </TransitionLink>
          </div>
        </div>
      </section>

      {error && (
        <Alert variant="destructive" className="mb-4">
          <TriangleAlert />
          <AlertTitle>Could not load campaigns</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      <div className="mt-10 mb-3 flex items-center gap-2">
        <h2 className="text-[13px] font-medium text-ink">Campaigns</h2>
        {campaigns && campaigns.length > 0 && (
          <span className="font-mono text-[11px] tabular-nums text-ink-3">
            {campaigns.length}
          </span>
        )}
        {campaigns && campaigns.length > 3 && (
          <InputGroup className="ml-auto h-8 w-56 shrink-0 px-2.5">
            <InputGroupInput
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Filter campaigns"
              aria-label="Filter campaigns"
              className="h-8 text-[13px]"
            />
            <InputGroupAddon>
              <Search strokeWidth={1.5} aria-hidden />
            </InputGroupAddon>
          </InputGroup>
        )}
      </div>

      {!error && campaigns === null && <CardsSkeleton />}

      {campaigns?.length === 0 && (
        <Empty>
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <Crosshair strokeWidth={1.5} />
            </EmptyMedia>
            <EmptyTitle>No campaigns yet</EmptyTitle>
            <EmptyDescription>
              Start one from the box above, or set up a full brief with scope
              and instructions.
            </EmptyDescription>
          </EmptyHeader>
          <EmptyContent>
            <Button asChild variant="outline" size="sm">
              <TransitionLink to="/campaigns/new">
                <Plus className="size-4" strokeWidth={2} /> New campaign
              </TransitionLink>
            </Button>
          </EmptyContent>
        </Empty>
      )}

      {shown && shown.length > 0 && (
        <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {shown.map((c, i) => (
            <li
              key={c.id}
              // stagger 50ms per card (Emil: 30–80ms), first paint only
              className={cn(!revealed.current && "opacity-0")}
              style={
                revealed.current
                  ? undefined
                  : {
                      animation: `fade-up 300ms var(--ease-out-strong) ${Math.min(i, 8) * 50}ms both`,
                    }
              }
            >
              <Card
                size="sm"
                className="group/card relative h-full gap-0 px-3.5 transition-[box-shadow,transform] duration-150 ease-out hover:shadow-raised"
              >
                <div className="flex items-center gap-2">
                  {c.running ? (
                    <span className="flex items-center gap-1.5 text-[11px] font-medium text-ink-2">
                      <Pulse />
                      {c.mode === "goal"
                        ? "running · until goal"
                        : "running 24x7"}
                    </span>
                  ) : (
                    <StatusBadge status={c.status} />
                  )}
                  {/* z-20 keeps these above the card-wide link overlay */}
                  <div className="relative z-20 ml-auto flex items-center gap-0.5">
                    {c.running && (
                      <IconAction
                        variant="ghost"
                        size="icon-xs"
                        label="Stop campaign"
                        description={`Stop campaign ${c.target.split("\n")[0]}`}
                        disabled={busy === c.id}
                        onClick={() => stop(c)}
                        className="text-ink-3 hover:text-ink focus-visible:opacity-100 [@media(hover:hover)]:opacity-0 [@media(hover:hover)]:group-hover/card:opacity-100"
                      >
                        <Square className="size-3.5" strokeWidth={1.5} />
                      </IconAction>
                    )}
                    {!c.running && (
                      <IconAction
                        variant="ghost"
                        size="icon-xs"
                        label={
                          c.mode === "goal"
                            ? "Resume until goal"
                            : "Resume 24x7"
                        }
                        description={`Resume campaign ${c.target.split("\n")[0]}`}
                        disabled={busy === c.id}
                        onClick={() => resume(c)}
                        className="text-ink-3 hover:text-ink focus-visible:opacity-100 [@media(hover:hover)]:opacity-0 [@media(hover:hover)]:group-hover/card:opacity-100"
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
                      description={`Delete campaign ${c.target.split("\n")[0]}`}
                      disabled={busy === c.id}
                      onClick={() => setPendingDelete(c)}
                      className="text-ink-3 hover:text-destructive focus-visible:opacity-100 [@media(hover:hover)]:opacity-0 [@media(hover:hover)]:group-hover/card:opacity-100"
                    >
                      <Trash2 className="size-3.5" strokeWidth={1.5} />
                    </IconAction>
                  </div>
                </div>

                <TransitionLink
                  to={`/campaigns/${c.id}`}
                  title={c.target}
                  className="mt-2.5 line-clamp-2 font-mono text-[13px] leading-snug text-ink outline-none after:absolute after:inset-0 after:rounded-[12px] focus-visible:after:ring-2 focus-visible:after:ring-ring/60"
                >
                  {c.target.split("\n")[0]}
                </TransitionLink>

                <div className="mt-auto flex flex-wrap items-center gap-x-2 gap-y-1 pt-3 text-[11px] text-ink-3">
                  <span className="font-mono">{c.model}</span>
                  <span aria-hidden>·</span>
                  <span className="tabular-nums">
                    {c.message_count ?? 0} messages
                  </span>
                  {(c.usage_in ?? 0) + (c.usage_out ?? 0) > 0 && (
                    <>
                      <span aria-hidden>·</span>
                      <span
                        className="tabular-nums"
                        title={`${(c.usage_in ?? 0).toLocaleString()} in / ${(c.usage_out ?? 0).toLocaleString()} out`}
                      >
                        {fmtTokens((c.usage_in ?? 0) + (c.usage_out ?? 0))}{" "}
                        tokens
                      </span>
                    </>
                  )}
                  <span aria-hidden>·</span>
                  <span className="tabular-nums">{fmtDate(c.created_at)}</span>
                </div>
              </Card>
            </li>
          ))}
        </ul>
      )}

      {campaigns && campaigns.length > 0 && shown?.length === 0 && (
        <Empty>
          <EmptyTitle>No campaigns match “{query.trim()}”</EmptyTitle>
          <EmptyContent>
            <Button variant="outline" size="sm" onClick={() => setQuery("")}>
              Clear filter
            </Button>
          </EmptyContent>
        </Empty>
      )}

      <ConfirmDialog
        open={pendingDelete !== null}
        onOpenChange={(open) => !open && setPendingDelete(null)}
        title="Delete this campaign?"
        description={
          pendingDelete ? (
            <>
              <span className="font-mono break-all">
                {pendingDelete.target.split("\n")[0]}
              </span>{" "}
              and all of its messages and files are removed. This cannot be
              undone.
            </>
          ) : undefined
        }
        confirmLabel="Delete campaign"
        busy={busy === pendingDelete?.id}
        onConfirm={remove}
      />
    </div>
  );
}

function CardsSkeleton() {
  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3" aria-hidden>
      {[0, 1, 2, 3].map((i) => (
        <Card key={i} size="sm" className="gap-3 px-3.5">
          <Skeleton className="h-4 w-20 rounded-full" />
          <Skeleton className="h-3.5 w-[70%]" />
          <Skeleton className="h-3 w-[45%]" />
        </Card>
      ))}
    </div>
  );
}
