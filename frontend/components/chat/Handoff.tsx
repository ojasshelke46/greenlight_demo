"use client";

import { ArrowSquareOut, Binoculars, CaretDown, ListChecks, Notebook, Receipt as ReceiptIcon, ShieldCheck, Wrench } from "@phosphor-icons/react";
import clsx from "clsx";
import { useState } from "react";
import { useRunStream } from "@/lib/use-run-stream";
import { ROLE_LABEL, type Block, type ChildRun, type Role } from "@/lib/state";
import { AgentMessage, withoutThoughts } from "./AgentText";
import { ActionRow, StepRail, TerminalBlock } from "./Blocks";
import { ThinkingPill } from "./Thought";

export type HelperPhase = "not_started" | "running" | "waiting" | "done" | "failed";

/** Where a helper stands, from the backend's status for its run. */
export function helperPhase(status: string): HelperPhase {
  if (status === "running") return "running";
  if (status === "awaiting_approval" || status === "awaiting_input") return "waiting";
  if (status === "done") return "done";
  if (status === "error" || status === "cancelled" || status === "resume_failed") return "failed";
  return "not_started";
}

const PHASE: Record<HelperPhase, { label: string; dot: string; text: string }> = {
  not_started: { label: "Not started", dot: "border border-line-strong", text: "text-fg-subtle" },
  running: { label: "Running", dot: "dot-pulse bg-progress shadow-[0_0_10px_rgb(255_176_32/0.6)]", text: "text-progress" },
  waiting: { label: "Waiting", dot: "bg-progress", text: "text-progress" },
  done: { label: "Done", dot: "bg-accent shadow-[0_0_10px_color-mix(in_oklab,var(--accent)_55%,transparent)]", text: "text-accent" },
  failed: { label: "Stopped", dot: "bg-fail", text: "text-fail" },
};

const ROLE_ICON: Record<Role, typeof ShieldCheck> = {
  fixer: Wrench,
  prover: ShieldCheck,
  policy: ListChecks,
  scout: Binoculars,
  receipt: ReceiptIcon,
  auditor: Notebook,
};

const PR_URL = /https:\/\/github\.com\/[\w.-]+\/[\w.-]+\/pull\/(\d+)/;

/** "Prover is verifying GHSA… on PR #3", from the purpose the backend recorded when it started the helper. */
function sentence(child: ChildRun, phase: HelperPhase): string {
  const agent = ROLE_LABEL[child.role];
  const done = phase === "done";
  const purpose = child.purpose ?? "";
  if (child.role === "prover") {
    const pr = PR_URL.exec(purpose);
    const advisory = purpose.split(" ").pop() ?? "";
    const target = [advisory, pr ? `on PR #${pr[1]}` : ""].filter(Boolean).join(" ");
    return done ? `${agent} checked ${target}` : `${agent} is verifying ${target}`;
  }
  if (child.role === "auditor") return done ? `${agent} wrote the audit report` : `${agent} is writing the audit report`;
  if (child.role === "receipt") return done ? `${agent} wrote the cost breakdown` : `${agent} is writing the cost breakdown`;
  return `${agent} is ${purpose || "working"}`;
}

/** A helper agent's run inside its parent's message: what it is doing, its status, and a collapsed live view. */
export function HandoffBlock({ child, repo }: { child: ChildRun; repo: string }) {
  const [open, setOpen] = useState(false);
  // Mounted from the first open on, so closing folds the view away instead of emptying it first.
  const [seen, setSeen] = useState(false);
  const phase = helperPhase(child.status);
  const Icon = ROLE_ICON[child.role];
  const tone = PHASE[phase];

  return (
    <section className="rounded-[var(--radius-card)] border border-line bg-panel/80" aria-label={`${ROLE_LABEL[child.role]} handoff`}>
      <button type="button" onClick={() => {
          setOpen((o) => !o);
          setSeen(true);
        }}
        aria-expanded={open}
        className="flex w-full items-center gap-3 px-4 py-3 text-left">
        <span className="grid size-8 shrink-0 place-items-center rounded-full border border-line-strong bg-card text-fg-muted">
          <Icon weight="bold" className="size-4" aria-hidden />
        </span>
        <span className="min-w-0 flex-1 truncate text-[0.9rem] text-fg">{sentence(child, phase)}</span>
        <span className={clsx("flex shrink-0 items-center gap-2 text-[0.78rem] font-medium", tone.text)}>
          <span aria-hidden className={clsx("size-2 rounded-full", tone.dot)} />
          {tone.label}
        </span>
        <CaretDown weight="bold" className={clsx("size-4 shrink-0 text-fg-subtle transition-transform duration-200 ease-[var(--ease-out)]", !open && "-rotate-90")} aria-hidden />
      </button>

      {child.role === "prover" && phase !== "running" && phase !== "not_started" && <ProverResult result={child.result} failed={phase === "failed"} />}

      {/* Accordion: height is the one tolerated layout animation. Grid rows keep it off JS; 240ms ease out. */}
      <div className={clsx("grid transition-[grid-template-rows] duration-[240ms] ease-[var(--ease-out)] motion-reduce:transition-none", open ? "grid-rows-[1fr]" : "grid-rows-[0fr]")}>
        <div className="min-h-0 overflow-hidden">{seen && <LiveView child={child} repo={repo} />}</div>
      </div>
    </section>
  );
}

/** The helper's own step rail, terminal and words, from its own event stream. Subscribed from the first open. */
function LiveView({ child, repo }: { child: ChildRun; repo: string }) {
  const run = useRunStream(child.runId, child.role, repo, true);
  if (!run) {
    return (
      <div className="border-t border-line px-4 py-3">
        <p className="text-[0.85rem] text-fg-subtle">Connecting to the {ROLE_LABEL[child.role].toLowerCase()} run</p>
      </div>
    );
  }
  const working = run.status.working;
  return (
    <div className="flex flex-col gap-3 border-t border-line px-4 py-4">
      <StepRail steps={run.steps} />
      {run.blocks.length === 0 && working && <ThinkingPill />}
      {run.blocks.map((block, i) => (
        <HelperBlock key={block.id} block={block} live={working && i === run.blocks.length - 1} />
      ))}
      {run.failure && <p className="text-[0.85rem] text-fg-muted">{run.failure.reason}</p>}
    </div>
  );
}

function HelperBlock({ block, live }: { block: Block; live: boolean }) {
  if (block.kind === "text") return <AgentMessage text={block.text} live={live} />;
  if (block.kind === "terminal") return <TerminalBlock commands={block.commands} />;
  if (block.kind === "action") return <ActionRow block={block} />;
  return null;
}

function outcome(value: unknown): string {
  return value === "fail" ? "fails" : value === "pass" ? "passes" : value === "error" ? "could not run" : "not reported";
}

/** "Independently verified", with before and after, once the prover reported; errors in plain neutral text. */
function ProverResult({ result, failed }: { result: Record<string, unknown> | null; failed: boolean }) {
  if (!result || result.parse_error) {
    const raw = typeof result?.raw === "string" ? withoutThoughts(result.raw).trim() : "";
    return (
      <div className="border-t border-line px-4 py-3 text-[0.85rem] leading-relaxed text-fg-muted">
        <p>{failed ? "The prover stopped before it reported a result." : "The prover finished without a readable result."}</p>
        {raw && <p className="mt-1 line-clamp-3 whitespace-pre-wrap text-fg-subtle">{raw}</p>}
      </div>
    );
  }
  const before = result.before;
  const after = result.after;
  const verified = before === "fail" && after === "pass";
  const still = after === "fail";
  const errored = before === "error" || after === "error";
  const comment = typeof result.comment_url === "string" && result.comment_url ? result.comment_url : null;
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 border-t border-line px-4 py-3 text-[0.85rem]">
      <span className={clsx("font-medium", verified ? "text-accent" : still ? "text-fail" : "text-fg-muted")}>
        {verified ? "Independently verified" : still ? "Still exploitable" : errored ? "Could not verify" : "Inconclusive"}
      </span>
      <span className="text-fg-muted">
        Proof test <span className="font-mono text-fg">{outcome(before)}</span> before the fix and <span className="font-mono text-fg">{outcome(after)}</span> after it
      </span>
      {comment && (
        <a href={comment} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-fg-muted underline decoration-line-strong underline-offset-4 transition-colors duration-150 hover:text-fg">
          PR comment
          <ArrowSquareOut weight="bold" className="size-3.5" aria-hidden />
        </a>
      )}
    </div>
  );
}
