"use client";

import { ArrowsClockwise, GithubLogo } from "@phosphor-icons/react";
import clsx from "clsx";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { Fragment, useState, type ReactNode } from "react";
import { ProofPanel } from "@/components/features/proof/ProofPanel";
import { Orb } from "@/components/Orb";
import { MODE_LABEL, type Block, type PendingApproval, type RunState, type Tone } from "@/lib/state";
import { ApprovalCard, CompletionCard, FailureCard, NotAllowedCard, PausedCard, QuestionCard, ReceiptCard, RejectedCard } from "./Approval";
import { ActionRow, DiffBlock, PrCard, StepRail, TerminalBlock, VulnBlock } from "./Blocks";

const TONE_TEXT: Record<Tone, string> = { fail: "text-fail", progress: "text-progress", done: "text-accent" };

// Inline blocks enter as their event lands: occasional, 220ms strong ease out, a short lift.
const ENTER = { duration: 0.22, ease: [0.23, 1, 0.32, 1] } as const;

type Props = {
  run: RunState;
  flare: boolean;
  retrying: boolean;
  resuming: boolean;
  controlError: string | null;
  onDecide: (decision: "approve" | "reject") => void;
  onRetry: () => void;
  onResume: () => void;
};

export function Conversation({ run, flare, retrying, resuming, controlError, onDecide, onRetry, onResume }: Props) {
  const reduce = useReducedMotion();
  // Blocks that already existed on first render (a replayed run) appear without an entrance.
  const [initialIds] = useState(() => new Set(run.blocks.map((b) => b.id)));

  const canShip = run.mode === "ship" && !run.viaFork;
  const approval = run.approval;
  const pending = approval.status === "pending" || approval.status === "sending" || approval.status === "refused" ? approval.pending : null;
  const awaiting = pending !== null && canShip && pending.tool === "merge_pull_request";
  const decided = approval.status === "decided" ? approval : null;

  const enter = (id: string) =>
    initialIds.has(id) || reduce ? false : { opacity: 0, transform: "translateY(6px)" };

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-7 px-5 pb-10 pt-8">
      <motion.div
        initial={reduce ? false : { opacity: 0, transform: "translateY(6px)" }}
        animate={{ opacity: awaiting ? 0.35 : 1, transform: "translateY(0px)" }}
        transition={ENTER}
        className="flex justify-end"
      >
        <div className="flex max-w-[85%] items-center gap-3 rounded-full border border-line bg-card py-2 pl-4 pr-2">
          <GithubLogo weight="fill" className="size-5 shrink-0 text-fg-muted" aria-hidden />
          <span className="truncate font-mono text-[0.88rem] text-fg">https://github.com/{run.repo}</span>
          <span className={clsx("shrink-0 rounded-full px-3 py-1 text-[0.78rem] font-medium", run.mode === "ship" ? "bg-accent/12 text-accent" : "bg-raised text-fg-muted")}>
            {MODE_LABEL[run.mode]}
          </span>
        </div>
      </motion.div>

      <AnimatePresence initial={false}>
        {run.connection === "reconnecting" && (
          <motion.p
            role="status"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.15 }}
            className="mx-auto flex items-center gap-2 rounded-full border border-progress/30 bg-progress/10 px-3.5 py-1.5 text-[0.8rem] text-progress"
          >
            <ArrowsClockwise weight="bold" className="size-4 animate-spin motion-reduce:animate-none" aria-hidden />
            Reconnecting
          </motion.p>
        )}
      </AnimatePresence>

      <div className="grid grid-cols-[28px_minmax(0,1fr)] gap-x-4">
        <div className="pt-0.5">
          <Orb size={28} layoutId="orb" working={run.status.working} flare={flare} />
        </div>

        <div className="flex min-w-0 flex-col gap-4">
          <motion.div animate={{ opacity: awaiting ? 0.35 : 1 }} transition={{ duration: 0.3, ease: [0.23, 1, 0.32, 1] }} className="flex flex-col gap-4">
            <p className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
              <span className="font-display text-[0.98rem] font-semibold text-fg">Greenlight</span>
              <span className={clsx("text-[0.85rem] font-medium", TONE_TEXT[run.status.tone])} aria-live="polite">
                {run.status.label}
              </span>
              <span className="min-w-0 truncate text-[0.85rem] text-fg-subtle">{run.status.detail}</span>
            </p>

            <StepRail steps={run.steps} />

            {run.blocks.map((block) => (
              <motion.div key={block.id} initial={enter(block.id)} animate={{ opacity: 1, transform: "translateY(0px)" }} transition={ENTER}>
                <BlockView block={block} />
              </motion.div>
            ))}

            {run.blocks.length === 0 && run.status.working && (
              <div className="flex flex-col gap-2" aria-label="Waiting for the agent">
                <div className="h-4 w-2/3 animate-pulse rounded-full bg-card motion-reduce:animate-none" />
                <div className="h-4 w-1/2 animate-pulse rounded-full bg-card motion-reduce:animate-none" />
              </div>
            )}

            <ProofPanel variant="run" run={run} />
          </motion.div>

          <Outcome run={run} awaiting={awaiting} decided={decided} pending={pending} canShip={canShip} retrying={retrying} resuming={resuming} controlError={controlError} onDecide={onDecide} onRetry={onRetry} onResume={onResume} enter={enter} />
        </div>
      </div>
    </div>
  );
}

function Outcome({
  run,
  awaiting,
  decided,
  pending,
  canShip,
  retrying,
  resuming,
  controlError,
  onDecide,
  onRetry,
  onResume,
  enter,
}: {
  run: RunState;
  awaiting: boolean;
  decided: Extract<RunState["approval"], { status: "decided" }> | null;
  pending: PendingApproval | null;
  canShip: boolean;
  retrying: boolean;
  resuming: boolean;
  controlError: string | null;
  onDecide: (decision: "approve" | "reject") => void;
  onRetry: () => void;
  onResume: () => void;
  enter: (id: string) => false | { opacity: number; transform: string };
}) {
  const card = (id: string, node: ReactNode) => (
    <motion.div key={id} initial={enter(id)} animate={{ opacity: 1, transform: "translateY(0px)" }} transition={ENTER}>
      {node}
    </motion.div>
  );

  const out: ReactNode[] = [];
  if (awaiting && pending) {
    out.push(card("approval", <ApprovalCard run={run} pending={pending} context={run.approvalContext} approval={run.approval} onDecide={onDecide} />));
  } else if (pending && !canShip) {
    out.push(card("not-allowed", <NotAllowedCard pending={pending} sending={run.approval.status === "sending"} onReject={() => onDecide("reject")} />));
  }
  if (decided?.decision === "reject") out.push(card("rejected", <RejectedCard approver={decided.approver} />));
  if (decided?.decision === "approve" && run.merged) out.push(card("receipt", <ReceiptCard release={run.release} approval={run.approval} pr={run.pullRequest} />));
  if (!canShip && run.finished && run.pullRequest && !run.failure) out.push(card("completion", <CompletionCard pr={run.pullRequest} />));
  if (run.question) out.push(card("question", <QuestionCard question={run.question} onRetry={onRetry} retrying={retrying} />));
  if (run.paused) {
    const step = run.steps.find((s) => s.status === "paused")?.label ?? null;
    out.push(card("paused", <PausedCard step={step} onResume={onResume} resuming={resuming} onRetry={onRetry} retrying={retrying} error={controlError} />));
  }
  if (run.failure && !decided) {
    out.push(card("failure", <FailureCard reason={run.failure.reason} onRetry={onRetry} retrying={retrying} onResume={run.canResume ? onResume : undefined} resuming={resuming} />));
  }

  return <>{out.map((node, i) => <Fragment key={i}>{node}</Fragment>)}</>;
}

function BlockView({ block }: { block: Block }) {
  switch (block.kind) {
    case "text":
      return <AgentText text={block.text} />;
    case "terminal":
      return <TerminalBlock commands={block.commands} />;
    case "vulns":
      return <VulnBlock items={block.items} />;
    case "diff":
      return <DiffBlock files={block.files} />;
    case "pr":
      return <PrCard pr={block.pr} />;
    case "action":
      return <ActionRow block={block} />;
  }
}

/** The agent's own words. Light formatting only: paragraphs, bullets, bold and inline code. */
function AgentText({ text }: { text: string }) {
  const paragraphs = text.split(/\n{2,}/);
  return (
    <div className="flex flex-col gap-2.5 text-[0.95rem] leading-relaxed text-fg">
      {paragraphs.map((para, i) => {
        const lines = para.split("\n");
        if (lines.every((l) => /^\s*([-*]|\d+\.)\s+/.test(l))) {
          return (
            <ul key={i} className="flex list-disc flex-col gap-1 pl-5 marker:text-fg-subtle">
              {lines.map((l, j) => (
                <li key={j}>{inline(l.replace(/^\s*([-*]|\d+\.)\s+/, ""))}</li>
              ))}
            </ul>
          );
        }
        if (/^#{1,4}\s/.test(para)) {
          return (
            <p key={i} className="font-display font-semibold text-fg">
              {inline(para.replace(/^#{1,4}\s/, ""))}
            </p>
          );
        }
        return (
          <p key={i} className="whitespace-pre-wrap text-fg/90">
            {inline(para)}
          </p>
        );
      })}
    </div>
  );
}

function inline(text: string): ReactNode[] {
  return text.split(/(`[^`]+`|\*\*[^*]+\*\*)/g).map((part, i) => {
    if (part.startsWith("`") && part.endsWith("`") && part.length > 2) {
      return (
        <code key={i} className="rounded-md bg-card px-1.5 py-0.5 font-mono text-[0.85em] text-fg">
          {part.slice(1, -1)}
        </code>
      );
    }
    if (part.startsWith("**") && part.endsWith("**") && part.length > 4) {
      return (
        <strong key={i} className="font-semibold text-fg">
          {part.slice(2, -2)}
        </strong>
      );
    }
    return part;
  });
}
