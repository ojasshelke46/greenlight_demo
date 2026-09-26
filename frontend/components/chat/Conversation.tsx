"use client";

import { ArrowsClockwise, Binoculars, GithubLogo, ListChecks } from "@phosphor-icons/react";
import clsx from "clsx";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { Fragment, useState, type ReactNode } from "react";
import { ProofPanel } from "@/components/features/proof/ProofPanel";
import { Orb } from "@/components/Orb";
import { AuditSlot } from "@/components/run/AuditSlot";
import { ReceiptSlot } from "@/components/run/ReceiptSlot";
import { MODE_LABEL, type Block, type ChildRun, type PendingApproval, type RunState, type Tone } from "@/lib/state";
import { useChildren } from "@/lib/use-children";
import { AgentMessage } from "./AgentText";
import { ApprovalCard, CompletionCard, FailureCard, NotAllowedCard, PausedCard, QuestionCard, ReceiptCard, RejectedCard } from "./Approval";
import { ActionRow, DiffBlock, PrCard, StepRail, TerminalBlock, VulnBlock } from "./Blocks";
import { HandoffBlock } from "./Handoff";
import { ScoutBoard } from "./ScoutBoard";
import { ThinkingPill } from "./Thought";

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
  // Opens the ledger drawer on the auditor's run once "Generate audit report" started it.
  onAuditReport: (auditorRunId: string) => void;
  // Starts a Fix run on a repo from the scout's board, after the access check.
  onFixRepo: (repo: string) => void;
};

type TimelineItem = { key: string; at: number; block: Block | null; child: ChildRun | null };

/** Agent blocks and helper handoffs in time order. A block without a time keeps its place in the stream. */
function timeline(blocks: Block[], children: ChildRun[]): TimelineItem[] {
  const items: TimelineItem[] = [];
  let last = 0;
  for (const block of blocks) {
    last = block.at ?? last;
    items.push({ key: block.id, at: last, block, child: null });
  }
  // The receipt agent's words belong under the receipt line, not in the message.
  for (const child of children) {
    if (child.role === "receipt") continue;
    const at = Date.parse(child.createdAt);
    items.push({ key: `child:${child.runId}`, at: Number.isFinite(at) ? at : Number.MAX_SAFE_INTEGER, block: null, child });
  }
  // Stable: blocks keep their stream order, handoffs slot in after everything that happened before them.
  return items.map((item, index) => ({ item, index })).sort((a, b) => a.item.at - b.item.at || a.index - b.index).map(({ item }) => item);
}

/** "org:acme" is the GitHub org acme; "repos:a/b,c/d" is that list of repos. */
function scoutTarget(target: string): string {
  const colon = target.indexOf(":");
  const kind = target.slice(0, colon);
  const rest = target.slice(colon + 1);
  if (kind === "org" || kind === "user") return `every repo of the GitHub ${kind} ${rest}`;
  if (kind === "repos") return rest.split(",").join(", ");
  return target;
}

function RequestBubble({ run }: { run: RunState }) {
  if (run.role === "scout") {
    return (
      <div className="flex max-w-[85%] items-center gap-3 rounded-full border border-line bg-card py-2 pl-4 pr-2">
        <Binoculars weight="bold" className="size-5 shrink-0 text-fg-muted" aria-hidden />
        <span className="truncate font-mono text-[0.88rem] text-fg">{scoutTarget(run.repo)}</span>
        <span className="shrink-0 rounded-full bg-raised px-3 py-1 text-[0.78rem] font-medium text-fg-muted">Scout</span>
      </div>
    );
  }
  const policy = run.role === "policy";
  return (
    <div className="flex max-w-[85%] items-center gap-3 rounded-full border border-line bg-card py-2 pl-4 pr-2">
      {policy ? <ListChecks weight="bold" className="size-5 shrink-0 text-fg-muted" aria-hidden /> : <GithubLogo weight="fill" className="size-5 shrink-0 text-fg-muted" aria-hidden />}
      <span className="truncate font-mono text-[0.88rem] text-fg">https://github.com/{run.repo}</span>
      <span className={clsx("shrink-0 rounded-full px-3 py-1 text-[0.78rem] font-medium", !policy && run.mode === "ship" ? "bg-accent/12 text-accent" : "bg-raised text-fg-muted")}>
        {policy ? "Draft a policy" : MODE_LABEL[run.mode]}
      </span>
    </div>
  );
}

export function Conversation({ run, flare, retrying, resuming, controlError, onDecide, onRetry, onResume, onAuditReport, onFixRepo }: Props) {
  const reduce = useReducedMotion();
  // Blocks that already existed on first render (a replayed run) appear without an entrance.
  const [initialIds] = useState(() => new Set(run.blocks.map((b) => b.id)));

  const canShip = run.mode === "ship" && !run.viaFork;
  const approval = run.approval;
  const pending = approval.status === "pending" || approval.status === "sending" || approval.status === "refused" ? approval.pending : null;
  const awaiting = pending !== null && canShip && pending.tool === "merge_pull_request";
  const decided = approval.status === "decided" ? approval : null;
  const children = useChildren(run.id, !run.finished);
  const items = timeline(run.blocks, children);
  const lastBlockId = run.blocks[run.blocks.length - 1]?.id;
  const receiptHelper = children.filter((c) => c.role === "receipt").pop() ?? null;

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
        <RequestBubble run={run} />
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

            {items.map((item) => (
              <motion.div key={item.key} initial={enter(item.key)} animate={{ opacity: 1, transform: "translateY(0px)" }} transition={ENTER}>
                {item.block ? (
                  <BlockView block={item.block} live={run.status.working && item.block.id === lastBlockId} />
                ) : (
                  item.child && <HandoffBlock child={item.child} repo={run.repo} />
                )}
              </motion.div>
            ))}

            {run.blocks.length === 0 && run.status.working && <ThinkingPill />}

            {run.role === "scout" && <ScoutBoard runId={run.id} finished={run.finished} onFix={onFixRepo} />}

            <ProofPanel variant="run" run={run} />
          </motion.div>

          <Outcome run={run} awaiting={awaiting} decided={decided} pending={pending} canShip={canShip} retrying={retrying} resuming={resuming} controlError={controlError} onDecide={onDecide} onRetry={onRetry} onResume={onResume} enter={enter} receiptHelper={receiptHelper} onAuditReport={onAuditReport} />
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
  receiptHelper,
  onAuditReport,
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
  receiptHelper: ChildRun | null;
  onAuditReport: (auditorRunId: string) => void;
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
  if (decided?.decision === "approve" && run.merged) {
    const conclusion = run.release?.conclusion ?? null;
    const finalStatus = conclusion === null ? null : conclusion === "success" ? "shipped" : `release_${conclusion}`;
    out.push(
      card(
        "receipt",
        <div className="flex flex-col gap-4">
          <ReceiptCard release={run.release} approval={run.approval} pr={run.pullRequest} />
          <ReceiptSlot runId={run.id} finalStatus={finalStatus} breakdown={receiptHelper} />
          <AuditSlot runId={run.id} finalStatus={finalStatus} onReport={onAuditReport} />
        </div>,
      ),
    );
  }
  if (!canShip && run.finished && run.pullRequest && !run.failure) {
    out.push(
      card(
        "completion",
        <div className="flex flex-col gap-4">
          <CompletionCard pr={run.pullRequest} />
          <ReceiptSlot runId={run.id} finalStatus="handed_off" breakdown={receiptHelper} />
          <AuditSlot runId={run.id} finalStatus="handed_off" onReport={onAuditReport} />
        </div>,
      ),
    );
  }
  if (run.question) out.push(card("question", <QuestionCard question={run.question} onRetry={onRetry} retrying={retrying} />));
  if (run.paused) {
    const step = run.steps.find((s) => s.status === "paused")?.label ?? null;
    out.push(card("paused", <PausedCard step={step} onResume={onResume} resuming={resuming} onRetry={onRetry} retrying={retrying} error={controlError} />));
  }
  if (run.failure && !decided) {
    out.push(card("failure", <FailureCard reason={run.failure.reason} onRetry={onRetry} retrying={retrying} onResume={run.canResume ? onResume : undefined} resuming={resuming} />));
  }

  // Every other run that has ended (a helper agent's report, a fix that stopped short of a PR, a run that
  // failed) still has a receipt and a flight record, shown under its outcome card. Not while paused or asking.
  const ended = run.finished && !run.paused && !run.question && !pending && !decided;
  if (ended && !(!canShip && run.pullRequest && !run.failure)) {
    out.push(
      card(
        "done",
        <div className="flex flex-col gap-4">
          <ReceiptSlot runId={run.id} finalStatus="done" breakdown={receiptHelper} />
          <AuditSlot runId={run.id} finalStatus="done" onReport={onAuditReport} />
        </div>,
      ),
    );
  }
  return <>{out.map((node, i) => <Fragment key={i}>{node}</Fragment>)}</>;
}

function BlockView({ block, live }: { block: Block; live: boolean }) {
  switch (block.kind) {
    case "text":
      return <AgentMessage text={block.text} live={live} />;
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
