"use client";

import { ArrowSquareOut, CaretDown, CheckCircle, CircleNotch, GitPullRequest, Play, SkipForward, Stop, Warning } from "@phosphor-icons/react";
import clsx from "clsx";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { useCallback, useEffect, useRef, useState } from "react";
import { BlockView } from "@/components/chat/Conversation";
import { StepRail } from "@/components/chat/Blocks";
import { ThinkingPill } from "@/components/chat/Thought";
import { ProofPanel } from "@/components/features/proof/ProofPanel";
import { AuditSlot } from "@/components/run/AuditSlot";
import { ReceiptSlot } from "@/components/run/ReceiptSlot";
import { ApiError } from "@/lib/api";
import { campaignApi, usePolled, type Campaign, type CampaignItem, type ItemStatus } from "@/lib/campaigns";
import type { Mode } from "@/lib/state";
import { useRunStream } from "@/lib/use-run-stream";

// Entrances match the conversation: occasional, 220ms strong ease out, a short lift. Opacity only under reduced motion.
const ENTER = { duration: 0.22, ease: [0.23, 1, 0.32, 1] } as const;

const STATUS: Record<ItemStatus, { label: string; dot: string; text: string }> = {
  queued: { label: "Queued", dot: "border border-line-strong", text: "text-fg-subtle" },
  fixing: { label: "Fixing", dot: "dot-pulse bg-progress shadow-[0_0_10px_rgb(255_176_32/0.6)]", text: "text-progress" },
  awaiting_approval: { label: "Awaiting approval", dot: "bg-progress", text: "text-progress" },
  pr_opened: { label: "PR open", dot: "bg-accent shadow-[0_0_10px_color-mix(in_oklab,var(--accent)_55%,transparent)]", text: "text-accent" },
  merged: { label: "Merged", dot: "bg-accent shadow-[0_0_10px_color-mix(in_oklab,var(--accent)_55%,transparent)]", text: "text-accent" },
  failed: { label: "Failed", dot: "bg-fail", text: "text-fail" },
  skipped: { label: "Skipped", dot: "bg-line-strong", text: "text-fg-subtle" },
};

const SEVERITY: Record<string, string> = {
  critical: "border-fail/40 bg-fail/12 text-fail",
  high: "border-fail/40 bg-fail/12 text-fail",
  moderate: "border-progress/40 bg-progress/12 text-progress",
  medium: "border-progress/40 bg-progress/12 text-progress",
  low: "border-line-strong bg-card text-fg-muted",
};

const DONE = new Set<ItemStatus>(["pr_opened", "merged"]);
const busy = (c: Campaign) => c.scan_status === "running" || c.active !== null || c.next_start_in !== null;
const loadCampaign = (id: string) => campaignApi.get(id);

type Props = {
  campaignId: string;
  repo: string;
  // Opens a fix run as the main conversation, e.g. to approve its merge in ship mode.
  onOpenRun: (runId: string) => void;
  onAuditReport: (auditorRunId: string) => void;
};

/** The campaign under its scan: progress, the queue of vulnerable packages, and one section per fix run. */
export function CampaignPanel({ campaignId, repo, onOpenRun, onAuditReport }: Props) {
  const { value: campaign, error, refresh } = usePolled(campaignId, loadCampaign, busy);
  const [actionError, setActionError] = useState<string | null>(null);
  const [pending, setPending] = useState<string | null>(null);

  const act = useCallback(
    async (key: string, action: () => Promise<unknown>) => {
      setPending(key);
      setActionError(null);
      try {
        await action();
      } catch (e) {
        setActionError(e instanceof ApiError ? e.message : "That did not work");
      } finally {
        setPending(null);
        refresh();
      }
    },
    [refresh],
  );

  if (!campaign) return error ? <p className="text-[0.88rem] text-fg-muted">{error}</p> : null;
  if (campaign.scan_status === "running") return null;
  if (campaign.scan_status === "failed") {
    return <p className="text-[0.88rem] text-fg-muted">The scan ended without a list of vulnerable packages, so there is nothing to fix one at a time.</p>;
  }

  const fixRuns = campaign.items.filter((item) => item.run_id);
  return (
    <section className="flex flex-col gap-4" aria-label="Campaign">
      <ProgressStrip campaign={campaign} />
      <FoundCard campaign={campaign} pending={pending} error={actionError} onAct={act} />
      {fixRuns.map((item) => (
        <FixRunSection key={item.run_id} item={item} repo={repo} mode={campaign.mode} active={item.status === "fixing" || item.status === "awaiting_approval"} onOpenRun={onOpenRun} onAuditReport={onAuditReport} />
      ))}
      <NextPopup campaign={campaign} pending={pending} onAct={act} />
    </section>
  );
}

/** "<done> of <total> fixed", with a dot per package in its status colour. */
function ProgressStrip({ campaign }: { campaign: Campaign }) {
  if (campaign.total === 0) return null;
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded-[var(--radius-card)] border border-line bg-panel/80 px-4 py-3" role="status" aria-live="polite">
      <p className="text-[0.88rem] text-fg">
        <span className="font-mono tabular-nums">{campaign.done}</span> of <span className="font-mono tabular-nums">{campaign.total}</span> fixed
      </p>
      <ol className="flex flex-wrap items-center gap-1.5" aria-label="Packages by status">
        {campaign.items.map((item) => (
          <li key={item.package} title={`${item.package}: ${STATUS[item.status].label}`}>
            {/* A status change recolours the dot: 200ms, colour only, nothing moves. */}
            <span className={clsx("block size-2.5 rounded-full transition-[background-color,box-shadow,border-color] duration-200 ease-out", STATUS[item.status].dot)} />
            <span className="sr-only">
              {item.package}: {STATUS[item.status].label}
            </span>
          </li>
        ))}
      </ol>
      {campaign.stopped && (
        <p className="flex items-center gap-1.5 text-[0.82rem] text-fail">
          <Warning weight="bold" className="size-4" aria-hidden />
          Stopped at {campaign.stopped.package}: {campaign.stopped.reason}
        </p>
      )}
      {campaign.auto && !campaign.stopped && <p className="text-[0.82rem] text-fg-subtle md:ml-auto">Fixing all, one PR at a time</p>}
    </div>
  );
}

function FoundCard({ campaign, pending, error, onAct }: { campaign: Campaign; pending: string | null; error: string | null; onAct: (key: string, action: () => Promise<unknown>) => void }) {
  const reduce = useReducedMotion();
  const busyFix = campaign.active !== null;
  if (campaign.total === 0) {
    return (
      <p className="flex items-center gap-2 rounded-[var(--radius-card)] border border-line bg-card px-4 py-3 text-[0.9rem] text-fg">
        <CheckCircle weight="fill" className="size-5 text-accent" aria-hidden />
        No vulnerable packages found. Nothing to fix.
      </p>
    );
  }
  return (
    <motion.section
      initial={reduce ? { opacity: 0 } : { opacity: 0, transform: "translateY(6px)" }}
      animate={{ opacity: 1, transform: "translateY(0px)" }}
      transition={ENTER}
      className="rounded-[var(--radius-card)] border border-line bg-panel/80"
    >
      <header className="flex flex-wrap items-center gap-3 border-b border-line px-4 py-3">
        <h3 className="font-display text-[0.98rem] font-semibold text-fg">
          Found {campaign.total} vulnerable {campaign.total === 1 ? "package" : "packages"}
        </h3>
        <div className="ml-auto flex flex-wrap items-center gap-3">
          <AutoToggle campaign={campaign} disabled={pending !== null} onChange={(auto) => onAct("auto", () => campaignApi.auto(campaign.id, auto))} />
          <button
            type="button"
            disabled={campaign.next === null || busyFix || pending !== null}
            onClick={() => onAct("next", () => campaignApi.next(campaign.id))}
            className="inline-flex h-9 items-center gap-1.5 rounded-full bg-accent px-4 text-[0.85rem] font-semibold text-canvas transition-[opacity,transform] duration-150 ease-out active:scale-[0.97] disabled:cursor-not-allowed disabled:opacity-40"
          >
            {pending === "next" ? <CircleNotch weight="bold" className="size-4 animate-spin motion-reduce:animate-none" aria-hidden /> : <Play weight="fill" className="size-4" aria-hidden />}
            Fix next
          </button>
        </div>
      </header>
      {error && (
        <p role="alert" className="border-b border-line px-4 py-2.5 text-[0.85rem] text-fail">
          {error}
        </p>
      )}
      <ol className="flex flex-col">
        {campaign.items.map((item) => (
          <ItemRow key={item.package} item={item} canAct={!busyFix && pending === null} pending={pending} onFix={() => onAct(`fix:${item.package}`, () => campaignApi.fix(campaign.id, item.package))} onSkip={() => onAct(`skip:${item.package}`, () => campaignApi.skip(campaign.id, item.package))} />
        ))}
      </ol>
    </motion.section>
  );
}

function AutoToggle({ campaign, disabled, onChange }: { campaign: Campaign; disabled: boolean; onChange: (auto: boolean) => void }) {
  return (
    <label className={clsx("flex cursor-pointer items-center gap-2 text-[0.82rem] text-fg-muted", disabled && "cursor-wait opacity-60")}>
      <button
        type="button"
        role="switch"
        aria-checked={campaign.auto}
        disabled={disabled}
        onClick={() => onChange(!campaign.auto)}
        className={clsx("relative h-5 w-9 rounded-full border transition-colors duration-200 ease-out", campaign.auto ? "border-accent/50 bg-accent/25" : "border-line-strong bg-card")}
      >
        {/* The knob slides 16px on the strong ease out, 200ms; a toggle is a state change the user makes, not a show. */}
        <span className={clsx("absolute left-0.5 top-0.5 size-3.5 rounded-full transition-[transform,background-color] duration-200 ease-[var(--ease-out)]", campaign.auto ? "translate-x-4 bg-accent" : "translate-x-0 bg-fg-subtle")} />
      </button>
      Fix all, one PR at a time
    </label>
  );
}

function ItemRow({ item, canAct, pending, onFix, onSkip }: { item: CampaignItem; canAct: boolean; pending: string | null; onFix: () => void; onSkip: () => void }) {
  const status = STATUS[item.status];
  const actionable = item.status === "queued" || item.status === "failed" || item.status === "skipped";
  return (
    <li className="grid grid-cols-1 gap-2 border-b border-line px-4 py-3 last:border-b-0 md:grid-cols-[minmax(0,1fr)_auto] md:items-center md:gap-6">
      <div className="min-w-0">
        <p className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <span className="font-mono text-[0.9rem] font-semibold text-fg">{item.package}</span>
          {item.severity && (
            <span className={clsx("rounded-full border px-2 py-0.5 text-[0.7rem] font-semibold uppercase tracking-wide", SEVERITY[item.severity] ?? "border-line-strong text-fg-muted")}>{item.severity}</span>
          )}
          <span className={clsx("flex items-center gap-1.5 text-[0.78rem] font-medium", status.text)}>
            <span aria-hidden className={clsx("size-2 rounded-full", status.dot)} />
            {status.label}
          </span>
        </p>
        <p className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-[0.78rem] text-fg-muted">
          {(item.from || item.to) && (
            <span>
              <span className="text-fail">{item.from ?? "current"}</span>
              <span className="px-1.5 text-fg-subtle">to</span>
              <span className="text-accent">{item.to ?? "fixed"}</span>
            </span>
          )}
          {item.advisories.map((id) => (
            <a key={id} href={`https://github.com/advisories/${id}`} target="_blank" rel="noreferrer" className="text-fg-subtle underline decoration-line-strong underline-offset-4 hover:text-fg">
              {id}
            </a>
          ))}
        </p>
        {item.reason && item.status === "failed" && <p className="mt-1 text-[0.82rem] text-fg-muted">{item.reason}</p>}
      </div>
      <div className="flex items-center gap-2 md:justify-end">
        {item.pr_url && (
          <a href={item.pr_url} target="_blank" rel="noreferrer" className="inline-flex h-8 items-center gap-1.5 rounded-full border border-line-strong px-3 text-[0.8rem] text-fg transition-colors duration-150 hover:border-accent/50 hover:text-accent">
            <GitPullRequest weight="bold" className="size-3.5" aria-hidden />
            PR {item.pr_number !== null ? `#${item.pr_number}` : ""}
            <ArrowSquareOut weight="bold" className="size-3.5" aria-hidden />
          </a>
        )}
        {actionable && (
          <button type="button" disabled={!canAct} onClick={onFix} className="inline-flex h-8 items-center gap-1.5 rounded-full border border-accent/45 bg-accent/[0.07] px-3 text-[0.8rem] font-medium text-accent transition-[background-color,transform] duration-150 ease-out hover:bg-accent/15 active:scale-[0.97] disabled:cursor-not-allowed disabled:opacity-40">
            {pending === `fix:${item.package}` ? <CircleNotch weight="bold" className="size-3.5 animate-spin motion-reduce:animate-none" aria-hidden /> : <Play weight="fill" className="size-3.5" aria-hidden />}
            {item.status === "failed" ? "Try again" : "Fix this"}
          </button>
        )}
        {item.status === "queued" && (
          <button type="button" disabled={!canAct} onClick={onSkip} className="inline-flex h-8 items-center gap-1.5 rounded-full px-2.5 text-[0.8rem] text-fg-muted transition-colors duration-150 hover:text-fg disabled:cursor-not-allowed disabled:opacity-40">
            <SkipForward weight="bold" className="size-3.5" aria-hidden />
            Skip
          </button>
        )}
      </div>
    </li>
  );
}

/** One fix run: its own step rail, terminal, proof, PR, receipt and flight record, from its own event stream. */
function FixRunSection({ item, repo, mode, active, onOpenRun, onAuditReport }: { item: CampaignItem; repo: string; mode: Mode; active: boolean; onOpenRun: (runId: string) => void; onAuditReport: (auditorRunId: string) => void }) {
  const [open, setOpen] = useState(active);
  const [seen, setSeen] = useState(active);
  const [wasActive, setWasActive] = useState(active);
  // A fix that starts while this section is on screen opens itself; finished ones stay as the user left them.
  if (active !== wasActive) {
    setWasActive(active);
    if (active) {
      setOpen(true);
      setSeen(true);
    }
  }
  const status = STATUS[item.status];
  return (
    <section className="rounded-[var(--radius-card)] border border-line bg-panel/60" aria-label={`Fix for ${item.package}`}>
      <button
        type="button"
        onClick={() => {
          setOpen((o) => !o);
          setSeen(true);
        }}
        aria-expanded={open}
        className="flex w-full items-center gap-3 px-4 py-3 text-left"
      >
        <span className="min-w-0 flex-1 truncate text-[0.9rem] text-fg">
          Fix <span className="font-mono">{item.package}</span>
        </span>
        <span className={clsx("flex shrink-0 items-center gap-2 text-[0.78rem] font-medium", status.text)}>
          <span aria-hidden className={clsx("size-2 rounded-full", status.dot)} />
          {status.label}
        </span>
        <CaretDown weight="bold" className={clsx("size-4 shrink-0 text-fg-subtle transition-transform duration-200 ease-[var(--ease-out)]", !open && "-rotate-90")} aria-hidden />
      </button>
      {/* Accordion: height is the one tolerated layout animation. Grid rows keep it off JS; 240ms ease out. */}
      <div className={clsx("grid transition-[grid-template-rows] duration-[240ms] ease-[var(--ease-out)] motion-reduce:transition-none", open ? "grid-rows-[1fr]" : "grid-rows-[0fr]")}>
        <div className="min-h-0 overflow-hidden">{seen && item.run_id && <FixRunBody runId={item.run_id} repo={repo} mode={mode} status={item.status} onOpenRun={onOpenRun} onAuditReport={onAuditReport} />}</div>
      </div>
    </section>
  );
}

function FixRunBody({ runId, repo, mode, status, onOpenRun, onAuditReport }: { runId: string; repo: string; mode: Mode; status: ItemStatus; onOpenRun: (runId: string) => void; onAuditReport: (auditorRunId: string) => void }) {
  const run = useRunStream(runId, "fixer", repo, true, mode, "fix");
  if (!run) return <p className="border-t border-line px-4 py-3 text-[0.85rem] text-fg-subtle">Connecting to the fix run</p>;
  const lastId = run.blocks[run.blocks.length - 1]?.id;
  return (
    <div className="flex flex-col gap-4 border-t border-line px-4 py-4">
      <StepRail steps={run.steps} />
      {run.blocks.length === 0 && run.status.working && <ThinkingPill />}
      {run.blocks.map((block) => (
        <BlockView key={block.id} block={block} live={run.status.working && block.id === lastId} />
      ))}
      <ProofPanel variant="run" run={run} />
      {status === "awaiting_approval" && (
        <button type="button" onClick={() => onOpenRun(runId)} className="inline-flex h-10 w-fit items-center gap-2 rounded-full bg-accent px-4 text-[0.88rem] font-semibold text-canvas transition-[filter] duration-150 hover:brightness-110">
          Review and approve the merge
        </button>
      )}
      {run.finished && (
        <div className="flex flex-col gap-4">
          <ReceiptSlot runId={runId} finalStatus="done" />
          <AuditSlot runId={runId} finalStatus="done" onReport={onAuditReport} />
        </div>
      )}
    </div>
  );
}

/** After each PR (or resolved approval): what opened, what is next, and a moment to stop before auto mode moves on. */
function NextPopup({ campaign, pending, onAct }: { campaign: Campaign; pending: string | null; onAct: (key: string, action: () => Promise<unknown>) => void }) {
  const reduce = useReducedMotion();
  const statuses = useRef<Map<string, ItemStatus> | null>(null);
  const [opened, setOpened] = useState<CampaignItem | null>(null);
  const [countdown, setCountdown] = useState<number | null>(null);

  // Only a PR seen opening in this session raises the card; a campaign reopened later just shows its list.
  useEffect(() => {
    const previous = statuses.current;
    statuses.current = new Map(campaign.items.map((i) => [i.package, i.status]));
    if (!previous) return;
    const justOpened = campaign.items.find((i) => DONE.has(i.status) && previous.get(i.package) !== undefined && !DONE.has(previous.get(i.package)!));
    if (justOpened) setOpened(justOpened);
  }, [campaign.items]);

  // A new fix starting, or the campaign being stopped, retires the card.
  const nextStarted = opened !== null && campaign.active !== null && campaign.active.package !== opened.package;
  const visible = opened !== null && !nextStarted;

  // The countdown mirrors the backend's own timer (next_start_in), ticking locally between polls.
  useEffect(() => {
    if (!visible || campaign.next_start_in === null) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- the backend timer went away
      setCountdown(null);
      return;
    }
    const ends = Date.now() + campaign.next_start_in * 1000;
    const tick = () => setCountdown(Math.max(0, Math.ceil((ends - Date.now()) / 1000)));
    tick();
    const timer = setInterval(tick, 250);
    return () => clearInterval(timer);
  }, [visible, campaign.next_start_in]);

  const next = campaign.next;
  return (
    <AnimatePresence>
      {visible && opened && (
        <motion.div
          key={opened.package}
          role="status"
          aria-live="polite"
          // Enters and leaves the same way: 220ms strong ease out, an 8px lift. Opacity only under reduced motion.
          initial={reduce ? { opacity: 0 } : { opacity: 0, transform: "translateY(8px)" }}
          animate={{ opacity: 1, transform: "translateY(0px)" }}
          exit={reduce ? { opacity: 0 } : { opacity: 0, transform: "translateY(8px)" }}
          transition={ENTER}
          className="fixed bottom-36 left-1/2 z-30 w-[min(92vw,460px)] -translate-x-1/2 rounded-[var(--radius-card)] border border-accent/35 bg-raised p-4 shadow-[0_24px_60px_-20px_rgb(0_0_0/0.9)]"
        >
          <p className="flex items-start gap-2 text-[0.92rem] text-fg">
            <GitPullRequest weight="bold" className="mt-0.5 size-4 shrink-0 text-accent" aria-hidden />
            <span>
              {opened.status === "merged" ? "Merged" : "PR"} {opened.pr_number !== null ? `#${opened.pr_number}` : ""} {opened.status === "merged" ? "for" : "opened for"} <span className="font-mono">{opened.package}</span>.{" "}
              {next ? (
                <>
                  Next: <span className="font-mono">{next.package}</span>
                  {next.severity ? ` (${next.severity})` : ""}
                </>
              ) : (
                "That was the last one."
              )}
            </span>
          </p>
          {countdown !== null && next && (
            <p className="mt-2 text-[0.82rem] text-fg-muted">
              Starting <span className="font-mono">{next.package}</span> in <span className="font-mono tabular-nums">{countdown}</span>s
            </p>
          )}
          <div className="mt-3 flex items-center gap-2">
            {next && countdown === null && (
              <button
                type="button"
                disabled={pending !== null}
                onClick={() => {
                  setOpened(null);
                  onAct("next", () => campaignApi.next(campaign.id));
                }}
                className="inline-flex h-9 items-center gap-1.5 rounded-full bg-accent px-4 text-[0.85rem] font-semibold text-canvas transition-[opacity,transform] duration-150 ease-out active:scale-[0.97] disabled:opacity-40"
              >
                <Play weight="fill" className="size-4" aria-hidden />
                Fix next
              </button>
            )}
            <button
              type="button"
              onClick={() => {
                setOpened(null);
                if (campaign.auto) onAct("stop", () => campaignApi.stop(campaign.id));
              }}
              className="inline-flex h-9 items-center gap-1.5 rounded-full px-3 text-[0.85rem] text-fg-muted transition-colors duration-150 hover:text-fg"
            >
              <Stop weight="fill" className="size-3.5" aria-hidden />
              Stop here
            </button>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
