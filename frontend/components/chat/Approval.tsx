"use client";

import { ArrowClockwise, ArrowSquareOut, ChatCircleDots, PauseCircle, Play, CheckCircle, CircleNotch, FileText, GitMerge, GitPullRequest, Prohibit, ShieldCheck, Warning, XCircle } from "@phosphor-icons/react";
import clsx from "clsx";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { PolicyPanel } from "@/components/features/policy/PolicyPanel";
import { ProofPanel } from "@/components/features/proof/ProofPanel";
import type { AgentQuestion, ApprovalContext, ApprovalState, PendingApproval, PullRequest, Release, RunState } from "@/lib/state";
import { CopyButton } from "./Blocks";

const HOLD_MS = 2000;

/**
 * Press and hold for two seconds. The fill is progress, so it runs linear; letting go rewinds
 * with the strong ease out over 350ms (see .hold-fill in globals.css). Space and Enter hold too.
 */
function HoldToApprove({ disabled, busy, onComplete }: { disabled: boolean; busy: boolean; onComplete: () => void }) {
  const [holding, setHolding] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const stop = useCallback(() => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
    setHolding(false);
  }, []);

  const start = useCallback(() => {
    if (disabled || timer.current) return;
    setHolding(true);
    timer.current = setTimeout(() => {
      timer.current = null;
      setHolding(false);
      onComplete();
    }, HOLD_MS);
  }, [disabled, onComplete]);

  useEffect(() => stop, [stop]);

  const label = busy ? "Checking on the server" : "Hold to approve";

  return (
    <button
      type="button"
      data-holding={holding}
      aria-disabled={disabled}
      aria-label={busy ? "Checking the approval on the server" : "Press and hold for two seconds to approve the merge"}
      onPointerDown={(event) => {
        if (event.button !== 0) return;
        event.currentTarget.setPointerCapture(event.pointerId);
        start();
      }}
      onPointerUp={stop}
      onPointerCancel={stop}
      onLostPointerCapture={stop}
      onKeyDown={(event) => {
        if ((event.key === " " || event.key === "Enter") && !event.repeat) {
          event.preventDefault();
          start();
        }
      }}
      onKeyUp={(event) => {
        if (event.key === " " || event.key === "Enter") stop();
      }}
      onBlur={stop}
      onContextMenu={(event) => event.preventDefault()}
      className={clsx(
        "relative h-14 min-w-72 touch-none select-none overflow-hidden rounded-full border border-accent/45 bg-accent/[0.07] transition-transform duration-150 ease-out",
        disabled && "cursor-not-allowed opacity-60",
      )}
    >
      <span className="relative z-0 flex h-full items-center justify-center gap-2.5 px-8 font-display text-[1rem] font-semibold text-accent">
        {busy ? <CircleNotch weight="bold" className="size-5 animate-spin motion-reduce:animate-none" aria-hidden /> : <GitMerge weight="bold" className="size-5" aria-hidden />}
        {label}
      </span>
      <span aria-hidden className="hold-fill absolute inset-0 z-10 flex items-center justify-center gap-2.5 bg-accent px-8 font-display text-[1rem] font-semibold text-canvas">
        <GitMerge weight="bold" className="size-5" />
        {label}
      </span>
    </button>
  );
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-1.5">
      <dt className="text-[0.78rem] font-medium text-fg-subtle">{label}</dt>
      <dd className="text-[0.9rem] leading-snug text-fg">{children}</dd>
    </div>
  );
}

export function ApprovalCard({
  run,
  pending,
  context,
  approval,
  onDecide,
}: {
  run: RunState;
  pending: PendingApproval;
  context: ApprovalContext | null;
  approval: ApprovalState;
  onDecide: (decision: "approve" | "reject") => void;
}) {
  const branch = run.defaultBranch ?? "the default branch";
  const sending = approval.status === "sending";
  const refused = approval.status === "refused" ? approval.reason : null;

  return (
    <section
      aria-labelledby="approval-title"
      className="relative overflow-hidden rounded-[var(--radius-panel)] border border-accent/30 bg-panel p-6 shadow-[0_0_80px_-20px_color-mix(in_oklab,var(--accent)_45%,transparent)]"
    >
      <div aria-hidden className="pointer-events-none absolute -right-24 -top-24 size-72 rounded-full bg-[radial-gradient(circle,color-mix(in_oklab,var(--accent)_22%,transparent),transparent_65%)]" />
      <div className="relative">
        <p className="flex items-center gap-2 text-[0.82rem] font-medium text-accent">
          <ShieldCheck weight="bold" className="size-4" aria-hidden />
          Your approval is needed
        </p>
        <h2 id="approval-title" className="mt-2 font-display text-[1.5rem] font-semibold leading-tight tracking-tight text-fg">
          Greenlight wants to merge{" "}
          {pending.pullUrl ? (
            <a href={pending.pullUrl} target="_blank" rel="noreferrer" className="text-accent underline decoration-accent/40 underline-offset-4">
              PR #{pending.pullNumber}
            </a>
          ) : (
            <span className="text-accent">PR #{pending.pullNumber ?? "?"}</span>
          )}{" "}
          into <span className="font-mono text-[0.9em]">{branch}</span>
        </h2>

        <dl className="mt-5 grid gap-5 sm:grid-cols-2">
          <Fact label="Files changed">
            {context && context.filesChanged.length > 0 ? (
              <span className="flex flex-wrap gap-1.5">
                {context.filesChanged.map((file) => (
                  <span key={file} className="inline-flex items-center gap-1 rounded-full border border-line px-2 py-0.5 font-mono text-[0.78rem] text-fg-muted">
                    <FileText weight="bold" className="size-3.5" aria-hidden />
                    {file}
                  </span>
                ))}
              </span>
            ) : (
              <span className="text-fg-muted">See the pull request</span>
            )}
          </Fact>
          <Fact label="Tests">{context?.testSummary ? <span className="text-accent">{context.testSummary}</span> : <span className="text-fg-muted">No test run reported</span>}</Fact>
          <Fact label="What the merge triggers">
            Merging into <span className="font-mono">{branch}</span> runs the GitHub Actions workflows on {branch}, including any release or publish step. This is the one step that cannot be undone by closing the PR.
          </Fact>
          <Fact label={context?.rollbackFromAgent ? "Rollback, from the agent" : "Rollback"}>
            <span className="flex items-start gap-1 rounded-xl border border-line bg-[#070707] py-1.5 pl-3 pr-1">
              <code className="min-w-0 flex-1 whitespace-pre-wrap break-words py-1 font-mono text-[0.78rem] text-fg">{context?.rollback ?? "git revert -m 1 <merge commit>"}</code>
              <CopyButton text={context?.rollback ?? ""} label="Copy rollback command" />
            </span>
          </Fact>
        </dl>

        {refused && (
          <p role="alert" className="mt-5 flex items-start gap-2 rounded-xl border border-fail/30 bg-fail/10 px-4 py-3 text-[0.9rem] text-fail">
            <Warning weight="bold" className="mt-0.5 size-4 shrink-0" aria-hidden />
            {refused}
          </p>
        )}

        <ProofPanel variant="approval" run={run} />
        <PolicyPanel variant="approval" run={run} />

        <div className="mt-6 flex flex-wrap items-center gap-5">
          <HoldToApprove disabled={sending} busy={sending && approval.decision === "approve"} onComplete={() => onDecide("approve")} />
          <button
            type="button"
            disabled={sending}
            onClick={() => onDecide("reject")}
            className="rounded-full px-3 py-2 text-[0.9rem] text-fg-muted transition-colors duration-150 hover:text-fail disabled:cursor-not-allowed disabled:opacity-50"
          >
            {sending && approval.decision === "reject" ? "Rejecting" : "Reject"}
          </button>
        </div>
        <p className="mt-4 text-[0.78rem] text-fg-subtle">The backend revalidates every approval before anything merges.</p>
      </div>
    </section>
  );
}

/** A PR only or fork run never merges. If the agent still asks, the only answer is no. */
export function NotAllowedCard({ pending, sending, onReject }: { pending: PendingApproval; sending: boolean; onReject: () => void }) {
  return (
    <section className="rounded-[var(--radius-card)] border border-fail/30 bg-fail/[0.06] p-5">
      <p className="flex items-center gap-2 text-[0.95rem] font-semibold text-fail">
        <Prohibit weight="bold" className="size-5" aria-hidden />
        The agent asked to run <span className="font-mono">{pending.tool}</span>
      </p>
      <p className="mt-2 text-[0.88rem] leading-relaxed text-fg-muted">This run can only open a pull request, so this action is never allowed. Reject it so the agent can finish.</p>
      <button
        type="button"
        onClick={onReject}
        disabled={sending}
        className="mt-4 inline-flex h-10 items-center gap-2 rounded-full border border-fail/40 px-4 text-[0.88rem] font-medium text-fail transition-colors duration-150 hover:bg-fail/10 disabled:opacity-50"
      >
        {sending ? <CircleNotch weight="bold" className="size-4 animate-spin" aria-hidden /> : <XCircle weight="bold" className="size-4" aria-hidden />}
        Reject
      </button>
    </section>
  );
}

function when(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export function ReceiptCard({ release, approval, pr }: { release: Release | null; approval: ApprovalState; pr: PullRequest | null }) {
  const decided = approval.status === "decided" ? approval : null;
  const done = release?.conclusion === "success";
  const failed = Boolean(release?.conclusion) && !done;
  return (
    <section className={clsx("rounded-[var(--radius-panel)] border bg-panel p-6", failed ? "border-fail/30" : "border-accent/30")}>
      <p className={clsx("flex items-center gap-2 font-display text-[1.25rem] font-semibold", failed ? "text-fail" : "text-accent")}>
        {failed ? <XCircle weight="fill" className="size-6" aria-hidden /> : <CheckCircle weight="fill" className="size-6" aria-hidden />}
        {done ? "Merged and released" : failed ? `Merged, but the release ${release?.conclusion}` : "Merged"}
      </p>
      <dl className="mt-5 grid gap-5 sm:grid-cols-2">
        <Fact label="Merge commit">
          {release?.mergeCommitSha ? (
            <span className="flex items-center gap-1 font-mono text-[0.85rem]">
              {release.mergeCommitSha.slice(0, 12)}
              <CopyButton text={release.mergeCommitSha} label="Copy commit sha" />
            </span>
          ) : (
            <span className="text-fg-muted">Waiting for GitHub</span>
          )}
        </Fact>
        <Fact label="Release">
          {release?.url ? (
            <a href={release.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1.5 underline decoration-line-strong underline-offset-4 hover:text-accent">
              {release.conclusion ?? release.status?.replace("_", " ") ?? "Actions run"}
              <ArrowSquareOut weight="bold" className="size-4" aria-hidden />
            </a>
          ) : (
            <span className="flex items-center gap-2 text-progress">
              <CircleNotch weight="bold" className="size-4 animate-spin motion-reduce:animate-none" aria-hidden />
              Waiting for the Actions run
            </span>
          )}
        </Fact>
        <Fact label="Approved by">{decided ? decided.approver : "Unknown"}</Fact>
        <Fact label="Approved at">{decided?.decidedAt ? when(decided.decidedAt) : "Unknown"}</Fact>
        {pr?.url && (
          <Fact label="Pull request">
            <a href={pr.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1.5 underline decoration-line-strong underline-offset-4 hover:text-accent">
              #{pr.number}
              <ArrowSquareOut weight="bold" className="size-4" aria-hidden />
            </a>
          </Fact>
        )}
      </dl>
    </section>
  );
}

export function CompletionCard({ pr }: { pr: PullRequest }) {
  return (
    <section className="flex flex-col items-start gap-4 rounded-[var(--radius-panel)] border border-line bg-panel p-5 sm:flex-row sm:items-center">
      <span className="grid size-11 shrink-0 place-items-center rounded-full bg-accent/12 text-accent">
        <GitPullRequest weight="bold" className="size-6" aria-hidden />
      </span>
      <div className="min-w-0 flex-1">
        <p className="font-display text-[1.1rem] font-semibold text-fg">PR opened for the owner to review</p>
        <p className="mt-0.5 text-[0.85rem] text-fg-muted">Greenlight stops here. The repo owner decides whether to merge.</p>
      </div>
      {pr.url && (
        <a href={pr.url} target="_blank" rel="noreferrer" className="inline-flex h-10 shrink-0 items-center gap-1.5 rounded-full bg-accent px-4 text-[0.88rem] font-semibold text-canvas transition-[filter] duration-150 hover:brightness-110">
          Open PR #{pr.number}
          <ArrowSquareOut weight="bold" className="size-4" aria-hidden />
        </a>
      )}
    </section>
  );
}

export function RejectedCard({ approver }: { approver: string }) {
  return (
    <section className="flex items-center gap-3 rounded-[var(--radius-card)] border border-line bg-panel px-5 py-4 text-[0.92rem]">
      <XCircle weight="fill" className="size-5 shrink-0 text-fail" aria-hidden />
      <span className="text-fg">
        Merge rejected by {approver}. <span className="text-fg-muted">The pull request stays open.</span>
      </span>
    </section>
  );
}

function ResumeButton({ onResume, resuming }: { onResume: () => void; resuming: boolean }) {
  return (
    <button
      type="button"
      onClick={onResume}
      disabled={resuming}
      className="inline-flex h-10 items-center gap-2 rounded-full bg-accent px-4 text-[0.88rem] font-semibold text-canvas transition-[filter,transform] duration-150 ease-out hover:brightness-110 active:scale-[0.97] disabled:opacity-60"
    >
      {resuming ? <CircleNotch weight="bold" className="size-4 animate-spin" aria-hidden /> : <Play weight="fill" className="size-4" aria-hidden />}
      Resume
    </button>
  );
}

export function PausedCard({ step, onResume, resuming, onRetry, retrying, error }: { step: string | null; onResume: () => void; resuming: boolean; onRetry: () => void; retrying: boolean; error: string | null }) {
  return (
    <section className="rounded-[var(--radius-panel)] border border-progress/30 bg-panel p-5">
      <p className="flex items-center gap-2 text-[0.98rem] font-semibold text-progress">
        <PauseCircle weight="fill" className="size-5" aria-hidden />
        Paused{step ? ` at ${step}` : ""}
      </p>
      <p className="mt-1.5 text-[0.88rem] leading-relaxed text-fg-muted">Resume continues in the same session, with everything the agent has done so far.</p>
      {error && <p role="alert" className="mt-3 text-[0.85rem] text-fail">{error}</p>}
      <div className="mt-4 flex flex-wrap items-center gap-3">
        <ResumeButton onResume={onResume} resuming={resuming} />
        <button type="button" onClick={onRetry} disabled={retrying} className="rounded-full px-3 py-2 text-[0.88rem] text-fg-muted transition-colors duration-150 hover:text-fg disabled:opacity-50">
          Start over
        </button>
      </div>
    </section>
  );
}

export function FailureCard({ reason, onRetry, retrying, onResume, resuming }: { reason: string; onRetry: () => void; retrying: boolean; onResume?: () => void; resuming?: boolean }) {
  return (
    <section className="rounded-[var(--radius-card)] border border-fail/30 bg-fail/[0.06] p-5">
      <p className="flex items-center gap-2 text-[0.98rem] font-semibold text-fail">
        <Warning weight="bold" className="size-5" aria-hidden />
        {reason}
      </p>
      <div className="mt-4 flex flex-wrap items-center gap-3">
        {onResume && <ResumeButton onResume={onResume} resuming={resuming ?? false} />}
        <button
          type="button"
          onClick={onRetry}
          disabled={retrying}
          className="inline-flex h-10 items-center gap-2 rounded-full border border-line-strong px-4 text-[0.88rem] font-medium text-fg transition-colors duration-150 hover:border-accent/50 hover:text-accent disabled:opacity-50"
        >
          {retrying ? <CircleNotch weight="bold" className="size-4 animate-spin" aria-hidden /> : <ArrowClockwise weight="bold" className="size-4" aria-hidden />}
          Try again
        </button>
      </div>
    </section>
  );
}

/** The agent paused on ask_user_question. Greenlight shows it honestly; answering happens in TrueForge. */
export function QuestionCard({ question, onRetry, retrying }: { question: AgentQuestion; onRetry: () => void; retrying: boolean }) {
  return (
    <section className="rounded-[var(--radius-panel)] border border-progress/30 bg-panel p-5">
      <p className="flex items-center gap-2 text-[0.85rem] font-medium text-progress">
        <ChatCircleDots weight="bold" className="size-4" aria-hidden />
        The agent is waiting for your answer
      </p>
      <p className="mt-2 text-[0.98rem] leading-relaxed text-fg">{question.text}</p>
      {question.options.length > 0 && (
        <ol className="mt-3 flex flex-col gap-1.5">
          {question.options.map((option, i) => (
            <li key={i} className="rounded-xl border border-line bg-card px-3.5 py-2 text-[0.88rem] text-fg-muted">
              {option}
            </li>
          ))}
        </ol>
      )}
      <p className="mt-4 text-[0.82rem] text-fg-subtle">Answering from Greenlight is not supported yet. Answer it in the TrueForge session, or start the run again.</p>
      <button
        type="button"
        onClick={onRetry}
        disabled={retrying}
        className="mt-3 inline-flex h-10 items-center gap-2 rounded-full border border-line-strong px-4 text-[0.88rem] font-medium text-fg transition-colors duration-150 hover:border-accent/50 hover:text-accent disabled:opacity-50"
      >
        {retrying ? <CircleNotch weight="bold" className="size-4 animate-spin" aria-hidden /> : <ArrowClockwise weight="bold" className="size-4" aria-hidden />}
        Try again
      </button>
    </section>
  );
}
