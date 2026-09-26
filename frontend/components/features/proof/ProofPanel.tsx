"use client";

import { ShieldCheck, ShieldWarning } from "@phosphor-icons/react";
import clsx from "clsx";
import { motion, useReducedMotion, type Transition } from "motion/react";
import { useEffect, useState, type ReactNode } from "react";
import { withoutThoughts } from "@/components/chat/AgentText";
import { fetchVerification, type Verification } from "@/lib/api";
import type { AdvisoryProof, ProofPhase, ProofVerdict } from "@/lib/features/proof";
import type { RunState } from "@/lib/state";

export type ProofPanelProps = {
  variant: "run" | "approval";
  // Null where the slot is shown outside a run, e.g. the policy sheet before any run starts.
  run: RunState | null;
};

// Rows enter like every other block in the conversation: occasional, 220ms strong ease out, a short lift.
const ENTER = { duration: 0.22, ease: [0.23, 1, 0.32, 1] } as const;
// The Closed flip happens once per advisory per run, so it may spend a little more: 240ms settle, 400ms wash.
const SETTLE = { duration: 0.24, ease: [0.23, 1, 0.32, 1] } as const;
const WASH: Transition = { duration: 0.4, ease: [0.23, 1, 0.32, 1], times: [0, 0.3, 1] };

const GHSA = /^GHSA(-[a-z0-9]{4}){3}$/i;

export function ProofPanel({ variant, run }: ProofPanelProps) {
  const advisories = run?.features.proof.advisories ?? [];
  if (variant === "approval") {
    return (
      <>
        <ApprovalLine advisories={advisories} />
        {run && <VerificationLine runId={run.id} />}
      </>
    );
  }
  return <RunPanel advisories={advisories} working={Boolean(run?.status.working)} />;
}

function RunPanel({ advisories, working }: { advisories: AdvisoryProof[]; working: boolean }) {
  const reduce = useReducedMotion();
  // Rows already there when the panel mounted (a replayed run) appear without an entrance.
  const [initial] = useState(() => new Set(advisories.map((a) => a.advisory)));
  if (advisories.length === 0) return null;

  return (
    <motion.section
      aria-labelledby="proof-title"
      initial={initial.size > 0 || reduce ? false : { opacity: 0, transform: "translateY(6px)" }}
      animate={{ opacity: 1, transform: "translateY(0px)" }}
      transition={ENTER}
      className="rounded-[var(--radius-card)] border border-line bg-panel/80"
    >
      <h3 id="proof-title" className="flex items-center gap-2 px-4 pb-1 pt-3 text-[0.9rem] font-semibold text-fg">
        <ShieldCheck weight="bold" className="size-4 text-fg-subtle" aria-hidden />
        Proof of fix
      </h3>
      <ul className="flex flex-col gap-4 px-4 pb-4 pt-2">
        {advisories.map((proof) => (
          <motion.li
            key={proof.advisory}
            initial={initial.has(proof.advisory) || reduce ? false : { opacity: 0, transform: "translateY(6px)" }}
            animate={{ opacity: 1, transform: "translateY(0px)" }}
            transition={ENTER}
          >
            <AdvisoryRow proof={proof} working={working} />
          </motion.li>
        ))}
      </ul>
    </motion.section>
  );
}

const VERDICT_LABEL: Record<ProofVerdict, { text: string; className: string }> = {
  proven_fixed: { text: "Proven fixed", className: "text-accent" },
  unproven: { text: "Unproven", className: "text-fg-muted" },
  still_exploitable: { text: "Still exploitable", className: "text-fail" },
  pending: { text: "Checking", className: "text-fg-subtle" },
};

function AdvisoryRow({ proof, working }: { proof: AdvisoryProof; working: boolean }) {
  const verdict = VERDICT_LABEL[proof.verdict];
  return (
    <>
      <p className="flex items-baseline justify-between gap-3">
        <Advisory id={proof.advisory} className="text-[0.82rem] text-fg" />
        <span className={clsx("shrink-0 text-[0.8rem] font-medium", verdict.className)}>{verdict.text}</span>
      </p>
      <div className="mt-2 grid grid-cols-2 gap-2 max-sm:grid-cols-1">
        <BeforeCell phase={proof.before} working={working} />
        <AfterCell phase={proof.after} working={working} />
      </div>
    </>
  );
}

type Tone = "fail" | "done" | "working" | "neutral";

const TONE_TEXT: Record<Tone, string> = {
  fail: "text-fail",
  done: "text-accent",
  working: "text-progress",
  neutral: "text-fg-muted",
};

function Dot({ tone }: { tone: Tone }) {
  return (
    <span
      aria-hidden
      className={clsx(
        "size-2 shrink-0 rounded-full",
        tone === "fail" && "bg-fail",
        tone === "done" && "bg-accent shadow-[0_0_10px_color-mix(in_oklab,var(--accent)_55%,transparent)]",
        tone === "working" && "dot-pulse bg-progress shadow-[0_0_10px_rgb(255_176_32/0.6)]",
        tone === "neutral" && "border border-line-strong",
      )}
    />
  );
}

function Cell({ label, tone, children, className }: { label: string; tone: Tone; children: ReactNode; className?: string }) {
  return (
    <div
      className={clsx(
        "relative overflow-hidden rounded-xl border bg-[#070707] px-3 py-2.5 transition-colors duration-200 ease-[var(--ease-out)]",
        tone === "done" ? "border-accent/30" : tone === "fail" ? "border-fail/30" : "border-line",
        className,
      )}
    >
      <p className="text-[0.72rem] font-medium text-fg-subtle">{label}</p>
      {children}
    </div>
  );
}

function StateLine({ tone, text, evidence }: { tone: Tone; text: string; evidence: string | null }) {
  return (
    <>
      <p className={clsx("mt-1 flex items-center gap-2 text-[0.88rem] font-medium", TONE_TEXT[tone])}>
        <Dot tone={tone} />
        {text}
      </p>
      {evidence && <p className="mt-1 whitespace-pre-wrap break-words font-mono text-[0.76rem] leading-relaxed text-fg-muted">{evidence}</p>}
    </>
  );
}

function BeforeCell({ phase, working }: { phase: ProofPhase | null; working: boolean }) {
  if (phase?.result === "exploitable") {
    return (
      <Cell label="Before" tone="fail">
        <StateLine tone="fail" text="Exploitable" evidence={phase.evidence} />
      </Cell>
    );
  }
  if (phase?.result === "not_reproduced") {
    return (
      <Cell label="Before" tone="neutral">
        <StateLine tone="neutral" text="Not reproduced" evidence={phase.evidence} />
      </Cell>
    );
  }
  return (
    <Cell label="Before" tone={working ? "working" : "neutral"}>
      <StateLine tone={working ? "working" : "neutral"} text={working ? "Running the proof test" : "Not run"} evidence={null} />
    </Cell>
  );
}

function AfterCell({ phase, working }: { phase: ProofPhase | null; working: boolean }) {
  const reduce = useReducedMotion();
  const closed = phase?.result === "closed";
  // Only a flip seen on screen plays the moment; a row that arrives already closed just shows it.
  const [closedOnMount] = useState(closed);
  const confirm = closed && !closedOnMount;

  if (closed) {
    return (
      <Cell label="After" tone="done">
        {confirm && (
          <motion.span
            aria-hidden
            initial={{ opacity: 0 }}
            animate={{ opacity: [0, 1, 0] }}
            transition={WASH}
            className="pointer-events-none absolute inset-0 bg-accent/[0.12]"
          />
        )}
        <motion.div
          initial={confirm ? { opacity: 0, transform: reduce ? "scale(1)" : "scale(0.96)" } : false}
          animate={{ opacity: 1, transform: "scale(1)" }}
          transition={SETTLE}
          style={{ transformOrigin: "left center" }}
          className="relative"
        >
          <StateLine tone="done" text="Closed" evidence={phase.evidence} />
        </motion.div>
      </Cell>
    );
  }
  if (phase?.result === "still_exploitable") {
    return (
      <Cell label="After" tone="fail">
        <StateLine tone="fail" text="Still exploitable" evidence={phase.evidence} />
      </Cell>
    );
  }
  return (
    <Cell label="After" tone={working ? "working" : "neutral"}>
      <StateLine tone={working ? "working" : "neutral"} text={working ? "Waiting for the fix" : "Not run"} evidence={null} />
    </Cell>
  );
}

function Advisory({ id, className }: { id: string; className?: string }) {
  const style = clsx("font-mono", className);
  return GHSA.test(id) ? (
    <a href={`https://github.com/advisories/${id}`} target="_blank" rel="noreferrer" className={clsx(style, "underline decoration-line-strong underline-offset-4 transition-colors duration-150 hover:decoration-fg-muted")}>
      {id}
    </a>
  ) : (
    <span className={style}>{id}</span>
  );
}

function List({ ids }: { ids: string[] }) {
  return (
    <>
      {ids.map((id, i) => (
        <span key={id}>
          {i > 0 && (i === ids.length - 1 ? " and " : ", ")}
          <Advisory id={id} className="text-[0.85em]" />
        </span>
      ))}
    </>
  );
}

/** One line above the hold control. Worst news first; an unproven or missing proof is always said plainly. */
function ApprovalLine({ advisories }: { advisories: AdvisoryProof[] }) {
  const by = (verdict: ProofVerdict) => advisories.filter((a) => a.verdict === verdict).map((a) => a.advisory);
  const still = by("still_exploitable");
  const unproven = by("unproven");
  const pending = by("pending");
  const proven = by("proven_fixed");
  const worst = still.length > 0 ? "fail" : unproven.length > 0 || advisories.length === 0 || pending.length > 0 ? "warn" : "done";
  const Icon = worst === "done" ? ShieldCheck : ShieldWarning;

  return (
    <p className="mt-5 flex items-start gap-2 text-[0.88rem] leading-relaxed text-fg">
      <Icon weight="bold" className={clsx("mt-[3px] size-4 shrink-0", worst === "fail" ? "text-fail" : worst === "warn" ? "text-progress" : "text-accent")} aria-hidden />
      <span>
        {advisories.length === 0 && <span className="text-fg-muted">No proof of fix was reported for this merge.</span>}
        {still.length > 0 && (
          <span>
            <span className="font-medium text-fail">Still exploitable:</span> <List ids={still} /> still reproduced after the fix.{" "}
          </span>
        )}
        {unproven.length > 0 && (
          <span>
            <span className="font-medium text-progress">Unproven:</span> <List ids={unproven} /> could not be reproduced before the fix, so the fix is not proven.{" "}
          </span>
        )}
        {pending.length > 0 && (
          <span>
            <span className="font-medium text-fg-muted">Proof pending:</span> <List ids={pending} /> not yet checked after the fix.{" "}
          </span>
        )}
        {proven.length > 0 && (
          <span>
            <span className="font-medium text-accent">Proven fixed:</span> <List ids={proven} /> reproduced, then closed.
          </span>
        )}
      </span>
    </p>
  );
}

const VERIFY_POLL_MS = 3000;

/** Why a prover could not verify, in its own words when it gave any. */
function proverReason(verification: Verification): string {
  for (const prover of verification.provers) {
    if (prover.state !== "error" && prover.state !== "inconclusive") continue;
    const result = prover.result;
    if (!result) return `the prover run ${prover.run_status === "done" ? "ended without a result" : "stopped"}`;
    if (result.parse_error) {
      const raw = typeof result.raw === "string" ? withoutThoughts(result.raw).trim() : "";
      return raw ? raw.split("\n").filter(Boolean).pop()!.slice(0, 200) : "the prover gave no readable result";
    }
    if (result.before === "error" || result.after === "error") return "the proof test could not run";
    return `the proof test ${result.before === "pass" ? "passed" : "did not fail"} before the fix`;
  }
  return "no result";
}

/** One line under the proof line: what the independent prover agent found for this run's PR. */
function VerificationLine({ runId }: { runId: string }) {
  const [verification, setVerification] = useState<Verification | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = async () => {
      try {
        const next = await fetchVerification(runId, controller.signal);
        if (controller.signal.aborted) return;
        setVerification(next);
        if (next.status !== "running") return;
      } catch {
        if (controller.signal.aborted) return;
      }
      timer = setTimeout(tick, VERIFY_POLL_MS);
    };
    void tick();
    return () => {
      controller.abort();
      if (timer) clearTimeout(timer);
    };
  }, [runId]);

  if (!verification) return null;
  const advisories = verification.provers.map((p) => p.advisory).filter((a): a is string => Boolean(a));
  const { status } = verification;
  const tone = status === "still_exploitable" ? "text-fail" : status === "proven" ? "text-accent" : status === "running" ? "text-progress" : "text-fg-muted";
  const Icon = status === "proven" ? ShieldCheck : ShieldWarning;

  return (
    <p className="mt-2 flex items-start gap-2 text-[0.88rem] leading-relaxed text-fg">
      <Icon weight="bold" className={clsx("mt-[3px] size-4 shrink-0", tone)} aria-hidden />
      <span>
        {status === "none" && <span className="text-fg-muted">No independent verification ran for this PR.</span>}
        {status === "running" && (
          <span>
            <span className="font-medium text-progress">Independent verification running</span>
            {advisories.length > 0 && <> for <List ids={advisories} /></>}.
          </span>
        )}
        {status === "proven" && (
          <span>
            <span className="font-medium text-accent">Independently verified:</span> the proof test failed before the fix and passes after it.
          </span>
        )}
        {status === "still_exploitable" && (
          <span>
            <span className="font-medium text-fail">Independent verification: still exploitable.</span> The proof test still fails after the fix.
          </span>
        )}
        {(status === "error" || status === "inconclusive") && (
          <span className="text-fg-muted">
            <span className="font-medium">Couldn&apos;t verify independently:</span> {proverReason(verification)}
          </span>
        )}
      </span>
    </p>
  );
}
